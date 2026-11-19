"""医生端辅助服务：处方明细解析与本地规则校验。

设计说明：
- 数据表由 docs/sql 建好，模型 managed=False，这里只做业务计算与校验，不碰表结构；
- 处方 AI 校验结果写入 prescription.ai_check_result / ai_check_comment，
  取值与种子数据口径一致（通过 / 风险提示），供药师端复核参考；
- 规则仅做提示，除库存不足外不强制拦截，最终由药师审核把关。
"""

from decimal import Decimal

from core.models import Drug, Patient, PrescriptionItem

RISK_RESULT = '风险提示'
PASS_RESULT = '通过'

# 常见配伍禁忌（课设演示用简化表，命中即提示人工复核）
COMBINATION_WARNINGS = [
    (('阿莫西林', '甲硝唑'), '联用需确认适应证，注意胃肠道反应。'),
    (('布洛芬', '阿司匹林'), '同类解热镇痛药联用增加消化道出血风险。'),
    (('华法林', '阿司匹林'), '抗凝与抗血小板联用出血风险增高。'),
    (('地高辛', '呋塞米'), '利尿剂可能引起低钾，增加地高辛中毒风险。'),
]

MAX_QTY_PER_ITEM = 30


def parse_rows(post):
    """从 POST 的并行数组还原处方明细草稿。

    返回 (rows, errors)：rows 为 [{drug_id, qty, dosage, is_decoction, decoction_num}]，
    errors 为面向医生的中文提示列表。
    """
    drug_ids = post.getlist('drug_id')
    qtys = post.getlist('qty')
    dosages = post.getlist('dosage')
    decoctions = post.getlist('is_decoction')
    decoction_nums = post.getlist('decoction_num')

    rows, errors = [], []
    seen = set()
    for idx, raw_drug in enumerate(drug_ids):
        raw_drug = (raw_drug or '').strip()
        if not raw_drug:
            continue
        if not raw_drug.isdigit():
            errors.append('第 %d 行药品选择无效，请重新选择。' % (idx + 1))
            continue
        drug_id = int(raw_drug)
        if drug_id in seen:
            errors.append('第 %d 行与前面的药品重复，同一药品只能添加一次。' % (idx + 1))
            continue
        seen.add(drug_id)

        raw_qty = (qtys[idx] if idx < len(qtys) else '').strip()
        if not raw_qty.isdigit() or int(raw_qty) <= 0:
            errors.append('第 %d 行数量必须为正整数。' % (idx + 1))
            continue

        dosage = (dosages[idx] if idx < len(dosages) else '').strip()
        if not dosage:
            errors.append('第 %d 行请填写用法用量。' % (idx + 1))
            continue

        decoction = (decoctions[idx] if idx < len(decoctions) else '0').strip() or '0'
        decoction_num = (decoction_nums[idx] if idx < len(decoction_nums) else '').strip()

        rows.append({
            'drug_id': drug_id,
            'qty': int(raw_qty),
            'dosage': dosage,
            'is_decoction': 1 if decoction == '1' else 0,
            'decoction_num': int(decoction_num) if decoction_num.isdigit() else None,
        })
    return rows, errors


def build_items(rows):
    """把明细草稿换算成带价格的行数据（单价、小计取自药品目录）。"""
    items, errors = [], []
    drugs = {d.drug_id: d for d in Drug.objects.filter(drug_id__in=[r['drug_id'] for r in rows])}
    for row in rows:
        drug = drugs.get(row['drug_id'])
        if not drug:
            errors.append('药品不存在，请重新选择。')
            continue
        if row['qty'] > drug.stock_qty:
            errors.append('「%s」库存不足（当前 %d，开具 %d）。'
                          % (drug.name, drug.stock_qty, row['qty']))
            continue
        unit_price = Decimal(str(drug.unit_price))
        items.append({
            'drug': drug,
            'drug_name': drug.name,
            'qty': row['qty'],
            'dosage': row['dosage'],
            'unit_price': unit_price,
            'subtotal': (unit_price * row['qty']).quantize(Decimal('0.01')),
            'is_decoction': row['is_decoction'],
            'decoction_num': row['decoction_num'],
        })
    return items, errors


def check_prescription(items, patient):
    """本地处方规则校验，返回 (ai_check_result, ai_check_comment)。

    规则：单次超量、过敏史命中、配伍禁忌、库存临界。
    """
    warnings = []
    allergy = (patient.allergy or '').strip() if patient else ''
    names = [i['drug'].name for i in items]

    for item in items:
        drug = item['drug']
        if item['qty'] > MAX_QTY_PER_ITEM:
            warnings.append('「%s」单次开具 %d 盒，超过常规上限 %d 盒，请确认疗程。'
                            % (drug.name, item['qty'], MAX_QTY_PER_ITEM))
        contra = (drug.contraindication or '').strip()
        if allergy and allergy not in ('无', '无记录'):
            for token in [allergy] + [a for a in allergy.replace('、', ',').replace('，', ',').split(',') if a]:
                token = token.strip()
                if token and (token in drug.name or token in contra):
                    warnings.append('患者过敏史含「%s」，与「%s」存在冲突，请改用替代药品。'
                                    % (token, drug.name))

    for pair, hint in COMBINATION_WARNINGS:
        if all(any(key in n for n in names) for key in pair):
            warnings.append('联用提示：%s' % hint)

    if warnings:
        return RISK_RESULT, '；'.join(warnings[:4])
    return PASS_RESULT, '配伍与剂量未见明显禁忌。'


def existing_item_count(presc_id):
    return PrescriptionItem.objects.filter(prescription_id=presc_id).count()

"""处方流转服务。复用既有字段；锁序为处方 -> 药品 ID 升序 -> 操作日志。

配药为核对步骤，实际交付时才扣库存。外购不由院内药师代确认取药。
明细始终使用 values / 双键 filter，不对联合主键实例 save/delete。
"""
import hashlib
import json

from django.core import signing
from django.db import transaction
from django.db.models import F, Max
from django.utils import timezone

from core.log_service import write_log
from core.models import Drug, OperationLog, Prescription, PrescriptionItem


class WorkflowError(Exception):
    def __init__(self, message, status=409):
        self.message, self.status = message, status
        super().__init__(message)


def _snapshot(p):
    items = list(PrescriptionItem.objects.filter(prescription_id=p.pk)
                 .order_by('drug_id').values('drug_id', 'qty', 'dosage', 'unit_price',
                                            'subtotal', 'is_decoction', 'decoction_num'))
    last_log = OperationLog.objects.filter(target_type='prescription', target_id=p.pk).aggregate(
        value=Max('log_id'))['value']
    fields = (p.pk, p.status, p.pharmacist_id, p.doctor_confirm, p.pickup_method,
              p.pickup_status, p.updated_at, p.audit_time, p.dispensed_at, last_log, items)
    return hashlib.sha256(json.dumps(fields, default=str, ensure_ascii=False).encode()).hexdigest()


def version_token(p, purpose):
    return signing.dumps({'id': p.pk, 'purpose': purpose, 'version': _snapshot(p)},
                         salt='pharmacist.workflow')


def check_version(p, token, purpose):
    try:
        data = signing.loads(token or '', salt='pharmacist.workflow', max_age=7200)
    except (signing.BadSignature, TypeError):
        raise WorkflowError('操作凭证无效或已过期，请刷新页面后重试。')
    if data != {'id': p.pk, 'purpose': purpose, 'version': _snapshot(p)}:
        raise WorkflowError('处方已经变化或被处理，请刷新页面核对最新内容后重试。')


def _lock(presc_id, actor_id, role):
    p = Prescription.objects.select_for_update().filter(pk=presc_id).first()
    permitted = p and (p.patient_id == actor_id if role == 'patient' else
                      p.doctor_id == actor_id if role == 'doctor' else
                      p.pharmacist_id in (None, actor_id))
    if not permitted:
        raise WorkflowError('未找到可操作的处方。', 404)
    return p


def _items(p):
    items = list(PrescriptionItem.objects.filter(prescription_id=p.pk)
                 .order_by('drug_id').values('drug_id', 'qty', 'dosage',
                                            'is_decoction', 'decoction_num'))
    if not items or any(row['qty'] <= 0 or not row['dosage'].strip() for row in items):
        raise WorkflowError('处方药品明细不完整，不能继续操作。')
    return items


@transaction.atomic
def audit(presc_id, pharmacist_id, decision, reason, token):
    if decision not in ('approve', 'reject'):
        raise WorkflowError('请选择有效的审核结论。', 400)
    reason = (reason or '').strip()
    if decision == 'reject' and (not reason or len(reason) > 252):
        raise WorkflowError('驳回理由必填，且最多 252 个字符。', 400)
    p = _lock(presc_id, pharmacist_id, 'pharmacist')
    if p.status != '待审核' or p.doctor_confirm != 1 or p.pickup_status or p.dispensed_at:
        raise WorkflowError('仅医生正式提交且尚未处理的待审核处方可以审核。')
    check_version(p, token, 'audit')
    if decision == 'approve':
        _items(p)
    p.status = '已通过' if decision == 'approve' else '已驳回'
    p.pharmacist_id = pharmacist_id
    p.audit_conclusion = '处方审核通过。' if decision == 'approve' else '驳回：' + reason
    p.audit_time = p.updated_at = timezone.now()
    p.pickup_method = None
    p.save(update_fields=['status', 'pharmacist', 'audit_conclusion', 'audit_time',
                          'pickup_method', 'updated_at'])
    write_log(pharmacist_id, '药师', '审核通过' if decision == 'approve' else '驳回处方',
              'prescription', p.pk)
    return p


@transaction.atomic
def choose_pickup(presc_id, patient_id, post):
    method = post.get('pickup_method')
    if method not in ('0', '1'):
        raise WorkflowError('请选择有效的取药方式。', 400)
    p = _lock(presc_id, patient_id, 'patient')
    if p.status != '已通过' or p.pickup_status or p.dispensed_at or p.pickup_method is not None:
        raise WorkflowError('当前处方不能重复选择或更改取药方式。')
    check_version(p, post.get('version'), 'pickup')
    items = _items(p)
    ids, flags, nums = (post.getlist(key) for key in
                        ('item_drug_id', 'item_decoction', 'item_decoction_num'))
    if len(ids) != len(items) or len(flags) != len(ids) or len(nums) != len(ids):
        raise WorkflowError('药品明细不完整，请刷新处方后重试。', 400)
    expected = {str(row['drug_id']) for row in items}
    if set(ids) != expected or len(set(ids)) != len(ids):
        raise WorkflowError('药品明细与当前处方不一致。', 400)
    updates = []
    for drug_id, flag, raw in zip(ids, flags, nums):
        if flag not in ('0', '1'):
            raise WorkflowError('代煎选项无效。', 400)
        if flag == '1' and (method == '1' or not raw.isascii() or not raw.isdigit()
                            or len(raw) > 5 or not 1 <= int(raw) <= 9999):
            raise WorkflowError('院内代煎剂数须为 1–9999 的整数；社会药店外购不提供院内代煎。', 400)
        updates.append((int(drug_id), int(flag), int(raw) if flag == '1' else None))
    for drug_id, flag, num in updates:
        PrescriptionItem.objects.filter(prescription_id=p.pk, drug_id=drug_id).update(
            is_decoction=flag, decoction_num=num)
    p.pickup_method = int(method)
    p.status = '待配药' if method == '0' else '已通过'
    p.updated_at = timezone.now()
    p.save(update_fields=['pickup_method', 'status', 'updated_at'])
    write_log(patient_id, '患者', '选择院内取药' if method == '0' else '选择社会药店外购',
              'prescription', p.pk)
    return p


@transaction.atomic
def dispense(presc_id, pharmacist_id, post):
    p = _lock(presc_id, pharmacist_id, 'pharmacist')
    if (p.status != '待配药' or p.pharmacist_id != pharmacist_id or p.pickup_method != 0
            or p.pickup_status or p.dispensed_at or not p.audit_time or p.doctor_confirm != 1):
        raise WorkflowError('仅本人审核且选择院内取药、尚未发药的待配药处方可以发药。')
    check_version(p, post.get('version'), 'dispense')
    if post.get('checked') != '1':
        raise WorkflowError('请确认已核对药品、数量、患者身份并完成实际交付。', 400)
    items = _items(p)
    if any(row['is_decoction'] for row in items):
        if post.get('decoction_done') != '1' or any(
                row['is_decoction'] and (not row['decoction_num'] or row['decoction_num'] <= 0)
                for row in items):
            raise WorkflowError('须核对代煎剂数并确认代煎已完成后才能实际发药。', 400)
    drugs = {drug.pk: drug for drug in Drug.objects.select_for_update().filter(
        pk__in=[row['drug_id'] for row in items]).order_by('drug_id')}
    for row in items:
        drug = drugs.get(row['drug_id'])
        if drug is None or drug.stock_qty < row['qty']:
            raise WorkflowError('库存不足，未扣减任何药品，未完成发药。')
    now = timezone.now()
    for row in items:
        changed = Drug.objects.filter(pk=row['drug_id'], stock_qty__gte=row['qty']).update(
            stock_qty=F('stock_qty') - row['qty'], updated_at=now)
        if changed != 1:
            raise WorkflowError('库存发生变化，发药已取消，请刷新核对。')
    p.status, p.pickup_status = '已完成', 1
    p.dispensed_at = p.updated_at = now
    p.save(update_fields=['status', 'pickup_status', 'dispensed_at', 'updated_at'])
    write_log(pharmacist_id, '药师', '配药扣库存', 'prescription', p.pk)
    if any(row['is_decoction'] for row in items):
        write_log(pharmacist_id, '药师', '确认代煎完成', 'prescription', p.pk)
    write_log(pharmacist_id, '药师', '确认实际发药', 'prescription', p.pk)
    return p

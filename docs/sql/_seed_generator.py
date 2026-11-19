# -*- coding: utf-8 -*-
"""
种子数据生成器（开发期工具，普通组员初始化数据库无需运行本脚本）。
直接按顺序执行生成好的 3 个 SQL 即可：
  create_tables.sql → seed_data.sql → seed_business_extra.sql
需要调整数据规模时，修改本脚本后重跑，重新生成下面两个种子文件：
  seed_data.sql          账号(管理员2/患者80/医生55/药师50) + 药品95
  seed_business_extra.sql 排班/问诊/AI评估/消息/转诊/处方/明细/操作日志
编号分段：
  admin 1-2；patient 3-82；doctor 83-137；pharmacist 138-187；drug 1-95
"""
import os
import random

random.seed(20261118)

BASE = os.path.dirname(os.path.abspath(__file__))

# ---------- 基础字典 ----------
SURNAMES = list("王李张刘陈杨赵黄周吴徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾肖田董袁潘蒋蔡余杜叶程苏魏吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏韦付方白邹孟熊秦邱江尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严覃武戴莫孔向汤")
GIVEN = ["伟","芳","娜","敏","静","磊","军","洋","勇","艳","杰","涛","明","超","秀英","霞","平","刚","桂英","文华","建国","志强","雪","晨","宇","欣","子涵","雨泽","欣怡","浩然","梓萱","一诺","思远","嘉琪","文博","雅静","俊杰","晓东","淑芬","桂兰"]

DEPTS = ["内科","呼吸科","儿科","皮肤科","消化科","内分泌科","心血管科","中医科","神经内科","骨科","眼科","耳鼻喉科"]
TITLES = ["主任医师","副主任医师","主治医师","住院医师"]
SKILLS = {
    "内科": "常见内科感染、慢病随访",
    "呼吸科": "肺炎、哮喘、慢性咳嗽",
    "儿科": "儿童发热、咳嗽、过敏性鼻炎",
    "皮肤科": "湿疹、荨麻疹、常见皮炎",
    "消化科": "胃炎、消化不良、肠道疾病",
    "内分泌科": "糖尿病、甲状腺疾病随访",
    "心血管科": "高血压、冠心病随访",
    "中医科": "中医辨证调理、慢性病调养",
    "神经内科": "头痛、眩晕、睡眠障碍调理",
    "骨科": "颈肩腰腿痛、关节退行性病变",
    "眼科": "常见眼表疾病、视力疲劳",
    "耳鼻喉科": "鼻炎、咽炎、耳鸣耳部不适",
}
PHARM_TYPES = ["主任药师","主管药师","药师"]
AREAS = ["天津市南开区","天津市和平区","天津市河西区","天津市河北区","天津市红桥区","天津市河东区","天津市津南区","天津市北辰区"]
ALLERGIES = ["无","青霉素过敏","花粉过敏","海鲜过敏","坚果过敏"]

# 药品：名称, 规格, 单价, 是否处方药(1/0), 禁忌
DRUGS = [
    ("阿莫西林胶囊","0.25g*24粒",18.50,1,"青霉素过敏者禁用"),
    ("布洛芬缓释胶囊","0.3g*20粒",22.00,0,"胃功能不全者慎用"),
    ("氨酚伪麻美芬片","12片/盒",15.80,0,"高血压、心脏病患者慎用"),
    ("孟鲁司特钠片","10mg*5片",35.00,1,"对本品成分过敏者禁用"),
    ("氯雷他定片","10mg*6片",12.60,0,"严重肝功能不全者慎用"),
    ("奥美拉唑肠溶胶囊","20mg*14粒",20.30,1,"对本品过敏者禁用"),
    ("复方甘草片","100片",9.90,1,"不宜长期服用，孕妇慎用"),
    ("连花清瘟胶囊","0.35g*24粒",14.80,0,"风寒感冒者不适用"),
    ("盐酸氨溴索口服溶液","100ml",17.20,0,"对本品过敏者禁用"),
    ("头孢克肟分散片","0.1g*12片",26.40,1,"头孢类过敏者禁用"),
    ("二甲双胍缓释片","0.5g*30片",24.00,1,"肾功能不全者慎用"),
    ("氨氯地平片","5mg*14片",19.50,1,"低血压患者慎用"),
    ("维生素C片","100mg*100片",8.80,0,"长期大量服用需遵医嘱"),
    ("蒙脱石散","3g*10袋",13.40,0,"便秘时停用"),
    ("健胃消食片","0.8g*32片",10.20,0,"忌生冷油腻食物"),
    ("对乙酰氨基酚片","0.5g*20片",12.00,0,"胃肠不适者慎用"),
    ("阿司匹林肠溶片","100mg*30片",16.80,1,"出血倾向、胃溃疡者慎用"),
    ("酚麻美敏片","12片/盒",18.60,0,"高血压、心脏病患者慎用"),
    ("小儿氨酚黄那敏颗粒","6g*12袋",15.00,0,"1岁以下婴儿遵医嘱使用"),
    ("复方感冒灵颗粒","14g*9袋",13.50,0,"风寒感冒者不适用"),
    ("感冒清热颗粒","12g*18袋",19.80,0,"风热感冒者不适用"),
    ("双黄连口服液","10ml*10支",16.20,0,"脾胃虚寒者慎用"),
    ("清热解毒口服液","10ml*10支",14.40,0,"对本品成分过敏者禁用"),
    ("头孢氨苄胶囊","0.25g*24粒",21.00,1,"头孢类过敏者禁用"),
    ("阿奇霉素片","0.25g*6片",28.50,1,"肝功能不全者慎用"),
    ("左氧氟沙星片","0.1g*12片",23.80,1,"18岁以下及孕妇禁用"),
    ("克拉霉素片","0.25g*8片",32.00,1,"肝功能不全者慎用"),
    ("氨溴特罗口服溶液","100ml",31.50,1,"甲状腺亢进、心律失常者慎用"),
    ("沙丁胺醇气雾剂","100μg*200揿",42.00,1,"严重心律失常者慎用"),
    ("羧甲司坦颗粒","0.5g*12袋",18.00,0,"胃溃疡患者慎用"),
    ("小柴胡颗粒","10g*10袋",12.60,0,"对本品成分过敏者禁用"),
    ("多潘立酮片","10mg*30片",25.40,1,"严重心脏疾病者慎用"),
    ("法莫替丁片","20mg*24片",19.20,1,"肾功能不全者慎用"),
    ("枸橼酸铋钾胶囊","0.3g*24粒",22.60,1,"服药期间大便可发黑，属正常现象"),
    ("盐酸西替利嗪片","10mg*12片",14.80,0,"肾功能不全者慎用"),
    ("头孢呋辛酯片","0.25g*12片",27.60,1,"头孢类过敏者禁用"),
    ("罗红霉素胶囊","0.15g*12粒",23.00,1,"肝功能不全者慎用"),
    ("甲硝唑片","0.2g*21片",6.50,1,"孕妇及哺乳期妇女禁用"),
    ("诺氟沙星胶囊","0.1g*24粒",9.80,1,"18岁以下禁用"),
    ("磷酸奥司他韦胶囊","75mg*10粒",68.00,1,"对本品成分过敏者禁用"),
    ("盐酸甲氧那明片","12片",24.50,1,"高血压、青光眼患者慎用"),
    ("乙酰半胱氨酸颗粒","0.2g*12袋",19.60,0,"哮喘患者慎用"),
    ("急支糖浆","100ml",15.30,0,"对本品成分过敏者禁用"),
    ("川贝枇杷膏","150ml",28.80,0,"糖尿病患者慎用"),
    ("银黄含片","24片",11.20,0,"脾胃虚寒者慎用"),
    ("西瓜霜含片","24片",9.50,0,"孕妇慎用"),
    ("板蓝根颗粒","10g*20袋",13.60,0,"对本品成分过敏者禁用"),
    ("牛黄解毒片","24片",8.20,0,"孕妇、哺乳期禁用，不宜久服"),
    ("黄连上清片","36片",10.80,0,"脾胃虚寒者慎用"),
    ("藿香正气水","10ml*10支",12.40,0,"含酒精，服药后不得驾车"),
    ("保和丸","36g",14.20,0,"忌食生冷油腻"),
    ("香砂养胃丸","36g",16.50,0,"胃痛阴虚者慎用"),
    ("雷贝拉唑钠肠溶片","10mg*14片",28.90,1,"对本品过敏者禁用"),
    ("铝碳酸镁咀嚼片","0.5g*24片",21.30,0,"严重肾功能不全者慎用"),
    ("复方消化酶胶囊","20粒",26.70,1,"急性胰腺炎早期禁用"),
    ("双歧杆菌三联活菌胶囊","36粒",32.40,1,"需冷藏保存，温水送服"),
    ("硝苯地平缓释片","20mg*30片",22.80,1,"低血压患者慎用"),
    ("美托洛尔缓释片","47.5mg*14片",25.60,1,"心动过缓、哮喘患者慎用"),
    ("辛伐他汀片","20mg*14片",18.90,1,"肝功能不全者慎用"),
    ("氯沙坦钾片","50mg*14片",31.20,1,"妊娠中晚期禁用"),
    ("格列美脲片","2mg*30片",23.50,1,"可能引起低血糖"),
    ("甘精胰岛素注射液","3ml:300单位",78.00,1,"须遵医嘱皮下注射，注意低血糖"),
    ("左甲状腺素钠片","50μg*100片",26.30,1,"剂量需个体化，定期复查"),
    ("甲钴胺片","0.5mg*20片",19.80,1,"从事汞及其化合物工作者不宜久服"),
    ("布洛芬混悬液","100ml",16.70,0,"儿童按体重折算剂量"),
    ("小儿麻甘颗粒","6g*10袋",14.90,0,"对本品成分过敏者禁用"),
    ("葡萄糖酸钙锌口服溶液","10ml*24支",29.60,0,"肾功能不全者慎用"),
    ("蒙脱石粉","3g*15袋",18.20,0,"便秘时停用"),
    ("小儿止咳糖浆","100ml",13.80,0,"忌食生冷辛辣"),
    ("糠酸莫米松乳膏","10g",24.30,1,"皮肤破损处慎用，不宜长期大面积使用"),
    ("丁酸氢化可的松乳膏","10g",17.50,0,"感染性皮肤病禁用"),
    ("夫西地酸乳膏","10g",21.80,1,"对本品过敏者禁用"),
    ("复方酮康唑软膏","10g",12.60,0,"病毒性感染皮损禁用"),
    ("尿素维E乳膏","40g",11.40,0,"皮肤破溃处慎用"),
    ("阿达帕林凝胶","30g",28.90,1,"孕妇禁用，避免日晒"),
    ("壬二酸乳膏","15g",23.70,0,"用药初期可能有局部刺激"),
    ("双氯芬酸钠凝胶","40g",19.20,0,"破损皮肤禁用"),
    ("云南白药气雾剂","85g",38.50,0,"孕妇禁用，皮肤破损处禁用"),
    ("活血止痛胶囊","30粒",16.80,0,"孕妇禁用"),
    ("颈复康颗粒","5g*10袋",27.40,0,"消化道溃疡患者慎用"),
    ("迈之灵片","20片",33.60,1,"胃溃疡患者慎用"),
    ("盐酸氟桂利嗪胶囊","5mg*20粒",18.50,1,"抑郁病史、帕金森患者慎用"),
    ("尼莫地平片","20mg*30片",21.90,1,"低血压患者慎用"),
    ("帕罗西汀片","20mg*14片",46.80,1,"须精神科医生指导，不可骤停"),
    ("艾司唑仑片","1mg*20片",12.30,1,"二类精神药品，成瘾性，遵医嘱"),
    ("谷维素片","10mg*100片",9.60,0,"对本品成分过敏者禁用"),
    ("归脾丸","36g",15.80,0,"感冒发热者暂停服用"),
    ("杞菊地黄丸","36g",17.20,0,"脾胃虚寒便溏者慎用"),
    ("安神补脑液","10ml*10支",22.60,0,"糖尿病患者慎用"),
    ("阿胶补血口服液","10ml*20支",35.80,0,"感冒期间停用"),
    ("多维元素片","30片",26.40,0,"不宜超过推荐剂量"),
    ("叶酸片","0.4mg*31片",12.80,0,"对本品成分过敏者禁用"),
    ("碳酸钙D3片","60片",28.70,0,"高钙血症者禁用"),
    ("褪黑素片","30片",19.50,0,"驾车、高空作业者睡前勿服"),
    ("复方甘草酸苷片","100片",32.90,1,"醛固酮症、肌病患者慎用"),
]
assert len(DRUGS) == 95, len(DRUGS)

# ---------- 编号分段 ----------
ADMIN_IDS = [1, 2]
PAT_IDS = list(range(3, 83))      # 80
DOC_IDS = list(range(83, 138))   # 55
PHARM_IDS = list(range(138, 188)) # 50
assert len(PAT_IDS) == 80 and len(DOC_IDS) == 55 and len(PHARM_IDS) == 50

PWD = "demo123456"

def esc(s):
    return str(s).replace("'", "''")

# ---------- 生成账号/档案 ----------
def gen_users_and_profiles():
    rows_user, rows_admin, rows_patient, rows_doc, rows_pharm = [], [], [], [], []

    # 管理员
    admin_names = ["系统管理员", "后台管理员"]
    admin_phones = ["13800000001", "13800000003"]
    for i, uid in enumerate(ADMIN_IDS):
        phone = admin_phones[i]
        rows_user.append((uid, admin_names[i], phone, PWD, 0, 1, "2026-11-18 08:00:00"))
        rows_admin.append((uid, f"admin{i+1}" if i else "admin", "2026-11-01 09:00:00"))

    # 患者：user_id 3..82；user_id=3 兼容旧演示号 13800000002
    for k, pid in enumerate(PAT_IDS):
        sur = SURNAMES[k % len(SURNAMES)]
        giv = GIVEN[k % len(GIVEN)]
        name = sur + giv
        phone = "13800000002" if pid == 3 else f"139{pid:08d}"
        rows_user.append((pid, name, phone, PWD, 1, 1, "2026-11-18 10:00:00" if k % 3 else None))
        gender = "女" if k % 2 else "男"
        age = 18 + (k * 7) % 70
        allergy = ALLERGIES[k % len(ALLERGIES)]
        area = AREAS[k % len(AREAS)]
        idcard = f"130102{1960 + (k % 65):04d}{(k % 12) + 1:02d}{(k % 28) + 1:02d}{pid:04d}"
        medical = f"BL2026{pid - 2:04d}"
        rows_patient.append((pid, idcard, gender, age, allergy, area, medical, "2026-10-20 10:00:00"))

    # 医生：83..137
    for k, did in enumerate(DOC_IDS):
        dept = DEPTS[k % len(DEPTS)]
        title = TITLES[k % len(TITLES)]
        skill = SKILLS[dept]
        gender = "女" if k % 2 else "男"
        status = [0, 1, 2][k % 3]
        name = f"{SURNAMES[k % len(SURNAMES)]}医生"
        phone = f"137{did:08d}"
        rows_user.append((did, name, phone, PWD, 2, 1, "2026-11-18 08:30:00" if k % 4 else None))
        rows_doc.append((did, status, gender, dept, title, skill))

    # 药师：138..187
    for k, hid in enumerate(PHARM_IDS):
        ptype = PHARM_TYPES[k % len(PHARM_TYPES)]
        gender = "女" if k % 2 else "男"
        status = 1 if k % 3 else 0
        name = f"{SURNAMES[k % len(SURNAMES)]}药师"
        phone = f"136{hid:08d}"
        rows_user.append((hid, name, phone, PWD, 3, 1, "2026-11-18 08:15:00" if k % 3 else None))
        rows_pharm.append((hid, ptype, status, gender))

    return rows_user, rows_admin, rows_patient, rows_doc, rows_pharm


def fmt_user(r):
    uid, name, phone, pwd, role, status, last = r
    last_sql = "NULL" if last is None else f"'{last}'"
    return f"({uid}, '{esc(name)}', '{phone}', '{pwd}', {role}, {status}, {last_sql})"


def build_seed_data():
    users, admin, patient, doc, pharm = gen_users_and_profiles()
    lines = []
    lines.append("-- =====================================================================")
    lines.append("-- 智慧医疗平台 · 基础种子数据（账号 + 档案 + 药品目录）")
    lines.append("-- 前置：先执行 create_tables.sql；本脚本可重复执行（先清空再灌入）")
    lines.append("-- 规模：管理员 2 / 患者 80 / 医生 55 / 药师 50 / 药品 95；密码为演示占位串")
    lines.append("-- =====================================================================")
    lines.append("")
    lines.append("USE medical_platform;")
    lines.append("SET FOREIGN_KEY_CHECKS = 0;")
    lines.append("")
    for t in ["operation_log","prescription_item","prescription","referral_log",
              "consultation_message","ai_pre_assessment","consultation","duty_schedule",
              "drug","pharmacist","doctor","patient","admin","sys_user"]:
        lines.append(f"TRUNCATE TABLE {t};")
    lines.append("")

    # 用户
    lines.append("-- 1. 用户表 sys_user")
    lines.append("INSERT INTO sys_user (user_id, name, phone, password, role_type, status, last_login) VALUES")
    lines.append(",\n".join(fmt_user(r) for r in users) + ";")
    lines.append("")

    # 管理员
    lines.append("-- 2. 管理员 admin")
    lines.append("INSERT INTO admin (admin_id, account, created_at) VALUES")
    lines.append(",\n".join(f"({a}, '{acc}', '{t}')" for a, acc, t in admin) + ";")
    lines.append("")

    # 患者
    lines.append("-- 3. 患者 patient")
    lines.append("INSERT INTO patient (patient_id, id_card_no, gender, age, allergy, address, medical_no, created_at) VALUES")
    pvals = []
    for pid, idc, g, age, al, addr, med, t in patient:
        pvals.append(f"({pid}, '{idc}', '{g}', {age}, '{esc(al)}', '{addr}', '{med}', '{t}')")
    lines.append(",\n".join(pvals) + ";")
    lines.append("")

    # 医生
    lines.append("-- 4. 医生 doctor")
    lines.append("INSERT INTO doctor (doctor_id, work_status, gender, department, title, skill) VALUES")
    dvals = []
    for did, st, g, dept, title, skill in doc:
        dvals.append(f"({did}, {st}, '{g}', '{dept}', '{title}', '{esc(skill)}')")
    lines.append(",\n".join(dvals) + ";")
    lines.append("")

    # 药师
    lines.append("-- 5. 药师 pharmacist")
    lines.append("INSERT INTO pharmacist (pharmacist_id, pharmacist_type, pharmacist_status, gender) VALUES")
    hvals = []
    for hid, pt, st, g in pharm:
        hvals.append(f"({hid}, '{pt}', {st}, '{g}')")
    lines.append(",\n".join(hvals) + ";")
    lines.append("")

    # 药品
    lines.append("-- 6. 药品目录 drug（95 种）")
    lines.append("INSERT INTO drug (drug_id, drug_code, name, specification, unit_price, stock_qty, is_prescription, contraindication, updated_at) VALUES")
    dvals = []
    for i, (name, spec, price, rx, contra) in enumerate(DRUGS, start=1):
        code = f"YX{i:03d}"
        stock = 60 + (i * 13) % 260
        dvals.append(f"({i}, '{code}', '{esc(name)}', '{spec}', {price:.2f}, {stock}, {rx}, '{esc(contra)}', '2026-11-18 08:00:00')")
    lines.append(",\n".join(dvals) + ";")
    lines.append("")
    lines.append("SET FOREIGN_KEY_CHECKS = 1;")
    return "\n".join(lines) + "\n"


# ---------- 业务表 ----------
COMPLAINTS = [
    "咳嗽两天，伴低热 37.8℃，无胸痛，自服感冒药效果一般。",
    "孩子发热一天，最高 38.5℃，精神尚可，无皮疹。",
    "双上肢红疹三天，瘙痒明显，近期未接触新衣物。",
    "餐后反酸、腹胀两周，偶有胃痛，饮食不规律。",
    "头晕伴血压偏高一周，既往未规律服药。",
    "口干、多饮多尿两周，体重略有下降。",
    "反复头痛三天，以双侧颞部为主，无恶心呕吐。",
    "颈部酸痛伴右肩放射痛一周，低头时加重。",
    "双眼干涩、视物易疲劳一周，近期长时间看屏幕。",
    "鼻塞流涕三天，伴咽部不适，无明显发热。",
]
AI_SUMM = [
    "患者主诉{comp}，暂未描述胸痛、气促等急症指征，按常见轻症整理，供医生复核。",
    "结合主诉与病史，症状偏向慢性/常见问题，未见明显红旗体征，建议线下随访观察。",
    "已结构化主诉、伴随症状与持续时间，AI 结论仅供参考，最终诊断以医生判断为准。",
]
DOSAGES = ["口服，一次 1 粒，一日 2 次", "口服，一次 1 片，一日 3 次", "口服，一次 10ml，一日 2 次",
           "嚼服，一次 3 片，一日 3 次", "温水冲服，一次 1 袋，一日 3 次", "外用，涂于患处，一日 2 次"]
AUDIT_PASS = ["处方合理，审核通过。", "配伍与剂量未见明显禁忌，予以通过。", "用药与诊断匹配，审核通过。"]
AUDIT_REJECT = ["驳回：请调整用药联用方案后重新提交。", "驳回：儿童剂量需按体重折算，请复核后再提交。", "驳回：患者有相关过敏史，请更换药物。"]

CONSULT_STATUS = ["已结束"] * 38 + ["进行中"] * 10 + ["已转诊"] * 6 + ["已取消"] * 6
random.shuffle(CONSULT_STATUS)

DUTY_DOCTORS = DOC_IDS  # 每位医生一条排班


def _dt(day, hh, mm):
    return f"2026-11-{day:02d} {hh:02d}:{mm:02d}:00"


def build_business():
    lines = []
    lines.append("-- =====================================================================")
    lines.append("-- 智慧医疗平台 · 业务种子数据（排班/问诊/AI评估/消息/转诊/处方/明细/日志）")
    lines.append("-- 前置：已执行 create_tables.sql 与 seed_data.sql；本脚本可重复执行")
    lines.append("-- =====================================================================")
    lines.append("")
    lines.append("USE medical_platform;")
    lines.append("SET FOREIGN_KEY_CHECKS = 0;")
    lines.append("")
    for t in ["operation_log", "prescription_item", "prescription", "referral_log",
              "consultation_message", "ai_pre_assessment", "consultation", "duty_schedule"]:
        lines.append(f"TRUNCATE TABLE {t};")
    lines.append("")

    # 1. 排班 55 条
    schedules = []
    duty_rows = []
    for k, doc in enumerate(DUTY_DOCTORS, start=1):
        day = 13 + (k % 6)
        shift = "白班" if k % 2 else "夜班"
        if shift == "白班":
            st, et = _dt(day, 8, 0), _dt(day, 17, 0)
        else:
            st, et = _dt(day, 22, 0), _dt(day + 1, 8, 0) if day < 18 else _dt(day, 23, 59)
        status = "正常" if k % 9 else ("调班" if k % 3 else "停诊")
        duty_rows.append(f"({k}, {doc}, '{shift}', '{st}', '{et}', '{status}', '{_dt(12, 10, 0)}', 1)")
        schedules.append({"duty_code": k, "doctor_id": doc})
    lines.append("-- 1. 值班排班 duty_schedule（55 条）")
    lines.append("INSERT INTO duty_schedule (duty_code, doctor_id, shift_type, start_time, end_time, status, updated_at, created_by) VALUES")
    lines.append(",\n".join(duty_rows) + ";")
    lines.append("")

    # 2. 问诊 60 条
    consults = []
    consult_rows = []
    pre_rows = []
    msg_rows = []
    for cid in range(1, 61):
        pid = PAT_IDS[(cid - 1) % len(PAT_IDS)]
        doc = DOC_IDS[(cid - 1) % len(DOC_IDS)]
        duty = schedules[(cid - 1) % len(schedules)]["duty_code"] if cid % 3 else None
        status = CONSULT_STATUS[cid - 1]
        comp = COMPLAINTS[cid % len(COMPLAINTS)]
        day = 12 + (cid % 7)
        start = _dt(day, 9 + (cid % 10), (cid * 7) % 60)
        if status == "进行中":
            end = None
            review = "待复核"
        elif status == "已转诊":
            end = _dt(day, (10 + cid) % 12, (cid * 11) % 60)
            review = "已否决"
        elif status == "已取消":
            end = _dt(day, (9 + cid) % 12, (cid * 5) % 60)
            review = "待复核"
        else:
            end = _dt(day, (10 + cid) % 12, (cid * 9) % 60)
            review = ["已采纳", "已修正", "已采纳"][cid % 3]
        review_op = None
        if review == "已修正":
            review_op = "补充相关病史后，修正 AI 摘要"
        elif review == "已否决":
            review_op = "急症特征明显，直接转线下急诊"
        consults.append({"cid": cid, "pid": pid, "doc": doc, "duty": duty,
                          "status": status, "review": review, "start": start, "end": end, "comp": comp})
        end_sql = "NULL" if end is None else f"'{end}'"
        duty_sql = "NULL" if duty is None else str(duty)
        review_op_sql = "NULL" if review_op is None else f"'{esc(review_op)}'"
        diagnosis = "结合病史考虑常见轻症，建议对症处理并随访"
        diag_sql = "NULL" if status != "已结束" else f"'{esc(diagnosis)}'"
        consult_rows.append(
            f"({cid}, {doc}, {pid}, {duty_sql}, '{esc(comp)}', '{status}', '{review}', "
            f"{review_op_sql}, {diag_sql}, '{start}', {end_sql})"
        )
        # AI 评估
        risk = "高危" if status == "已转诊" else "中低危"
        keyword = "胸痛;呼吸困难;大汗" if status == "已转诊" else None
        summ = AI_SUMM[cid % 3].format(comp=comp)
        review_result = {"待复核": None, "已采纳": "已采纳", "已修正": "已修正", "已否决": "已否决"}[review]
        comment = review_op if review_result in ("已修正", "已否决") else None
        keyword_sql = "NULL" if keyword is None else f"'{esc(keyword)}'"
        review_sql = "NULL" if review_result is None else f"'{esc(review_result)}'"
        comment_sql = "NULL" if comment is None else f"'{esc(comment)}'"
        pre_rows.append(
            f"({cid}, {cid}, '{risk}', '{esc(summ)}', {keyword_sql}, "
            f"{review_sql}, {comment_sql}, '{start}')"
        )
        # 消息：患者一条 + 医生一条（已结束/进行中/转诊有医生回复；已取消仅患者一条）
        msg_rows.append(f"({cid}, '患者', {pid}, 'text', '{esc('医生您好，' + comp)}', NULL, '{start}', 1)")
        if status != "已取消":
            ans = "收到，我先结合病史了解一下具体情况，请补充有无加重或缓解因素。"
            read_flag = 1 if status == "已结束" else 0
            msg_rows.append(f"({cid}, '医生', {doc}, 'text', '{esc(ans)}', NULL, '{_dt(day, (10 + cid) % 12, (cid * 9) % 60)}', {read_flag})")

    lines.append("-- 2. 问诊记录 consultation（60 条）")
    lines.append("INSERT INTO consultation (consult_id, doctor_id, patient_id, duty_code, record_content, status, doctor_review_status, doctor_review_opinion, final_diagnosis, start_time, end_time) VALUES")
    lines.append(",\n".join(consult_rows) + ";")
    lines.append("")

    lines.append("-- 3. AI 预问诊评估 ai_pre_assessment（60 条）")
    lines.append("INSERT INTO ai_pre_assessment (pre_id, consult_id, risk_level, ai_summary, matched_keyword, review_result, review_comment, created_at) VALUES")
    lines.append(",\n".join(pre_rows) + ";")
    lines.append("")

    lines.append("-- 4. 问诊消息 consultation_message")
    lines.append("INSERT INTO consultation_message (consult_id, sender_role, sender_id, msg_type, content, media_url, send_time, read_status) VALUES")
    lines.append(",\n".join(msg_rows) + ";")
    lines.append("")

    # 5. 转诊日志 8 条：6 条已转诊 + 2 条坚持线上
    referrals = [c for c in consults if c["status"] == "已转诊"][:6]
    extra = [c for c in consults if c["status"] == "已结束"][:2]
    ref_rows = []
    rid = 1
    for c in referrals:
        ref_rows.append(
            f"({rid}, {c['cid']}, {c['pid']}, '高危急症', '胸痛;呼吸困难;大汗', "
            f"'引导线下急诊', {c['doc']}, '确认转诊', '医生', '{c['end']}')"
        )
        rid += 1
    for c in extra:
        ref_rows.append(
            f"({rid}, {c['cid']}, {c['pid']}, '高危急症', NULL, "
            f"'风险提示后继续线上', 1, '坚持线上', '系统', '{c['start']}')"
        )
        rid += 1
    lines.append("-- 5. 转诊日志 referral_log（8 条）")
    lines.append("INSERT INTO referral_log (referral_id, consult_id, patient_id, risk_level, matched_keyword, action, operator_id, patient_choice, operator_type, created_at) VALUES")
    lines.append(",\n".join(ref_rows) + ";")
    lines.append("")

    # 6. 处方 50 条（关联前 50 条问诊）
    price = {i + 1: d[2] for i, d in enumerate(DRUGS)}
    drug_names = {i + 1: d[0] for i, d in enumerate(DRUGS)}
    presc_status_seq = (["已完成"] * 14 + ["已通过"] * 10 + ["待配药"] * 8 +
                        ["待审核"] * 8 + ["已驳回"] * 5 + ["草稿"] * 5)
    random.shuffle(presc_status_seq)
    presc_rows, item_rows, log_rows = [], [], []
    for pidx, c in enumerate(consults[:50], start=1):
        st = presc_status_seq[pidx - 1]
        pharmacist = PHARM_IDS[pidx % len(PHARM_IDS)] if st in ("已通过", "已驳回", "待配药", "已完成") else "NULL"
        audit_concl = None
        audit_time = None
        ai_res, ai_comment, confirm, dispensed = "通过", "配伍与剂量未见明显禁忌。", 1, None
        if st == "已驳回":
            audit_concl = random.choice(AUDIT_REJECT)
            audit_time = c["start"]
            ai_res, ai_comment = "风险提示", "请复核联用与过敏史。"
        elif st in ("已通过", "待配药", "已完成"):
            audit_concl = random.choice(AUDIT_PASS)
            audit_time = c["end"] or c["start"]
        elif st == "草稿":
            ai_res, ai_comment, confirm = None, None, 0
        if st == "已完成":
            dispensed = c["end"] or c["start"]
        pickup_method = "NULL" if st == "草稿" else (1 if pidx % 3 == 0 else 0)
        pickup_status = 1 if st == "已完成" else 0
        disp_sql = "NULL" if dispensed is None else f"'{dispensed}'"
        audit_concl_sql = "NULL" if audit_concl is None else f"'{esc(audit_concl)}'"
        audit_time_sql = "NULL" if audit_time is None else f"'{audit_time}'"
        ai_res_sql = "NULL" if ai_res is None else f"'{esc(ai_res)}'"
        ai_comment_sql = "NULL" if ai_comment is None else f"'{esc(ai_comment)}'"
        presc_rows.append(
            f"({pidx}, {c['cid']}, {c['doc']}, {c['pid']}, '{st}', {pharmacist}, "
            f"{audit_concl_sql}, {audit_time_sql}, {pickup_method}, {pickup_status}, "
            f"{ai_res_sql}, {ai_comment_sql}, {confirm}, {disp_sql}, '{c['start']}', {audit_time_sql})"
        )
        # 明细 1-3 种
        n_items = random.randint(1, 3)
        chosen = random.sample(range(1, 96), n_items)
        for did in chosen:
            qty = random.randint(1, 3)
            up = price[did]
            sub = round(up * qty, 2)
            dosage = random.choice(DOSAGES)
            deco = 1 if did in () else 0
            dnum = "NULL"
            item_rows.append(
                f"({pidx}, {did}, '{esc(drug_names[did])}', {qty}, '{dosage}', {up:.2f}, {sub:.2f}, {deco}, {dnum})"
            )

    lines.append("-- 6. 处方信息 prescription（50 条）")
    lines.append("INSERT INTO prescription (presc_id, consult_id, doctor_id, patient_id, status, pharmacist_id, audit_conclusion, audit_time, pickup_method, pickup_status, ai_check_result, ai_check_comment, doctor_confirm, dispensed_at, created_at, updated_at) VALUES")
    lines.append(",\n".join(presc_rows) + ";")
    lines.append("")

    lines.append("-- 7. 处方明细 prescription_item")
    lines.append("INSERT INTO prescription_item (presc_id, drug_id, drug_name, qty, dosage, unit_price, subtotal, is_decoction, decoction_num) VALUES")
    lines.append(",\n".join(item_rows) + ";")
    lines.append("")

    # 8. 操作日志
    log_seq = []
    # 管理员：登录/排班
    for i in range(1, 9):
        log_seq.append((1, "管理员", "排班" if i % 2 else "登录", "duty", i))
    # 医生：登录/复核/开方
    for c in consults:
        log_seq.append((c["doc"], "医生", "登录", "sys_user", c["doc"]))
        if c["status"] in ("已结束", "已转诊"):
            log_seq.append((c["doc"], "医生", "复核", "consultation", c["cid"]))
    # 处方相关
    for pidx, c in enumerate(consults[:50], start=1):
        st = presc_status_seq[pidx - 1]
        log_seq.append((c["doc"], "医生", "开方", "prescription", pidx))
        if st in ("已通过", "已驳回", "待配药", "已完成"):
            log_seq.append((PHARM_IDS[pidx % len(PHARM_IDS)], "药师", "审核" if st != "已完成" else "配药", "prescription", pidx))
    # 患者登录
    for c in consults[:30]:
        log_seq.append((c["pid"], "患者", "登录", "sys_user", c["pid"]))
    log_rows = []
    for lid, (oid, otype, action, ttype, tid) in enumerate(log_seq, start=1):
        day = 12 + (lid % 7)
        log_rows.append(f"({oid}, '{otype}', '{action}', '{ttype}', {tid}, '{_dt(day, 8 + (lid % 10), (lid * 3) % 60)}')")
    lines.append("-- 8. 操作日志 operation_log")
    lines.append("INSERT INTO operation_log (operator_id, operator_type, action, target_type, target_id, created_at) VALUES")
    lines.append(",\n".join(log_rows) + ";")
    lines.append("")
    lines.append("SET FOREIGN_KEY_CHECKS = 1;")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    with open(os.path.join(BASE, "seed_data.sql"), "w", encoding="utf-8") as f:
        f.write(build_seed_data())
    with open(os.path.join(BASE, "seed_business_extra.sql"), "w", encoding="utf-8") as f:
        f.write(build_business())
    print("seed_data.sql / seed_business_extra.sql generated")

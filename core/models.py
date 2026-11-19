"""
业务表 ORM 映射（core）。

说明：业务表已由 docs/sql/create_tables.sql 在 MySQL 中建好，这里只做映射，
Meta.managed = False，Django 不会通过迁移重复创建这些表。
字段命名、枚举口径与建表脚本保持一致。
"""

from django.db import models


class SysUser(models.Model):
    """用户表：所有角色账号的基础信息。role_type: 0管理员 1患者 2医生 3药师"""
    user_id = models.BigAutoField(primary_key=True)
    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=11, unique=True)
    password = models.CharField(max_length=200)
    role_type = models.SmallIntegerField()
    status = models.SmallIntegerField(default=1, help_text='帐号状态：0=停用，1=启用')
    last_login = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'sys_user'


class Admin(models.Model):
    """管理员信息表，与 sys_user 共享主键（主键即 sys_user.user_id）。"""
    admin_id = models.BigIntegerField(primary_key=True)
    account = models.CharField(max_length=50, unique=True)
    created_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = 'admin'


class Patient(models.Model):
    """患者信息表，与 sys_user 共享主键（主键即 sys_user.user_id）。"""
    patient_id = models.BigIntegerField(primary_key=True)
    id_card_no = models.CharField(max_length=18, unique=True)
    gender = models.CharField(max_length=20, null=True, blank=True)
    age = models.IntegerField(null=True, blank=True)
    allergy = models.CharField(max_length=200, null=True, blank=True)
    address = models.CharField(max_length=200, null=True, blank=True)
    medical_history = models.CharField(max_length=500, null=True, blank=True,
                                       help_text='既往病史/慢性病症')
    medical_no = models.CharField(max_length=30, unique=True)
    created_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = 'patient'


class Doctor(models.Model):
    """医生信息表，与 sys_user 共享主键（主键即 sys_user.user_id）。"""
    doctor_id = models.BigIntegerField(primary_key=True)
    department = models.CharField(max_length=100)
    title = models.CharField(max_length=50, null=True, blank=True)
    skill = models.CharField(max_length=200, null=True, blank=True)
    work_status = models.SmallIntegerField(default=0)
    gender = models.CharField(max_length=20, null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'doctor'


class Pharmacist(models.Model):
    """药师信息表，与 sys_user 共享主键（主键即 sys_user.user_id）。"""
    pharmacist_id = models.BigIntegerField(primary_key=True)
    pharmacist_type = models.CharField(max_length=50, null=True, blank=True)
    pharmacist_status = models.SmallIntegerField(default=0)
    gender = models.CharField(max_length=20, null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'pharmacist'


class Drug(models.Model):
    """药品目录表。"""
    drug_id = models.BigAutoField(primary_key=True)
    drug_code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=100)
    specification = models.CharField(max_length=200)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    stock_qty = models.IntegerField(default=0)
    is_prescription = models.SmallIntegerField(default=0)
    contraindication = models.CharField(max_length=200, null=True, blank=True)
    updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'drug'


class DutySchedule(models.Model):
    """值班排班表。"""
    duty_code = models.BigAutoField(primary_key=True)
    doctor = models.ForeignKey(Doctor, db_column='doctor_id', on_delete=models.PROTECT)
    shift_type = models.CharField(max_length=200)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    status = models.CharField(max_length=200, default='正常')
    created_by = models.BigIntegerField()
    updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'duty_schedule'


class Consultation(models.Model):
    """问诊记录表。status: 进行中/已结束/已转诊/已取消"""
    consult_id = models.BigAutoField(primary_key=True)
    patient = models.ForeignKey(Patient, db_column='patient_id', on_delete=models.PROTECT)
    doctor = models.ForeignKey(Doctor, db_column='doctor_id', null=True, blank=True,
                                on_delete=models.PROTECT)
    duty = models.ForeignKey(DutySchedule, db_column='duty_code', null=True, blank=True,
                             on_delete=models.PROTECT)
    record_content = models.TextField()
    status = models.CharField(max_length=20, default='进行中')
    doctor_review_status = models.CharField(max_length=20, default='待复核')
    doctor_review_opinion = models.TextField(null=True, blank=True)
    final_diagnosis = models.TextField(null=True, blank=True)
    advice_type = models.CharField(max_length=20, null=True, blank=True,
                                    help_text='结束建议：无需用药/需开处方')
    start_time = models.DateTimeField()
    end_time = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'consultation'


class AiPreAssessment(models.Model):
    """AI 预问诊评估表（内部规则引擎产出，供医生复核）。"""
    pre_id = models.BigAutoField(primary_key=True)
    consultation = models.ForeignKey(Consultation, db_column='consult_id',
                                     on_delete=models.PROTECT)
    risk_level = models.CharField(max_length=20)
    ai_summary = models.TextField()
    matched_keyword = models.CharField(max_length=200, null=True, blank=True)
    review_result = models.CharField(max_length=20, null=True, blank=True)
    review_comment = models.TextField(null=True, blank=True)
    risk_consent = models.SmallIntegerField(default=0)
    created_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = 'ai_pre_assessment'


class ConsultationMessage(models.Model):
    """问诊消息表。"""
    msg_id = models.BigAutoField(primary_key=True)
    consultation = models.ForeignKey(Consultation, db_column='consult_id',
                                      on_delete=models.PROTECT)
    sender_role = models.CharField(max_length=10)
    sender_id = models.BigIntegerField()
    msg_type = models.CharField(max_length=20, default='text')
    content = models.TextField(null=True, blank=True)
    media_url = models.CharField(max_length=500, null=True, blank=True)
    send_time = models.DateTimeField()
    read_status = models.SmallIntegerField(default=0)

    class Meta:
        managed = False
        db_table = 'consultation_message'


class ReferralLog(models.Model):
    """转诊日志表。"""
    referral_id = models.BigAutoField(primary_key=True)
    consultation = models.ForeignKey(Consultation, db_column='consult_id',
                                    on_delete=models.PROTECT)
    patient = models.ForeignKey(Patient, db_column='patient_id', on_delete=models.PROTECT)
    risk_level = models.CharField(max_length=20)
    matched_keyword = models.CharField(max_length=200, null=True, blank=True)
    patient_choice = models.CharField(max_length=20)
    action = models.CharField(max_length=20)
    operator_id = models.BigIntegerField(null=True, blank=True)
    operator_type = models.CharField(max_length=20, null=True, blank=True)
    created_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = 'referral_log'


class Prescription(models.Model):
    """处方信息表。"""
    presc_id = models.BigAutoField(primary_key=True)
    consultation = models.ForeignKey(Consultation, db_column='consult_id',
                                    on_delete=models.PROTECT)
    doctor = models.ForeignKey(Doctor, db_column='doctor_id', on_delete=models.PROTECT)
    patient = models.ForeignKey(Patient, db_column='patient_id', on_delete=models.PROTECT)
    status = models.CharField(max_length=20, default='草稿')
    ai_check_result = models.CharField(max_length=20, null=True, blank=True)
    ai_check_comment = models.TextField(null=True, blank=True)
    doctor_confirm = models.SmallIntegerField(default=0)
    pharmacist = models.ForeignKey(Pharmacist, db_column='pharmacist_id', null=True,
                                   blank=True, on_delete=models.PROTECT)
    audit_conclusion = models.CharField(max_length=255, null=True, blank=True)
    audit_time = models.DateTimeField(null=True, blank=True)
    pickup_method = models.SmallIntegerField(null=True, blank=True)
    pickup_status = models.SmallIntegerField(default=0)
    dispensed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'prescription'


class PrescriptionItem(models.Model):
    """处方明细表，联合主键 (presc_id, drug_id)。"""
    prescription = models.ForeignKey(Prescription, db_column='presc_id', primary_key=True,
                                     on_delete=models.PROTECT)
    drug = models.ForeignKey(Drug, db_column='drug_id',
                             on_delete=models.PROTECT)
    drug_name = models.CharField(max_length=100)
    qty = models.IntegerField(default=1)
    dosage = models.CharField(max_length=200)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    is_decoction = models.SmallIntegerField(default=0)
    decoction_num = models.IntegerField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'prescription_item'
        unique_together = (('prescription', 'drug'),)


class OperationLog(models.Model):
    """操作日志表：关键动作留痕，按日志号可回溯。"""
    log_id = models.BigAutoField(primary_key=True)
    operator_id = models.BigIntegerField()
    operator_type = models.CharField(max_length=20)
    action = models.CharField(max_length=20)
    target_type = models.CharField(max_length=20, null=True, blank=True)
    target_id = models.BigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = 'operation_log'

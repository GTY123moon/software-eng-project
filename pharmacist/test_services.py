"""处方审核服务层组件测试（隔离 MySQL，不经视图/挂号流程）。

直接构造待审核处方记录，调用 pharmacist.services.audit，验证基本路径与
参数校验、版本锁、状态前置条件。运行：
    python manage.py test pharmacist.test_services --settings=pharmacist.test_settings -v 2
"""
from unittest import skipUnless

from django.conf import settings
from django.test import TestCase
from django.utils import timezone

from core.models import Consultation, Drug, OperationLog, Prescription, PrescriptionItem
from . import services
from .test_support import assert_isolated, create_actors

ISOLATED = getattr(settings, 'PHARMACIST_ISOLATED_TESTS', False)


@skipUnless(ISOLATED, '请使用 --settings=pharmacist.test_settings 启动隔离 MySQL 测试')
class AuditServiceTests(TestCase):
    """审核服务基本路径与异常分支。"""

    def setUp(self):
        assert_isolated()
        self.actors = create_actors()
        self.now = timezone.now()

    def make_prescription(self, status='待审核', doctor_confirm=1, pharmacist_id=None):
        consult = Consultation.objects.create(
            patient_id=self.actors['patient'], doctor_id=self.actors['doctor'],
            record_content='组件测试病情', status='进行中', accepted=1,
            start_time=self.now)
        p = Prescription.objects.create(
            consultation_id=consult.consult_id, doctor_id=self.actors['doctor'],
            patient_id=self.actors['patient'], status=status,
            doctor_confirm=doctor_confirm, pharmacist_id=pharmacist_id,
            created_at=self.now)
        # approve 路径要求药品明细完整，造两条明细
        for idx, drug in enumerate(Drug.objects.order_by('pk'), start=1):
            PrescriptionItem.objects.create(
                prescription_id=p.presc_id, drug_id=drug.pk,
                drug_name=drug.name, qty=idx + 1,
                dosage='按测试医嘱甲' if idx == 1 else '按测试医嘱乙',
                unit_price='10.00', subtotal='20.00' if idx == 1 else '30.00',
                is_decoction=0)
        return p

    def token(self, p, purpose='audit'):
        return services.version_token(p, purpose)

    def test_approve_basic_path_marks_passed_and_logs(self):
        p = self.make_prescription()
        token = self.token(p)
        result = services.audit(p.presc_id, self.actors['pharmacist'],
                               'approve', '', token)
        result.refresh_from_db()
        self.assertEqual(result.status, '已通过')
        self.assertEqual(result.pharmacist_id, self.actors['pharmacist'])
        self.assertIsNotNone(result.audit_time)
        self.assertEqual(
            OperationLog.objects.filter(target_type='prescription',
                                        target_id=p.presc_id,
                                        action='审核通过').count(), 1)

    def test_reject_blank_reason_rejected_400(self):
        p = self.make_prescription()
        with self.assertRaises(services.WorkflowError) as ctx:
            services.audit(p.presc_id, self.actors['pharmacist'],
                           'reject', '', self.token(p))
        self.assertEqual(ctx.exception.status, 400)
        p.refresh_from_db()
        self.assertEqual(p.status, '待审核')

    def test_reject_reason_too_long_rejected_400(self):
        p = self.make_prescription()
        with self.assertRaises(services.WorkflowError) as ctx:
            services.audit(p.presc_id, self.actors['pharmacist'],
                           'reject', 'x' * 253, self.token(p))
        self.assertEqual(ctx.exception.status, 400)

    def test_invalid_decision_rejected_400(self):
        p = self.make_prescription()
        with self.assertRaises(services.WorkflowError) as ctx:
            services.audit(p.presc_id, self.actors['pharmacist'],
                           'maybe', '', self.token(p))
        self.assertEqual(ctx.exception.status, 400)

    def test_stale_or_tampered_token_rejected_409(self):
        p = self.make_prescription()
        with self.assertRaises(services.WorkflowError) as ctx:
            services.audit(p.presc_id, self.actors['pharmacist'],
                           'approve', '', 'tampered-token')
        self.assertEqual(ctx.exception.status, 409)
        p.refresh_from_db()
        self.assertEqual(p.status, '待审核')

    def test_draft_or_unconfirmed_cannot_audit_409(self):
        for status, confirm in (('草稿', 0), ('待审核', 0)):
            p = self.make_prescription(status=status, doctor_confirm=confirm)
            with self.assertRaises(services.WorkflowError) as ctx:
                services.audit(p.presc_id, self.actors['pharmacist'],
                               'approve', '', self.token(p))
            self.assertEqual(ctx.exception.status, 409)

    def test_reject_records_reason_and_status(self):
        p = self.make_prescription()
        result = services.audit(p.presc_id, self.actors['pharmacist'],
                               'reject', '请修改第二项用法', self.token(p))
        result.refresh_from_db()
        self.assertEqual(result.status, '已驳回')
        self.assertIn('请修改第二项用法', result.audit_conclusion)

    def test_reject_reason_exactly_252_chars_accepted(self):
        # 边界值：理由恰为上界 252 字，应通过校验并驳回
        p = self.make_prescription()
        reason = '修' * 252
        result = services.audit(p.presc_id, self.actors['pharmacist'],
                               'reject', reason, self.token(p))
        result.refresh_from_db()
        self.assertEqual(result.status, '已驳回')

    def test_reject_reason_single_char_accepted(self):
        # 边界值：理由恰为 1 字（下界），非空即应通过校验并驳回
        p = self.make_prescription()
        result = services.audit(p.presc_id, self.actors['pharmacist'],
                               'reject', '改', self.token(p))
        result.refresh_from_db()
        self.assertEqual(result.status, '已驳回')


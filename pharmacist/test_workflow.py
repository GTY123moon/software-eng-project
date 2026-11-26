"""真实 MySQL 跨角色集成测试，只有专用隔离 settings 下才运行。

python manage.py test pharmacist --settings=pharmacist.test_settings --verbosity 2
外部 AI 禁用以验证项目原有降级；登录、Session、CSRF、处方和库存全部真实落库。
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.db import connections, connection, OperationalError
from django.http import QueryDict
from django.test import Client, SimpleTestCase, TransactionTestCase
from django.urls import reverse

from core.models import Consultation, Drug, OperationLog, Prescription, PrescriptionItem
from . import services
from .test_support import (assert_isolated, clear_test_rows, create_actors,
                           TEST_PASSWORD, TEST_PHONES)

ISOLATED = getattr(settings, 'PHARMACIST_ISOLATED_TESTS', False)
TestBase = TransactionTestCase if ISOLATED else SimpleTestCase


@skipUnless(ISOLATED, '请使用 --settings=pharmacist.test_settings 启动隔离 MySQL 测试')
class WorkflowIntegrationTests(TestBase):
    databases = {'default'} if ISOLATED else set()

    def setUp(self):
        assert_isolated()
        self.actors = create_actors()
        self.addCleanup(patch.stopall)
        # 当前接入层已收敛到 core.ai_provider；两处调用方都直接 import 了函数，
        # 因此在调用点 mock，避免真实访问远程大模型。
        patch('doctor.ai_client.chat_completion', return_value=None).start()
        patch('core.ai_service.chat_completion', return_value=None).start()
        patch.dict('os.environ', {'ARK_API_KEY': '', 'ARK_MODEL': ''}).start()
        self.clients = {role: self.login(role) for role in TEST_PHONES}
        self.drugs = list(Drug.objects.order_by('pk'))

    def _fixture_teardown(self):
        clear_test_rows()

    def post(self, client, url, data=None, follow=False):
        payload = dict(data or {})
        payload['csrfmiddlewaretoken'] = client.cookies['csrftoken'].value
        return client.post(url, payload, follow=follow)

    def login(self, role):
        client = Client(enforce_csrf_checks=True)
        staff = 'patient' not in role
        login_role = 'pharmacist' if 'pharmacist' in role else 'doctor'
        url = '/accounts/staff/%s/' % login_role if staff else '/accounts/login/'
        self.assertEqual(client.get(url).status_code, 200)
        response = self.post(client, url, {'phone': TEST_PHONES[role],
                             'secret' if staff else 'password': TEST_PASSWORD})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(client.session.get('role_type'), 3 if 'pharmacist' in role else 2 if staff else 1)
        return client

    def draft(self):
        patient, doctor = self.clients['patient'], self.clients['doctor']
        response = self.post(patient, '/accounts/consult/create/', {'doctor': self.actors['doctor']})
        self.assertEqual(response.status_code, 302)
        consult = Consultation.objects.get(patient_id=self.actors['patient'], status='进行中')
        url = '/accounts/consult/%d/assess/' % consult.pk
        patient.get(url)
        self.post(patient, url, {'answer': '轻微咳嗽两天，无其他症状'})
        self.post(patient, url, {'action': 'finish'})
        self.post(doctor, '/doctor/consult/%d/review/' % consult.pk, {'result': '采纳'})
        response = self.post(doctor, '/doctor/consult/%d/advice/' % consult.pk,
                             {'final_diagnosis': '集成测试诊断', 'advice': 'need_drug'})
        self.assertEqual(response.status_code, 302)
        response = self.post(doctor, '/doctor/consult/%d/prescription/' % consult.pk,
                             {'drug_id': [d.pk for d in self.drugs], 'qty': ['2', '3'],
                              'dosage': ['按测试医嘱甲', '按测试医嘱乙']})
        self.assertEqual(response.status_code, 302)
        p = Prescription.objects.get(consultation_id=consult.pk)
        self.assertEqual(p.status, '草稿')
        self.assertEqual(p.doctor_confirm, 0)
        self.assertEqual(list(PrescriptionItem.objects.filter(prescription_id=p.pk)
                              .order_by('drug_id').values_list('qty', flat=True)), [2, 3])
        return p

    def submit(self, p):
        client = self.clients['doctor']
        client.get('/doctor/prescriptions/%d/review/' % p.pk)
        response = self.post(client, '/doctor/prescriptions/%d/submit/' % p.pk,
                             {'ack_risk': '1'})
        self.assertEqual(response.status_code, 302)
        p.refresh_from_db()
        self.assertEqual(p.status, '待审核')
        self.assertEqual(p.doctor_confirm, 1)
        return p

    def token(self, p, role='pharmacist', name='audit_token'):
        response = self.clients[role].get('/pharmacist/prescriptions/%d/' % p.pk)
        self.assertEqual(response.status_code, 200)
        return response.context[name]

    def audit(self, p, decision='approve', reason=''):
        token = self.token(p)
        response = self.post(self.clients['pharmacist'], '/pharmacist/prescriptions/%d/audit/' % p.pk,
                             {'decision': decision, 'reason': reason, 'version': token})
        self.assertEqual(response.status_code, 302)
        p.refresh_from_db()
        return p

    def pickup(self, p, method='0', decoction=False):
        response = self.clients['patient'].get('/accounts/prescriptions/%d/' % p.pk)
        # 患者端取药不再校验页面令牌，改由服务端状态与 pickup_status 原子控制重复提交
        data = {'pickup_method': method,
                'item_drug_id': [d.pk for d in self.drugs],
                'item_decoction': ['1' if decoction else '0', '0'],
                'item_decoction_num': ['2' if decoction else '', '']}
        result = self.post(self.clients['patient'], '/accounts/prescriptions/%d/pickup/' % p.pk, data)
        self.assertEqual(result.status_code, 302)
        p.refresh_from_db()
        return p

    def prepared(self, decoction=False):
        return self.pickup(self.audit(self.submit(self.draft())), decoction=decoction)

    def test_normal_round_trip_real_login_csrf_two_items_and_inventory(self):
        p = self.submit(self.draft())
        counts = self.clients['pharmacist'].get('/pharmacist/').context['counts']
        self.assertEqual(counts, {'pending_review': 1, 'pending_dispense': 0, 'processed': 0})
        self.audit(p)
        counts = self.clients['pharmacist'].get('/pharmacist/').context['counts']
        self.assertEqual(counts, {'pending_review': 0, 'pending_dispense': 0, 'processed': 1})
        self.pickup(p, decoction=True)
        self.assertEqual(p.status, '待配药')
        counts = self.clients['pharmacist'].get('/pharmacist/').context['counts']
        self.assertEqual(counts, {'pending_review': 0, 'pending_dispense': 1, 'processed': 1})
        token = self.token(p, name='dispense_token')
        self.assertEqual(list(Drug.objects.order_by('pk').values_list('stock_qty', flat=True)), [100, 100])
        response = self.post(self.clients['pharmacist'], '/pharmacist/prescriptions/%d/dispense/' % p.pk,
                             {'version': token, 'checked': '1', 'decoction_done': '1'})
        self.assertEqual(response.status_code, 302)
        p.refresh_from_db()
        self.assertEqual((p.status, p.pickup_status), ('已完成', 1))
        self.assertIsNotNone(p.dispensed_at)
        self.assertEqual(list(Drug.objects.order_by('pk').values_list('stock_qty', flat=True)), [98, 97])
        response = self.clients['patient'].get('/accounts/prescriptions/%d/' % p.pk)
        self.assertContains(response, '已完成')
        self.assertContains(response, '已确认取药方式')
        self.assertContains(response, 'P%09d' % p.pk)
        self.assertEqual(OperationLog.objects.filter(target_type='prescription', target_id=p.pk,
                                                     action='确认实际发药').count(), 1)
        counts = self.clients['pharmacist'].get('/pharmacist/').context['counts']
        self.assertEqual(counts, {'pending_review': 0, 'pending_dispense': 0, 'processed': 1})
        print('VERIFIED NORMAL PRESCRIPTION: P%09d; stock 100/100 -> 98/97' % p.pk)

    def test_rejection_modify_resubmit_same_prescription_and_stale_audit(self):
        p = self.submit(self.draft())
        stale_token = self.token(p)
        self.audit(p, 'reject', '请修改第二项用法')
        self.assertEqual(p.status, '已驳回')
        self.assertIn('请修改第二项用法', p.audit_conclusion)
        response = self.clients['doctor'].get('/doctor/prescriptions/')
        self.assertContains(response, '请修改第二项用法')
        edit = self.clients['doctor'].get('/doctor/prescriptions/%d/edit/' % p.pk)
        self.assertContains(edit, '保存草稿')
        self.post(self.clients['doctor'], '/doctor/prescriptions/%d/edit/' % p.pk,
                  {'drug_id': [d.pk for d in self.drugs],
                   'qty': ['2', '3'], 'dosage': ['修改后甲', '修改后乙']})
        p.refresh_from_db()
        self.assertEqual((p.status, p.pharmacist_id, p.doctor_confirm), ('草稿', None, 0))
        self.submit(p)
        response = self.post(self.clients['pharmacist'], '/pharmacist/prescriptions/%d/audit/' % p.pk,
                             {'decision': 'approve', 'version': stale_token})
        self.assertEqual(response.status_code, 409)
        p.refresh_from_db()
        self.assertEqual(p.status, '待审核')
        self.audit(p)
        self.assertEqual(p.status, '已通过')
        self.assertEqual(Prescription.objects.count(), 1)

    def test_external_purchase_does_not_fake_delivery_or_deduct_stock(self):
        p = self.pickup(self.audit(self.submit(self.draft())), method='1')
        self.assertEqual((p.status, p.pickup_status), ('已通过', 0))
        self.assertIsNone(p.dispensed_at)
        response = self.clients['patient'].get('/accounts/prescriptions/%d/' % p.pk)
        self.assertContains(response, '社会药店购药')
        response = self.post(self.clients['pharmacist'], '/pharmacist/prescriptions/%d/dispense/' % p.pk,
                             {'checked': '1'})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(list(Drug.objects.values_list('stock_qty', flat=True)), [100, 100])

    def test_post_csrf_anonymous_and_other_roles(self):
        p = self.submit(self.draft())
        url = '/pharmacist/prescriptions/%d/audit/' % p.pk
        self.assertEqual(Client(enforce_csrf_checks=True).post(url, {'decision': 'approve'}).status_code, 403)
        self.assertEqual(Client().post(url, {'decision': 'approve'}).status_code, 302)
        for role in ('patient', 'doctor'):
            response = self.post(self.clients[role], url, {'decision': 'approve'})
            self.assertEqual(response.status_code, 302)
        self.assertEqual(self.clients['pharmacist'].get(url).status_code, 405)
        p.refresh_from_db()
        self.assertEqual(p.status, '待审核')

    def test_draft_and_missing_confirmation_cannot_be_audited(self):
        p = self.draft()
        for status in ('草稿', '待审核'):
            Prescription.objects.filter(pk=p.pk).update(status=status, doctor_confirm=0)
            response = self.post(self.clients['pharmacist'], '/pharmacist/prescriptions/%d/audit/' % p.pk,
                                 {'decision': 'approve', 'version': 'bad'})
            self.assertEqual(response.status_code, 409)

    def test_reject_reason_and_token_validation(self):
        p = self.submit(self.draft())
        for data, code in [({'decision': 'reject', 'reason': ''}, 400),
                           ({'decision': 'reject', 'reason': 'x' * 253}, 400),
                           ({'decision': 'invalid'}, 400),
                           ({'decision': 'approve', 'version': 'tampered'}, 409)]:
            response = self.post(self.clients['pharmacist'], '/pharmacist/prescriptions/%d/audit/' % p.pk, data)
            self.assertEqual(response.status_code, code)
        p.refresh_from_db()
        self.assertEqual(p.status, '待审核')

    def test_duplicate_audit_and_other_pharmacist_privacy(self):
        p = self.submit(self.draft())
        token = self.token(p)
        self.audit(p)
        for role, code in (('pharmacist', 409), ('other_pharmacist', 404)):
            response = self.post(self.clients[role], '/pharmacist/prescriptions/%d/audit/' % p.pk,
                                 {'decision': 'reject', 'reason': '重复', 'version': token})
            self.assertEqual(response.status_code, code)
        self.assertEqual(self.clients['other_pharmacist'].get(
            '/pharmacist/prescriptions/%d/' % p.pk).status_code, 404)
        response = self.clients['other_patient'].get('/accounts/prescriptions/%d/' % p.pk)
        self.assertNotContains(response, '集成测试诊断')

    def test_stock_failure_does_not_partially_deduct(self):
        p = self.prepared()
        Drug.objects.filter(pk=self.drugs[1].pk).update(stock_qty=1)
        token = self.token(p, name='dispense_token')
        response = self.post(self.clients['pharmacist'], '/pharmacist/prescriptions/%d/dispense/' % p.pk,
                             {'version': token, 'checked': '1'})
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, '库存不足', status_code=409)
        self.assertEqual(list(Drug.objects.order_by('pk').values_list('stock_qty', flat=True)), [100, 1])
        p.refresh_from_db()
        self.assertEqual(p.status, '待配药')

    def test_log_failure_rolls_back_stock_status_and_time(self):
        p = self.prepared()
        token = self.token(p, name='dispense_token')
        with patch('pharmacist.services.write_log', side_effect=OperationalError('test log unavailable')):
            response = self.post(self.clients['pharmacist'], '/pharmacist/prescriptions/%d/dispense/' % p.pk,
                                 {'version': token, 'checked': '1'})
        self.assertEqual(response.status_code, 503)
        self.assertNotContains(response, 'test log unavailable', status_code=503)
        p.refresh_from_db()
        self.assertEqual((p.status, p.pickup_status, p.dispensed_at), ('待配药', 0, None))
        self.assertEqual(list(Drug.objects.values_list('stock_qty', flat=True)), [100, 100])

    def test_repeat_dispense_and_decoction_confirmation(self):
        p = self.prepared(decoction=True)
        token = self.token(p, name='dispense_token')
        url = '/pharmacist/prescriptions/%d/dispense/' % p.pk
        self.assertEqual(self.post(self.clients['pharmacist'], url,
            {'version': token, 'checked': '1'}).status_code, 400)
        self.assertEqual(self.post(self.clients['pharmacist'], url,
            {'version': token, 'checked': '1', 'decoction_done': '1'}).status_code, 302)
        self.assertEqual(self.post(self.clients['pharmacist'], url,
            {'version': token, 'checked': '1', 'decoction_done': '1'}).status_code, 409)
        self.assertEqual(list(Drug.objects.values_list('stock_qty', flat=True)), [98, 97])

    def race(self, jobs):
        barrier = Barrier(len(jobs))
        def run(job):
            connections.close_all()
            try:
                assert_isolated()
                barrier.wait(timeout=10)
                job()
                return 200
            except services.WorkflowError as error:
                return error.status
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=len(jobs)) as executor:
            return sorted(executor.map(run, jobs))

    def test_two_pharmacists_race_for_shared_audit(self):
        p = self.submit(self.draft())
        token = self.token(p)
        jobs = [lambda actor=actor: services.audit(p.pk, actor, 'approve', '', token)
                for actor in (self.actors['pharmacist'], self.actors['other_pharmacist'])]
        self.assertEqual(self.race(jobs), [200, 404])
        self.assertEqual(OperationLog.objects.filter(target_id=p.pk, target_type='prescription',
                                                     action='审核通过').count(), 1)

    def test_simultaneous_double_dispense_deducts_once(self):
        p = self.prepared()
        token = self.token(p, name='dispense_token')
        data = QueryDict('', mutable=True)
        data.update({'version': token, 'checked': '1'})
        job = lambda: services.dispense(p.pk, self.actors['pharmacist'], data)
        self.assertEqual(self.race([job, job]), [200, 409])
        self.assertEqual(list(Drug.objects.values_list('stock_qty', flat=True)), [98, 97])

    def test_double_pickup_selection_is_blocked(self):
        p = self.audit(self.submit(self.draft()))
        self.pickup(p)
        p.refresh_from_db()
        self.assertEqual((p.status, p.pickup_method), ('待配药', 0))
        # 已选定取药方式后再次提交应被状态守卫拒绝，不得改写为外购
        self.post(self.clients['patient'], '/accounts/prescriptions/%d/pickup/' % p.pk,
                  {'pickup_method': '1', 'item_drug_id': [self.drugs[0].pk]})
        p.refresh_from_db()
        self.assertEqual((p.status, p.pickup_method), ('待配药', 0))

    def test_other_patient_pickup_and_other_pharmacist_dispense_are_denied(self):
        p = self.audit(self.submit(self.draft()))
        token = services.version_token(p, 'pickup')
        self.post(self.clients['other_patient'], '/accounts/prescriptions/%d/pickup/' % p.pk,
                  {'version': token, 'pickup_method': '0'})
        p.refresh_from_db()
        self.assertIsNone(p.pickup_method)
        self.pickup(p)
        token = self.token(p, name='dispense_token')
        response = self.post(self.clients['other_pharmacist'],
                             '/pharmacist/prescriptions/%d/dispense/' % p.pk,
                             {'version': token, 'checked': '1'})
        self.assertEqual(response.status_code, 404)
        p.refresh_from_db()
        self.assertEqual(p.status, '待配药')

    def test_old_doctor_edit_cannot_revert_a_submitted_prescription(self):
        p = self.draft()
        self.submit(p)
        self.post(self.clients['doctor'], '/doctor/prescriptions/%d/edit/' % p.pk,
                  {'drug_id': [self.drugs[0].pk],
                   'qty': ['9'], 'dosage': ['不应保存']})
        p.refresh_from_db()
        self.assertEqual(p.status, '待审核')
        self.assertEqual(PrescriptionItem.objects.filter(prescription_id=p.pk).count(), 2)

    def test_multiple_prescriptions_sharing_drugs_do_not_corrupt_joint_key_items(self):
        p = self.draft()
        other = Prescription.objects.create(consultation_id=p.consultation_id,
            doctor_id=p.doctor_id, patient_id=p.patient_id, created_at=p.created_at)
        for drug in self.drugs:
            PrescriptionItem.objects.create(prescription_id=other.pk, drug_id=drug.pk,
                drug_name=drug.name, qty=7, dosage='另一张处方', unit_price=10, subtotal=70)
        edit = self.clients['doctor'].get('/doctor/prescriptions/%d/edit/' % p.pk)
        self.post(self.clients['doctor'], '/doctor/prescriptions/%d/edit/' % p.pk,
                  {'drug_id': [d.pk for d in self.drugs],
                   'qty': ['4', '5'], 'dosage': ['修改甲', '修改乙']})
        self.assertEqual(list(PrescriptionItem.objects.filter(prescription_id=other.pk)
                              .order_by('drug_id').values_list('qty', flat=True)), [7, 7])
        self.assertEqual(list(PrescriptionItem.objects.filter(prescription_id=p.pk)
                              .order_by('drug_id').values_list('qty', flat=True)), [4, 5])

    def test_two_prescriptions_compete_for_stock_without_overselling(self):
        p = self.prepared()
        other = Prescription.objects.create(consultation_id=p.consultation_id,
            doctor_id=p.doctor_id, patient_id=p.patient_id, created_at=p.created_at,
            status='待配药', pharmacist_id=p.pharmacist_id, doctor_confirm=1,
            audit_time=p.audit_time, pickup_method=0)
        for item in PrescriptionItem.objects.filter(prescription_id=p.pk).values(
                'drug_id', 'drug_name', 'qty', 'dosage', 'unit_price', 'subtotal'):
            PrescriptionItem.objects.create(prescription_id=other.pk, **item)
        Drug.objects.filter(pk=self.drugs[0].pk).update(stock_qty=3)
        jobs = []
        for target in (p, other):
            target.refresh_from_db()
            data = QueryDict('', mutable=True)
            data.update({'version': services.version_token(target, 'dispense'), 'checked': '1'})
            jobs.append(lambda target_id=target.pk, post=data:
                        services.dispense(target_id, self.actors['pharmacist'], post))
        self.assertEqual(self.race(jobs), [200, 409])
        self.assertEqual(list(Drug.objects.order_by('pk').values_list('stock_qty', flat=True)), [1, 97])
        self.assertEqual(Prescription.objects.filter(status='已完成').count(), 1)

    def test_disabled_pharmacist_cannot_audit_and_log_failure_rolls_back_audit(self):
        from core.models import SysUser
        p = self.submit(self.draft())
        token = self.token(p)
        SysUser.objects.filter(pk=self.actors['pharmacist']).update(status=0)
        response = self.post(self.clients['pharmacist'], '/pharmacist/prescriptions/%d/audit/' % p.pk,
                             {'version': token, 'decision': 'approve'})
        self.assertEqual(response.status_code, 302)
        SysUser.objects.filter(pk=self.actors['pharmacist']).update(status=1)
        with patch('pharmacist.services.write_log', side_effect=OperationalError('test audit error')):
            response = self.post(self.clients['pharmacist'], '/pharmacist/prescriptions/%d/audit/' % p.pk,
                                 {'version': token, 'decision': 'approve'})
        self.assertEqual(response.status_code, 503)
        p.refresh_from_db()
        self.assertEqual((p.status, p.pharmacist_id), ('待审核', None))

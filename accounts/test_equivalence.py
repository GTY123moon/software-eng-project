"""注册 / 个人中心编辑的黑盒等价类划分 + 边界值分析测试（隔离 MySQL）。

通过 Django test Client 直接发 POST 请求，验证视图层输入校验：
- 注册：手机号必须为 8 位（长度等价类 + 7/9 位边界），手机号/身份证去重；
- 个人中心：姓名非空、手机号 8 位、年龄 0–200 整数（0/200 边界、-1/201 越界、非整数）。

运行：
    python manage.py test accounts.test_equivalence --settings=pharmacist.test_settings -v 2
"""
from unittest import skipUnless

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from core.models import SysUser, Patient
from pharmacist.test_support import assert_isolated, create_actors

ISOLATED = getattr(settings, 'PHARMACIST_ISOLATED_TESTS', False)


@skipUnless(ISOLATED, '请使用 --settings=pharmacist.test_settings 启动隔离 MySQL 测试')
class RegisterEquivalenceTests(TestCase):
    """患者注册：手机号 8 位有效/无效等价类与长度边界值。"""

    def setUp(self):
        assert_isolated()
        self.actors = create_actors()
        self.url = reverse('accounts:register')
        self.seed_id_card = Patient.objects.get(
            patient_id=self.actors['patient']).id_card_no
        self.valid = {
            'name': '测试患者甲', 'phone': '87654321', 'password': 'Pass2026!',
            'gender': '女', 'age': '25', 'id_card': '220201199003078899',
            'allergy': '无', 'address': '测试市',
        }

    def post(self, **overrides):
        data = {**self.valid, **overrides}
        return self.client.post(self.url, data)

    def test_valid_register_creates_user_and_patient(self):
        # 有效等价类：姓名非空 + 8 位手机号 + 未登记身份证 → 注册成功跳转
        response = self.post()
        self.assertEqual(response.status_code, 302)
        self.assertTrue(SysUser.objects.filter(phone='87654321').exists())
        # 隔离种子含 2 名患者（patient / other_patient），新注册后共 3 名
        self.assertEqual(Patient.objects.count(), 3)

    def test_blank_name_rejected(self):
        # 无效等价类：姓名为空
        response = self.post(name='')
        self.assertEqual(response.status_code, 200)
        self.assertIn('8 位手机号', response.context['register_error'])

    def test_blank_phone_rejected(self):
        # 无效等价类：手机号为空
        response = self.post(phone='')
        self.assertEqual(response.status_code, 200)
        self.assertIn('8 位手机号', response.context['register_error'])

    def test_phone_seven_digits_boundary_below(self):
        # 边界值：7 位（合法长度下界 8 的下界-1）
        response = self.post(phone='1234567')
        self.assertEqual(response.status_code, 200)
        self.assertIn('8 位手机号', response.context['register_error'])

    def test_phone_nine_digits_boundary_above(self):
        # 边界值：9 位（合法长度上界 8 的上界+1）
        response = self.post(phone='123456789')
        self.assertEqual(response.status_code, 200)
        self.assertIn('8 位手机号', response.context['register_error'])

    def test_duplicate_phone_rejected(self):
        # 无效等价类：手机号已注册
        SysUser.objects.create(user_id=90001, name='占号', phone='87654321',
                               password='x', role_type=1, status=1)
        response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertIn('该手机号已注册', response.context['register_error'])

    def test_duplicate_id_card_rejected(self):
        # 无效等价类：身份证号已登记（复用隔离种子患者的身份证）
        response = self.post(phone='87654321', id_card=self.seed_id_card)
        self.assertEqual(response.status_code, 200)
        self.assertIn('该身份证号已登记', response.context['register_error'])


@skipUnless(ISOLATED, '请使用 --settings=pharmacist.test_settings 启动隔离 MySQL 测试')
class ProfileEquivalenceTests(TestCase):
    """个人中心编辑：手机号 8 位、年龄 0–200 的等价类与边界值。"""

    def setUp(self):
        assert_isolated()
        self.actors = create_actors()
        self.pid = self.actors['patient']
        self.url = reverse('accounts:profile')
        # 建立患者登录会话
        session = self.client.session
        session['patient_id'] = self.pid
        session.save()
        # 8 位、未被占用的合法手机号；年龄默认 30
        self.valid = {
            'name': '联调测试_patient', 'phone': '87654321', 'age': '30',
            'gender': '女', 'allergy': '无', 'address': '测试市',
            'medical_history': '无',
        }

    def post(self, **overrides):
        data = {**self.valid, **overrides}
        return self.client.post(self.url, data)

    def errors_of(self, response):
        return '；'.join(response.context['errors'])

    def test_age_lower_bound_0_saved(self):
        # 边界值：年龄 0（合法下界）可保存
        response = self.post(age='0')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Patient.objects.get(patient_id=self.pid).age, 0)

    def test_age_upper_bound_200_saved(self):
        # 边界值：年龄 200（合法上界）可保存
        response = self.post(age='200')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Patient.objects.get(patient_id=self.pid).age, 200)

    def test_blank_name_rejected(self):
        # 无效等价类：姓名为空
        response = self.post(name='')
        self.assertEqual(response.status_code, 200)
        self.assertIn('姓名不能为空', self.errors_of(response))

    def test_phone_seven_digits_rejected(self):
        # 边界值：手机号 7 位（下界-1）
        response = self.post(phone='1234567')
        self.assertEqual(response.status_code, 200)
        self.assertIn('8 位手机号', self.errors_of(response))

    def test_phone_nine_digits_rejected(self):
        # 边界值：手机号 9 位（上界+1）
        response = self.post(phone='123456789')
        self.assertEqual(response.status_code, 200)
        self.assertIn('8 位手机号', self.errors_of(response))

    def test_phone_taken_by_other_rejected(self):
        # 无效等价类：8 位手机号已被其他账号占用
        SysUser.objects.create(user_id=90002, name='占号乙', phone='87654321',
                               password='x', role_type=1, status=1)
        response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertIn('该手机号已被其他账号使用', self.errors_of(response))

    def test_age_negative_boundary_below(self):
        # 边界值：年龄 -1（合法下界 0 的下界-1）
        response = self.post(age='-1')
        self.assertEqual(response.status_code, 200)
        self.assertIn('年龄需在 0–200 之间', self.errors_of(response))

    def test_age_201_boundary_above(self):
        # 边界值：年龄 201（合法上界 200 的上界+1）
        response = self.post(age='201')
        self.assertEqual(response.status_code, 200)
        self.assertIn('年龄需在 0–200 之间', self.errors_of(response))

    def test_age_non_integer_rejected(self):
        # 无效等价类：年龄非整数
        response = self.post(age='abc')
        self.assertEqual(response.status_code, 200)
        self.assertIn('年龄须为整数', self.errors_of(response))

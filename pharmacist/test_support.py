"""隔离 MySQL 测试支持。只在随机新库执行提取的 CREATE TABLE；不执行 SQL 脚本。

本模块不读取/复制业务数据，不向原库写入。所有清理先核实连接库名。
"""
from pathlib import Path
import re

from django.conf import settings
from django.contrib.sessions.models import Session
from django.db import connection
from django.test.runner import DiscoverRunner
from django.utils import timezone

from core.models import SysUser, Patient, Doctor, Pharmacist, Drug

TEST_PASSWORD = 'LocalWorkflowDemo2026!'
TEST_PHONES = {'patient': '13990000001', 'doctor': '13990000002',
               'pharmacist': '13990000003', 'other_pharmacist': '13990000004',
               'other_patient': '13990000005'}
TABLES_REVERSED = (
    'operation_log', 'prescription_item', 'prescription', 'referral_log',
    'consultation_message', 'ai_pre_assessment', 'consultation', 'duty_schedule',
    'drug', 'pharmacist', 'doctor', 'patient', 'admin', 'sys_user', 'django_session',
)


def assert_isolated():
    name = connection.settings_dict['NAME']
    if (not getattr(settings, 'PHARMACIST_ISOLATED_TESTS', False)
            or name != settings.PHARMACIST_TEST_NAME
            or not re.fullmatch(r'test_pharmacist_[0-9a-f]{16}', name)):
        raise RuntimeError('拒绝操作：连接不属于本轮隔离测试库。')


def create_isolated_database():
    name = settings.PHARMACIST_TEST_NAME
    original = connection.settings_dict['NAME']
    if original == name or not re.fullmatch(r'test_pharmacist_[0-9a-f]{16}', name):
        raise RuntimeError('隔离库名无效。')
    connection.close()
    # 不带 IF NOT EXISTS；遇到已有同名库直接失败，绝不复用/清空它。
    with connection._nodb_cursor() as cursor:
        cursor.execute('CREATE DATABASE `%s` CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci' % name)
    connection.settings_dict['NAME'] = name
    assert_isolated()
    source = (Path(settings.BASE_DIR) / 'docs/sql/create_tables.sql').read_text(encoding='utf-8')
    statements = re.findall(r'CREATE TABLE\s+\w+\s*\(.*?\)\s*ENGINE\s*=\s*InnoDB[^;]*;',
                            source, re.S | re.I)
    if len(statements) != 14:
        drop_isolated_database(original, name)
        raise RuntimeError('未能明确识别 14 条 CREATE TABLE，拒绝执行。')
    try:
        with connection.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)
        with connection.schema_editor() as editor:
            editor.create_model(Session)
    except Exception:
        drop_isolated_database(original, name)
        raise
    print('ISOLATED MYSQL:', name, '(no business data copied)')
    return original, name


def drop_isolated_database(original, name):
    assert_isolated()
    if name != settings.PHARMACIST_TEST_NAME or name == original:
        raise RuntimeError('隔离库清理检查失败。')
    connection.close()
    connection.settings_dict['NAME'] = original
    with connection._nodb_cursor() as cursor:
        cursor.execute('DROP DATABASE `%s`' % name)


def clear_test_rows():
    assert_isolated()
    with connection.cursor() as cursor:
        for table in TABLES_REVERSED:
            cursor.execute('DELETE FROM `%s`' % table)


def create_actors():
    assert_isolated()
    now = timezone.now()
    # AI 引导消息沿用系统账号 ID=1，所有账号均为本轮合成测试数据。
    SysUser.objects.create(user_id=1, name='隔离测试系统', phone='13990000000',
                           password=TEST_PASSWORD, role_type=0, status=1)
    roles = {'patient': 1, 'doctor': 2, 'pharmacist': 3,
             'other_pharmacist': 3, 'other_patient': 1}
    actors = {}
    for offset, (role, phone) in enumerate(TEST_PHONES.items(), 10001):
        user = SysUser.objects.create(user_id=offset, name='联调测试_' + role,
                                     phone=phone, password=TEST_PASSWORD,
                                     role_type=roles[role], status=1)
        actors[role] = user.pk
        if roles[role] == 1:
            Patient.objects.create(patient_id=user.pk, id_card_no=str(offset).zfill(18),
                                   medical_no='E2E' + str(offset), created_at=now,
                                   age=30, gender='女', allergy='无', medical_history='无')
        elif roles[role] == 2:
            Doctor.objects.create(doctor_id=user.pk, department='测试内科', work_status=1,
                                  title='测试医生')
        else:
            Pharmacist.objects.create(pharmacist_id=user.pk, pharmacist_type='测试药师',
                                      pharmacist_status=1)
    for code, name in (('E2E-A', '联调药品甲'), ('E2E-B', '联调药品乙')):
        Drug.objects.create(drug_code=code, name=name, specification='测试规格',
                            unit_price='10.00', stock_qty=100, is_prescription=1)
    return actors


class IsolatedMySQLRunner(DiscoverRunner):
    def setup_databases(self, **kwargs):
        return create_isolated_database()

    def teardown_databases(self, old_config, **kwargs):
        drop_isolated_database(*old_config)

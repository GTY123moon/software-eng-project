"""仅用于药师跨角色测试；由专用 runner 创建随机命名的空白隔离 MySQL 库。

用法：python manage.py test pharmacist --settings=pharmacist.test_settings
不会执行仓库建库/种子脚本，不会迁移或写入原业务库。
"""
from copy import deepcopy
import uuid

from medical_platform.settings import *  # noqa: F403

DATABASES = deepcopy(DATABASES)  # noqa: F405
PHARMACIST_ISOLATED_TESTS = True
PHARMACIST_TEST_NAME = 'test_pharmacist_' + uuid.uuid4().hex[:16]
DATABASES['default']['TEST'] = {'NAME': PHARMACIST_TEST_NAME}
TEST_RUNNER = 'pharmacist.test_support.IsolatedMySQLRunner'
# 与日常服务相同的数据库 Session 后端，测试库单独创建 django_session。
SESSION_ENGINE = 'django.contrib.sessions.backends.db'
SESSION_COOKIE_NAME = 'pharmacist_test_session'
CSRF_COOKIE_NAME = 'csrftoken'
DEBUG = True
ARK_API_KEY = ''

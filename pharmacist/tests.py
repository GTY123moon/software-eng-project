"""无数据库测试：权限与模板用模拟对象，查询 SQL 在内存 CTE 上验证。

不连接 MySQL，不创建表、测试库或持久化演示数据。
"""
from datetime import datetime, timedelta
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

from django.db import OperationalError
from django.db.models.sql.constants import MULTI, SINGLE
from django.test import RequestFactory, SimpleTestCase

from . import views


class DashboardTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = SimpleNamespace(name='测试药师')
        self.pharmacist = SimpleNamespace(pharmacist_id=138, pharmacist_type='执业药师')

    def request(self, session=None, method='get'):
        request = getattr(self.factory, method)('/pharmacist/')
        request.session = session if session is not None else {'staff_id': 138, 'role_type': 3}
        return request

    def render_dashboard(self, counts=None, recent=None, **kwargs):
        with patch.object(views, '_current_pharmacist', return_value=(self.user, self.pharmacist)):
            with patch.object(views, '_workbench_data', return_value=(
                counts or {'pending_review': 0, 'pending_dispense': 0, 'processed': 0},
                recent or [],
            ), **kwargs):
                return views.dashboard(self.request())

    def test_unauthorized_roles_never_query_business_data(self):
        sessions = [
            {}, {'staff_id': 138, 'role_type': 0},
            {'staff_id': 138, 'role_type': 1}, {'staff_id': 138, 'role_type': 2},
            {'staff_id': '138', 'role_type': 3}, {'staff_id': True, 'role_type': 3},
        ]
        with patch.object(views.Pharmacist.objects, 'filter') as profiles:
            with patch.object(views, '_workbench_data') as data:
                for session in sessions:
                    with self.subTest(session=session):
                        response = views.dashboard(self.request(session))
                        self.assertEqual(response.status_code, 302)
                        self.assertEqual(response.url, '/accounts/staff/pharmacist/')
                profiles.assert_not_called()
                data.assert_not_called()

    def test_missing_profile_denies_access(self):
        with patch.object(views.Pharmacist.objects, 'filter') as profiles:
            profiles.return_value.first.return_value = None
            with patch.object(views, '_workbench_data') as data:
                self.assertEqual(views.dashboard(self.request()).status_code, 302)
                data.assert_not_called()

    def test_missing_disabled_or_wrong_role_account_denies_access(self):
        with patch.object(views.Pharmacist.objects, 'filter') as profiles:
            profiles.return_value.first.return_value = self.pharmacist
            with patch.object(views.SysUser.objects, 'filter') as users:
                users.return_value.first.return_value = None
                with patch.object(views, '_workbench_data') as data:
                    self.assertEqual(views.dashboard(self.request()).status_code, 302)
                    users.assert_called_once_with(user_id=138, role_type=3, status=1)
                    data.assert_not_called()

    def test_valid_staff_uses_existing_session(self):
        with patch.object(views.Pharmacist.objects, 'filter') as profiles:
            profiles.return_value.first.return_value = self.pharmacist
            with patch.object(views.SysUser.objects, 'filter') as users:
                users.return_value.first.return_value = self.user
                self.assertEqual(views._current_pharmacist(self.request()), (self.user, self.pharmacist))

    def test_empty_data_renders_zero_and_empty_state(self):
        response = self.render_dashboard()
        self.assertContains(response, '您好，测试药师')
        self.assertContains(response, '暂无处方数据')
        self.assertContains(response, '>0</div>', count=3)
        self.assertNotContains(response, 'role="alert"')
        self.assertContains(response, '/static/pharmacist/dashboard.css')

    def test_populated_list_has_badges_and_no_write_controls(self):
        response = self.render_dashboard(
            counts={'pending_review': 7, 'pending_dispense': 3, 'processed': 2},
            recent=[{'presc_id': 42, 'status': '待审核', 'badge': 'badge-orange',
                     'assignment': '待分配', 'created_at': datetime(2026, 11, 19, 12, 0)}],
        )
        self.assertContains(response, '>7</div>')
        self.assertContains(response, '>3</div>')
        self.assertContains(response, '>2</div>')
        self.assertContains(response, '#42')
        self.assertContains(response, 'badge badge-orange')
        self.assertContains(response, '待分配')
        self.assertContains(response, '2026-11-19 12:00')
        self.assertNotContains(response, '暂无处方数据')
        self.assertNotContains(response, '<form')
        self.assertNotContains(response, '<button')

    def test_staff_name_is_escaped(self):
        self.user.name = '<script>alert(1)</script>'
        response = self.render_dashboard()
        self.assertContains(response, '&lt;script&gt;')
        self.assertNotContains(response, '<script>alert(1)</script>')

    def test_prescription_failure_is_not_displayed_as_empty_or_zero(self):
        response = self.render_dashboard(side_effect=OperationalError('private db details'))
        self.assertContains(response, '处方数据暂不可用', status_code=503)
        self.assertContains(response, '—</div>', count=3, status_code=503)
        self.assertNotContains(response, '>0</div>', status_code=503)
        self.assertNotContains(response, '暂无处方数据', status_code=503)
        self.assertNotContains(response, 'private db details', status_code=503)

    def test_authentication_failure_never_loads_prescriptions(self):
        with patch.object(views, '_current_pharmacist', side_effect=OperationalError('secret')):
            with patch.object(views, '_workbench_data') as data:
                response = views.dashboard(self.request())
                self.assertContains(response, '身份信息暂时无法核验', status_code=503)
                self.assertNotContains(response, 'secret', status_code=503)
                data.assert_not_called()

    def test_unsafe_methods_are_rejected_before_queries(self):
        with patch.object(views, '_current_pharmacist') as staff:
            for method in ('post', 'put', 'patch', 'delete'):
                with self.subTest(method=method):
                    self.assertEqual(views.dashboard(self.request(method=method)).status_code, 405)
            staff.assert_not_called()

    def test_navigation_points_to_existing_routes_or_sections(self):
        response = self.render_dashboard()
        for target in ('/pharmacist/', '/accounts/logout/', '#recent-prescriptions', '#workbench-notes'):
            self.assertContains(response, 'href="%s"' % target)
        self.assertContains(response, 'id="recent-prescriptions"')
        self.assertContains(response, 'id="workbench-notes"')


class WorkbenchQueryTests(SimpleTestCase):
    """执行 ORM 编译出的 SELECT；测试样例只作为内存 CTE，不建表。"""
    def query_data(self, rows):
        statements = []
        with sqlite3.connect(':memory:') as memory:
            def execute(compiler, result_type=MULTI, chunked_fetch=False, chunk_size=100):
                sql, params = compiler.as_sql()
                self.assertTrue(sql.lstrip().upper().startswith('SELECT'))
                statements.append(sql)
                if rows:
                    cte = 'VALUES ' + ', '.join(['(?, ?, ?, ?)'] * len(rows))
                    inputs = [value for row in rows for value in row]
                else:
                    cte = 'SELECT NULL, NULL, NULL, NULL WHERE 0'
                    inputs = []
                cursor = memory.execute(
                    'WITH prescription(presc_id, status, pharmacist_id, created_at) AS ('
                    + cte + ') ' + sql.replace('%s', '?'), inputs + list(params),
                )
                result = cursor.fetchall()
                names = [column[0] for column in cursor.description]
                if 'created_at' in names:
                    index = names.index('created_at')
                    result = [tuple(datetime.fromisoformat(value) if i == index else value
                                    for i, value in enumerate(row)) for row in result]
                return (result[0] if result else None) if result_type == SINGLE else [result]

            with patch('django.db.models.sql.compiler.SQLCompiler.execute_sql', new=execute):
                result = views._workbench_data(138)
        self.assertEqual(len(statements), 2)
        return result

    def test_empty_query_returns_real_zeroes(self):
        counts, recent = self.query_data([])
        self.assertEqual(counts, {'pending_review': 0, 'pending_dispense': 0, 'processed': 0})
        self.assertEqual(recent, [])

    def test_status_counts_and_visibility_boundaries(self):
        statuses = ('草稿', '待审核', '已通过', '已驳回', '待配药', '已完成', '未知状态')
        rows = [(owner_index * 10 + status_index, status, owner, '2026-11-19 12:00:00')
                for owner_index, owner in enumerate((None, 138, 139))
                for status_index, status in enumerate(statuses)]
        counts, recent = self.query_data(rows)
        self.assertEqual(counts, {'pending_review': 2, 'pending_dispense': 2, 'processed': 4})
        self.assertEqual({row['presc_id'] for row in recent}, {1, 4, 11, 12, 13, 14, 15})
        self.assertEqual({row['assignment'] for row in recent}, {'待分配', '本人'})
        for row in recent:
            self.assertEqual(row['badge'], views.STATUS_BADGES[row['status']])

    def test_recent_limit_and_stable_time_order_do_not_limit_totals(self):
        start = datetime(2026, 11, 19, 12, 0)
        rows = [(i, '待审核', None, (start + timedelta(minutes=i // 2)).isoformat())
                for i in range(15)]
        counts, recent = self.query_data(rows)
        self.assertEqual(counts['pending_review'], 15)
        self.assertEqual([row['presc_id'] for row in recent], list(range(14, 4, -1)))

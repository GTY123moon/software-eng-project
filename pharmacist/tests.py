"""无数据库测试：权限与模板用模拟对象，查询 SQL 在内存 CTE 上验证。

不连接 MySQL，不创建表、测试库或持久化演示数据。
"""
from datetime import datetime, timedelta
from decimal import Decimal
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

from django.db import OperationalError
from django.db.models.sql.constants import MULTI, SINGLE
from django.core.paginator import Paginator
from django.http import Http404
from django.test import RequestFactory, SimpleTestCase
from django.urls import resolve, reverse

from . import views


class DashboardTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = SimpleNamespace(name='测试药师', phone='13900000138')
        self.pharmacist = SimpleNamespace(pharmacist_id=138, pharmacist_type='执业药师', gender='女')

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
        self.assertContains(response, '💊 药师')
        self.assertContains(response, '暂无处方数据')
        self.assertContains(response, '>0</div>', count=3)
        self.assertNotContains(response, 'role="alert"')
        self.assertContains(response, '/static/pharmacist/dashboard.css')

    def test_populated_list_has_badges_and_no_write_controls(self):
        response = self.render_dashboard(
            counts={'pending_review': 7, 'pending_dispense': 3, 'processed': 2},
            recent=[{'presc_id': 42, 'status': '待审核', 'badge': 'badge-orange',
                     'assignment': '未处理', 'created_at': datetime(2026, 11, 19, 12, 0)}],
        )
        self.assertContains(response, '>7</div>')
        self.assertContains(response, '>3</div>')
        self.assertContains(response, '>2</div>')
        self.assertContains(response, '#42')
        self.assertContains(response, 'badge badge-orange')
        self.assertContains(response, '未处理')
        self.assertContains(response, '2026-11-19 12:00')
        self.assertNotContains(response, '暂无处方数据')
        self.assertNotContains(response, '<form')
        self.assertNotContains(response, '<button')

    def test_staff_name_is_escaped(self):
        # 工作台顶栏已改为固定“药师”标识，姓名不再展示；转而在个人中心
        # 的表单回显里验证转义，避免 XSS 回归。
        self.user.name = '<script>alert(1)</script>'
        request = self.factory.get('/pharmacist/profile/')
        request.session = {}
        with patch.object(views, '_current_pharmacist', return_value=(self.user, self.pharmacist)):
            response = views.profile(request)
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

    def test_navigation_points_to_independent_pages(self):
        response = self.render_dashboard()
        for target in ('/pharmacist/', '/accounts/logout/',
                       '/pharmacist/prescriptions/',
                       '/pharmacist/prescriptions/pending-review/',
                       '/pharmacist/prescriptions/pending-dispense/',
                       '/pharmacist/prescriptions/processed/'):
            self.assertContains(response, 'href="%s"' % target)
        self.assertNotContains(response, 'href="#')

    def test_home_preview_is_short_and_links_to_real_detail(self):
        rows = [{'presc_id': i, 'status': '待审核', 'badge': 'badge-orange',
                 'assignment': '未处理', 'created_at': datetime(2026, 11, 19)}
                for i in range(10)]
        response = self.render_dashboard(recent=rows)
        self.assertContains(response, 'aria-label="查看处方', count=5)
        self.assertContains(response, '/pharmacist/prescriptions/0/')

    def test_all_routes_resolve_and_reject_writes_before_authentication(self):
        targets = [('dashboard', {}), ('prescription_list', {}),
                   ('pending_review', {}), ('pending_dispense', {}),
                   ('processed', {}), ('prescription_detail', {'presc_id': 42})]
        with patch.object(views, '_current_pharmacist') as auth:
            for name, kwargs in targets:
                match = resolve(reverse('pharmacist:' + name, kwargs=kwargs))
                for method in ('post', 'put', 'patch', 'delete'):
                    self.assertEqual(match.func(self.request(method=method), **match.kwargs).status_code, 405)
            auth.assert_not_called()

    def test_write_routes_require_post_and_pharmacist(self):
        for name in ('prescription_audit', 'prescription_dispense'):
            match = resolve(reverse('pharmacist:' + name, kwargs={'presc_id': 42}))
            get_response = match.func(self.request(method='get'), **match.kwargs)
            self.assertEqual(get_response.status_code, 405)
            with patch.object(views, '_current_pharmacist', return_value=None):
                post_response = match.func(self.request(session={'staff_id': 138, 'role_type': 1},
                                                        method='post'), **match.kwargs)
            self.assertEqual(post_response.status_code, 302)
            self.assertEqual(post_response.url, '/accounts/staff/pharmacist/')

    def test_new_pages_require_pharmacist_identity(self):
        for name, kwargs in [('prescription_list', {}), ('pending_review', {}),
                             ('pending_dispense', {}), ('processed', {}),
                             ('prescription_detail', {'presc_id': 42})]:
            match = resolve(reverse('pharmacist:' + name, kwargs=kwargs))
            for session in ({}, {'staff_id': 138, 'role_type': 1},
                            {'staff_id': '138', 'role_type': 3}):
                response = match.func(self.request(session), **match.kwargs)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.url, '/accounts/staff/pharmacist/')

    def list_response(self, params=None, rows=None, error=None, section='all'):
        request = self.factory.get('/pharmacist/prescriptions/', params or {})
        request.session = {}
        with patch.object(views, '_current_pharmacist', return_value=(self.user, self.pharmacist)):
            with patch.object(views, '_list_data', return_value=Paginator(rows or [], 20).get_page(1),
                              side_effect=error):
                return views.prescription_list(request, section=section)

    def test_list_preserves_filters_sort_and_detail_return_state(self):
        row = {'presc_id': 42, 'status': '已通过', 'badge': 'badge-green',
               'assignment': '本人', 'created_at': datetime(2026, 11, 19)}
        response = self.list_response({'status': '已通过', 'sort': 'oldest'}, [row], section='processed')
        self.assertContains(response, '<option value="已通过" selected>已通过</option>', html=True)
        self.assertContains(response, '<option value="oldest" selected>旧到新</option>', html=True)
        self.assertContains(response, 'from=processed&amp;status=')
        self.assertContains(response, 'sort=oldest&amp;page=1')
        self.assertContains(response, 'method="get"')
        self.assertNotContains(response, 'method="post"')
        self.assertContains(response, '恢复默认')

    def test_list_empty_and_query_failure_are_distinct(self):
        response = self.list_response({'status': '已驳回'})
        self.assertContains(response, '当前筛选下暂无处方')
        response = self.list_response(error=OperationalError('secret database details'))
        self.assertContains(response, '处方列表暂不可用', status_code=503)
        self.assertNotContains(response, '当前筛选下暂无处方', status_code=503)
        self.assertNotContains(response, '共 <strong>0</strong>', status_code=503)
        self.assertNotContains(response, 'secret database details', status_code=503)

    def test_pagination_links_keep_state_and_page_out_of_range_recovers(self):
        rows = [{'presc_id': i, 'status': '已通过', 'badge': 'badge-green',
                 'assignment': '本人', 'created_at': datetime(2026, 11, 19)} for i in range(45)]
        request = self.factory.get('/pharmacist/prescriptions/', {'status': '已通过', 'sort': 'oldest'})
        request.session = {}
        with patch.object(views, '_current_pharmacist', return_value=(self.user, self.pharmacist)):
            with patch.object(views, '_list_data', return_value=Paginator(rows, 20).get_page(2)):
                response = views.prescription_list(request)
        self.assertContains(response, 'sort=oldest&amp;page=1')
        self.assertContains(response, 'sort=oldest&amp;page=3')
        self.assertContains(response, '显示 21–40 条')

    def test_detail_handles_missing_and_failure_without_leaking_content(self):
        request = self.factory.get('/pharmacist/prescriptions/42/',
                                   {'from': '//outside.example', 'sort': 'oldest'})
        request.session = {}
        with patch.object(views, '_current_pharmacist', return_value=(self.user, self.pharmacist)):
            with patch.object(views, '_detail_data', side_effect=Http404):
                response = views.prescription_detail(request, 42)
                self.assertContains(response, '未找到可查看的处方', status_code=404)
                self.assertContains(response, 'href="/pharmacist/prescriptions/?sort=oldest&amp;page=1"', status_code=404)
                self.assertNotContains(response, 'outside.example', status_code=404)
            with patch.object(views, '_detail_data', side_effect=OperationalError('secret')):
                response = views.prescription_detail(request, 42)
                self.assertContains(response, '处方详情暂不可用', status_code=503)
                self.assertNotContains(response, 'secret', status_code=503)

    def test_detail_uses_visibility_query_before_reading_items(self):
        with patch.object(views, '_visible_prescriptions') as visible:
            visible.return_value.select_related.return_value.filter.return_value.first.return_value = None
            with patch.object(views.PrescriptionItem.objects, 'filter') as items:
                with self.assertRaises(Http404):
                    views._detail_data(138, 42)
                visible.assert_called_once_with(138)
                visible.return_value.select_related.return_value.filter.assert_called_once_with(presc_id=42)
                items.assert_not_called()

    def test_readonly_detail_renders_items_and_escapes_user_content(self):
        consultation = SimpleNamespace(final_diagnosis='<script>private</script>',
            doctor_review_status='已采纳', doctor_review_opinion='已核对')
        prescription = SimpleNamespace(presc_id=42, status='待审核', consultation_id=7,
            created_at=datetime(2026, 11, 19), consultation=consultation,
            patient=SimpleNamespace(gender='女', age=30, allergy='未填写', medical_history='无'),
            doctor=SimpleNamespace(department='内科'), ai_check_result='通过',
            ai_check_comment='供参考', doctor_confirm=1, audit_conclusion=None,
            audit_time=None, pickup_status=0, dispensed_at=None)
        data = {'prescription': prescription, 'patient_name': '测试患者', 'doctor_name': '测试医生',
                'pharmacist_name': '未处理', 'badge': 'badge-orange', 'pickup_text': '尚未选择',
                'total': Decimal('24.60'), 'items': [{'drug_name': '测试药品',
                'drug__specification': '10片', 'qty': 2, 'dosage': '遵医嘱',
                'unit_price': Decimal('12.30'), 'subtotal': Decimal('24.60'), 'is_decoction': 0}]}
        with patch.object(views, '_current_pharmacist', return_value=(self.user, self.pharmacist)):
            with patch.object(views, '_detail_data', return_value=data):
                response = views.prescription_detail(self.request(), 42)
        self.assertContains(response, '测试药品')
        self.assertContains(response, '24.60')
        self.assertContains(response, '&lt;script&gt;private&lt;/script&gt;')
        self.assertNotContains(response, '<form')
        self.assertNotContains(response, '<button')
        self.assertNotContains(response, '/accounts/prescriptions/')


class WorkbenchQueryTests(SimpleTestCase):
    """执行 ORM 编译出的 SELECT；测试样例只作为内存 CTE，不建表。"""
    def query_data(self, rows, operation=None):
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
                result = operation() if operation else views._workbench_data(138)
        if operation is None:
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
        self.assertEqual({row['assignment'] for row in recent}, {'未处理', '本人'})
        for row in recent:
            self.assertEqual(row['badge'], views.STATUS_BADGES[row['status']])

    def test_recent_limit_and_stable_time_order_do_not_limit_totals(self):
        start = datetime(2026, 11, 19, 12, 0)
        rows = [(i, '待审核', None, (start + timedelta(minutes=i // 2)).isoformat())
                for i in range(15)]
        counts, recent = self.query_data(rows)
        self.assertEqual(counts['pending_review'], 15)
        self.assertEqual([row['presc_id'] for row in recent], list(range(14, 4, -1)))

    def test_list_filters_before_pagination_and_never_exposes_other_owners(self):
        rows = [(i, '待审核', None, '2026-11-19 12:00:00') for i in range(45)]
        rows += [(100, '待审核', 139, '2026-11-19 12:00:00'),
                 (101, '草稿', 138, '2026-11-19 12:00:00')]
        state = views._list_state({'sort': 'oldest', 'page': '2'}, 'review')
        page = self.query_data(rows, lambda: views._list_data(138, state))
        self.assertEqual(page.paginator.count, 45)
        self.assertEqual([row['presc_id'] for row in page.object_list], list(range(20, 40)))
        state['page'] = 999
        page = self.query_data(rows, lambda: views._list_data(138, state))
        self.assertEqual(page.number, 3)

    def test_section_counts_agree_with_home_counts(self):
        statuses = ('草稿', '待审核', '已通过', '已驳回', '待配药', '已完成')
        rows = [(a * 10 + b, status, owner, '2026-11-19 12:00:00')
                for a, owner in enumerate((None, 138, 139)) for b, status in enumerate(statuses)]
        counts, _ = self.query_data(rows)
        for section, key in [('review', 'pending_review'), ('dispense', 'pending_dispense'), ('processed', 'processed')]:
            state = views._list_state({}, section)
            page = self.query_data(rows, lambda: views._list_data(138, state))
            self.assertEqual(page.paginator.count, counts[key])
        state = views._list_state({'status': '已驳回'}, 'processed')
        page = self.query_data(rows, lambda: views._list_data(138, state))
        self.assertEqual([row['presc_id'] for row in page], [13])

    def test_invalid_query_parameters_cannot_change_scope_or_sort_fields(self):
        for raw_page in ('oops', '-4', '9' * 5000):
            state = views._list_state({'status': '草稿', 'sort': 'patient__name', 'page': raw_page}, 'review')
            self.assertEqual(state, {'section': 'review', 'status': '', 'sort': 'newest', 'page': 1})
        state = views._list_state({'status': '已通过'}, 'dispense')
        self.assertEqual(state['status'], '')

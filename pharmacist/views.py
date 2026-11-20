"""药师只读工作区：所有页面共用角色验证和处方可见范围。"""
from decimal import Decimal
from functools import wraps
from urllib.parse import urlencode

from django.core.paginator import Paginator
from django.db import DatabaseError
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import render, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_safe

from core.models import SysUser, Pharmacist, Prescription, PrescriptionItem


QUEUE_STATUSES = ('待审核', '待配药')
PROCESSED_STATUSES = ('已通过', '已驳回', '待配药', '已完成')
STATUS_BADGES = {
    '待审核': 'badge-orange',
    '待配药': 'badge-blue',
    '已通过': 'badge-green',
    '已驳回': 'badge-red',
    '已完成': 'badge-green',
}
RECENT_LIMIT = 10
PAGE_SIZE = 20
SECTIONS = {
    'all': ('全部可见处方', 'prescription_list', tuple(STATUS_BADGES)),
    'review': ('待审核处方', 'pending_review', ('待审核',)),
    'dispense': ('待配药处方', 'pending_dispense', ('待配药',)),
    'processed': ('本人已处理', 'processed', PROCESSED_STATUSES),
}
SORT_ORDERS = {
    'newest': ('-created_at', '-presc_id'),
    'oldest': ('created_at', 'presc_id'),
}


def _current_pharmacist(request):
    """沿用 staff_id / role_type 会话，核对药师档案与启用账号。"""
    uid = request.session.get('staff_id')
    if type(uid) is not int or request.session.get('role_type') != 3:
        return None
    pharmacist = Pharmacist.objects.filter(pharmacist_id=uid).first()
    if not pharmacist:
        return None
    user = SysUser.objects.filter(user_id=uid, role_type=3, status=1).first()
    if not user:
        return None
    return user, pharmacist


def _visible_prescriptions(pharmacist_id):
    """查询、统计和详情都从此范围开始，拒绝草稿与他人专属记录。"""
    return Prescription.objects.filter(
        Q(pharmacist_id__isnull=True, status__in=QUEUE_STATUSES)
        | Q(pharmacist_id=pharmacist_id, status__in=tuple(STATUS_BADGES))
    )


def _decorate_rows(rows):
    for row in rows:
        row['badge'] = STATUS_BADGES.get(row['status'], 'badge-gray')
        row['assignment'] = '待分配' if row['pharmacist_id'] is None else '本人'
    return rows


def _workbench_data(pharmacist_id):
    visible = _visible_prescriptions(pharmacist_id)
    counts = visible.aggregate(
        pending_review=Count('presc_id', filter=Q(status='待审核')),
        pending_dispense=Count('presc_id', filter=Q(status='待配药')),
        processed=Count('presc_id', filter=Q(
            pharmacist_id=pharmacist_id, status__in=PROCESSED_STATUSES)),
    )
    recent = list(visible.order_by('-created_at', '-presc_id').values(
        'presc_id', 'status', 'pharmacist_id', 'created_at',
    )[:RECENT_LIMIT])
    return counts, _decorate_rows(recent)


def pharmacist_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        try:
            staff = _current_pharmacist(request)
        except DatabaseError:
            return render(request, 'pharmacist/unavailable.html', {
                'page_title': '工作台暂不可用',
                'data_error': '身份信息暂时无法核验，请稍后重试；工作台数据暂不可用。',
            }, status=503)
        if not staff:
            return redirect(reverse('accounts:staff_login', args=['pharmacist']))
        request.pharmacist_staff = staff
        return view(request, *args, **kwargs)
    return wrapped


def _base_context(request, title, nav):
    user, pharmacist = request.pharmacist_staff
    return {
        'page_title': title, 'nav': nav,
        'staff_name': user.name,
        'staff_type': pharmacist.pharmacist_type or '药师',
        'data_error': '', 'loaded_at': timezone.now(),
    }


def _list_state(params, section):
    """只接受已知参数；排序字段和返回目标都不由外部任意指定。"""
    if section not in SECTIONS:
        section = 'all'
    status = params.get('status', '')
    if status not in SECTIONS[section][2]:
        status = ''
    sort = params.get('sort', 'newest')
    if sort not in SORT_ORDERS:
        sort = 'newest'
    raw_page = params.get('page', '1')
    try:
        page = max(1, int(raw_page)) if len(str(raw_page)) < 10 else 1
    except (ValueError, TypeError):
        page = 1
    return {'section': section, 'status': status, 'sort': sort, 'page': page}


def _state_query(state, **changes):
    values = dict(state, **changes)
    return urlencode({key: value for key, value in values.items()
                      if key != 'section' and value != ''})


def _list_url(state):
    return reverse('pharmacist:' + SECTIONS[state['section']][1])


def _filtered_prescriptions(pharmacist_id, state):
    prescriptions = _visible_prescriptions(pharmacist_id)
    if state['section'] == 'processed':
        prescriptions = prescriptions.filter(
            pharmacist_id=pharmacist_id, status__in=PROCESSED_STATUSES)
    elif state['section'] != 'all':
        prescriptions = prescriptions.filter(status__in=SECTIONS[state['section']][2])
    if state['status']:
        prescriptions = prescriptions.filter(status=state['status'])
    return prescriptions.order_by(*SORT_ORDERS[state['sort']])


def _list_data(pharmacist_id, state):
    rows = _filtered_prescriptions(pharmacist_id, state).values(
        'presc_id', 'status', 'pharmacist_id', 'created_at')
    page = Paginator(rows, PAGE_SIZE).get_page(state['page'])
    page.object_list = _decorate_rows(list(page.object_list))
    return page


@require_safe
@pharmacist_required
def dashboard(request):
    context = _base_context(request, '药师工作台', 'home')
    context.update(counts=None, recent_prescriptions=[])
    try:
        context['counts'], context['recent_prescriptions'] = _workbench_data(
            request.pharmacist_staff[1].pharmacist_id)
        context['recent_prescriptions'] = context['recent_prescriptions'][:5]
    except DatabaseError:
        context['data_error'] = '处方数据暂不可用，请稍后重试；当前未显示统计结果。'
        return render(request, 'pharmacist/dashboard.html', context, status=503)
    return render(request, 'pharmacist/dashboard.html', context)


@require_safe
@pharmacist_required
def prescription_list(request, section='all'):
    state = _list_state(request.GET, section)
    context = _base_context(request, SECTIONS[state['section']][0], state['section'])
    context.update(
        state=state, status_options=SECTIONS[state['section']][2],
        list_url=_list_url(state), page_obj=None,
        filtered=bool(state['status']) or state['sort'] != 'newest',
    )
    try:
        page = _list_data(request.pharmacist_staff[1].pharmacist_id, state)
        state['page'] = page.number
        context['page_obj'] = page
        for row in page.object_list:
            row['detail_query'] = urlencode({'from': state['section']}) + '&' + _state_query(state)
        if page.has_previous():
            context['previous_query'] = _state_query(state, page=page.previous_page_number())
        if page.has_next():
            context['next_query'] = _state_query(state, page=page.next_page_number())
    except DatabaseError:
        context['data_error'] = '处方列表暂不可用，请稍后重试；当前未显示查询结果。'
    context['refresh_url'] = _list_url(state) + '?' + _state_query(state)
    return render(request, 'pharmacist/prescription_list.html', context,
                  status=503 if context['data_error'] else 200)


def _detail_data(pharmacist_id, presc_id):
    prescription = _visible_prescriptions(pharmacist_id).select_related(
        'consultation', 'patient', 'doctor').filter(presc_id=presc_id).first()
    if prescription is None:
        raise Http404('未找到可查看的处方')
    # 明细仅使用 values 读取，不对联合主键模型执行实例 save/delete。
    items = list(PrescriptionItem.objects.filter(prescription_id=presc_id)
                 .order_by('drug_id').values(
                     'drug_name', 'drug__specification', 'qty', 'dosage',
                     'unit_price', 'subtotal', 'is_decoction', 'decoction_num'))
    names = dict(SysUser.objects.filter(user_id__in=(
        prescription.patient_id, prescription.doctor_id,
        prescription.pharmacist_id)).values_list('user_id', 'name'))
    return {
        'prescription': prescription, 'items': items,
        'total': sum((item['subtotal'] for item in items), Decimal('0.00')),
        'patient_name': names.get(prescription.patient_id, '未提供'),
        'doctor_name': names.get(prescription.doctor_id, '未提供'),
        'pharmacist_name': names.get(prescription.pharmacist_id, '待分配'),
        'badge': STATUS_BADGES[prescription.status],
        'pickup_text': {0: '院药房取药', 1: '社会药店购药'}.get(
            prescription.pickup_method, '尚未选择'),
    }


@require_safe
@pharmacist_required
def prescription_detail(request, presc_id):
    state = _list_state(request.GET, request.GET.get('from', 'all'))
    context = _base_context(request, '处方详情', state['section'])
    context['back_url'] = _list_url(state) + '?' + _state_query(state)
    context['back_label'] = SECTIONS[state['section']][0]
    try:
        context.update(_detail_data(request.pharmacist_staff[1].pharmacist_id, presc_id))
    except Http404:
        context['missing'] = True
        return render(request, 'pharmacist/prescription_detail.html', context, status=404)
    except DatabaseError:
        context['data_error'] = '处方详情暂不可用，请稍后重试；未显示不完整的处方信息。'
    return render(request, 'pharmacist/prescription_detail.html', context,
                  status=503 if context['data_error'] else 200)

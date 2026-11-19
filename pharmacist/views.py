"""药师工作台：复用职工会话，仅查询处方概览，不执行业务写入。"""
from django.db import DatabaseError
from django.db.models import Count, Q
from django.shortcuts import render, redirect
from django.urls import reverse
from django.views.decorators.http import require_safe

from core.models import SysUser, Pharmacist, Prescription


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


def _workbench_data(pharmacist_id):
    """共享待办加本人记录；不读取草稿、其他药师记录或患者隐私。"""
    visible = Prescription.objects.filter(
        Q(pharmacist_id__isnull=True, status__in=QUEUE_STATUSES)
        | Q(pharmacist_id=pharmacist_id, status__in=tuple(STATUS_BADGES))
    )
    counts = visible.aggregate(
        pending_review=Count('presc_id', filter=Q(status='待审核')),
        pending_dispense=Count('presc_id', filter=Q(status='待配药')),
        processed=Count('presc_id', filter=Q(
            pharmacist_id=pharmacist_id, status__in=PROCESSED_STATUSES)),
    )
    recent = list(visible.order_by('-created_at', '-presc_id').values(
        'presc_id', 'status', 'pharmacist_id', 'created_at',
    )[:RECENT_LIMIT])
    for row in recent:
        row['badge'] = STATUS_BADGES.get(row['status'], 'badge-gray')
        row['assignment'] = '待分配' if row['pharmacist_id'] is None else '本人'
    return counts, recent


@require_safe
def dashboard(request):
    context = {
        'staff_name': '药师',
        'staff_type': '药师',
        'counts': None,
        'recent_prescriptions': [],
        'data_error': '',
    }
    try:
        staff = _current_pharmacist(request)
    except DatabaseError:
        context['data_error'] = '身份信息暂时无法核验，请稍后重试；工作台数据暂不可用。'
        return render(request, 'pharmacist/dashboard.html', context, status=503)
    if not staff:
        return redirect(reverse('accounts:staff_login', args=['pharmacist']))
    user, pharmacist = staff
    context.update(staff_name=user.name, staff_type=pharmacist.pharmacist_type or '药师')
    try:
        context['counts'], context['recent_prescriptions'] = _workbench_data(
            pharmacist.pharmacist_id)
    except DatabaseError:
        context['data_error'] = '处方数据暂不可用，请稍后重试；当前未显示统计结果。'
        return render(request, 'pharmacist/dashboard.html', context, status=503)
    return render(request, 'pharmacist/dashboard.html', context)

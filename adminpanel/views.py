"""管理员后台视图：药品目录、值班排班、日志审计、账号管理、首页统计。

会话口径与职工登录一致：session['staff_id'] + session['role_type'] == 0。
所有写操作统一通过 core.log_service.write_log 落 operation_log，
日志号（log_id）即为"按日志号回溯"的入口。
"""
from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from core.log_service import write_log
from core.models import (
    Admin, AiPreAssessment, Consultation, Doctor, Drug, DutySchedule, OperationLog,
    Patient, Pharmacist, Prescription, PrescriptionItem, ReferralLog, SysUser,
)

# 角色枚举（与 README / 建表脚本一致）
ROLE_ADMIN, ROLE_PATIENT, ROLE_DOCTOR, ROLE_PHARMACIST = 0, 1, 2, 3
ROLE_CN = {ROLE_ADMIN: '管理员', ROLE_PATIENT: '患者', ROLE_DOCTOR: '医生', ROLE_PHARMACIST: '药师'}

# 药品低库存预警阈值（仅页面提示，不改变库存扣减逻辑）
LOW_STOCK_THRESHOLD = 100

OPERATOR_TYPES = ['患者', '医生', '药师', '管理员']
TARGET_TYPES = ['consultation', 'prescription', 'duty', 'drug', 'sys_user',
                'patient', 'doctor', 'pharmacist', 'ai_pre_assessment']
# 目标类型中文名：筛选值仍用英文代码，页面展示用中文
TARGET_CN = {
    'consultation': '问诊', 'prescription': '处方', 'duty': '排班',
    'drug': '药品', 'sys_user': '账号', 'patient': '患者档案',
    'doctor': '医生档案', 'pharmacist': '药师档案',
    'ai_pre_assessment': 'AI 预评估',
}
SHIFT_TYPES = ['白班', '夜班']
DUTY_STATUS = ['正常', '调班', '停诊']
# 取药方式枚举（与患者端选择口径一致）
PICKUP_METHOD = {0: '院药房取药', 1: '社会药店购药'}


# --------------------------------------------------------------------------
# 公共：登录态与上下文
# --------------------------------------------------------------------------
def _current_admin(request):
    """校验会话为管理员角色且管理员档案存在；返回 (user, admin) 或 None。"""
    uid = request.session.get('staff_id')
    if not isinstance(uid, int) or request.session.get('role_type') != ROLE_ADMIN:
        return None
    admin = Admin.objects.filter(admin_id=uid).first()
    if not admin:
        return None
    user = SysUser.objects.filter(user_id=uid).first()
    return user, admin


def admin_required(view):
    """未登录或非管理员时跳回管理员登录页，并把当前管理员挂到 request 上。"""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        staff = _current_admin(request)
        if not staff:
            return redirect(reverse('accounts:staff_login', args=['admin']))
        request.admin_user, request.admin_profile = staff
        return view(request, *args, **kwargs)
    return wrapper


def _common(request, active):
    """侧边栏高亮与顶部管理员信息。"""
    return {
        'active': active,
        'staff_name': request.admin_user.name if request.admin_user else '管理员',
        'staff_account': request.admin_profile.account,
    }


def _names(user_ids):
    """批量取 sys_user 姓名，避免逐行查询。"""
    ids = [i for i in user_ids if i]
    if not ids:
        return {}
    return dict(SysUser.objects.filter(user_id__in=ids).values_list('user_id', 'name'))


def _fmt(dt):
    """数据库时间转 datetime-local 输入框格式（本机时区）。"""
    if not dt:
        return ''
    if timezone.is_aware(dt):
        dt = timezone.localtime(dt)
    return dt.strftime('%Y-%m-%dT%H:%M')


def _fmt_show(dt):
    """展示用时间字符串。"""
    if not dt:
        return ''
    if timezone.is_aware(dt):
        dt = timezone.localtime(dt)
    return dt.strftime('%Y-%m-%d %H:%M')


def _parse_dt(value):
    """解析 datetime-local 提交值；非法返回 None。"""
    if not value:
        return None
    dt = parse_datetime(value)
    if dt is None:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def _day_range(day):
    """把某一天换成本机时区下的 [当天0点, 次日0点) 时间区间。

    不用 __date 查询：MySQL 的 __date 会走 CONVERT_TZ，
    而 Windows 版 MySQL 默认没导入时区表，CONVERT_TZ 返回 NULL，
    会导致按日期筛选/今日统计恒为 0。改用显式区间比较可绕开该限制。
    """
    start = timezone.make_aware(
        datetime.combine(day, time.min), timezone.get_current_timezone()
    )
    return start, start + timedelta(days=1)


# --------------------------------------------------------------------------
# 首页概览：真实统计
# --------------------------------------------------------------------------
@admin_required
def dashboard(request):
    today = timezone.localdate()
    day_start, day_end = _day_range(today)
    duties = list(DutySchedule.objects.filter(
        start_time__gte=day_start, start_time__lt=day_end, status__in=['正常', '调班']
    ).order_by('start_time')[:10])
    names = _names({d.doctor_id for d in duties})
    for d in duties:
        d.doctor_name = names.get(d.doctor_id, '未知医生')
        d.start_text = _fmt_show(d.start_time)[-5:]
        d.end_text = _fmt_show(d.end_time)[-5:]

    logs = list(OperationLog.objects.order_by('-log_id')[:8])
    log_names = _names({l.operator_id for l in logs})
    for l in logs:
        l.operator_name = log_names.get(l.operator_id, '未知')
        l.time_text = _fmt_show(l.created_at)

    ctx = _common(request, 'home')
    ctx.update({
        'drug_total': Drug.objects.count(),
        'low_stock': Drug.objects.filter(stock_qty__lt=LOW_STOCK_THRESHOLD).count(),
        'today_duty': DutySchedule.objects.filter(start_time__gte=day_start, start_time__lt=day_end, status__in=['正常', '调班']).count(),
        'active_consult': Consultation.objects.filter(status='进行中').count(),
        'pending_prescription': Prescription.objects.filter(status='待审核').count(),
        'today_log': OperationLog.objects.filter(created_at__gte=day_start, created_at__lt=day_end).count(),
        'today_duties': duties,
        'recent_logs': logs,
        'low_stock_threshold': LOW_STOCK_THRESHOLD,
    })
    return render(request, 'adminpanel/dashboard.html', ctx)


# --------------------------------------------------------------------------
# 一、药品目录管理
# --------------------------------------------------------------------------
DRUG_SORT = {
    'stock_asc': ('stock_qty', 'drug_id'),
    'stock_desc': ('-stock_qty', 'drug_id'),
    'price_asc': ('unit_price', 'drug_id'),
    'price_desc': ('-unit_price', 'drug_id'),
    'name_asc': ('name', 'drug_id'),
}


@admin_required
def drug_list(request):
    keyword = request.GET.get('q', '').strip()
    low_only = request.GET.get('low') == '1'
    sort = request.GET.get('sort', '').strip()

    drugs = Drug.objects.all().order_by(*DRUG_SORT.get(sort, ('drug_id',)))
    if keyword:
        drugs = drugs.filter(
            Q(name__icontains=keyword)
            | Q(drug_code__icontains=keyword)
            | Q(specification__icontains=keyword)
        )
    if low_only:
        drugs = drugs.filter(stock_qty__lt=LOW_STOCK_THRESHOLD)

    ctx = _common(request, 'drugs')
    ctx.update({
        'drugs': list(drugs[:300]),
        'shown': min(drugs.count(), 300),
        'keyword': keyword,
        'low_only': low_only,
        'sort': sort,
        'total': Drug.objects.count(),
        'low_count': Drug.objects.filter(stock_qty__lt=LOW_STOCK_THRESHOLD).count(),
        'low_stock_threshold': LOW_STOCK_THRESHOLD,
        'ok': request.GET.get('ok', ''),
        'err': request.GET.get('err', ''),
    })
    return render(request, 'adminpanel/drug_list.html', ctx)


def _read_drug_form(post):
    """读取并校验药品表单，返回 (数据字典, 错误列表)。"""
    errors = []
    code = post.get('drug_code', '').strip()
    name = post.get('name', '').strip()
    specification = post.get('specification', '').strip()
    contraindication = post.get('contraindication', '').strip()

    if not code:
        errors.append('药品编码不能为空')
    if not name:
        errors.append('药品名称不能为空')
    if not specification:
        errors.append('规格/剂型不能为空')

    price = Decimal('0')
    price_raw = post.get('unit_price', '').strip() or '0'
    try:
        price = Decimal(price_raw)
    except (InvalidOperation, ValueError):
        errors.append('单价必须是数字，如 12.50')
    else:
        if price < 0:
            errors.append('单价不能为负数')

    stock = 0
    stock_raw = post.get('stock_qty', '').strip() or '0'
    try:
        stock = int(stock_raw)
    except ValueError:
        errors.append('库存必须是整数')
    else:
        if stock < 0:
            errors.append('库存不能为负数')

    is_prescription = 1 if post.get('is_prescription') == '1' else 0

    return {
        'drug_code': code,
        'name': name,
        'specification': specification,
        'unit_price': price,
        'stock_qty': stock,
        'is_prescription': is_prescription,
        'contraindication': contraindication or None,
    }, errors


@admin_required
def drug_form(request, drug_id=None):
    drug = get_object_or_404(Drug, pk=drug_id) if drug_id else None
    errors = []
    form = {}

    if request.method == 'POST':
        data, errors = _read_drug_form(request.POST)
        form = {
            'drug_code': request.POST.get('drug_code', ''),
            'name': request.POST.get('name', ''),
            'specification': request.POST.get('specification', ''),
            'unit_price': request.POST.get('unit_price', ''),
            'stock_qty': request.POST.get('stock_qty', ''),
            'is_prescription': request.POST.get('is_prescription', '0'),
            'contraindication': request.POST.get('contraindication', ''),
        }
        if not errors:
            dup = Drug.objects.filter(drug_code=data['drug_code'])
            if drug:
                dup = dup.exclude(pk=drug.pk)
            if dup.exists():
                errors.append('药品编码 %s 已存在，请更换' % data['drug_code'])

        if not errors:
            try:
                with transaction.atomic():
                    if drug is None:
                        drug = Drug.objects.create(updated_at=timezone.now(), **data)
                        action = '新增药品'
                    else:
                        for key, value in data.items():
                            setattr(drug, key, value)
                        drug.updated_at = timezone.now()
                        drug.save()
                        action = '修改药品'
                    write_log(request.admin_profile.admin_id, '管理员', action, 'drug', drug.drug_id)
            except IntegrityError:
                errors.append('保存失败：药品编码重复或字段超出数据库约束（单价/库存需 ≥ 0）')
            else:
                return redirect(reverse('adminpanel:drug_list') + '?ok=saved')
    else:
        if drug:
            form = {
                'drug_code': drug.drug_code,
                'name': drug.name,
                'specification': drug.specification,
                'unit_price': drug.unit_price,
                'stock_qty': drug.stock_qty,
                'is_prescription': str(drug.is_prescription),
                'contraindication': drug.contraindication or '',
            }
        else:
            form = {'drug_code': '', 'name': '', 'specification': '',
                    'unit_price': '', 'stock_qty': '0', 'is_prescription': '0',
                    'contraindication': ''}

    ctx = _common(request, 'drugs')
    ctx.update({'form': form, 'drug': drug, 'errors': errors})
    return render(request, 'adminpanel/drug_form.html', ctx)


@admin_required
def drug_delete(request, drug_id):
    """删除药品。被处方明细引用时数据库外键会拒绝，此时给出提示而不是报 500。"""
    get_object_or_404(Drug, pk=drug_id)
    if request.method != 'POST':
        return redirect(reverse('adminpanel:drug_list'))
    try:
        with transaction.atomic():
            Drug.objects.filter(pk=drug_id).delete()
            write_log(request.admin_profile.admin_id, '管理员', '删除药品', 'drug', drug_id)
    except IntegrityError:
        return redirect(reverse('adminpanel:drug_list') + '?err=used')
    return redirect(reverse('adminpanel:drug_list') + '?ok=deleted')


# --------------------------------------------------------------------------
# 二、值班排班管理
# --------------------------------------------------------------------------
def _doctor_options():
    doctors = list(Doctor.objects.all().order_by('doctor_id'))
    names = _names({d.doctor_id for d in doctors})
    return [{
        'id': d.doctor_id,
        'name': names.get(d.doctor_id, '未知医生'),
        'department': d.department,
        'title': d.title or '',
    } for d in doctors]


@admin_required
def duty_list(request):
    doctor_id = request.GET.get('doctor_id', '').strip()
    department = request.GET.get('department', '').strip()
    status = request.GET.get('status', '').strip()
    sort = request.GET.get('sort', '').strip()
    date_raw = request.GET.get('date', '').strip()
    date_value = parse_date(date_raw) if date_raw else None

    order = ('start_time', 'duty_code') if sort == 'time_asc' else ('-start_time', '-duty_code')
    duties = DutySchedule.objects.all().order_by(*order)
    if department:
        dept_doctor_ids = list(
            Doctor.objects.filter(department=department).values_list('doctor_id', flat=True)
        )
        duties = duties.filter(doctor_id__in=dept_doctor_ids)
    if doctor_id.isdigit():
        duties = duties.filter(doctor_id=int(doctor_id))
    if status:
        duties = duties.filter(status=status)
    if date_value:
        day_start, day_end = _day_range(date_value)
        duties = duties.filter(start_time__gte=day_start, start_time__lt=day_end)

    rows = list(duties[:300])
    names = _names({d.doctor_id for d in rows})
    for d in rows:
        d.doctor_name = names.get(d.doctor_id, '未知医生')
        d.start_text = _fmt_show(d.start_time)
        d.end_text = _fmt_show(d.end_time)

    departments = sorted(
        {d for d in Doctor.objects.values_list('department', flat=True) if d}
    )

    ctx = _common(request, 'duties')
    ctx.update({
        'rows': rows,
        'doctors': _doctor_options(),
        'departments': departments,
        'department': department,
        'sort': sort,
        'doctor_id': doctor_id,
        'status': status,
        'date': date_raw,
        'status_options': DUTY_STATUS,
        'total': duties.count(),
        'ok': request.GET.get('ok', ''),
        'err': request.GET.get('err', ''),
    })
    return render(request, 'adminpanel/duty_list.html', ctx)


def _read_duty_form(post):
    """校验排班表单，返回 (数据字典, 错误列表)。"""
    errors = []
    doctor_id = post.get('doctor_id', '').strip()
    shift_type = post.get('shift_type', '').strip()
    status = post.get('status', '').strip() or '正常'
    start_time = _parse_dt(post.get('start_time', '').strip())
    end_time = _parse_dt(post.get('end_time', '').strip())

    if not doctor_id.isdigit():
        errors.append('请选择值班医生')
    if shift_type not in SHIFT_TYPES:
        errors.append('值班类型只能是白班或夜班')
    if status not in DUTY_STATUS:
        errors.append('状态只能是正常、调班或停诊')
    if post.get('start_time') and start_time is None:
        errors.append('开始时间格式不正确')
    if post.get('end_time') and end_time is None:
        errors.append('结束时间格式不正确')
    if start_time and end_time and end_time <= start_time:
        errors.append('结束时间必须晚于开始时间')

    return {
        'doctor_id': int(doctor_id) if doctor_id.isdigit() else None,
        'shift_type': shift_type,
        'start_time': start_time,
        'end_time': end_time,
        'status': status,
    }, errors


@admin_required
def duty_form(request, duty_code=None):
    duty = get_object_or_404(DutySchedule, pk=duty_code) if duty_code else None
    errors = []
    form = {}

    if request.method == 'POST':
        data, errors = _read_duty_form(request.POST)
        form = {
            'doctor_id': request.POST.get('doctor_id', ''),
            'shift_type': request.POST.get('shift_type', '白班'),
            'start_time': request.POST.get('start_time', ''),
            'end_time': request.POST.get('end_time', ''),
            'status': request.POST.get('status', '正常'),
        }

        # 同一医生同一时段不允许排两个有效班次（停诊记录不占号）
        if not errors and data['status'] != '停诊':
            overlap = DutySchedule.objects.filter(
                doctor_id=data['doctor_id'],
                start_time__lt=data['end_time'],
                end_time__gt=data['start_time'],
            ).exclude(status='停诊')
            if duty:
                overlap = overlap.exclude(pk=duty.pk)
            if overlap.exists():
                errors.append('该医生在此时间段已有排班，请调整时间或先修改原班次')

        if not errors:
            try:
                with transaction.atomic():
                    if duty is None:
                        duty = DutySchedule.objects.create(
                            doctor_id=data['doctor_id'],
                            shift_type=data['shift_type'],
                            start_time=data['start_time'],
                            end_time=data['end_time'],
                            status=data['status'],
                            created_by=request.admin_profile.admin_id,
                            updated_at=timezone.now(),
                        )
                        action = '新增排班'
                    else:
                        duty.doctor_id = data['doctor_id']
                        duty.shift_type = data['shift_type']
                        duty.start_time = data['start_time']
                        duty.end_time = data['end_time']
                        duty.status = data['status']
                        duty.updated_at = timezone.now()
                        duty.save()
                        action = '调整排班'
                    write_log(request.admin_profile.admin_id, '管理员', action, 'duty', duty.duty_code)
            except IntegrityError:
                errors.append('保存失败：请检查医生是否存在、班次与状态取值是否合法')
            else:
                return redirect(reverse('adminpanel:duty_list') + '?ok=saved')
    else:
        if duty:
            form = {
                'doctor_id': str(duty.doctor_id),
                'shift_type': duty.shift_type,
                'start_time': _fmt(duty.start_time),
                'end_time': _fmt(duty.end_time),
                'status': duty.status,
            }
        else:
            form = {'doctor_id': '', 'shift_type': '白班', 'start_time': '', 'end_time': '', 'status': '正常'}

    ctx = _common(request, 'duties')
    ctx.update({
        'form': form,
        'duty': duty,
        'errors': errors,
        'doctors': _doctor_options(),
        'shift_types': SHIFT_TYPES,
        'status_options': DUTY_STATUS,
    })
    return render(request, 'adminpanel/duty_form.html', ctx)


# --------------------------------------------------------------------------
# 三、日志审计
# --------------------------------------------------------------------------
@admin_required
def log_list(request):
    operator_type = request.GET.get('operator_type', '').strip()
    target_type = request.GET.get('target_type', '').strip()
    action = request.GET.get('action', '').strip()
    sort = request.GET.get('sort', '').strip()
    date_raw = request.GET.get('date', '').strip()
    date_value = parse_date(date_raw) if date_raw else None

    logs = OperationLog.objects.all().order_by('log_id' if sort == 'time_asc' else '-log_id')
    if operator_type:
        logs = logs.filter(operator_type=operator_type)
    if target_type:
        logs = logs.filter(target_type=target_type)
    if action:
        logs = logs.filter(action__icontains=action)
    if date_value:
        day_start, day_end = _day_range(date_value)
        logs = logs.filter(created_at__gte=day_start, created_at__lt=day_end)

    rows = list(logs[:300])
    names = _names({l.operator_id for l in rows})
    for l in rows:
        l.operator_name = names.get(l.operator_id, '未知')
        l.time_text = _fmt_show(l.created_at)
        l.target_cn = TARGET_CN.get(l.target_type, l.target_type or '—')

    ctx = _common(request, 'logs')
    ctx.update({
        'rows': rows,
        'total': logs.count(),
        'operator_type': operator_type,
        'target_type': target_type,
        'action': action,
        'sort': sort,
        'date': date_raw,
        'operator_types': OPERATOR_TYPES,
        'target_options': [{'code': c, 'label': TARGET_CN[c]} for c in TARGET_TYPES],
    })
    return render(request, 'adminpanel/log_list.html', ctx)


def _target_detail(log):
    """按 target_type + target_id 还原被操作对象，用于日志回溯。"""
    if not log.target_type or not log.target_id:
        return []
    if log.target_type == 'drug':
        obj = Drug.objects.filter(pk=log.target_id).first()
        if obj:
            return [('药品编码', obj.drug_code), ('药品名称', obj.name),
                    ('规格', obj.specification), ('单价', obj.unit_price),
                    ('库存', obj.stock_qty)]
    elif log.target_type == 'duty':
        obj = DutySchedule.objects.filter(pk=log.target_id).first()
        if obj:
            names = _names({obj.doctor_id})
            return [('值班医生', names.get(obj.doctor_id, '未知')),
                    ('班次类型', obj.shift_type),
                    ('开始时间', _fmt_show(obj.start_time)),
                    ('结束时间', _fmt_show(obj.end_time)),
                    ('状态', obj.status)]
    elif log.target_type == 'consultation':
        obj = Consultation.objects.filter(pk=log.target_id).first()
        if obj:
            return [('问诊ID', obj.consult_id), ('问诊状态', obj.status),
                    ('复核状态', obj.doctor_review_status),
                    ('开始时间', _fmt_show(obj.start_time))]
    elif log.target_type == 'prescription':
        obj = Prescription.objects.filter(pk=log.target_id).first()
        if obj:
            related_ids = {obj.doctor_id, obj.patient_id}
            if obj.pharmacist_id:
                related_ids.add(obj.pharmacist_id)
            names = _names(related_ids)
            items = list(PrescriptionItem.objects.filter(prescription_id=obj.presc_id))
            total = sum((i.subtotal for i in items), Decimal('0'))
            if items:
                drug_text = '；'.join(
                    '%s ×%d（用法：%s，小计 ¥%s）' % (
                        i.drug_name, i.qty, i.dosage or '—', i.subtotal)
                    for i in items
                )
            else:
                drug_text = '（无药品明细）'
            detail = [
                ('处方ID', obj.presc_id),
                ('处方状态', obj.status),
                ('开方医生', names.get(obj.doctor_id, '未知')),
                ('患者', names.get(obj.patient_id, '未知')),
                ('所属问诊', obj.consultation_id),
                ('药品明细', drug_text),
                ('合计金额', '¥%s' % total),
                ('AI 校验结论', obj.ai_check_result or '—'),
            ]
            if obj.pharmacist_id:
                detail.append(('审方药师', names.get(obj.pharmacist_id, '未知')))
            if obj.audit_time:
                detail.append(('审方时间', _fmt_show(obj.audit_time)))
            detail.append(('审方结论', obj.audit_conclusion or '—'))
            detail.append(('取药方式', PICKUP_METHOD.get(obj.pickup_method, '未选择')))
            detail.append(('取药状态', '已取药' if obj.pickup_status else '未取药'))
            detail.append(('开方时间', _fmt_show(obj.created_at)))
            return detail
    elif log.target_type == 'sys_user':
        obj = SysUser.objects.filter(pk=log.target_id).first()
        if obj:
            return [('姓名', obj.name), ('手机号', obj.phone),
                    ('角色', ROLE_CN.get(obj.role_type, obj.role_type)),
                    ('账号状态', '启用' if obj.status == 1 else '停用')]
    elif log.target_type == 'doctor':
        obj = Doctor.objects.filter(pk=log.target_id).first()
        if obj:
            names = _names({obj.doctor_id})
            return [('医生ID', obj.doctor_id),
                    ('姓名', names.get(obj.doctor_id, '未知')),
                    ('科室', obj.department or '—'),
                    ('职称', obj.title or '—'),
                    ('擅长', obj.skill or '—'),
                    ('在岗状态', '在岗' if obj.work_status == 1 else '停诊')]
    elif log.target_type == 'pharmacist':
        obj = Pharmacist.objects.filter(pk=log.target_id).first()
        if obj:
            names = _names({obj.pharmacist_id})
            return [('药师ID', obj.pharmacist_id),
                    ('姓名', names.get(obj.pharmacist_id, '未知')),
                    ('药师类型', obj.pharmacist_type or '—'),
                    ('在岗状态', '在岗' if obj.pharmacist_status == 1 else '停用')]
    elif log.target_type == 'patient':
        obj = Patient.objects.filter(pk=log.target_id).first()
        if obj:
            names = _names({obj.patient_id})
            return [('患者ID', obj.patient_id),
                    ('姓名', names.get(obj.patient_id, '未知')),
                    ('病历号', obj.medical_no or '—'),
                    ('档案建立时间', _fmt_show(obj.created_at))]
    elif log.target_type == 'ai_pre_assessment':
        obj = AiPreAssessment.objects.filter(pk=log.target_id).first()
        if obj:
            return [('预评估ID', obj.pre_id),
                    ('所属问诊', obj.consultation_id),
                    ('风险等级', obj.risk_level),
                    ('命中关键词', obj.matched_keyword or '—'),
                    ('复核结果', obj.review_result or '—'),
                    ('生成时间', _fmt_show(obj.created_at))]
    return []


@admin_required
def log_detail(request, log_id):
    log = get_object_or_404(OperationLog, pk=log_id)
    names = _names({log.operator_id})
    ctx = _common(request, 'logs')
    ctx.update({
        'log': log,
        'operator_name': names.get(log.operator_id, '未知'),
        'time_text': _fmt_show(log.created_at),
        'target_cn': TARGET_CN.get(log.target_type, log.target_type or '—'),
        'target_detail': _target_detail(log),
    })
    return render(request, 'adminpanel/log_detail.html', ctx)


@admin_required
def referral_list(request):
    risk_level = request.GET.get('risk_level', '').strip()
    date_raw = request.GET.get('date', '').strip()
    date_value = parse_date(date_raw) if date_raw else None

    referrals = ReferralLog.objects.all().order_by('-referral_id')
    if risk_level:
        referrals = referrals.filter(risk_level=risk_level)
    if date_value:
        day_start, day_end = _day_range(date_value)
        referrals = referrals.filter(created_at__gte=day_start, created_at__lt=day_end)

    rows = list(referrals[:300])
    names = _names({r.patient_id for r in rows})
    for r in rows:
        r.patient_name = names.get(r.patient_id, '未知患者')
        r.time_text = _fmt_show(r.created_at)

    ctx = _common(request, 'referrals')
    ctx.update({
        'rows': rows,
        'total': referrals.count(),
        'risk_level': risk_level,
        'date': date_raw,
    })
    return render(request, 'adminpanel/referral_list.html', ctx)


# --------------------------------------------------------------------------
# 四、账号管理（开通 / 停用医生、药师）
# --------------------------------------------------------------------------
@admin_required
def account_list(request):
    role = request.GET.get('role', '').strip()
    status = request.GET.get('status', '').strip()
    keyword = request.GET.get('q', '').strip()

    users = SysUser.objects.filter(role_type__in=[ROLE_DOCTOR, ROLE_PHARMACIST]).order_by('user_id')
    if role.isdigit():
        users = users.filter(role_type=int(role))
    if status in ('0', '1'):
        users = users.filter(status=int(status))
    if keyword:
        users = users.filter(Q(name__icontains=keyword) | Q(phone__icontains=keyword))

    rows = list(users[:300])
    for u in rows:
        u.role_cn = ROLE_CN.get(u.role_type, u.role_type)
        if u.role_type == ROLE_DOCTOR:
            profile = Doctor.objects.filter(doctor_id=u.user_id).first()
            u.extra = '%s · %s' % (profile.department, profile.title or '医师') if profile else '未建医生档案'
        else:
            profile = Pharmacist.objects.filter(pharmacist_id=u.user_id).first()
            u.extra = (profile.pharmacist_type or '药师') if profile else '未建药师档案'

    ctx = _common(request, 'accounts')
    ctx.update({
        'rows': rows,
        'total': users.count(),
        'role': role,
        'status': status,
        'keyword': keyword,
        'ok': request.GET.get('ok', ''),
        'err': request.GET.get('err', ''),
    })
    return render(request, 'adminpanel/account_list.html', ctx)


def _read_account_form(post):
    """校验开通账号表单。"""
    errors = []
    role = post.get('role', '').strip()
    name = post.get('name', '').strip()
    phone = post.get('phone', '').strip()
    password = post.get('password', '').strip()
    gender = post.get('gender', '').strip()
    department = post.get('department', '').strip()
    title = post.get('title', '').strip()
    skill = post.get('skill', '').strip()
    pharmacist_type = post.get('pharmacist_type', '').strip()

    if role not in ('2', '3'):
        errors.append('角色只能选医生或药师')
    if not name:
        errors.append('姓名不能为空')
    if not (phone.isdigit() and len(phone) == 8):
        errors.append('工作账号必须是 8 位数字（首位区分角色：患者 1、医生 2、药师 3、管理员 4）')
    elif role == '2' and not phone.startswith('2'):
        errors.append('医生账号首位必须是 2，例如 20000015')
    elif role == '3' and not phone.startswith('3'):
        errors.append('药师账号首位必须是 3，例如 30000015')
    if len(password) < 6:
        errors.append('内部密钥至少 6 位')
    if role == '2' and not department:
        errors.append('医生必须填写所属科室')

    if phone.isdigit() and len(phone) == 8 and SysUser.objects.filter(phone=phone).exists():
        errors.append('工作账号 %s 已被占用，请换一个' % phone)

    return {
        'role': int(role) if role in ('2', '3') else None,
        'name': name,
        'phone': phone,
        'password': password,
        'gender': gender or None,
        'department': department,
        'title': title or None,
        'skill': skill or None,
        'pharmacist_type': pharmacist_type or None,
    }, errors


@admin_required
def account_form(request):
    errors = []
    form = {'role': '2', 'name': '', 'phone': '', 'password': '',
            'gender': '', 'department': '', 'title': '', 'skill': '', 'pharmacist_type': ''}

    if request.method == 'POST':
        data, errors = _read_account_form(request.POST)
        form = {
            'role': request.POST.get('role', '2'),
            'name': request.POST.get('name', ''),
            'phone': request.POST.get('phone', ''),
            'password': request.POST.get('password', ''),
            'gender': request.POST.get('gender', ''),
            'department': request.POST.get('department', ''),
            'title': request.POST.get('title', ''),
            'skill': request.POST.get('skill', ''),
            'pharmacist_type': request.POST.get('pharmacist_type', ''),
        }
        if not errors:
            try:
                with transaction.atomic():
                    user = SysUser.objects.create(
                        name=data['name'],
                        phone=data['phone'],
                        password=data['password'],
                        role_type=data['role'],
                        status=1,
                    )
                    if data['role'] == ROLE_DOCTOR:
                        Doctor.objects.create(
                            doctor_id=user.user_id,
                            department=data['department'],
                            title=data['title'],
                            skill=data['skill'],
                            gender=data['gender'],
                        )
                    else:
                        Pharmacist.objects.create(
                            pharmacist_id=user.user_id,
                            pharmacist_type=data['pharmacist_type'],
                            gender=data['gender'],
                        )
                    write_log(request.admin_profile.admin_id, '管理员', '开通账号', 'sys_user', user.user_id)
            except IntegrityError:
                errors.append('开通失败：手机号重复或档案表主键冲突，请重试')
            else:
                return redirect(reverse('adminpanel:account_list') + '?ok=created')

    ctx = _common(request, 'accounts')
    ctx.update({'form': form, 'errors': errors})
    return render(request, 'adminpanel/account_form.html', ctx)


@admin_required
def account_toggle(request, user_id):
    """停用 / 启用职工账号（仅医生、药师）。"""
    user = get_object_or_404(SysUser, pk=user_id, role_type__in=[ROLE_DOCTOR, ROLE_PHARMACIST])
    if request.method != 'POST':
        return redirect(reverse('adminpanel:account_list'))

    new_status = 0 if user.status == 1 else 1
    user.status = new_status
    user.save(update_fields=['status'])
    write_log(request.admin_profile.admin_id, '管理员',
              '启用账号' if new_status == 1 else '停用账号', 'sys_user', user.user_id)
    return redirect(reverse('adminpanel:account_list') + '?ok=toggled')

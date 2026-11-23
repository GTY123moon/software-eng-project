"""患者端视图：登录/注册、工作台、发起问诊、我的处方（列表与详情）。

数据全部来自 MySQL（core.models 映射）；关键动作写 operation_log。
课设演示阶段密码与种子数据一致，按明文比对；生产环境应使用密码哈希。
"""

import os
import uuid

from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone

from core.models import (
    SysUser, Patient, Doctor, Pharmacist, Admin, Consultation,
    ConsultationMessage, Prescription, PrescriptionItem, AiPreAssessment,
)
from core.ai_service import assess_risk, next_guide_question, guide_history_text, GUIDE_TOPICS, _local_guide_next
from core.log_service import write_log


PICKUP_METHOD = {0: '院药房取药', 1: '社会药店购药'}

# 患者端可见的处方状态：药师审核通过后的流转态；草稿 / 待审核 / 已驳回不对患者展示
PATIENT_VISIBLE_STATUS = ('已通过', '待配药', '已完成')

# 单医生待接诊排队上限：accepted=0 的排队问诊至多 3 人，满员后不可再挂
QUEUE_LIMIT = 3

# 职工角色（医生 / 药师 / 管理员）内部密钥登录口径：
# 工作账号沿用手机号；内部密钥存 sys_user.password（课设演示明文比对）；
# 登录后按角色跳转各自占位工作台。
STAFF_ROLES = {
    'doctor': {
        'role_type': 2, 'role_cn': '医生', 'redirect': 'doctor:dashboard',
        'profile': Doctor, 'profile_key': 'doctor_id',
    },
    'pharmacist': {
        'role_type': 3, 'role_cn': '药师', 'redirect': 'pharmacist:dashboard',
        'profile': Pharmacist, 'profile_key': 'pharmacist_id',
    },
    'admin': {
        'role_type': 0, 'role_cn': '管理员', 'redirect': 'adminpanel:dashboard',
        'profile': Admin, 'profile_key': 'admin_id',
    },
}


def _current_patient(request):
    """从会话取当前登录患者；未登录、档案不存在或账号异常返回 None。"""
    pid = request.session.get('patient_id')
    if not isinstance(pid, int):
        return None
    try:
        return Patient.objects.get(patient_id=pid)
    except Patient.DoesNotExist:
        return None


def _format_time(dt):
    return dt.strftime('%Y-%m-%d %H:%M') if dt else ''


def _user_name(uid):
    """按账号整数 ID 取姓名；无账号返回待分配。"""
    if not uid:
        return '待分配'
    u = SysUser.objects.filter(user_id=uid).first()
    return u.name if u else '待分配'


def _doctor_dept(uid):
    """按医生整数 ID 取所属科室。"""
    d = Doctor.objects.filter(doctor_id=uid).first()
    return d.department if d else ''


def _doctor_options(current_dept=''):
    """组装医生列表：实时按进行中问诊推导在岗与排队情况。

    单接待口径：一名医生同一时刻只接诊一位（accepted=1 进行中），
    其余选定该医生的患者进入排队（accepted=0 进行中）；离线医生不可选，
    在线 / 接诊中的医生都允许挂号排队。
    """
    qs = Doctor.objects.all()
    if current_dept:
        qs = qs.filter(department=current_dept)
    active_map, wait_map = {}, {}
    for r in Consultation.objects.filter(status='进行中'):
        if r.accepted == 1:
            active_map[r.doctor_id] = active_map.get(r.doctor_id, 0) + 1
        else:
            wait_map[r.doctor_id] = wait_map.get(r.doctor_id, 0) + 1
    options = []
    for d in qs:
        active = active_map.get(d.doctor_id, 0)
        waiting = wait_map.get(d.doctor_id, 0)
        offline = d.work_status == 0
        full = waiting >= QUEUE_LIMIT
        available = not offline and not full
        if offline:
            label, badge = '离线', 'badge-gray'
        elif full:
            label, badge = '队列已满', 'badge-gray'
        elif active > 0:
            label, badge = '接诊中', 'badge-orange'
        elif waiting > 0:
            label, badge = '排队中', 'badge-blue'
        else:
            label, badge = '在线', 'badge-green'
        options.append({
            'id': d.doctor_id,
            'name': _user_name(d.doctor_id),
            'title': d.title or '医师',
            'department': d.department,
            'status': label,
            'badge': badge,
            'available': available,
            'queue': waiting,
            'full': full,
        })
    return options


def _queue_ahead(consult):
    """排队前方人数：医生正在接诊的 1 人 + 同队早于本条的排队人数；非排队态返回 0。"""
    if consult.status != '进行中' or consult.accepted == 1:
        return 0
    earlier = Consultation.objects.filter(
        doctor_id=consult.doctor_id, status='进行中', accepted=0,
    ).filter(
        Q(start_time__lt=consult.start_time)
        | Q(start_time=consult.start_time, consult_id__lt=consult.consult_id)
    ).count()
    active = Consultation.objects.filter(
        doctor_id=consult.doctor_id, status='进行中', accepted=1).count()
    return earlier + active


def _presc_brief(p):
    """把处方 ORM 对象适配成列表模板期望的字典。"""
    items = PrescriptionItem.objects.filter(prescription_id=p.presc_id)
    items_text = '；'.join(f"{i.drug_name} × {i.qty} 盒" for i in items)
    return {
        'id': f"P{p.presc_id:09d}",
        'presc_id': p.presc_id,
        'doctor': _user_name(p.doctor_id),
        'department': _doctor_dept(p.doctor_id),
        'status': p.status,
        'pickup': PICKUP_METHOD.get(p.pickup_method, '未选择'),
        'time': _format_time(p.created_at),
        'items': items_text,
        # 审核通过、患者尚未选取药方式：视为新到待查收处方
        'pending_receive': p.status == '已通过' and p.pickup_method is None,
    }


def _presc_detail(p):
    """把处方 ORM 对象适配成详情模板期望的字典（含药品明细）。"""
    items = PrescriptionItem.objects.filter(prescription_id=p.presc_id).select_related('drug')
    detail_items = [{
        'drug_id': i.drug_id,
        'drug': i.drug_name,
        'spec': i.drug.specification if i.drug_id else '',
        'qty': i.qty,
        'dosage': i.dosage,
        'price': i.unit_price,
        'subtotal': i.subtotal,
        'is_decoction': i.is_decoction,
        'decoction_num': i.decoction_num or '',
        'decoction_text': ('代煎 %s 剂' % i.decoction_num) if i.is_decoction else '自行煎煮',
    } for i in items]
    diagnosis = p.consultation.final_diagnosis if p.consultation_id else None
    return {
        'id': f"P{p.presc_id:09d}",
        'presc_id': p.presc_id,
        'doctor': _user_name(p.doctor_id),
        'department': _doctor_dept(p.doctor_id),
        'status': p.status,
        'diagnosis': diagnosis or '待医生填写诊断',
        'pickup': PICKUP_METHOD.get(p.pickup_method, '未选择'),
        'pickup_method': p.pickup_method,
        # 药师审核通过、尚未取药时，患者可选择取药方式与代煎；已完成则锁定
        'can_choose': p.status == '已通过' and p.pickup_status == 0,
        'audit': p.audit_conclusion or '等待药师审核',
        'time': _format_time(p.created_at),
        'items': detail_items,
    }


# ---------------------------------------------------------------------------
# 身份选择 / 职工内部密钥登录 / 退出 / 患者登录注册
# ---------------------------------------------------------------------------

def role_select_view(request):
    """平台入口：先选择身份（患者 / 医生 / 药师 / 管理员）。"""
    return render(request, 'accounts/role_select.html')


def _authenticate_staff(role, phone, secret):
    """职工统一鉴权：按角色取档案校验工作账号与内部密钥。

    返回 ((user, meta), None) 成功；否则 (None, 错误提示)。
    """
    meta = STAFF_ROLES.get(role)
    if meta is None:
        return None, '登录角色无效，请重新选择'
    user = SysUser.objects.filter(
        phone=phone, role_type=meta['role_type']).first()

    if not user or user.password != secret:
        return None, '工作账号或内部密钥错误，请重试'
    if user.status != 1:
        return None, '该账号已停用，请联系管理员开通'
    # 校验对应角色档案存在，防止跨角色登录
    profile_exists = meta['profile'].objects.filter(
        **{meta['profile_key']: user.user_id}).exists()
    if not profile_exists:
        return None, f'该账号不是{meta["role_cn"]}档案，请使用对应职工账号登录'
    return (user, meta), None


def _login_staff_session(request, user, meta):
    """职工鉴权通过后写入会话、记日志并跳转工作台。"""
    request.session['user_id'] = user.user_id
    request.session['name'] = user.name
    request.session['role_type'] = user.role_type
    request.session['staff_id'] = user.user_id
    request.session['role_cn'] = meta['role_cn']
    user.last_login = timezone.now()
    user.save(update_fields=['last_login'])
    write_log(user.user_id, meta['role_cn'], '登录',
              target_type='sys_user', target_id=user.user_id)
    return redirect(reverse(meta['redirect']))


def staff_entry_view(request):
    """职工统一入口：页内选择医生 / 药师 / 管理员角色后密钥登录。"""
    if request.method == 'POST':
        role = request.POST.get('role', '').strip()
        phone = request.POST.get('phone', '').strip()
        secret = request.POST.get('secret', '')
        (auth, error) = _authenticate_staff(role, phone, secret)
        if error:
            return render(request, 'accounts/staff_login.html', {
                'role': role, 'role_cn': STAFF_ROLES.get(role, {}).get('role_cn', '职工'),
                'error': error, 'form': request.POST,
            })
        user, meta = auth
        return _login_staff_session(request, user, meta)

    return render(request, 'accounts/staff_login.html',
                  {'role': None, 'role_cn': '职工'})


def staff_login_view(request, role):
    """职工登录（按角色固定入口，兼容旧链接）：工作手机号 + 内部密钥。"""
    meta = STAFF_ROLES.get(role)
    if meta is None:
        return redirect('accounts:role_select')

    if request.method == 'POST':
        phone = request.POST.get('phone', '').strip()
        secret = request.POST.get('secret', '')
        (auth, error) = _authenticate_staff(role, phone, secret)
        if error:
            return render(request, 'accounts/staff_login.html', {
                'role': role, 'role_cn': meta['role_cn'],
                'error': error, 'form': request.POST,
            })
        user, meta = auth
        return _login_staff_session(request, user, meta)

    return render(request, 'accounts/staff_login.html',
                  {'role': role, 'role_cn': meta['role_cn']})


def logout_view(request):
    """退出登录：清空会话并回到身份选择页。"""
    request.session.flush()
    return redirect('accounts:role_select')


def login_view(request):
    """患者登录：校验手机号 + 密码，写入会话并记登录日志。仅患者角色可登录。"""
    if request.method == 'POST':
        phone = request.POST.get('phone', '').strip()
        password = request.POST.get('password', '')
        user = SysUser.objects.filter(phone=phone, role_type=1).first()
        if user and user.password == password:
            try:
                Patient.objects.get(patient_id=user.user_id)
            except Patient.DoesNotExist:
                return render(request, 'accounts/login.html',
                              {'error': '该账号不是患者档案，请使用患者手机号登录'})
            request.session['user_id'] = user.user_id
            request.session['name'] = user.name
            request.session['role_type'] = user.role_type
            request.session['patient_id'] = user.user_id
            user.last_login = timezone.now()
            user.save(update_fields=['last_login'])
            write_log(user.user_id, '患者', '登录',
                      target_type='sys_user', target_id=user.user_id)
            return redirect('accounts:dashboard')
        return render(request, 'accounts/login.html',
                      {'error': '手机号或密码错误，请重试'})
    return render(request, 'accounts/login.html')


def register_view(request):
    """患者注册：独立注册页；事务内先建 sys_user(role=1)，再建 patient 档案。"""
    if request.method != 'POST':
        return render(request, 'accounts/register.html')

    name = request.POST.get('name', '').strip()
    phone = request.POST.get('phone', '').strip()
    password = request.POST.get('password', '')
    gender = request.POST.get('gender', '').strip() or None
    age = request.POST.get('age', '').strip() or None
    id_card = request.POST.get('id_card', '').strip()
    allergy = request.POST.get('allergy', '').strip() or None
    address = request.POST.get('address', '').strip() or None

    if not name or not phone or len(phone) != 8:
        return render(request, 'accounts/register.html',
                      {'register_error': '请填写姓名并输入 8 位手机号', 'form': request.POST})
    if SysUser.objects.filter(phone=phone).exists():
        return render(request, 'accounts/register.html',
                      {'register_error': '该手机号已注册', 'form': request.POST})
    if Patient.objects.filter(id_card_no=id_card).exists():
        return render(request, 'accounts/register.html',
                      {'register_error': '该身份证号已登记', 'form': request.POST})

    age = int(age) if age else None
    seq = Patient.objects.count() + 1
    medical_no = f'BL2026{seq:04d}'

    try:
        with transaction.atomic():
            user = SysUser.objects.create(
                name=name, phone=phone, password=password, role_type=1)
            Patient.objects.create(
                patient_id=user.user_id, id_card_no=id_card, gender=gender, age=age,
                allergy=allergy, address=address, medical_no=medical_no,
                created_at=timezone.now())
            write_log(user.user_id, '患者', '注册',
                      target_type='patient', target_id=user.user_id)
    except Exception:
        return render(request, 'accounts/register.html',
                      {'register_error': '注册失败，请检查填写内容后重试',
                       'form': request.POST})

    return redirect('accounts:login')


# ---------------------------------------------------------------------------
# 工作台
# ---------------------------------------------------------------------------

def dashboard_view(request):
    patient = _current_patient(request)
    if not patient:
        return redirect('accounts:login')

    consults = Consultation.objects.filter(patient_id=patient.patient_id) \
        .select_related('doctor').order_by('-start_time')
    consultations = [{
        'id': c.consult_id,
        'doctor': _user_name(c.doctor_id),
        'department': _doctor_dept(c.doctor_id),
        'status': c.status,
        'accepted': c.accepted,
        'ahead': _queue_ahead(c),
        'time': _format_time(c.start_time),
        'assessed': AiPreAssessment.objects.filter(consultation_id=c.consult_id).exists(),
    } for c in consults]

    active = next((c for c in consultations if c['status'] == '进行中'), None)
    history = [c for c in consultations if c['status'] != '进行中']
    active_count = sum(1 for c in consultations if c['status'] == '进行中')

    prescs = Prescription.objects.filter(
        patient_id=patient.patient_id, status__in=PATIENT_VISIBLE_STATUS) \
        .select_related('doctor', 'consultation').order_by('-created_at')
    prescriptions = [_presc_brief(p) for p in prescs]
    pending_receives = [p for p in prescriptions if p['pending_receive']]

    unread = ConsultationMessage.objects.filter(
        consultation__patient_id=patient.patient_id,
        sender_role='医生', read_status=0).count()

    user = SysUser.objects.filter(user_id=patient.patient_id).first()
    context = {
        'patient': {
            'name': _user_name(patient.patient_id),
            'phone': user.phone if user else '',
            'medical_no': patient.medical_no,
            'gender': patient.gender or '未填写',
            'age': patient.age if patient.age is not None else '未填写',
            'allergy': patient.allergy or '无记录',
            'address': patient.address or '未填写',
            'history': patient.medical_history or '无记录',
        },
        'consultations': consultations,
        'history': history,
        'active': active,
        'active_count': active_count,
        'prescriptions': prescriptions,
        'pending_receives': pending_receives,
        'unread': unread,
    }
    return render(request, 'accounts/dashboard.html', context)


# ---------------------------------------------------------------------------
# 个人中心 / 健康档案
# ---------------------------------------------------------------------------

def profile_view(request):
    """个人中心：查看并编辑本人基础信息与健康档案（病史、过敏史、住址等）。"""
    patient = _current_patient(request)
    if not patient:
        return redirect('accounts:login')
    user = SysUser.objects.filter(user_id=patient.patient_id).first()

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        phone = request.POST.get('phone', '').strip()
        gender = request.POST.get('gender', '').strip() or None
        age_raw = request.POST.get('age', '').strip()
        allergy = request.POST.get('allergy', '').strip() or None
        address = request.POST.get('address', '').strip() or None
        history = request.POST.get('medical_history', '').strip() or None

        errors = []
        if not name:
            errors.append('姓名不能为空')
        if not phone or len(phone) != 8:
            errors.append('请输入 8 位手机号')
        elif SysUser.objects.filter(phone=phone).exclude(user_id=patient.patient_id).exists():
            errors.append('该手机号已被其他账号使用')

        age = None
        if age_raw:
            try:
                age = int(age_raw)
                if not (0 <= age <= 200):
                    errors.append('年龄需在 0–200 之间')
            except ValueError:
                errors.append('年龄须为整数')
        if gender and gender not in ('男', '女', '其他'):
            gender = None

        if errors:
            return render(request, 'accounts/profile.html', {
                'form': request.POST, 'medical_no': patient.medical_no,
                'errors': errors, 'saved': False,
            })

        with transaction.atomic():
            if user:
                user.name = name
                user.phone = phone
                user.save(update_fields=['name', 'phone'])
            Patient.objects.filter(patient_id=patient.patient_id).update(
                gender=gender, age=age, allergy=allergy,
                address=address, medical_history=history)
        write_log(patient.patient_id, '患者', '修改个人资料',
                  target_type='patient', target_id=patient.patient_id)
        return redirect(reverse('accounts:profile') + '?saved=1')

    form = {
        'name': user.name if user else '',
        'phone': user.phone if user else '',
        'gender': patient.gender or '',
        'age': patient.age if patient.age is not None else '',
        'allergy': patient.allergy or '',
        'address': patient.address or '',
        'medical_history': patient.medical_history or '',
    }
    return render(request, 'accounts/profile.html', {
        'form': form, 'medical_no': patient.medical_no,
        'saved': request.GET.get('saved') == '1',
    })


# ---------------------------------------------------------------------------
# 发起问诊
# ---------------------------------------------------------------------------

def consult_create_view(request):
    patient = _current_patient(request)
    if not patient:
        return redirect('accounts:login')

    # 同时只允许一条进行中的挂号
    active = Consultation.objects.filter(
        patient_id=patient.patient_id, status='进行中').first()

    current_dept = request.GET.get('dept', '').strip()
    departments = list(
        Doctor.objects.values_list('department', flat=True).distinct().order_by('department'))
    doctors = _doctor_options(current_dept)

    if request.method == 'POST':
        if active:
            return render(request, 'accounts/consult_create.html', {
                'active_consult': active,
                'active_doctor': _user_name(active.doctor_id),
                'active_dept': _doctor_dept(active.doctor_id),
                'active_time': _format_time(active.start_time),
                'doctors': doctors, 'departments': departments, 'current_dept': current_dept,
                'error': '您已有一条进行中的挂号，请结束当前问诊后再发起新挂号。',
            })

        doctor_id = request.POST.get('doctor', '').strip()
        doc = Doctor.objects.filter(doctor_id=doctor_id).first() if doctor_id else None

        # 仅离线医生不可选；在线 / 接诊中的医生都可挂号，其余患者进入排队
        if not doc or doc.work_status == 0:
            error = '该医生当前离线，请选择其他在线医生。'
            return render(request, 'accounts/consult_create.html', {
                'active_consult': active,
                'active_doctor': _user_name(active.doctor_id) if active else '',
                'active_dept': _doctor_dept(active.doctor_id) if active else '',
                'active_time': _format_time(active.start_time) if active else '',
                'doctors': doctors, 'departments': departments, 'current_dept': current_dept,
                'error': error,
            })

        # 待接诊队列达上限（排队人数 ≥ QUEUE_LIMIT）：不再放新患者进入队列
        waiting = Consultation.objects.filter(
            doctor_id=doc.doctor_id, status='进行中', accepted=0).count()
        if waiting >= QUEUE_LIMIT:
            error = '当前等待队列已满（%d 人在排队），请稍后再试或选择其他医生。' % QUEUE_LIMIT
            return render(request, 'accounts/consult_create.html', {
                'active_consult': active,
                'active_doctor': _user_name(active.doctor_id) if active else '',
                'active_dept': _doctor_dept(active.doctor_id) if active else '',
                'active_time': _format_time(active.start_time) if active else '',
                'doctors': doctors, 'departments': departments, 'current_dept': current_dept,
                'error': error,
            })

        # 进入排队：先建立问诊记录（accepted=0），病情采集与风险分级在 AI 评估页完成；
        # 医生手动接诊后才置 accepted=1 进入对话，不在挂号时占用医生忙碌态
        consult = Consultation.objects.create(
            patient_id=patient.patient_id, doctor_id=doc.doctor_id,
            record_content='待 AI 预问诊采集', status='进行中', accepted=0,
            start_time=timezone.now())

        write_log(patient.patient_id, '患者', '发起挂号',
                  target_type='consultation', target_id=consult.consult_id)
        return redirect('accounts:consult_assess', consult.consult_id)

    return render(request, 'accounts/consult_create.html', {
        'active_consult': active,
        'active_doctor': _user_name(active.doctor_id) if active else '',
        'active_dept': _doctor_dept(active.doctor_id) if active else '',
        'active_time': _format_time(active.start_time) if active else '',
        'doctors': doctors,
        'departments': departments,
        'current_dept': current_dept,
    })


def consult_assess_view(request, consult_id):
    """AI 引导式预问诊：采集病情 → 风险分级 → 通过后再进入医生对话。"""
    patient = _current_patient(request)
    if not patient:
        return redirect('accounts:login')

    consult = Consultation.objects.filter(
        consult_id=consult_id, patient_id=patient.patient_id).first()
    if not consult:
        return redirect('accounts:messages')

    # 已完成评估：已接诊进对话，仍在排队则回工作台等待叫号
    if AiPreAssessment.objects.filter(consultation_id=consult.consult_id).exists():
        if consult.accepted == 1:
            return redirect('accounts:consult_chat', consult.consult_id)
        return redirect('accounts:dashboard')

    MAX_ROUNDS = len(GUIDE_TOPICS)

    def _guide_msgs():
        return ConsultationMessage.objects.filter(
            consultation_id=consult.consult_id,
            sender_role__in=['AI', '患者']
        ).order_by('send_time')

    def _finish_assessment():
        """汇总多轮引导问答，生成风险分级与病情摘要，落评估后进入对话。"""
        qa = [{'role': m.sender_role, 'text': m.content or ''}
              for m in _guide_msgs()]
        case_text = guide_history_text(qa)
        consult.record_content = case_text
        consult.save(update_fields=['record_content'])

        ai = assess_risk(case_text)
        AiPreAssessment.objects.create(
            consultation_id=consult.consult_id, risk_level=ai['risk_level'],
            ai_summary=ai['ai_summary'], matched_keyword=ai['matched_keyword'],
            created_at=timezone.now())
        write_log(patient.patient_id, '患者', 'AI预问诊评估',
                  target_type='consultation', target_id=consult.consult_id)
        # 排队期间完成预问诊：尚未接诊则回工作台等待叫号；已接诊才进入医患对话
        if consult.accepted == 1:
            return redirect('accounts:consult_chat', consult.consult_id)
        return redirect('accounts:dashboard')

    if request.method == 'POST':
        # 同一问诊只保留一条 AI 评估，重复提交不再重复插入
        if AiPreAssessment.objects.filter(consultation_id=consult.consult_id).exists():
            return redirect('accounts:consult_chat', consult.consult_id)

        action = request.POST.get('action', 'answer')

        # 患者主动点“补充完毕”，直接收尾
        if action == 'finish':
            return _finish_assessment()

        answer = request.POST.get('answer', '').strip()
        if answer:
            ConsultationMessage.objects.create(
                consultation_id=consult.consult_id, sender_role='患者',
                sender_id=patient.patient_id, msg_type='pre', content=answer,
                send_time=timezone.now(), read_status=1)

        msgs = list(_guide_msgs())
        qa = [{'role': m.sender_role, 'text': m.content or ''} for m in msgs]
        patient_turns = sum(1 for x in qa if x['role'] == '患者')

        # 达到轮次上限强制收尾，避免无限追问
        if patient_turns >= MAX_ROUNDS:
            return _finish_assessment()

        nxt = next_guide_question(qa)
        # 远程返回了空问题时回退本地清单，避免落库空气泡或卡住无下文
        if not nxt.get('done') and not nxt.get('question'):
            nxt = _local_guide_next(qa)
        if nxt['done'] or not nxt.get('question'):
            return _finish_assessment()

        ConsultationMessage.objects.create(
            consultation_id=consult.consult_id, sender_role='AI', sender_id=1,
            msg_type='pre', content=nxt['question'],
            send_time=timezone.now(), read_status=1)
        return redirect('accounts:consult_assess', consult.consult_id)

    msgs = list(_guide_msgs())
    # 首轮进入：落地第一条引导问题（本地清单首题，避免页面加载即调远程模型）
    if not msgs:
        ConsultationMessage.objects.create(
            consultation_id=consult.consult_id, sender_role='AI', sender_id=1,
            msg_type='pre', content=GUIDE_TOPICS[0][2],
            send_time=timezone.now(), read_status=1)
        msgs = list(_guide_msgs())

    return render(request, 'accounts/consult_assess.html', {
        'consult': {
            'id': consult.consult_id,
            'doctor': _user_name(consult.doctor_id),
            'department': _doctor_dept(consult.doctor_id),
            'time': _format_time(consult.start_time),
        },
        'messages': [{
            'role': m.sender_role,
            'content': m.content or '',
            'time': _format_time(m.send_time),
            'mine': m.sender_role == '患者',
        } for m in msgs],
    })


# ---------------------------------------------------------------------------
# 我的处方：列表 / 详情
# ---------------------------------------------------------------------------

def prescription_list_view(request):
    patient = _current_patient(request)
    if not patient:
        return redirect('accounts:login')

    prescs = Prescription.objects.filter(
        patient_id=patient.patient_id, status__in=PATIENT_VISIBLE_STATUS) \
        .select_related('doctor', 'consultation').order_by('-created_at')
    prescriptions = [_presc_brief(p) for p in prescs]
    return render(request, 'accounts/prescription_list.html',
                  {'prescriptions': prescriptions})


def prescription_detail_view(request, presc_id):
    patient = _current_patient(request)
    if not patient:
        return redirect('accounts:login')

    # 仅允许查看本人、且已审核通过后的处方；草稿 / 待审核 / 已驳回不对患者展示
    p = Prescription.objects.filter(
        presc_id=presc_id, patient_id=patient.patient_id,
        status__in=PATIENT_VISIBLE_STATUS) \
        .select_related('doctor', 'consultation', 'pharmacist').first()

    if not p:
        return render(request, 'accounts/prescription_detail.html',
                      {'prescription': None, 'total': 0})

    prescription = _presc_detail(p)
    total = sum(float(i['subtotal']) for i in prescription['items'])
    return render(request, 'accounts/prescription_detail.html',
                  {'prescription': prescription, 'total': round(total, 2)})


def prescription_pickup_view(request, presc_id):
    """患者在处方审核通过后选择取药方式，并登记各明细是否代煎。

    - 取药方式（院药房取药 / 社会药店购药）写入 prescription.pickup_method；
    - 代煎（是否代煎、剂数）按药品明细写回 prescription_item；
    - 仅「已通过」且未取药的处方可提交，已完成 / 其他状态锁定。
    """
    patient = _current_patient(request)
    if not patient:
        return redirect('accounts:login')

    p = Prescription.objects.filter(presc_id=presc_id, patient_id=patient.patient_id).first()
    if not p:
        messages.error(request, '未找到该处方信息。')
        return redirect('accounts:prescription_list')

    if request.method != 'POST':
        return redirect('accounts:prescription_detail', presc_id=presc_id)

    if not (p.status == '已通过' and p.pickup_status == 0):
        messages.error(request, '当前处方状态为「%s」，不能选择取药方式。' % p.status)
        return redirect('accounts:prescription_detail', presc_id=presc_id)

    pickup_method = request.POST.get('pickup_method')
    if pickup_method not in ('0', '1'):
        messages.error(request, '请选择取药方式（院药房取药 / 社会药店购药）。')
        return redirect('accounts:prescription_detail', presc_id=presc_id)

    p.pickup_method = int(pickup_method)
    # 院内取药进入待配药，由药师核对并发药；社会药店外购不进入院内配药，保持已通过。
    p.status = '待配药' if pickup_method == '0' else '已通过'
    p.updated_at = timezone.now()
    p.save(update_fields=['pickup_method', 'status', 'updated_at'])

    # 逐行回写代煎：按药品明细匹配，数组按提交顺序对齐。
    # 注意：明细表在 ORM 里主键映射到处方外键，对象 save 会按处方 id 命中多行，
    # 因此这里用 filter().update() 按 (presc_id, drug_id) 精确定位单行更新。
    drug_ids = request.POST.getlist('item_drug_id')
    decoctions = request.POST.getlist('item_decoction')
    decoction_nums = request.POST.getlist('item_decoction_num')
    for idx, raw in enumerate(drug_ids):
        if not raw.isdigit():
            continue
        drug_id = int(raw)
        is_dec = 1 if idx < len(decoctions) and decoctions[idx] == '1' else 0
        num_raw = (decoction_nums[idx] if idx < len(decoction_nums) else '').strip()
        decoction_num = int(num_raw) if is_dec == 1 and num_raw.isdigit() else None
        PrescriptionItem.objects.filter(
            prescription_id=p.presc_id, drug_id=drug_id
        ).update(is_decoction=is_dec, decoction_num=decoction_num)

    method_label = PICKUP_METHOD.get(p.pickup_method, '未选择')
    presc_no = f"P{p.presc_id:09d}"
    write_log(patient.patient_id, '患者',
              f'选择取药方式（{method_label}）与代煎',
              target_type='prescription', target_id=p.presc_id)
    if pickup_method == '0':
        messages.success(
            request,
            f'处方 {presc_no} 取药方式已更新为「{method_label}」，已通知药师核对库存并安排配药发药。')
    else:
        messages.success(
            request,
            f'处方 {presc_no} 取药方式已更新为「{method_label}」，无需院内配药、药师无需处理，请凭电子处方到社会药店取药。')
    return redirect('accounts:prescription_detail', presc_id=presc_id)


# ---------------------------------------------------------------------------
# 排队状态轮询接口
# ---------------------------------------------------------------------------

def consult_status_view(request, consult_id):
    """供患者等待页定时刷新：返回前方人数、是否已接诊叫号、可否进入对话。"""
    patient = _current_patient(request)
    if not patient:
        return JsonResponse({'error': 'unauthorized'}, status=401)
    consult = Consultation.objects.filter(
        consult_id=consult_id, patient_id=patient.patient_id).first()
    if not consult:
        return JsonResponse({'error': 'not_found'}, status=404)
    assessed = AiPreAssessment.objects.filter(
        consultation_id=consult.consult_id).exists()
    can_enter = consult.status == '进行中' and consult.accepted == 1 and assessed
    return JsonResponse({
        'status': consult.status,
        'accepted': consult.accepted,
        'queuing': consult.status == '进行中' and consult.accepted == 0,
        'ahead': _queue_ahead(consult),
        'assessed': assessed,
        'can_enter': can_enter,
    })


# ---------------------------------------------------------------------------
# 消息记录
# ---------------------------------------------------------------------------

def messages_view(request):
    """按问诊会话展示当前患者的全部聊天记录。"""
    patient = _current_patient(request)
    if not patient:
        return redirect('accounts:login')

    consults = Consultation.objects.filter(patient_id=patient.patient_id) \
        .select_related('doctor').order_by('-start_time')
    threads = []
    for c in consults:
        last = ConsultationMessage.objects.filter(
            consultation_id=c.consult_id).exclude(sender_role='AI') \
            .exclude(msg_type='pre').order_by('-send_time').first()
        if not last:
            preview = '暂无消息'
        elif last.msg_type == 'image':
            preview = '[图片]'
        elif last.msg_type == 'video':
            preview = '[视频]'
        else:
            preview = (last.content or '').strip()[:30] or '暂无消息'
        unread = ConsultationMessage.objects.filter(
            consultation_id=c.consult_id, sender_role='医生', read_status=0).count()
        threads.append({
            'id': c.consult_id,
            'doctor': _user_name(c.doctor_id),
            'department': _doctor_dept(c.doctor_id),
            'status': c.status,
            'accepted': c.accepted,
            'ahead': _queue_ahead(c),
            'time': _format_time(c.start_time),
            'preview': preview,
            'unread': unread,
        })
    return render(request, 'accounts/messages.html', {'threads': threads})


# ---------------------------------------------------------------------------
# 问诊聊天（图文对话 + AI 预评估 + 视频入口）
# ---------------------------------------------------------------------------

def consult_chat_view(request, consult_id):
    """患者与医生的图文会话；仅本人可查看/发言。"""
    patient = _current_patient(request)
    if not patient:
        return redirect('accounts:login')

    consult = Consultation.objects.filter(
        consult_id=consult_id, patient_id=patient.patient_id).first()
    if not consult:
        return redirect('accounts:messages')

    # 未完成 AI 预问诊评估的问诊，先回到评估页，不允许直接进入对话
    ai = AiPreAssessment.objects.filter(
        consultation_id=consult.consult_id).first()
    if not ai:
        return redirect('accounts:consult_assess', consult.consult_id)

    # 排队中：尚未被医生接诊，只展示排队等待与前方人数，不开放对话 / 发言
    if consult.status == '进行中' and consult.accepted == 0:
        if request.method == 'POST':
            return redirect('accounts:consult_chat', consult.consult_id)
        return render(request, 'accounts/consult_chat.html', {
            'consult': {
                'id': consult.consult_id,
                'doctor': _user_name(consult.doctor_id),
                'department': _doctor_dept(consult.doctor_id),
                'status': '排队中',
                'time': _format_time(consult.start_time),
            },
            'queuing': True,
            'ahead': _queue_ahead(consult),
            'assessed': True,
            'messages': [],
            'ai': {'risk_level': ai.risk_level, 'summary': ai.ai_summary,
                   'keyword': ai.matched_keyword, 'consent': ai.risk_consent,
                   'time': _format_time(ai.created_at)},
        })

    # 高危且尚未签署知情同意：先弹出风险告知，确认后才允许发言
    need_consent = (ai.risk_level == '高危' and ai.risk_consent == 0
                    and consult.status == '进行中')
    can_chat = consult.status == '进行中' and not need_consent

    if request.method == 'POST':
        action = request.POST.get('action', 'message')

        if action == 'consent':
            # 患者已知晓高危风险并选择继续线上咨询，风险自行承担
            ai.risk_consent = 1
            ai.save(update_fields=['risk_consent'])
            write_log(patient.patient_id, '患者', '高危知情同意',
                      target_type='consultation', target_id=consult.consult_id)
            return redirect('accounts:consult_chat', consult.consult_id)

        if action == 'finish':
            # 患者不能单方面结束问诊：仅可发起结束申请，待接诊医生确认后才结束；
            # 医生可主动结束，无需患者确认。
            if consult.accepted != 1:
                messages.error(request, '问诊尚未接诊，无法发起结束申请。')
            elif consult.end_request == 1:
                messages.info(request, '结束申请已提交，正在等待医生确认。')
            else:
                consult.end_request = 1
                consult.save(update_fields=['end_request'])
                write_log(patient.patient_id, '患者', '申请结束问诊',
                          target_type='consultation', target_id=consult.consult_id)
                messages.success(request, '已向医生发起结束申请，待医生确认后结束本次问诊。')
            return redirect('accounts:consult_chat', consult.consult_id)

        # 未解锁发言（未完成知情同意 / 已结束）时不允许发送消息
        if not can_chat:
            return redirect('accounts:consult_chat', consult.consult_id)

        content = request.POST.get('content', '').strip()
        image = request.FILES.get('image')
        msg_type = 'text'
        media_url = None

        if image:
            ext = os.path.splitext(image.name)[1].lower()
            if ext not in ('.jpg', '.jpeg', '.png', '.gif', '.webp'):
                ext = '.jpg'
            fname = f"chat_{timezone.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}{ext}"
            save_dir = os.path.join(settings.MEDIA_ROOT, 'chat')
            os.makedirs(save_dir, exist_ok=True)
            with open(os.path.join(save_dir, fname), 'wb') as f:
                for chunk in image.chunks():
                    f.write(chunk)
            media_url = '/' + settings.MEDIA_URL.lstrip('/') + 'chat/' + fname
            msg_type = 'image'
        elif not content:
            return redirect('accounts:consult_chat', consult.consult_id)

        ConsultationMessage.objects.create(
            consultation_id=consult.consult_id, sender_role='患者',
            sender_id=patient.patient_id, msg_type=msg_type,
            content=content or None, media_url=media_url,
            send_time=timezone.now(), read_status=1)
        write_log(patient.patient_id, '患者', '发送消息',
                  target_type='consultation', target_id=consult.consult_id)
        return redirect('accounts:consult_chat', consult.consult_id)

    msg_list = ConsultationMessage.objects.filter(
        consultation_id=consult.consult_id
    ).exclude(sender_role='AI').exclude(msg_type='pre').order_by('send_time')

    # 进入会话即把医生发来的未读消息标记为已读，工作台未读数与列表未读角标随之清零
    ConsultationMessage.objects.filter(
        consultation_id=consult.consult_id, sender_role='医生', read_status=0
    ).update(read_status=1)

    context = {
        'consult': {
            'id': consult.consult_id,
            'doctor': _user_name(consult.doctor_id),
            'department': _doctor_dept(consult.doctor_id),
            'status': consult.status,
            'time': _format_time(consult.start_time),
        },
        'can_chat': can_chat,
        'need_consent': need_consent,
        'end_request': consult.end_request,
        'messages': [{
            'role': m.sender_role,
            'content': m.content or '',
            'msg_type': m.msg_type,
            'media_url': m.media_url or '',
            'time': _format_time(m.send_time),
            'mine': m.sender_role == '患者',
        } for m in msg_list],
        'ai': {
            'risk_level': ai.risk_level,
            'summary': ai.ai_summary,
            'keyword': ai.matched_keyword,
            'consent': ai.risk_consent,
            'time': _format_time(ai.created_at),
        },
    }
    return render(request, 'accounts/consult_chat.html', context)

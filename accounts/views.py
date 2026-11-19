"""患者端视图：登录/注册、工作台、发起问诊、我的处方（列表与详情）。

数据全部来自 MySQL（core.models 映射）；关键动作写 operation_log。
课设演示阶段密码与种子数据一致，按明文比对；生产环境应使用密码哈希。
"""

import os
import uuid

from django.conf import settings
from django.db import transaction
from django.db.models import Count
from django.shortcuts import render, redirect
from django.utils import timezone

from core.models import (
    SysUser, Patient, Doctor, Consultation, ConsultationMessage,
    Prescription, PrescriptionItem, AiPreAssessment,
)
from core.ai_service import assess_risk
from core.log_service import write_log


PICKUP_METHOD = {0: '院药房取药', 1: '社会药店购药'}


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
    """组装医生列表：实时按进行中问诊推导忙碌态，附排队人数与可接诊标记。

    可接诊条件：非离线、未被置忙碌、当前没有进行中的问诊（一医一患）。
    """
    qs = Doctor.objects.all()
    if current_dept:
        qs = qs.filter(department=current_dept)
    active_map = {
        r['doctor_id']: r['n']
        for r in Consultation.objects.filter(status='进行中')
        .values('doctor_id').annotate(n=Count('doctor_id'))
    }
    options = []
    for d in qs:
        active = active_map.get(d.doctor_id, 0)
        offline = d.work_status == 0
        busy = active > 0 or d.work_status == 2
        available = not offline and not busy
        if offline:
            label, badge = '离线', 'badge-gray'
        elif active > 0:
            label, badge = '忙碌', 'badge-orange'
        elif d.work_status == 2:
            label, badge = '忙碌', 'badge-orange'
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
            'queue': active,
        })
    return options


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
    }


def _presc_detail(p):
    """把处方 ORM 对象适配成详情模板期望的字典（含药品明细）。"""
    items = PrescriptionItem.objects.filter(prescription_id=p.presc_id).select_related('drug')
    detail_items = [{
        'drug': i.drug_name,
        'spec': i.drug.specification if i.drug_id else '',
        'qty': i.qty,
        'dosage': i.dosage,
        'price': i.unit_price,
        'subtotal': i.subtotal,
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
        'audit': p.audit_conclusion or '等待药师审核',
        'time': _format_time(p.created_at),
        'items': detail_items,
    }


# ---------------------------------------------------------------------------
# 登录 / 注册
# ---------------------------------------------------------------------------

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
        'time': _format_time(c.start_time),
        'assessed': AiPreAssessment.objects.filter(consultation_id=c.consult_id).exists(),
    } for c in consults]

    active = next((c for c in consultations if c['status'] == '进行中'), None)
    history = [c for c in consultations if c['status'] != '进行中']
    active_count = sum(1 for c in consultations if c['status'] == '进行中')

    prescs = Prescription.objects.filter(patient_id=patient.patient_id) \
        .select_related('doctor', 'consultation').order_by('-created_at')
    prescriptions = [_presc_brief(p) for p in prescs]

    unread = ConsultationMessage.objects.filter(
        consultation__patient_id=patient.patient_id, read_status=0).count()

    context = {
        'patient': {
            'name': _user_name(patient.patient_id),
            'medical_no': patient.medical_no,
            'gender': patient.gender or '未填写',
            'age': patient.age if patient.age is not None else '未填写',
            'allergy': patient.allergy or '无记录',
        },
        'consultations': consultations,
        'history': history,
        'active': active,
        'active_count': active_count,
        'prescriptions': prescriptions,
        'unread': unread,
    }
    return render(request, 'accounts/dashboard.html', context)


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
        queue = Consultation.objects.filter(
            doctor_id=doctor_id, status='进行中').count() if doc else 0

        unavailable = (
            not doc or doc.work_status == 0 or doc.work_status == 2 or queue > 0)
        if unavailable:
            if doc and doc.work_status == 0:
                error = '该医生当前离线，请选择其他在线医生。'
            else:
                error = f'该医生正在接诊，前方还有 {queue} 位患者等待，请选择其他在线医生。'
            return render(request, 'accounts/consult_create.html', {
                'active_consult': active,
                'active_doctor': _user_name(active.doctor_id) if active else '',
                'active_dept': _doctor_dept(active.doctor_id) if active else '',
                'active_time': _format_time(active.start_time) if active else '',
                'doctors': doctors, 'departments': departments, 'current_dept': current_dept,
                'error': error,
            })

        # 占号：先建立问诊记录，医生置忙碌；病情采集与风险分级在 AI 评估页完成
        consult = Consultation.objects.create(
            patient_id=patient.patient_id, doctor_id=doc.doctor_id,
            record_content='待 AI 预问诊采集', status='进行中',
            start_time=timezone.now())
        Doctor.objects.filter(doctor_id=doc.doctor_id).update(work_status=2)

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

    # 已完成评估的问诊直接进入对话
    if AiPreAssessment.objects.filter(consultation_id=consult.consult_id).exists():
        return redirect('accounts:consult_chat', consult.consult_id)

    if request.method == 'POST':
        chief = request.POST.get('chief_complaint', '').strip()
        onset = request.POST.get('onset_time', '').strip()
        symptoms = request.POST.get('symptoms', '').strip()
        history = request.POST.get('history', '').strip()

        record_parts = [f'主诉：{chief}']
        if onset:
            record_parts.append(f'起病时间：{onset}')
        if symptoms:
            record_parts.append(f'伴随症状：{symptoms}')
        if history:
            record_parts.append(f'病史/用药过敏：{history}')
        record = '\n'.join(record_parts)

        consult.record_content = record
        consult.save(update_fields=['record_content'])

        ConsultationMessage.objects.create(
            consultation_id=consult.consult_id, sender_role='患者',
            sender_id=patient.patient_id, msg_type='text', content=record,
            send_time=timezone.now(), read_status=1)

        ai = assess_risk(record)
        AiPreAssessment.objects.create(
            consultation_id=consult.consult_id, risk_level=ai['risk_level'],
            ai_summary=ai['ai_summary'], matched_keyword=ai['matched_keyword'],
            created_at=timezone.now())

        write_log(patient.patient_id, '患者', 'AI预问诊评估',
                  target_type='consultation', target_id=consult.consult_id)
        return redirect('accounts:consult_chat', consult.consult_id)

    return render(request, 'accounts/consult_assess.html', {
        'consult': {
            'id': consult.consult_id,
            'doctor': _user_name(consult.doctor_id),
            'department': _doctor_dept(consult.doctor_id),
            'time': _format_time(consult.start_time),
        },
    })


# ---------------------------------------------------------------------------
# 我的处方：列表 / 详情
# ---------------------------------------------------------------------------

def prescription_list_view(request):
    patient = _current_patient(request)
    if not patient:
        return redirect('accounts:login')

    prescs = Prescription.objects.filter(patient_id=patient.patient_id) \
        .select_related('doctor', 'consultation').order_by('-created_at')
    prescriptions = [_presc_brief(p) for p in prescs]
    return render(request, 'accounts/prescription_list.html',
                  {'prescriptions': prescriptions})


def prescription_detail_view(request, presc_id):
    patient = _current_patient(request)
    if not patient:
        return redirect('accounts:login')

    # 仅允许查看本人处方
    p = Prescription.objects.filter(presc_id=presc_id, patient_id=patient.patient_id) \
        .select_related('doctor', 'consultation', 'pharmacist').first()

    if not p:
        return render(request, 'accounts/prescription_detail.html',
                      {'prescription': None, 'total': 0})

    prescription = _presc_detail(p)
    total = sum(float(i['subtotal']) for i in prescription['items'])
    return render(request, 'accounts/prescription_detail.html',
                  {'prescription': prescription, 'total': round(total, 2)})


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
        last = ConsultationMessage.objects.filter(consultation_id=c.consult_id) \
            .order_by('-send_time').first()
        if not last:
            preview = '暂无消息'
        elif last.msg_type == 'image':
            preview = '[图片]'
        elif last.msg_type == 'video':
            preview = '[视频]'
        else:
            preview = (last.content or '').strip()[:30] or '暂无消息'
        threads.append({
            'id': c.consult_id,
            'doctor': _user_name(c.doctor_id),
            'department': _doctor_dept(c.doctor_id),
            'status': c.status,
            'time': _format_time(c.start_time),
            'preview': preview,
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
            consult.status = '已结束'
            consult.end_time = timezone.now()
            consult.save()
            # 释放医生接诊状态：该医生无其他进行中问诊时恢复在线
            if consult.doctor_id:
                other_active = Consultation.objects.filter(
                    doctor_id=consult.doctor_id, status='进行中'
                ).exclude(consult_id=consult.consult_id).count()
                if other_active == 0:
                    Doctor.objects.filter(doctor_id=consult.doctor_id).update(work_status=1)
            write_log(patient.patient_id, '患者', '结束问诊',
                      target_type='consultation', target_id=consult.consult_id)
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

    messages = ConsultationMessage.objects.filter(
        consultation_id=consult.consult_id).order_by('send_time')

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
        'messages': [{
            'role': m.sender_role,
            'content': m.content or '',
            'msg_type': m.msg_type,
            'media_url': m.media_url or '',
            'time': _format_time(m.send_time),
            'mine': m.sender_role == '患者',
        } for m in messages],
        'ai': {
            'risk_level': ai.risk_level,
            'summary': ai.ai_summary,
            'keyword': ai.matched_keyword,
            'consent': ai.risk_consent,
            'time': _format_time(ai.created_at),
        },
    }
    return render(request, 'accounts/consult_chat.html', context)

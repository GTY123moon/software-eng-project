"""医生端视图：接诊、AI 摘要复核、图文回复、开方草稿、提交审核。

数据口径与组内约定一致：
- 业务表由 docs/sql/create_tables.sql 建好，core.models 全部 managed=False，
  这里**不生成迁移、不建表**，只做查询与写入；
- 登录口径与 main 统一：会话键用 accounts.staff_login 写入的 staff_id / role_type，
  医生端不再自带登录页，未登录一律跳 accounts:staff_login(role='doctor')；
- 关键动作统一通过 core.log_service.write_log 写入 operation_log。
"""

import os
import uuid
from functools import wraps

from django.conf import settings
from django.contrib import messages as flash
from django.db import transaction
from django.shortcuts import render, redirect
from django.utils import timezone

from core.log_service import write_log
from core.models import (
    AiPreAssessment, Consultation, ConsultationMessage, Doctor, Drug,
    Patient, Prescription, PrescriptionItem, SysUser,
)

from . import ai_client
from .services import build_items, check_prescription, parse_rows

# 问诊状态与处方状态的展示样式（不改变库中取值）
DOCTOR_STATUS_TEXT = {0: '离线', 1: '在线', 2: '忙碌'}
DOCTOR_STATUS_BADGE = {0: 'badge-gray', 1: 'badge-green', 2: 'badge-orange'}
CONSULT_BADGE = {'进行中': 'badge-blue', '已结束': 'badge-gray',
                 '已转诊': 'badge-orange', '已取消': 'badge-gray'}
REVIEW_BADGE = {'待复核': 'badge-orange', '已采纳': 'badge-green',
                '已修正': 'badge-blue', '已否决': 'badge-red'}
PRESC_BADGE = {'草稿': 'badge-gray', '待审核': 'badge-orange', '已通过': 'badge-green',
               '已驳回': 'badge-red', '待配药': 'badge-blue', '已完成': 'badge-green'}

REVIEW_MAP = {'采纳': '已采纳', '修正': '已修正', '否决': '已否决'}


# --------------------------------------------------------------------------
# 通用工具
# --------------------------------------------------------------------------

def doctor_required(view_func):
    """医生身份守卫：未登录或非医生角色跳统一职工登录页（身份选择后进入）。"""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        staff_id = request.session.get('staff_id')
        if not isinstance(staff_id, int) or request.session.get('role_type') != 2:
            return redirect('accounts:staff_login', role='doctor')
        return view_func(request, *args, **kwargs)
    return wrapper


def _current_doctor(request):
    """取当前登录医生档案（会话由 accounts.staff_login 写入）；档案不存在视为未登录。"""
    staff_id = request.session.get('staff_id')
    if not isinstance(staff_id, int):
        return None
    if request.session.get('role_type') != 2:
        return None
    try:
        return Doctor.objects.get(doctor_id=staff_id)
    except Doctor.DoesNotExist:
        return None


def _fmt_time(dt):
    """统一按本地时区（东八区）展示，避免 UTC 时间与患者端相差 8 小时。"""
    if not dt:
        return ''
    if timezone.is_aware(dt):
        dt = timezone.localtime(dt)
    return dt.strftime('%Y-%m-%d %H:%M')


def _user_name(user_id):
    if not user_id:
        return '待分配'
    user = SysUser.objects.filter(user_id=user_id).first()
    return user.name if user else '待分配'


def _patient_card(patient):
    """患者档案卡片（姓名取自 sys_user，其余取自 patient 表）。"""
    if not patient:
        return None
    return {
        'id': patient.patient_id,
        'name': _user_name(patient.patient_id),
        'medical_no': patient.medical_no,
        'gender': patient.gender or '未填写',
        'age': patient.age if patient.age is not None else '未填写',
        'allergy': patient.allergy or '无记录',
        'address': patient.address or '未填写',
    }


def _patient_info(patient):
    """把患者档案压成一行文字，用于拼 AI 提示词。"""
    if not patient:
        return '无患者档案'
    return '姓名%s，%s，%s岁，过敏史：%s' % (
        _user_name(patient.patient_id), patient.gender or '性别未填',
        patient.age if patient.age is not None else '年龄未填',
        patient.allergy or '无记录')


def _message_list(consult_id):
    rows = ConsultationMessage.objects.filter(consultation_id=consult_id) \
        .order_by('send_time', 'msg_id')
    return [{
        'role': m.sender_role,
        'content': m.content or '',
        'msg_type': m.msg_type,
        'media_url': m.media_url or '',
        'time': _fmt_time(m.send_time),
        'mine': m.sender_role == '医生',
    } for m in rows]


def _save_image(image):
    """聊天图片落 media/chat/，与患者端保持同一目录与命名习惯。"""
    ext = os.path.splitext(image.name)[1].lower()
    if ext not in ('.jpg', '.jpeg', '.png', '.gif', '.webp'):
        ext = '.jpg'
    fname = 'chat_%s_%s%s' % (timezone.now().strftime('%Y%m%d%H%M%S'),
                             uuid.uuid4().hex[:8], ext)
    save_dir = os.path.join(settings.MEDIA_ROOT, 'chat')
    os.makedirs(save_dir, exist_ok=True)
    with open(os.path.join(save_dir, fname), 'wb') as f:
        for chunk in image.chunks():
            f.write(chunk)
    return '/' + settings.MEDIA_URL.lstrip('/') + 'chat/' + fname


# --------------------------------------------------------------------------
# 工作台
# --------------------------------------------------------------------------

@doctor_required
def workbench(request):
    doctor = _current_doctor(request)
    if not doctor:
        return redirect('accounts:staff_login', role='doctor')

    work_status = getattr(doctor, 'work_status', 0)

    # 待接诊：进行中且尚未分配医生的问诊（患者在发起时未指定医生的情形）
    pool = Consultation.objects.filter(status='进行中', doctor_id__isnull=True) \
        .select_related('patient').order_by('-start_time')

    mine = Consultation.objects.filter(status='进行中', doctor_id=doctor.doctor_id) \
        .select_related('patient').order_by('-start_time')

    # 待复核 AI 摘要：尚未复核且属于我进行中的问诊
    pending_ai = AiPreAssessment.objects.filter(
        review_result__isnull=True,
        consultation__doctor_id=doctor.doctor_id,
        consultation__status='进行中',
    ).select_related('consultation')

    prescs = Prescription.objects.filter(doctor_id=doctor.doctor_id) \
        .select_related('patient', 'consultation').order_by('-created_at')

    recent = list(mine) + list(pool)
    recent.sort(key=lambda c: c.start_time, reverse=True)

    # 历史问诊：我名下已结束/已转诊/已取消，取最近 5 条，与患者端“历史问诊”对应
    history = Consultation.objects.filter(doctor_id=doctor.doctor_id) \
        .exclude(status='进行中').select_related('patient').order_by('-start_time')[:5]

    context = {
        'doctor': {
            'name': _user_name(doctor.doctor_id),
            'department': doctor.department,
            'title': doctor.title or '医师',
            'skill': doctor.skill or '',
            # work_status: 0 离线 / 1 在线 / 2 忙碌（患者在挂号时会把医生置为忙碌）
            'work_status': work_status,
            'status_text': DOCTOR_STATUS_TEXT.get(work_status, '离线'),
            'status_badge': DOCTOR_STATUS_BADGE.get(work_status, 'badge-gray'),
            'online': work_status in (1, 2),
        },
        'pool': [{
            'id': c.consult_id,
            'patient': _user_name(c.patient_id),
            'gender': c.patient.gender or '',
            'age': c.patient.age if c.patient.age is not None else '',
            'status': c.status,
            'badge': CONSULT_BADGE.get(c.status, 'badge-gray'),
            'time': _fmt_time(c.start_time),
            'chief': (c.record_content or '').split('\n')[0],
        } for c in pool],
        'mine': [{
            'id': c.consult_id,
            'patient': _user_name(c.patient_id),
            'gender': c.patient.gender or '',
            'age': c.patient.age if c.patient.age is not None else '',
            'review_status': c.doctor_review_status,
            'review_badge': REVIEW_BADGE.get(c.doctor_review_status, 'badge-gray'),
            'time': _fmt_time(c.start_time),
            'chief': (c.record_content or '').split('\n')[0],
        } for c in mine],
        'pending_ai': [{
            'pre_id': a.pre_id,
            'cid': a.consultation_id,
            'patient': _user_name(a.consultation.patient_id),
            'risk_level': a.risk_level,
            'summary': a.ai_summary,
            'keyword': a.matched_keyword or '',
            'time': _fmt_time(a.created_at),
        } for a in pending_ai],
        'prescriptions': [{
            'id': p.presc_id,
            'no': 'P%09d' % p.presc_id,
            'patient': _user_name(p.patient_id),
            'status': p.status,
            'badge': PRESC_BADGE.get(p.status, 'badge-gray'),
            'ai_result': p.ai_check_result or '未校验',
            'time': _fmt_time(p.created_at),
        } for p in prescs],
        'history': [{
            'id': c.consult_id,
            'patient': _user_name(c.patient_id),
            'status': c.status,
            'badge': CONSULT_BADGE.get(c.status, 'badge-gray'),
            'review_status': c.doctor_review_status,
            'review_badge': REVIEW_BADGE.get(c.doctor_review_status, 'badge-gray'),
            'time': _fmt_time(c.start_time),
        } for c in history],
        'counts': {
            'pool': pool.count(),
            'mine': mine.count(),
            'ai': pending_ai.count(),
            'draft': prescs.filter(status='草稿').count(),
        },
    }
    return render(request, 'doctor/workbench.html', context)


# --------------------------------------------------------------------------
# 接诊
# --------------------------------------------------------------------------
@doctor_required
def consult_accept(request, cid):
    """接诊：把待接诊池中的问诊绑定到当前医生名下。"""
    if request.method != 'POST':
        return redirect('doctor:dashboard')

    doctor = _current_doctor(request)
    consult = Consultation.objects.filter(consult_id=cid).first()
    if not consult:
        flash.error(request, '问诊记录不存在或已被删除。')
        return redirect('doctor:dashboard')

    if consult.doctor_id and consult.doctor_id != doctor.doctor_id:
        flash.error(request, '该问诊已由其他医生接诊，不能重复接诊。')
        return redirect('doctor:dashboard')

    if consult.status != '进行中':
        flash.error(request, '该问诊状态为「%s」，不能接诊。' % consult.status)
        return redirect('doctor:dashboard')

    with transaction.atomic():
        if not consult.doctor_id:
            consult.doctor_id = doctor.doctor_id
            consult.save(update_fields=['doctor_id'])
        # 一医一患：接诊即占号，把医生置为忙碌（2）
        Doctor.objects.filter(doctor_id=doctor.doctor_id).update(work_status=2)
        write_log(doctor.doctor_id, '医生', '接诊',
                  target_type='consultation', target_id=consult.consult_id)

    flash.success(request, '接诊成功，已进入问诊详情。')
    return redirect('doctor:consult_detail', cid=consult.consult_id)


@doctor_required
def consult_detail(request, cid):
    """预问诊页：患者档案 + AI 摘要复核。复核后再进入图文沟通页（分步跳转）。"""
    doctor = _current_doctor(request)
    consult = Consultation.objects.filter(consult_id=cid) \
        .select_related('patient', 'doctor', 'duty').first()
    if not consult:
        flash.error(request, '问诊记录不存在。')
        return redirect('doctor:dashboard')

    # 只允许本人接诊的问诊，或尚未分配的待接诊问诊
    if consult.doctor_id and consult.doctor_id != doctor.doctor_id:
        flash.error(request, '该问诊由其他医生负责，无权查看。')
        return redirect('doctor:dashboard')

    ai = AiPreAssessment.objects.filter(consultation_id=consult.consult_id).first()
    # 高危问诊须患者先确认风险告知，未确认前线上对话不开启（与患者端口径一致）
    consent_blocked = bool(ai and ai.risk_level == '高危'
                           and getattr(ai, 'risk_consent', 0) != 1)

    context = {
        'consult': {
            'id': consult.consult_id,
            'status': consult.status,
            'badge': CONSULT_BADGE.get(consult.status, 'badge-gray'),
            'time': _fmt_time(consult.start_time),
            'end_time': _fmt_time(consult.end_time),
            'record': consult.record_content or '',
            'review_status': consult.doctor_review_status,
            'review_badge': REVIEW_BADGE.get(consult.doctor_review_status, 'badge-gray'),
            'review_opinion': consult.doctor_review_opinion or '',
            'final_diagnosis': consult.final_diagnosis or '',
            'is_mine': consult.doctor_id == doctor.doctor_id,
        },
        'patient': _patient_card(consult.patient),
        'ai': {
            'pre_id': ai.pre_id,
            'risk_level': ai.risk_level,
            'summary': ai.ai_summary,
            'keyword': ai.matched_keyword or '',
            'review_result': ai.review_result or '待复核',
            'review_comment': ai.review_comment or '',
            'risk_consent': getattr(ai, 'risk_consent', 0),
            'consent_text': '已确认风险，坚持线上就诊' if getattr(ai, 'risk_consent', 0) == 1 else '未确认（高危须线下就诊）',
            'time': _fmt_time(ai.created_at),
        } if ai else None,
        'ai_suggest': request.session.get('ai_suggest_%d' % consult.consult_id),
        'consent_blocked': consent_blocked,
        'reviewed': bool(ai and ai.review_result),
    }
    return render(request, 'doctor/consult_detail.html', context)


@doctor_required
def consult_chat_view(request, cid):
    """图文沟通页：消息对话为主，底部提供开处方与结束问诊入口。"""
    doctor = _current_doctor(request)
    consult = Consultation.objects.filter(consult_id=cid) \
        .select_related('patient', 'doctor', 'duty').first()
    if not consult:
        flash.error(request, '问诊记录不存在。')
        return redirect('doctor:dashboard')
    if consult.doctor_id and consult.doctor_id != doctor.doctor_id:
        flash.error(request, '该问诊由其他医生负责，无权查看。')
        return redirect('doctor:dashboard')

    ai = AiPreAssessment.objects.filter(consultation_id=consult.consult_id).first()
    consent_blocked = bool(ai and ai.risk_level == '高危'
                           and getattr(ai, 'risk_consent', 0) != 1)
    draft = Prescription.objects.filter(consultation_id=consult.consult_id,
                                        doctor_id=doctor.doctor_id,
                                        status='草稿').first()
    can_chat = (consult.status == '进行中'
                and consult.doctor_id == doctor.doctor_id
                and not consent_blocked)

    context = {
        'consult': {
            'id': consult.consult_id,
            'status': consult.status,
            'badge': CONSULT_BADGE.get(consult.status, 'badge-gray'),
            'time': _fmt_time(consult.start_time),
            'end_time': _fmt_time(consult.end_time),
            'record': consult.record_content or '',
            'final_diagnosis': consult.final_diagnosis or '',
            'is_mine': consult.doctor_id == doctor.doctor_id,
        },
        'patient': _patient_card(consult.patient),
        'ai': {'risk_level': ai.risk_level} if ai else None,
        'chats': _message_list(consult.consult_id),
        'can_chat': can_chat,
        'consent_blocked': consent_blocked,
        'draft': {'id': draft.presc_id} if draft else None,
    }
    return render(request, 'doctor/consult_chat.html', context)


@doctor_required
def ai_suggest(request, cid):
    """调方舟模型给出一条 AI 复核建议，结果暂存在会话里供医生参考（不写库）。"""
    if request.method != 'POST':
        return redirect('doctor:consult_detail', cid=cid)

    doctor = _current_doctor(request)
    consult = Consultation.objects.filter(consult_id=cid, doctor_id=doctor.doctor_id) \
        .select_related('patient').first()
    if not consult:
        flash.error(request, '无权为该问诊调用 AI 复核建议。')
        return redirect('doctor:dashboard')

    ai = AiPreAssessment.objects.filter(consultation_id=consult.consult_id).first()
    if not ai:
        flash.error(request, '该问诊没有 AI 预问诊记录，无法生成复核建议。')
        return redirect('doctor:consult_detail', cid=cid)

    suggestion = ai_client.review_suggestion(
        _patient_info(consult.patient),
        consult.record_content or '',
        ai.ai_summary or '',
        ai.risk_level or '',
        ai.matched_keyword or '',
    )
    if suggestion:
        suggestion['time'] = _fmt_time(timezone.now())
        request.session['ai_suggest_%d' % consult.consult_id] = suggestion
        flash.success(request, 'AI 复核建议已生成，可参考后确认复核结论。')
    else:
        flash.error(request, 'AI 服务暂时不可用（未配置、超时或返回异常），请按病情自行判断复核结论。')
    return redirect('doctor:consult_detail', cid=cid)


@doctor_required
def review_ai(request, cid):
    """AI 摘要复核：采纳 / 修正 / 否决，写 ai_pre_assessment 与 consultation 两处。

    注意：AI 结论只作为医生复核的参考，不自动写入 final_diagnosis（最终诊断由医生出具）。
    """
    if request.method != 'POST':
        return redirect('doctor:consult_detail', cid=cid)

    doctor = _current_doctor(request)
    consult = Consultation.objects.filter(consult_id=cid, doctor_id=doctor.doctor_id).first()
    if not consult:
        flash.error(request, '无权复核该问诊的 AI 摘要。')
        return redirect('doctor:dashboard')

    ai = AiPreAssessment.objects.filter(consultation_id=consult.consult_id).first()
    if not ai:
        flash.error(request, '该问诊没有 AI 预问诊记录，无法复核。')
        return redirect('doctor:consult_detail', cid=cid)

    action = request.POST.get('result', '').strip()
    opinion = request.POST.get('opinion', '').strip()
    if action not in REVIEW_MAP:
        flash.error(request, '请选择复核结论（采纳 / 修正 / 否决）。')
        return redirect('doctor:consult_detail', cid=cid)
    if action != '采纳' and not opinion:
        flash.error(request, '选择「%s」时必须填写复核意见，说明修改或否决理由。' % action)
        return redirect('doctor:consult_detail', cid=cid)

    mapped = REVIEW_MAP[action]
    with transaction.atomic():
        ai.review_result = mapped
        ai.review_comment = opinion or '摘要与主诉一致，采纳 AI 结论'
        ai.save(update_fields=['review_result', 'review_comment'])

        consult.doctor_review_status = mapped
        consult.doctor_review_opinion = ai.review_comment
        consult.save(update_fields=['doctor_review_status', 'doctor_review_opinion'])

        write_log(doctor.doctor_id, '医生', 'AI复核',
                  target_type='ai_pre_assessment', target_id=ai.pre_id)

    request.session.pop('ai_suggest_%d' % consult.consult_id, None)
    flash.success(request, 'AI 摘要已「%s」，复核意见已记录。' % mapped)
    return redirect('doctor:consult_detail', cid=cid)


# --------------------------------------------------------------------------
# 图文回复
# --------------------------------------------------------------------------
@doctor_required
def send_message(request, cid):
    """医生图文回复：文字或图片写入 consultation_message（read_status=0 表示患者未读）。"""
    if request.method != 'POST':
        return redirect('doctor:consult_chat', cid=cid)

    doctor = _current_doctor(request)
    consult = Consultation.objects.filter(consult_id=cid, doctor_id=doctor.doctor_id).first()
    if not consult:
        flash.error(request, '无权在该问诊中发言。')
        return redirect('doctor:dashboard')
    if consult.status != '进行中':
        flash.error(request, '问诊已「%s」，无法继续发送消息。' % consult.status)
        return redirect('doctor:consult_chat', cid=cid)

    ai = AiPreAssessment.objects.filter(consultation_id=consult.consult_id).first()
    if ai and ai.risk_level == '高危' and getattr(ai, 'risk_consent', 0) != 1:
        flash.error(request, '该问诊为高危，患者尚未确认风险告知，线上对话暂时不开放。')
        return redirect('doctor:consult_chat', cid=cid)

    content = request.POST.get('content', '').strip()
    image = request.FILES.get('image')
    msg_type, media_url = 'text', None

    if image:
        media_url = _save_image(image)
        msg_type = 'image'
    elif not content:
        flash.error(request, '请输入回复内容或选择一张图片。')
        return redirect('doctor:consult_chat', cid=cid)
    if len(content) > 2000:
        content = content[:2000]

    ConsultationMessage.objects.create(
        consultation_id=consult.consult_id, sender_role='医生',
        sender_id=doctor.doctor_id, msg_type=msg_type,
        content=content or None, media_url=media_url,
        send_time=timezone.now(), read_status=0)
    write_log(doctor.doctor_id, '医生', '回复消息',
              target_type='consultation', target_id=consult.consult_id)

    return redirect('doctor:consult_chat', cid=cid)


@doctor_required
def end_consult(request, cid):
    """结束问诊：写入最终诊断（可选）并置状态为「已结束」。"""
    if request.method != 'POST':
        return redirect('doctor:consult_chat', cid=cid)

    doctor = _current_doctor(request)
    consult = Consultation.objects.filter(consult_id=cid, doctor_id=doctor.doctor_id).first()
    if not consult:
        flash.error(request, '无权结束该问诊。')
        return redirect('doctor:dashboard')
    if consult.status != '进行中':
        flash.error(request, '该问诊当前状态为「%s」，无需重复结束。' % consult.status)
        return redirect('doctor:consult_chat', cid=cid)

    diagnosis = request.POST.get('final_diagnosis', '').strip()
    if not diagnosis:
        flash.error(request, '结束问诊前请填写最终诊断，诊断由医生出具、不由 AI 直接写入。')
        return redirect('doctor:consult_chat', cid=cid)

    with transaction.atomic():
        consult.status = '已结束'
        consult.final_diagnosis = diagnosis
        consult.end_time = timezone.now()
        consult.save(update_fields=['status', 'final_diagnosis', 'end_time'])
        # 一医一患：问诊结束释放占号，医生回到在线，患者可再次挂号
        Doctor.objects.filter(doctor_id=doctor.doctor_id, work_status=2).update(work_status=1)
        write_log(doctor.doctor_id, '医生', '结束问诊',
                  target_type='consultation', target_id=consult.consult_id)

    flash.success(request, '问诊已结束，最终诊断已记录。')
    return redirect('doctor:consult_chat', cid=cid)


# --------------------------------------------------------------------------
# 开方草稿 / 提交审核
# --------------------------------------------------------------------------
@doctor_required
def prescription_new(request, cid):
    """按问诊新建或继续编辑处方草稿。"""
    return _prescription_form(request, cid=cid)


@doctor_required
def prescription_edit(request, presc_id):
    """编辑我名下已有的处方（草稿可改，已提交的只读查看）。"""
    return _prescription_form(request, presc_id=presc_id)


def _prescription_form(request, cid=None, presc_id=None):
    doctor = _current_doctor(request)
    consult = presc = None

    if cid is not None:
        consult = Consultation.objects.filter(consult_id=cid, doctor_id=doctor.doctor_id) \
            .select_related('patient').first()
        if not consult:
            flash.error(request, '无权为该问诊开具处方。')
            return redirect('doctor:dashboard')
        presc = Prescription.objects.filter(consultation_id=consult.consult_id,
                                            doctor_id=doctor.doctor_id,
                                            status='草稿').first()
    else:
        presc = Prescription.objects.filter(presc_id=presc_id,
                                            doctor_id=doctor.doctor_id).first()
        if not presc:
            flash.error(request, '处方不存在或不属于当前医生。')
            return redirect('doctor:prescription_list')
        # 只有草稿可编辑；已提交（待审核及以后）的处方由药师端接手
        if presc.status != '草稿':
            flash.error(request, '该处方当前状态为「%s」，只有草稿可以编辑。' % presc.status)
            return redirect('doctor:prescription_list')
        consult = presc.consultation

    patient = consult.patient if consult else (presc.patient if presc else None)
    drugs = list(Drug.objects.order_by('name'))
    drugs_json = [{
        'id': d.drug_id,
        'name': d.name,
        'spec': d.specification,
        'price': float(d.unit_price),
        'stock': d.stock_qty,
        'is_rx': d.is_prescription,
        'contra': d.contraindication or '',
    } for d in drugs]

    if request.method == 'POST':
        rows, errors = parse_rows(request.POST)
        if not rows:
            errors.append('处方至少需要一行有效药品明细。')
        if errors:
            flash.error(request, '；'.join(errors))
            return _render_form(request, doctor, consult, presc, patient, drugs_json, rows)

        items, build_errors = build_items(rows)
        if build_errors:
            flash.error(request, '；'.join(build_errors))
            return _render_form(request, doctor, consult, presc, patient, drugs_json, rows)

        # 先跑本地规则（过敏/配伍/库存/单次超量），再交方舟做合理性预审
        local_result, local_comment = check_prescription(items, patient)
        items_text = '；'.join('%s %s×%d（%s）' % (
            i['drug_name'], i['drug'].specification or '', i['qty'], i['dosage']) for i in items)
        remote = ai_client.prescription_review(
            _patient_info(patient),
            (consult.final_diagnosis or consult.record_content or '').strip()[:120],
            items_text, local_comment)
        if remote:
            ai_result = remote['result']
            ai_comment = remote['comment']
            if local_result == '风险提示' and local_comment:
                ai_comment = '%s（系统规则初筛：%s）' % (ai_comment, local_comment)
        else:
            # 远程不可用：标记超时降级，保留本地规则结论，避免误判为“已通过”
            ai_result = '超时降级'
            ai_comment = 'AI 远程预审不可用，已按本地规则给出结果：%s' % (local_comment or '未见明显禁忌')
        now = timezone.now()

        with transaction.atomic():
            if presc:
                if presc.status != '草稿':
                    flash.error(request, '该处方状态为「%s」，已不能修改，请新建草稿。' % presc.status)
                    return redirect('doctor:prescription_list')
                presc.status = '草稿'
                presc.ai_check_result = ai_result
                presc.ai_check_comment = ai_comment
                presc.updated_at = now
                presc.save(update_fields=['status', 'ai_check_result', 'ai_check_comment', 'updated_at'])
            else:
                presc = Prescription.objects.create(
                    consultation_id=consult.consult_id,
                    doctor_id=doctor.doctor_id,
                    patient_id=patient.patient_id,
                    status='草稿',
                    ai_check_result=ai_result,
                    ai_check_comment=ai_comment,
                    doctor_confirm=0,
                    pickup_status=0,
                    created_at=now,
                    updated_at=now,
                )
            # 明细联合主键 (presc_id, drug_id)，整单重写最稳妥
            PrescriptionItem.objects.filter(prescription_id=presc.presc_id).delete()
            for item in items:
                PrescriptionItem.objects.create(
                    prescription_id=presc.presc_id,
                    drug_id=item['drug'].drug_id,
                    drug_name=item['drug_name'],
                    qty=item['qty'],
                    dosage=item['dosage'],
                    unit_price=item['unit_price'],
                    subtotal=item['subtotal'],
                    is_decoction=item['is_decoction'],
                    decoction_num=item['decoction_num'],
                )
            write_log(doctor.doctor_id, '医生', '保存处方草稿',
                      target_type='prescription', target_id=presc.presc_id)

        flash.success(request, '处方草稿已保存（AI 校验：%s）。' % ai_result)
        return redirect('doctor:prescription_edit', presc_id=presc.presc_id)

    existing = []
    if presc:
        existing = list(PrescriptionItem.objects.filter(prescription_id=presc.presc_id))
    return _render_form(request, doctor, consult, presc, patient, drugs_json, existing_rows=existing)


def _render_form(request, doctor, consult, presc, patient, drugs_json, rows=None, existing_rows=None):
    """渲染开方表单；rows 用于保存失败时回填，existing_rows 用于展示已有明细。"""
    if rows is None:
        rows = [{
            'drug_id': i.drug_id,
            'qty': i.qty,
            'dosage': i.dosage,
            'is_decoction': i.is_decoction,
            'decoction_num': i.decoction_num or '',
            'drug_name': i.drug_name,
            'unit_price': float(i.unit_price),
            'subtotal': float(i.subtotal),
        } for i in (existing_rows or [])]

    items_total = 0
    if presc:
        items_total = sum(float(i.subtotal) for i in
                          PrescriptionItem.objects.filter(prescription_id=presc.presc_id))

    context = {
        'doctor': {'name': _user_name(doctor.doctor_id), 'department': doctor.department},
        'patient': _patient_card(patient),
        'consult': {
            'id': consult.consult_id,
            'record': consult.record_content or '',
            'diagnosis': consult.final_diagnosis or '',
        } if consult else None,
        'presc': {
            'id': presc.presc_id,
            'no': 'P%09d' % presc.presc_id,
            'status': presc.status,
            'badge': PRESC_BADGE.get(presc.status, 'badge-gray'),
            'ai_result': presc.ai_check_result or '未校验',
            'ai_comment': presc.ai_check_comment or '',
            'time': _fmt_time(presc.created_at),
            'editable': presc.status == '草稿',
        } if presc else None,
        'rows': rows,
        'row_count': max(len(rows), 1),
        'drugs': drugs_json,
        'total': round(items_total, 2),
    }
    return render(request, 'doctor/prescription_form.html', context)


@doctor_required
def prescription_list(request):
    """我的处方：按状态筛选，草稿可继续编辑/提交，其余只读。"""
    doctor = _current_doctor(request)
    status = request.GET.get('status', '').strip()
    prescs = Prescription.objects.filter(doctor_id=doctor.doctor_id) \
        .select_related('patient', 'consultation').order_by('-created_at')
    if status:
        prescs = prescs.filter(status=status)

    rows = []
    for p in prescs:
        items = PrescriptionItem.objects.filter(prescription_id=p.presc_id)
        rows.append({
            'id': p.presc_id,
            'no': 'P%09d' % p.presc_id,
            'patient': _user_name(p.patient_id),
            'status': p.status,
            'badge': PRESC_BADGE.get(p.status, 'badge-gray'),
            'ai_result': p.ai_check_result or '未校验',
            'ai_comment': p.ai_check_comment or '',
            'time': _fmt_time(p.created_at),
            'items': '；'.join('%s×%d' % (i.drug_name, i.qty) for i in items) or '暂无明细',
            'amount': round(sum(float(i.subtotal) for i in items), 2),
            'editable': p.status == '草稿',
            'cid': p.consultation_id,
        })

    context = {
        'doctor': {'name': _user_name(doctor.doctor_id), 'department': doctor.department},
        'prescriptions': rows,
        'current_status': status,
        'status_tabs': ['', '草稿', '待审核', '已通过', '已驳回', '待配药', '已完成'],
    }
    return render(request, 'doctor/prescription_list.html', context)


@doctor_required
def prescription_submit(request, presc_id):
    """提交审核：草稿 → 待审核，doctor_confirm 置 1，供药师端承接。"""
    if request.method != 'POST':
        return redirect('doctor:prescription_list')

    doctor = _current_doctor(request)
    presc = Prescription.objects.filter(presc_id=presc_id, doctor_id=doctor.doctor_id).first()
    if not presc:
        flash.error(request, '处方不存在或不属于当前医生。')
        return redirect('doctor:prescription_list')

    if presc.status != '草稿':
        flash.error(request, '该处方当前状态为「%s」，不能重复提交。' % presc.status)
        return redirect('doctor:prescription_list')

    if not PrescriptionItem.objects.filter(prescription_id=presc.presc_id).exists():
        flash.error(request, '处方没有药品明细，不能提交审核。')
        return redirect('doctor:prescription_edit', presc_id=presc.presc_id)

    with transaction.atomic():
        presc.status = '待审核'
        presc.doctor_confirm = 1
        presc.updated_at = timezone.now()
        presc.save(update_fields=['status', 'doctor_confirm', 'updated_at'])
        write_log(doctor.doctor_id, '医生', '提交处方审核',
                  target_type='prescription', target_id=presc.presc_id)

    flash.success(request, '处方已提交药师审核，可在处方列表查看流转状态。')
    return redirect('doctor:prescription_list')

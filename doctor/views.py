"""医生端视图：接诊、AI 摘要复核、图文回复、开方草稿、提交审核。

数据口径与组内约定一致：
- 业务表由 docs/sql/create_tables.sql 建好，core.models 全部 managed=False，
  这里**不生成迁移、不建表**，只做查询与写入；
- 登录口径：独立会话槽位 staff_doctor_id（兼容旧 staff_id / role_type），
  医生端不再自带登录页，未登录一律跳 accounts:staff_login(role='doctor')；
- 关键动作统一通过 core.log_service.write_log 写入 operation_log。
"""

import json
import os
import uuid
from functools import wraps

from django.conf import settings
from django.contrib import messages as flash
from django.db import transaction
from django.db.models import Q
from django.shortcuts import render, redirect, reverse
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from core import call_signal
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
# 草稿可继续编辑；被药师驳回的处方允许医生修改明细后重新保存为草稿再提交
EDITABLE_STATUSES = ('草稿', '已驳回')

REVIEW_MAP = {'采纳': '已采纳', '修正': '已修正', '否决': '已否决'}


# --------------------------------------------------------------------------
# 通用工具
# --------------------------------------------------------------------------

def _doctor_session_id(request):
    """取医生登录槽位：优先独立槽位 staff_doctor_id，兼容旧共享 staff_id/role_type 会话。"""
    sid = request.session.get('staff_doctor_id')
    if sid is None and request.session.get('role_type') == 2:
        sid = request.session.get('staff_id')
    return sid if isinstance(sid, int) else None


def doctor_required(view_func):
    """医生身份守卫：未登录或非医生角色跳统一职工登录页（身份选择后进入）。"""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if _doctor_session_id(request) is None:
            return redirect('accounts:staff_login', role='doctor')
        return view_func(request, *args, **kwargs)
    return wrapper


def _current_doctor(request):
    """取当前登录医生档案（会话由 accounts.staff_login 写入）；档案不存在视为未登录。"""
    staff_id = _doctor_session_id(request)
    if staff_id is None:
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
        .exclude(sender_role='AI').exclude(msg_type='pre') \
        .order_by('send_time', 'msg_id')
    return [{
        'id': m.msg_id,
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

    # 接诊中：本人名下已接诊(accepted=1)的进行中问诊，同一时刻至多一条
    mine = Consultation.objects.filter(
        status='进行中', accepted=1, doctor_id=doctor.doctor_id
    ).select_related('patient').order_by('-start_time')

    # 待接诊队列：本人名下排队中(accepted=0)的问诊，按发起时间先到先得；
    # 兼容历史未分配医生(doctor_id 为空)的公共待分配记录
    pool = Consultation.objects.filter(
        status='进行中', accepted=0
    ).filter(
        Q(doctor_id=doctor.doctor_id) | Q(doctor_id__isnull=True)
    ).select_related('patient').order_by('start_time', 'consult_id')

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
            'queue_no': idx + 1,
            'patient': _user_name(c.patient_id),
            'gender': c.patient.gender or '',
            'age': c.patient.age if c.patient.age is not None else '',
            'status': c.status,
            'badge': CONSULT_BADGE.get(c.status, 'badge-gray'),
            'time': _fmt_time(c.start_time),
            'chief': (c.record_content or '').split('\n')[0],
        } for idx, c in enumerate(pool)],
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

    # 单接待：同一时刻只允许接诊一位，已有进行中接诊时须先结束
    busy = Consultation.objects.filter(
        doctor_id=doctor.doctor_id, status='进行中', accepted=1).exists()
    if busy:
        flash.error(request, '您当前已有一位接诊中的患者，请先结束后再接诊下一位。')
        return redirect('doctor:dashboard')

    with transaction.atomic():
        if not consult.doctor_id:
            consult.doctor_id = doctor.doctor_id
        consult.accepted = 1
        consult.save(update_fields=['doctor_id', 'accepted'])
        # 接诊即占号，把医生置为忙碌（2）
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
            'accepted': consult.accepted,
            'end_request': consult.end_request,
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
                and consult.accepted == 1
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
            'accepted': consult.accepted,
            'end_request': consult.end_request,
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
def messages_poll(request, cid):
    """对话态消息轮询：返回最新医患消息，供医生端自动加载患者新回复，无需手动刷新。"""
    doctor = _current_doctor(request)
    consult = Consultation.objects.filter(consult_id=cid).first()
    if not consult or (consult.doctor_id and consult.doctor_id != doctor.doctor_id):
        return JsonResponse({'error': 'forbidden'}, status=403)
    messages = _message_list(consult.consult_id)
    return JsonResponse({'count': len(messages), 'messages': messages})


@doctor_required
@require_http_methods(['GET', 'POST'])
def call_signal_view(request, cid):
    """音视频信令：GET 轮询房间信令，POST 上报 Offer/Answer/ICE/挂断。

    仅接诊该问诊的医生可读写；角色由后端固定为 doctor。
    """
    doctor = _current_doctor(request)
    consult = Consultation.objects.filter(consult_id=cid).first()
    if not consult or (consult.doctor_id and consult.doctor_id != doctor.doctor_id):
        return JsonResponse({'error': 'forbidden'}, status=403)

    if request.method == 'POST':
        try:
            payload = json.loads(request.body or '{}')
        except json.JSONDecodeError:
            return JsonResponse({'error': 'bad_request'}, status=400)
        call_signal.post(consult.consult_id, 'doctor', payload)
        return JsonResponse({'ok': True})

    return JsonResponse(call_signal.poll(consult.consult_id, 'doctor'))


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

    # 已完成复核的问诊锁定结论，不允许重复提交覆盖
    if ai.review_result:
        flash.error(request, '该问诊已完成复核（%s），结论已锁定，不可重复提交。' % ai.review_result)
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
    if consult.status != '进行中' or consult.accepted != 1:
        flash.error(request, '该问诊尚未接诊或已结束，无法继续发送消息。')
        return redirect('doctor:consult_chat', cid=cid)

    ai = AiPreAssessment.objects.filter(consultation_id=consult.consult_id).first()
    if ai and ai.risk_level == '高危' and getattr(ai, 'risk_consent', 0) != 1:
        flash.error(request, '该问诊为高危，患者尚未确认风险告知，线上对话暂时不开放。')
        return redirect('doctor:consult_chat', cid=cid)

    content = request.POST.get('content', '').strip()
    images = [f for f in request.FILES.getlist('image') if f]
    if not images and not content:
        flash.error(request, '请输入回复内容或选择图片。')
        return redirect('doctor:consult_chat', cid=cid)
    if len(content) > 2000:
        content = content[:2000]

    now = timezone.now()
    if images:
        # 多图上传：每张图单独一条图片消息，正文只挂在第一张，避免语义被拆散
        for idx, image in enumerate(images[:9]):
            ConsultationMessage.objects.create(
                consultation_id=consult.consult_id, sender_role='医生',
                sender_id=doctor.doctor_id, msg_type='image',
                content=(content or None) if idx == 0 else None,
                media_url=_save_image(image),
                send_time=now, read_status=0)
    else:
        ConsultationMessage.objects.create(
            consultation_id=consult.consult_id, sender_role='医生',
            sender_id=doctor.doctor_id, msg_type='text',
            content=content, media_url=None,
            send_time=now, read_status=0)
    write_log(doctor.doctor_id, '医生', '回复消息',
              target_type='consultation', target_id=consult.consult_id)

    return redirect('doctor:consult_chat', cid=cid)


@doctor_required
def consult_advice(request, cid):
    """结束问诊建议分流：填写诊断后选择「无需用药」或「需要开具处方」。

    - 无需用药：直接结束问诊，不生成处方；
    - 需要开具处方：保留问诊为进行中，跳转处方页进入开方环境。
    """
    doctor = _current_doctor(request)
    consult = Consultation.objects.filter(
        consult_id=cid, doctor_id=doctor.doctor_id) \
        .select_related('patient').first()
    if not consult:
        flash.error(request, '无权处理该问诊。')
        return redirect('doctor:dashboard')
    if consult.status != '进行中':
        flash.error(request, '该问诊当前状态为「%s」，无需重复结束。' % consult.status)
        return redirect('doctor:consult_chat', cid=cid)

    draft = Prescription.objects.filter(
        consultation_id=consult.consult_id, doctor_id=doctor.doctor_id,
        status='草稿').first()

    if request.method == 'POST':
        diagnosis = request.POST.get('final_diagnosis', '').strip()
        advice = request.POST.get('advice', '').strip()

        if not diagnosis:
            flash.error(request, '结束问诊前请填写最终诊断，诊断由医生出具、不由 AI 直接写入。')
            return render(request, 'doctor/consult_advice.html', {
                'consult': {'id': consult.consult_id, 'status': consult.status,
                            'time': _fmt_time(consult.start_time)},
                'patient': _patient_card(consult.patient),
                'form': {'final_diagnosis': diagnosis, 'advice': advice},
                'draft': {'id': draft.presc_id} if draft else None,
            })
        if advice not in ('no_drug', 'need_drug'):
            flash.error(request, '请选择本次处置建议：无需用药，或需要开具处方。')
            return render(request, 'doctor/consult_advice.html', {
                'consult': {'id': consult.consult_id, 'status': consult.status,
                            'time': _fmt_time(consult.start_time)},
                'patient': _patient_card(consult.patient),
                'form': {'final_diagnosis': diagnosis, 'advice': advice},
                'draft': {'id': draft.presc_id} if draft else None,
            })

        with transaction.atomic():
            consult.final_diagnosis = diagnosis
            if advice == 'no_drug':
                consult.advice_type = '无需用药'
                consult.status = '已结束'
                consult.end_time = timezone.now()
                consult.end_request = 0
                consult.save(update_fields=['status', 'final_diagnosis',
                                            'advice_type', 'end_time', 'end_request'])
                # 一医一患：问诊结束释放占号，医生回到在线
                Doctor.objects.filter(
                    doctor_id=doctor.doctor_id, work_status=2).update(work_status=1)
                write_log(doctor.doctor_id, '医生', '结束问诊',
                          target_type='consultation', target_id=consult.consult_id)
                flash.success(request, '问诊已结束，本次处置建议：无需用药。')
                return redirect('doctor:consult_chat', cid=cid)

            # 需要开具处方：保留进行中，进入开方环境，待处方提交后收尾
            consult.advice_type = '需开处方'
            consult.save(update_fields=['final_diagnosis', 'advice_type'])
            write_log(doctor.doctor_id, '医生', '确认需开具处方',
                      target_type='consultation', target_id=consult.consult_id)

        flash.success(request, '诊断已记录，可继续开具处方。')
        return redirect('doctor:prescription_new', cid=cid)

    return render(request, 'doctor/consult_advice.html', {
        'consult': {'id': consult.consult_id, 'status': consult.status,
                    'time': _fmt_time(consult.start_time)},
        'patient': _patient_card(consult.patient),
        'form': {'final_diagnosis': consult.final_diagnosis or '', 'advice': ''},
        'draft': {'id': draft.presc_id} if draft else None,
    })


@doctor_required
def end_request_decide(request, cid):
    """医生回应患者的结束申请：同意则结束问诊并释放占号；拒绝则继续问诊。

    患者不能单方面结束；结束申请须经接诊医生确认（双方同意）后才生效。
    """
    if request.method != 'POST':
        return redirect('doctor:consult_chat', cid=cid)

    doctor = _current_doctor(request)
    consult = Consultation.objects.filter(
        consult_id=cid, doctor_id=doctor.doctor_id).first()
    if not consult:
        flash.error(request, '无权处理该问诊。')
        return redirect('doctor:dashboard')
    if consult.status != '进行中':
        flash.error(request, '该问诊已结束，无需处理结束申请。')
        return redirect('doctor:consult_chat', cid=cid)

    action = request.POST.get('action', '').strip()
    if action == 'approve':
        with transaction.atomic():
            consult.status = '已结束'
            consult.end_time = timezone.now()
            consult.end_request = 0
            consult.save(update_fields=['status', 'end_time', 'end_request'])
            # 一医一患：结束后释放占号，医生回到在线
            Doctor.objects.filter(
                doctor_id=doctor.doctor_id, work_status=2).update(work_status=1)
        write_log(doctor.doctor_id, '医生', '同意结束问诊',
                  target_type='consultation', target_id=consult.consult_id)
        flash.success(request, '已同意患者的结束申请，本次问诊结束。')
    elif action == 'reject':
        consult.end_request = 0
        consult.save(update_fields=['end_request'])
        write_log(doctor.doctor_id, '医生', '拒绝结束申请',
                  target_type='consultation', target_id=consult.consult_id)
        flash.success(request, '已拒绝结束申请，继续本次问诊。')
    else:
        flash.error(request, '无效的操作，请选择同意或拒绝。')
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
        # 处方只能在线上问诊进行中环节开具；已结束/转诊/取消的问诊不可再写方
        if consult.status != '进行中':
            flash.error(request, '仅线上问诊进行中可开具处方，该问诊当前状态为「%s」。' % consult.status)
            return redirect('doctor:consult_chat', cid=cid)
        presc = Prescription.objects.filter(consultation_id=consult.consult_id,
                                            doctor_id=doctor.doctor_id,
                                            status='草稿').first()
    else:
        presc = Prescription.objects.filter(presc_id=presc_id,
                                            doctor_id=doctor.doctor_id).first()
        if not presc:
            flash.error(request, '处方不存在或不属于当前医生。')
            return redirect('doctor:prescription_list')
        # 草稿可编辑；已提交（待审核及以后）的处方只读查看，由药师端接手后续流程
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
        if presc and presc.status not in EDITABLE_STATUSES:
            flash.error(request, '该处方已提交审核，不可再修改。')
            return redirect('doctor:prescription_edit', presc_id=presc.presc_id)
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
                if presc.status not in EDITABLE_STATUSES:
                    flash.error(request, '该处方状态为「%s」，已不能修改，请新建草稿。' % presc.status)
                    return redirect('doctor:prescription_list')
                # 驳回单退回草稿：释放原药师归属、清空旧审核结论与取药记录，重新预审后再提交
                presc.status = '草稿'
                presc.doctor_confirm = 0
                presc.pharmacist = None
                presc.audit_conclusion = None
                presc.audit_time = None
                presc.pickup_method = None
                presc.pickup_status = 0
                presc.dispensed_at = None
                presc.ai_check_result = ai_result
                presc.ai_check_comment = ai_comment
                presc.updated_at = now
                presc.save(update_fields=['status', 'doctor_confirm', 'pharmacist',
                                          'audit_conclusion', 'audit_time', 'pickup_method',
                                          'pickup_status', 'dispensed_at', 'ai_check_result',
                                          'ai_check_comment', 'updated_at'])
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
                )
            write_log(doctor.doctor_id, '医生', '保存处方草稿',
                      target_type='prescription', target_id=presc.presc_id)

        flash.success(request, '处方草稿已保存（AI 校验：%s）。' % ai_result)
        return redirect('doctor:prescription_review', presc_id=presc.presc_id)

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
            'audit_comment': presc.audit_conclusion or '',
            'time': _fmt_time(presc.created_at),
            'editable': presc.status in EDITABLE_STATUSES,
        } if presc else None,
        'rows': rows,
        'row_count': max(len(rows), 1),
        'drugs': drugs_json,
        'total': round(items_total, 2),
        'editable': presc.status in EDITABLE_STATUSES if presc else True,
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
            'editable': p.status in EDITABLE_STATUSES,
            'audit_comment': p.audit_conclusion or '',
            'cid': p.consultation_id,
        })

    context = {
        'doctor': {'name': _user_name(doctor.doctor_id), 'department': doctor.department},
        'prescriptions': rows,
        'rejected_rows': [r for r in rows if r['status'] == '已驳回'],
        'current_status': status,
        'status_tabs': ['', '草稿', '待审核', '已通过', '已驳回', '待配药', '已完成'],
    }
    return render(request, 'doctor/prescription_list.html', context)


@doctor_required
def prescription_review(request, presc_id):
    """AI 合理性预审结果页：展示处方明细与预审结论，按结论放行提交药师审核。

    - 通过 / 超时降级：可常规提交药师审核；
    - 风险提示：不提供常规提交，仅保留“知悉风险仍提交”的强制入口，由药师最终把关；
    - 草稿可返回修改；已提交处方只读展示。
    """
    doctor = _current_doctor(request)
    presc = Prescription.objects.filter(
        presc_id=presc_id, doctor_id=doctor.doctor_id
    ).select_related('patient', 'consultation').first()
    if not presc:
        flash.error(request, '处方不存在或不属于当前医生。')
        return redirect('doctor:prescription_list')

    items = list(PrescriptionItem.objects.filter(
        prescription_id=presc.presc_id).select_related('drug'))
    total = round(sum(float(i.subtotal) for i in items), 2)
    ai_result = presc.ai_check_result or '未校验'
    is_draft = presc.status == '草稿'
    # 仅草稿可提交药师审核；被驳回的处方须先返回修改、保存为草稿后再提交
    can_submit = is_draft and ai_result in ('通过', '超时降级')
    is_risk = is_draft and ai_result == '风险提示'

    consult = presc.consultation
    context = {
        'doctor': {'name': _user_name(doctor.doctor_id), 'department': doctor.department},
        'presc': {
            'id': presc.presc_id,
            'no': 'P%09d' % presc.presc_id,
            'status': presc.status,
            'badge': PRESC_BADGE.get(presc.status, 'badge-gray'),
            'ai_result': ai_result,
            'ai_comment': presc.ai_check_comment or '—',
            'audit_comment': presc.audit_conclusion or '',
            'time': _fmt_time(presc.created_at),
            'editable': presc.status in EDITABLE_STATUSES,
        },
        'patient': _patient_card(presc.patient),
        'consult': {
            'diagnosis': consult.final_diagnosis if consult else '',
            'record': consult.record_content if consult else '',
        },
        'items': [{
            'drug_name': i.drug_name,
            'spec': i.drug.specification if i.drug_id else '',
            'qty': i.qty,
            'dosage': i.dosage,
            'unit_price': float(i.unit_price),
            'subtotal': float(i.subtotal),
        } for i in items],
        'total': total,
        'can_submit': can_submit,
        'is_risk': is_risk,
    }
    return render(request, 'doctor/prescription_review.html', context)


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
        # 处方提交药师审核后，收尾仍在进行中的线上问诊并释放医生占号
        consult = presc.consultation
        if consult and consult.status == '进行中':
            consult.status = '已结束'
            consult.end_time = timezone.now()
            consult.end_request = 0
            consult.save(update_fields=['status', 'end_time', 'end_request'])
            Doctor.objects.filter(
                doctor_id=doctor.doctor_id, work_status=2).update(work_status=1)
        write_log(doctor.doctor_id, '医生', '提交处方审核',
                  target_type='prescription', target_id=presc.presc_id)

    flash.success(request, '处方已提交药师审核，可在处方列表查看流转状态。')
    return redirect('doctor:prescription_list')


# --------------------------------------------------------------------------
# 个人中心
# --------------------------------------------------------------------------
@doctor_required
def profile(request):
    """医生个人中心：查看并维护本人基础信息与执业档案（科室/职称/擅长）。"""
    doctor = _current_doctor(request)
    if not doctor:
        return redirect('accounts:staff_login', role='doctor')
    user = SysUser.objects.filter(user_id=doctor.doctor_id).first()

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        phone = request.POST.get('phone', '').strip()
        gender = request.POST.get('gender', '').strip() or None
        department = request.POST.get('department', '').strip()
        title = request.POST.get('title', '').strip() or None
        skill = request.POST.get('skill', '').strip() or None

        errors = []
        if not name:
            errors.append('姓名不能为空')
        if not phone or len(phone) != 8:
            errors.append('请输入 8 位手机号')
        elif SysUser.objects.filter(phone=phone).exclude(user_id=doctor.doctor_id).exists():
            errors.append('该手机号已被其他账号使用')
        if not department:
            errors.append('科室不能为空')
        if gender and gender not in ('男', '女', '其他'):
            gender = None

        if errors:
            return render(request, 'doctor/profile.html', {
                'nav': 'profile',
                'form': request.POST, 'errors': errors,
                'doctor': {'name': name, 'department': department,
                           'title': title or '', 'skill': skill or '',
                           'gender': gender or ''},
            })

        with transaction.atomic():
            if user:
                user.name = name
                user.phone = phone
                user.save(update_fields=['name', 'phone'])
            Doctor.objects.filter(doctor_id=doctor.doctor_id).update(
                gender=gender, department=department, title=title, skill=skill)
        write_log(doctor.doctor_id, '医生', '修改个人资料',
                  target_type='doctor', target_id=doctor.doctor_id)
        return redirect(reverse('doctor:profile') + '?saved=1')

    form = {
        'name': user.name if user else '',
        'phone': user.phone if user else '',
        'gender': doctor.gender or '',
        'department': doctor.department or '',
        'title': doctor.title or '',
        'skill': doctor.skill or '',
    }
    return render(request, 'doctor/profile.html', {
        'nav': 'profile',
        'form': form,
        'doctor': {'name': form['name'], 'department': form['department'],
                   'title': form['title'], 'skill': form['skill']},
        'saved': request.GET.get('saved') == '1',
    })

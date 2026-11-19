"""医生端视图。

当前阶段仅提供登录鉴权后的占位工作台，业务模块（接诊、对话、开方、复核）
由后续迭代接入；会话口径与职工登录保持一致：staff_id / role_type。
"""
from django.shortcuts import render, redirect
from django.urls import reverse

from core.models import SysUser, Doctor


def _current_doctor(request):
    """校验会话为医生角色且医生档案存在；返回 (user, doctor) 或 None。"""
    uid = request.session.get('staff_id')
    if not isinstance(uid, int) or request.session.get('role_type') != 2:
        return None
    doctor = Doctor.objects.filter(doctor_id=uid).first()
    if not doctor:
        return None
    user = SysUser.objects.filter(user_id=uid).first()
    return user, doctor


def dashboard(request):
    staff = _current_doctor(request)
    if not staff:
        return redirect(reverse('accounts:staff_login', args=['doctor']))
    user, doctor = staff
    return render(request, 'doctor/dashboard.html', {
        'staff_name': user.name if user else '待分配',
        'staff_dept': doctor.department,
        'staff_title': doctor.title or '医师',
        'menu': [
            ('待接诊问诊', '占位：按排队顺序接收患者挂号'),
            ('医患图文对话', '占位：查看 AI 摘要并与患者交流'),
            ('开具处方', '占位：选药、填用法用量并提交审核'),
            ('AI 摘要复核', '占位：采纳 / 修正 / 否决 AI 预问诊结论'),
            ('我的排班', '占位：查看白班 / 夜班值班安排'),
        ],
    })

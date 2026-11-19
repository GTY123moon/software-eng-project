"""管理员后台视图。

当前阶段仅提供登录鉴权后的占位后台，业务模块（账号、药品目录、排班、日志审计）
由后续迭代接入；会话口径与职工登录保持一致：staff_id / role_type。
"""
from django.shortcuts import render, redirect
from django.urls import reverse

from core.models import SysUser, Admin


def _current_admin(request):
    """校验会话为管理员角色且管理员档案存在；返回 (user, admin) 或 None。"""
    uid = request.session.get('staff_id')
    if not isinstance(uid, int) or request.session.get('role_type') != 0:
        return None
    admin = Admin.objects.filter(admin_id=uid).first()
    if not admin:
        return None
    user = SysUser.objects.filter(user_id=uid).first()
    return user, admin


def dashboard(request):
    staff = _current_admin(request)
    if not staff:
        return redirect(reverse('accounts:staff_login', args=['admin']))
    user, admin = staff
    return render(request, 'adminpanel/dashboard.html', {
        'staff_name': user.name if user else '待分配',
        'staff_account': admin.account,
        'menu': [
            ('账号管理', '占位：开通 / 停用医生、药师账号'),
            ('药品目录维护', '占位：维护药品名称、规格、单价与库存'),
            ('值班排班配置', '占位：安排医生白班 / 夜班值班表'),
            ('日志审计', '占位：操作日志与转诊日志按日志号回溯'),
        ],
    })

"""药师端视图。

当前阶段仅提供登录鉴权后的占位工作台，业务模块（审方、配药发药、目录核对）
由后续迭代接入；会话口径与职工登录保持一致：staff_id / role_type。
"""
from django.shortcuts import render, redirect
from django.urls import reverse

from core.models import SysUser, Pharmacist


def _current_pharmacist(request):
    """校验会话为药师角色且药师档案存在；返回 (user, pharmacist) 或 None。"""
    uid = request.session.get('staff_id')
    if not isinstance(uid, int) or request.session.get('role_type') != 3:
        return None
    pharmacist = Pharmacist.objects.filter(pharmacist_id=uid).first()
    if not pharmacist:
        return None
    user = SysUser.objects.filter(user_id=uid).first()
    return user, pharmacist


def dashboard(request):
    staff = _current_pharmacist(request)
    if not staff:
        return redirect(reverse('accounts:staff_login', args=['pharmacist']))
    user, pharmacist = staff
    return render(request, 'pharmacist/dashboard.html', {
        'staff_name': user.name if user else '待分配',
        'staff_type': pharmacist.pharmacist_type or '药师',
        'menu': [
            ('待审核处方', '占位：核对用药合理性、剂量与配伍禁忌'),
            ('配药发药', '占位：扣减库存、确认代煎并完成发药'),
            ('药品目录核对', '占位：查看药品库存与禁忌说明'),
        ],
    })

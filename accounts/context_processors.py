"""患者端公共上下文注入。

待查收处方：审核通过（已通过）但患者尚未选取药方式的处方。
在任意患者端页面弹出全局提醒，点击跳转处方详情。
"""
from core.models import Prescription, Doctor, SysUser


def pending_receives(request):
    """注入当前登录患者的待查收处方列表；未登录返回空列表。"""
    patient_id = request.session.get('patient_id')
    if not isinstance(patient_id, int):
        return {'pending_receives': []}

    qs = Prescription.objects.filter(
        patient_id=patient_id, status='已通过', pickup_method__isnull=True
    ).select_related('doctor').order_by('-created_at')

    items = []
    for p in qs:
        user = SysUser.objects.filter(user_id=p.doctor_id).first()
        doctor = Doctor.objects.filter(doctor_id=p.doctor_id).first()
        items.append({
            'presc_id': p.presc_id,
            'id': f"P{p.presc_id:09d}",
            'doctor': user.name if user else '待分配',
            'department': doctor.department if doctor else '',
        })
    return {'pending_receives': items}

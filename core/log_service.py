"""操作日志写入服务（core）。

关键动作（登录、注册、发起问诊、开方、审方、发药等）统一在这里落 operation_log，
保证按日志号可回溯；AI 不作为操作人写日志。
"""

from django.utils import timezone

from core.models import OperationLog


def write_log(operator_id, operator_type, action, target_type=None, target_id=None):
    """写一条操作日志。operator_id 为空（未登录匿名）时不记录。"""
    if operator_id is None:
        return None
    return OperationLog.objects.create(
        operator_id=operator_id,
        operator_type=operator_type,
        action=action,
        target_type=target_type,
        target_id=target_id,
        created_at=timezone.now(),
    )

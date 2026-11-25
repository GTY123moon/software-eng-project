"""音视频通话信令（HTTP 轮询版）。

课设单机部署下不引入 WebSocket / Redis，按问诊会话隔离，把 SDP Offer/Answer、
ICE 候选与呼叫状态落到本地 JSON 文件；医患两端前端轮询本模块提供的房间数据
完成信令交换。媒体流仍走浏览器 WebRTC P2P，这里只传递信令描述，不传音视频。

- 房间文件：``MEDIA_ROOT/_signals/room_<consult_id>.json``
- 房间为临时态：进程重启或文件清理后自动失效，不做持久化；
- 角色仅 ``patient`` / ``doctor``，由后端视图按登录身份写入，前端不可伪造。
"""

import json
import time
from pathlib import Path

from django.conf import settings

# 房间空闲超过该秒数视为失效，避免旧信令残留影响下一次呼叫
ROOM_TTL = 10 * 60


def _room_dir() -> Path:
    path = Path(settings.MEDIA_ROOT) / '_signals'
    path.mkdir(parents=True, exist_ok=True)
    return path


def _room_path(consult_id) -> Path:
    return _room_dir() / ('room_%s.json' % consult_id)


def _load(consult_id) -> dict | None:
    path = _room_path(consult_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        return None
    # 超时房间作废
    if time.time() - float(data.get('updated', 0)) > ROOM_TTL:
        return None
    return data


def _save(consult_id, data: dict) -> None:
    data['updated'] = time.time()
    _room_path(consult_id).write_text(
        json.dumps(data, ensure_ascii=False), encoding='utf-8')


def _empty_room(mode: str, initiator: str) -> dict:
    return {
        'mode': mode,
        'status': 'ringing',
        'initiator': initiator,
        'offer': None,
        'answer': None,
        'ice': {'patient': [], 'doctor': []},
    }


def post(consult_id, role: str, payload: dict) -> None:
    """写入一条信令消息。

    payload.type 取值：``call`` / ``offer`` / ``answer`` / ``ice`` / ``hangup``。
    """
    msg_type = payload.get('type')
    room = _load(consult_id)

    if msg_type in ('call', 'offer'):
        # 新呼叫或主叫变更：重置房间（保留模式与主叫）
        mode = payload.get('mode') or (room or {}).get('mode') or 'video'
        room = _empty_room(mode, role)
        if msg_type == 'offer':
            room['offer'] = {'sdp': payload.get('sdp'), 'from': role}
        _save(consult_id, room)
        return

    if room is None:
        room = _empty_room('video', role)

    if msg_type == 'answer':
        room['answer'] = {'sdp': payload.get('sdp'), 'from': role}
        room['status'] = 'connected'
    elif msg_type == 'ice':
        candidate = payload.get('candidate')
        if candidate:
            queue = room.setdefault('ice', {}).setdefault(role, [])
            if candidate not in queue:
                queue.append(candidate)
    elif msg_type == 'hangup':
        room['status'] = 'ended'
    _save(consult_id, room)


def poll(consult_id, role: str) -> dict:
    """返回该角色可见的房间信令：对端的 offer/answer/ICE，以及房间状态。"""
    room = _load(consult_id)
    if room is None:
        return {'status': 'idle', 'mode': 'video', 'offer': None, 'answer': None, 'ice': []}

    other = 'doctor' if role == 'patient' else 'patient'
    offer = room.get('offer')
    answer = room.get('answer')
    return {
        'status': room.get('status', 'idle'),
        'mode': room.get('mode', 'video'),
        'initiator': room.get('initiator'),
        'offer': offer if offer and offer.get('from') != role else None,
        'answer': answer if answer and answer.get('from') != role else None,
        'ice': room.get('ice', {}).get(other, []),
    }

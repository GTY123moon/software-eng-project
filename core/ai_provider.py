"""通用 OpenAI 兼容大模型接入层（平台无关）。

设计原则：
- 任何提供 OpenAI ``/chat/completions`` 兼容接口的平台都能接（火山方舟、智谱、
  硅基流动、阿里百炼等），切换平台只改配置，不改业务代码；
- 凭据优先取环境变量，其次读 **不进 Git** 的 ``medical_platform/local_settings.py``；
  同时兼容旧的 ``ARK_API_KEY / ARK_MODEL`` 别名，便于从方舟平滑迁移；
- 未配置 Key/模型，或接口超时、报错、返回结构异常时，一律返回 None，由调用方
  降级到本地规则，保证课设离线也能跑通流程；
- ``service_tier`` 是方舟专有字段，只在方舟域名附带，其他平台不发送，避免被拒。

业务侧只需要：
    from core.ai_provider import chat_completion, provider_label, is_configured
    content = chat_completion(
        messages=[{'role': 'system', ...}, {'role': 'user', ...}],
        temperature=0.2, max_tokens=700, timeout=20)
"""

import json
import os
import time
import urllib.error
import urllib.request

# 通用配置名 -> 旧方舟别名 -> 默认值
_FIELDS = {
    'AI_BASE_URL': ('ARK_BASE_URL',
                    'https://ark.cn-beijing.volces.com/api/v3/chat/completions'),
    'AI_API_KEY': ('ARK_API_KEY', ''),
    'AI_MODEL': ('ARK_MODEL', ''),
}

try:
    from medical_platform import local_settings as _local_settings
except ImportError:
    _local_settings = None


def _setting(name: str) -> str:
    """环境变量优先；其次 local_settings 同名属性；再回退旧 ARK 别名；最后用默认值。"""
    alias, default = _FIELDS[name]
    for source in (os.environ.get(name), os.environ.get(alias)):
        if source:
            return source
    if _local_settings is not None:
        for attr in (name, alias):
            value = getattr(_local_settings, attr, None)
            if value:
                return value
    return default


def get_provider() -> tuple[str, str, str]:
    """返回 (base_url, api_key, model)；缺失项为空串。"""
    return (_setting('AI_BASE_URL'), _setting('AI_API_KEY'), _setting('AI_MODEL'))


def is_configured() -> bool:
    """是否已配齐 Key 与模型（base_url 有默认值，不算缺失）。"""
    _, api_key, model = get_provider()
    return bool(api_key and model)


def provider_label() -> str:
    """按接口地址推断平台中文名，用于结果留痕的 source 字段。"""
    base_url, _, _ = get_provider()
    if 'bigmodel.cn' in base_url or 'z.ai' in base_url:
        return '智谱GLM'
    if 'siliconflow' in base_url:
        return '硅基流动'
    if 'dashscope' in base_url:
        return '阿里百炼'
    if 'volces.com' in base_url:
        return '豆包方舟'
    return '远程大模型'


def chat_completion(messages: list[dict], temperature: float = 0.2,
                    max_tokens: int | None = None, timeout: int = 20) -> str | None:
    """发一次 OpenAI 兼容 chat/completions，返回助手文本；失败返回 None。

    :param messages: [{'role': 'system'/'user'/'assistant', 'content': str}, ...]
    :param temperature: 采样温度，预审/分级建议用低值保证稳定
    :param max_tokens: 限制输出长度；不传则由平台默认
    :param timeout: 单次请求超时秒数
    """
    base_url, api_key, model = get_provider()
    if not api_key or not model:
        return None

    payload: dict = {
        'model': model,
        'messages': messages,
        'temperature': temperature,
    }
    if max_tokens:
        payload['max_tokens'] = max_tokens
    # service_tier 为方舟专有字段，仅方舟域名附带；其他兼容平台不发送
    if 'volces.com' in base_url:
        payload['service_tier'] = 'fast'
    # 智谱 GLM-4.7 系列默认开启思维链，问诊/审方只需简短 JSON，关闭思考以降低延迟
    if 'bigmodel.cn' in base_url or 'z.ai' in base_url:
        payload['thinking'] = {'type': 'disabled'}

    def _open(pl: dict) -> dict:
        body = json.dumps(pl, ensure_ascii=False).encode('utf-8')
        request = urllib.request.Request(
            base_url, data=body,
            headers={'Authorization': 'Bearer %s' % api_key,
                     'Content-Type': 'application/json'},
            method='POST')
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8'))

    def _open_with_retry(pl: dict) -> dict:
        try:
            return _open(pl)
        except urllib.error.HTTPError as exc:
            # fast 档位未开通时，去掉 service_tier 走常规档重试一次（仅方舟）
            if exc.code == 404 and 'service_tier' in pl:
                fallback = {k: v for k, v in pl.items() if k != 'service_tier'}
                return _open(fallback)
            # 免费模型偶发 429 排队，短暂等待后重试一次；仍失败则抛给上层降级
            if exc.code == 429:
                time.sleep(2)
                return _open(pl)
            raise

    try:
        data = _open_with_retry(payload)
        return data['choices'][0]['message']['content']
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError,
            OSError, IndexError, TypeError):
        return None

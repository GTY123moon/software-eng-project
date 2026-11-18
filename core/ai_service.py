"""
AI 风险分级服务（core）。

接入说明：
- 远程：火山引擎方舟（豆包大模型），OpenAI 兼容接口；API Key 与模型 ID 走环境变量，
  不在代码里写死。未配置 Key / 模型，或接口超时、报错时，自动降级到本地关键词规则，
  保证课设离线也能跑通流程。
- AI 仅做预问诊风险分级与病情摘要，不替代医生诊断；结果在 ai_pre_assessment 表留痕，
  由医生复核。
"""

import json
import os
import re
import urllib.request
import urllib.error

# 高危关键词：命中任一即判高危，用于本地降级规则与远程结果兜底对照
HIGH_RISK_KEYWORDS = [
    '胸痛', '胸闷', '呼吸困难', '喘憋', '咯血', '呕血', '黑便', '大出血',
    '意识模糊', '昏迷', '意识障碍', '抽搐', '休克', '紫绀', '半身不遂',
    '言语不清', '剧烈腹痛', '高热不退', '过敏性休克', '喉头水肿',
]

ARK_BASE_URL = 'https://ark.cn-beijing.volces.com/api/v3/chat/completions'


def _local_assess(symptom: str) -> dict:
    """本地关键词规则分级（无外部依赖，离线可用）。"""
    hits = [kw for kw in HIGH_RISK_KEYWORDS if kw in symptom]
    risk_level = '高危' if hits else '中低危'
    summary = symptom[:200] if symptom else '患者未填写病情描述'
    return {
        'risk_level': risk_level,
        'ai_summary': summary,
        'matched_keyword': '、'.join(hits) if hits else None,
    }


def _parse_model_json(content: str) -> dict | None:
    """从模型返回文本中提取首个 JSON 对象，容错代码块包裹。"""
    if not content:
        return None
    match = re.search(r'\{.*\}', content, re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _normalize_level(value) -> str | None:
    """把模型返回的近义分级归一化到枚举：高危 / 中低危。"""
    if not value:
        return None
    v = str(value).strip()
    if v in ('高危', '中低危'):
        return v
    if any(k in v for k in ('高危', '高风险', '急症', '紧急', '危重')):
        return '高危'
    if any(k in v for k in ('中低危', '中危', '低危', '中风险', '低风险', '普通')):
        return '中低危'
    return None


def _remote_assess(symptom: str, timeout: int = 10) -> dict | None:
    """调用方舟豆包大模型；未配置凭据或失败时返回 None。"""
    api_key = os.environ.get('ARK_API_KEY')
    model = os.environ.get('ARK_MODEL')
    if not api_key or not model:
        return None

    system_prompt = (
        '你是在线问诊平台的预问诊辅助助手，只做风险分级和病情摘要，不做疾病诊断。'
        '根据患者描述，仅输出 JSON：risk_level（高危/中低危）、'
        'ai_summary（100 字以内病情摘要）、matched_keyword（命中的高危关键词，无则空字符串）。'
    )
    payload = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': symptom},
        ],
        'temperature': 0.2,
    }
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    request = urllib.request.Request(
        ARK_BASE_URL, data=body,
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        },
        method='POST',
    )
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        data = json.loads(resp.read().decode('utf-8'))

    content = data['choices'][0]['message']['content']
    parsed = _parse_model_json(content)
    if not parsed:
        return None

    risk_level = _normalize_level(parsed.get('risk_level'))
    if not risk_level:
        # 远程结果与枚举不一致时回退本地规则
        return None
    raw_keyword = parsed.get('matched_keyword') or None
    if raw_keyword:
        raw_keyword = str(raw_keyword).replace(',', '、').replace('，', '、').strip('、 ')
        raw_keyword = raw_keyword or None
    return {
        'risk_level': risk_level,
        'ai_summary': (parsed.get('ai_summary') or symptom)[:200],
        'matched_keyword': raw_keyword,
    }


def assess_risk(symptom: str) -> dict:
    """统一入口：优先远程豆包，失败降级本地规则。

    返回：risk_level / ai_summary / matched_keyword / source（豆包方舟 或 本地规则降级）
    """
    symptom = (symptom or '').strip()
    try:
        remote = _remote_assess(symptom)
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError, OSError):
        remote = None

    if remote:
        return {**remote, 'source': '豆包方舟'}
    return {**_local_assess(symptom), 'source': '本地规则降级'}

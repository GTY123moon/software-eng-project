"""医生端 AI 辅助客户端（火山引擎方舟 / 豆包大模型，OpenAI 兼容接口）。

设计原则：
- 只做「AI 摘要复核建议」和「处方合理性预审」，最终结论始终由医生出具，
  AI 结果不会自动写入 final_diagnosis；
- 凭据优先取环境变量 ARK_API_KEY / ARK_MODEL（ARK_MODEL 填接入点 ID，如 ep-xxxx）；
  未设置环境变量时，再读 **不进 Git** 的 medical_platform/local_settings.py，
  便于本地演示又不会把密钥提交到仓库；
- 未配置或调用失败一律返回 None，由调用方降级到本地规则，保证离线也能跑；
- 与 core.ai_service 的分工：那里负责患者端预问诊风险分级，这里负责医生端的复核与开方辅助，
  两者共用同一套方舟接口，但不互相依赖，避免改动公共文件。
"""

import json
import os
import re
import urllib.error
import urllib.request

ARK_BASE_URL = 'https://ark.cn-beijing.volces.com/api/v3/chat/completions'

# 本地演示凭据从 local_settings.py 读取（该文件已在 .gitignore 中，不会进仓库）。
# 没有配置时留空，_chat_json 会直接返回 None，页面按「AI 不可用」降级。
try:
    from medical_platform.local_settings import ARK_API_KEY as LOCAL_API_KEY
    from medical_platform.local_settings import ARK_MODEL as LOCAL_MODEL
except ImportError:
    LOCAL_API_KEY = ''
    LOCAL_MODEL = ''

TIMEOUT = 60


def _credentials():
    """环境变量优先，其次本地配置；两者都没有就返回空串，调用方据此降级。"""
    api_key = os.environ.get('ARK_API_KEY') or LOCAL_API_KEY
    model = os.environ.get('ARK_MODEL') or LOCAL_MODEL
    return api_key, model


def _chat_json(system_prompt, user_prompt, timeout=TIMEOUT):
    """调一次方舟 chat/completions，从返回文本里抠出第一个 JSON 对象。失败返回 None。"""
    api_key, model = _credentials()
    if not api_key or not model:
        return None

    payload = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_prompt},
        ],
        'temperature': 0.2,
        'max_tokens': 700,
        # 本演示账号未开通 fast 档位（返回 404 ModelNotOpen），直接用常规档，
        # 避免每次请求先白跑一趟 404 重试。
    }
    def _open(pl):
        body = json.dumps(pl, ensure_ascii=False).encode('utf-8')
        req = urllib.request.Request(
            ARK_BASE_URL, data=body,
            headers={'Authorization': 'Bearer %s' % api_key,
                     'Content-Type': 'application/json'},
            method='POST')
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8'))

    try:
        try:
            data = _open(payload)
        except urllib.error.HTTPError as e:
            # fast 档位未开通时，去掉 service_tier 走常规档重试一次
            if e.code == 404 and 'service_tier' in payload:
                data = _open({k: v for k, v in payload.items() if k != 'service_tier'})
            else:
                raise
        content = data['choices'][0]['message']['content']
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError, OSError):
        return None

    if not content:
        return None
    match = re.search(r'\{.*\}', content, re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _normalize_review(value):
    """把模型返回的复核动作归一化到 采纳 / 修正 / 否决。"""
    if not value:
        return None
    text = str(value)
    if text in ('采纳', '修正', '否决'):
        return text
    if any(k in text for k in ('否决', '驳回', '不同意', '拒绝')):
        return '否决'
    if any(k in text for k in ('修正', '修改', '补充', '调整')):
        return '修正'
    if any(k in text for k in ('采纳', '同意', '通过')):
        return '采纳'
    return None


def _normalize_level(value):
    if not value:
        return None
    text = str(value)
    if text in ('高危', '中低危'):
        return text
    if any(k in text for k in ('高危', '高风险', '急症', '危重', '紧急')):
        return '高危'
    if any(k in text for k in ('中低危', '中危', '低危', '风险较低', '普通')):
        return '中低危'
    return None


def review_suggestion(patient_info, record_content, ai_summary, risk_level, matched_keyword):
    """给医生的 AI 复核建议：推荐动作 + 理由 + 更像人写的摘要 + 建议分级。

    返回 dict(recommended, reason, refined_summary, suggested_level) 或 None。
    """
    system_prompt = (
        '你是在线问诊平台的病历整理助手。任务是基于患者主诉与 AI 预问诊摘要，'
        '给接诊医生一条复核建议。只输出 JSON，字段：'
        'recommended（只能是 采纳、修正、否决 三者之一）、'
        'reason（60 字以内的中文理由）、'
        'refined_summary（100 字以内、更贴近临床病历表述的病情摘要）、'
        'suggested_level（只能是 高危 或 中低危）。'
        '不要做疾病诊断，不要开药。'
    )
    user_prompt = (
        '患者信息：%s\n主诉与采集内容：%s\nAI 预问诊摘要：%s\n当前 AI 分级：%s\n命中高危词：%s'
        % (patient_info, record_content, ai_summary, risk_level, matched_keyword or '无')
    )
    parsed = _chat_json(system_prompt, user_prompt)
    if not parsed:
        return None

    recommended = _normalize_review(parsed.get('recommended'))
    if not recommended:
        return None
    return {
        'recommended': recommended,
        'reason': str(parsed.get('reason') or '')[:120],
        'refined_summary': str(parsed.get('refined_summary') or '')[:200],
        'suggested_level': _normalize_level(parsed.get('suggested_level')) or risk_level,
    }


def prescription_review(patient_info, diagnosis, items_text, local_notes):
    """处方合理性预审：返回 dict(result, comment) 或 None。

    result 取值与种子口径一致：通过 / 风险提示。
    """
    system_prompt = (
        '你是医院药房的审方药师助手。根据患者过敏史、诊断与处方明细做合理性预审：'
        '检查剂量、重复用药、配伍禁忌、与过敏史冲突、超说明书记录用量等。'
        '只输出 JSON，字段：result（只能是 通过 或 风险提示）、'
        'comment（120 字以内中文结论，风险提示时写清具体药品与原因）。'
        '不要改写处方，不要输出处方外的建议用药。'
    )
    user_prompt = (
        '患者信息：%s\n临床诊断：%s\n处方明细：%s\n系统规则初筛：%s'
        % (patient_info, diagnosis or '未填写', items_text, local_notes or '无')
    )
    parsed = _chat_json(system_prompt, user_prompt)
    if not parsed:
        return None

    result = str(parsed.get('result') or '').strip()
    if result not in ('通过', '风险提示'):
        if any(k in result for k in ('风险', '提示', '不通过', '警告')):
            result = '风险提示'
        elif any(k in result for k in ('通过', '合理', '无问题')):
            result = '通过'
        else:
            return None
    return {
        'result': result,
        'comment': str(parsed.get('comment') or '')[:200],
    }

"""医生端 AI 辅助客户端（通用 OpenAI 兼容接口，可切换方舟 / 硅基流动 / 百炼等）。

设计原则：
- 只做「AI 摘要复核建议」和「处方合理性预审」，最终结论始终由医生出具，
  AI 结果不会自动写入 final_diagnosis；
- 凭据与模型由 core.ai_provider 统一读取（环境变量优先，其次不进 Git 的
  medical_platform/local_settings.py），切换平台只改配置，不改这里的业务逻辑；
- 未配置或调用失败一律返回 None，由调用方降级到本地规则，保证离线也能跑；
- 与 core.ai_service 的分工：那里负责患者端预问诊风险分级，这里负责医生端的
  复核与开方辅助，两者共用同一套接入层，但不互相依赖。
"""

import json
import re

from core.ai_provider import chat_completion

TIMEOUT = 20


def _chat_json(system_prompt, user_prompt, timeout=TIMEOUT):
    """调一次兼容 chat/completions，从返回文本里抠出第一个 JSON 对象。失败返回 None。"""
    content = chat_completion(
        messages=[
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_prompt},
        ],
        temperature=0.2, max_tokens=700, timeout=timeout)
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

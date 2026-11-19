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


def _as_bool(value) -> bool:
    """把模型返回的布尔值归一化；字符串 "false"/"0" 不能被当成 True。"""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    v = str(value).strip().lower()
    return v in ('true', '1', 'yes', 'y', '是')


def _remote_assess(symptom: str, timeout: int = 12) -> dict | None:
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


# ---------------------------------------------------------------------
# 多轮引导式问诊：AI 主动按维度追问，信息收集充分后再汇总分级
# ---------------------------------------------------------------------

# 兜底追问维度：主题、识别关键词、问题（逐轮即时追问，远程模型仅用于收尾摘要分级）
GUIDE_TOPICS = [
    ('部位', ['哪里', '部位', '位置', '哪个', '哪儿', '肺', '头', '腹', '胃', '肚子',
             '胸口', '胸', '腰', '背', '肩', '颈', '腿', '膝', '腕', '手', '关节', '四肢',
             '喉', '咽', '咽喉', '鼻', '牙', '眼', '耳', '皮肤', '心', '便', '口腔', '舌'],
     '哪里不舒服？能说一下具体位置吗？'),
    ('时长', ['多久', '几天', '小时', '持续', '起病', '时间', '发作', '开始', '昨夜', '今天', '昨天'],
     '这种情况持续多久了？是突然发作，还是时好时坏？'),
    ('性质', ['钝痛', '刺痛', '胀痛', '绞痛', '灼痛', '性质', '感觉', '疼', '痛', '麻', '胀',
             '酸', '痒', '晕', '咳', '喘', '痰', '恶心', '乏力', '麻木', '腹泻', '呕吐'],
     '是钝痛、刺痛、胀痛，还是灼痛？'),
    ('诱因饮食', ['吃', '饮食', '诱因', '劳累', '饭', '食物', '饮酒', '喝酒', '受凉', '运动', '熬夜', '加班'],
     '发病前后吃过什么？有没有劳累、受凉这类诱发因素？'),
    ('外伤', ['外伤', '伤', '磕碰', '撞到', '运动', '扭', '摔', '碰', '砸', '拉伤'],
     '近期有没有磕碰、外伤或剧烈运动？'),
    ('作息既往', ['作息', '睡眠', '病史', '既往', '过敏', '药', '熬夜', '慢性病', '高血压', '糖尿病'],
     '最近作息怎么样？既往有没有相关疾病、用药或药物过敏？'),
]


def guide_history_text(qa: list[dict]) -> str:
    """把问答历史拼成可读文本；qa=[{'role':'患者'/'AI','text':...}, ...]。"""
    lines = []
    for item in qa:
        role = '患者' if item.get('role') == '患者' else 'AI'
        lines.append(f"{role}：{item.get('text', '')}")
    return '\n'.join(lines)


def _local_guide_next(qa: list[dict]) -> dict:
    """本地兜底：按固定维度清单顺序推进，跳过患者已答、AI 已问过的维度。

    - 患者答案命中该维度关键词 → 视为已答，跳过；
    - AI 历史里已经问过该题 → 视为已问，跳过，避免卡在首题反复重复；
    - 都没有剩余维度时收尾。
    """
    patient_text = ' '.join(i.get('text', '') for i in qa if i.get('role') == '患者')
    ai_text = ' '.join(i.get('text', '') for i in qa if i.get('role') == 'AI')
    for _topic, keywords, question in GUIDE_TOPICS:
        if any(k in patient_text for k in keywords):
            continue  # 该维度患者已答
        if question in ai_text:
            continue  # 该题 AI 已问过，不再重复
        return {'question': question, 'done': False, 'source': '本地清单兜底'}
    return {'question': '', 'done': True, 'source': '本地清单兜底'}


def _remote_guide_next(qa: list[dict], timeout: int = 10) -> dict | None:
    """远程模型动态判断下一问；失败/解析异常返回 None。"""
    api_key = os.environ.get('ARK_API_KEY')
    model = os.environ.get('ARK_MODEL')
    if not api_key or not model:
        return None

    system_prompt = (
        '你是在线问诊预问诊助手，通过多轮对话逐步收集病情。'
        '已关注维度：症状部位、持续时间、疼痛/不适性质、诱因与饮食、外伤史、作息与既往史/过敏史。'
        '先通读问答历史，患者已经回答清楚的维度不要重复追问；只针对仍缺失的最关键一项继续提问。'
        '根据问答历史判断是否还有关键信息缺失，仅输出 JSON：'
        '{"question": "下一个要问的一句话问题（口语化）", "done": true或false, "missing": ["仍缺失维度"]}。'
        '信息已足够时 done=true、question 留空；否则 done=false，question 只问最关键的一项。'
    )
    payload = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': guide_history_text(qa) or '（尚无回答）'},
        ],
        'temperature': 0.3,
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
    done = _as_bool(parsed.get('done'))
    question = str(parsed.get('question') or '').strip()
    if not done and not question:
        return None
    return {'question': question, 'done': done, 'source': '豆包方舟'}


def next_guide_question(qa: list[dict]) -> dict:
    """逐轮下一问：默认走本地清单即时出题。

    说明：当前接入点模型每轮生成引导问题需近 10 秒，实时阻塞会拖慢对话，
    因此逐轮追问不调远程；远程模型保留在收尾阶段做风险分级与病情摘要
    （见 assess_risk）。_remote_guide_next 保留备用，将来接入点提速后可切回。

    返回：question / done / source
    """
    return _local_guide_next(qa)

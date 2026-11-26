"""AI 风险分级服务单元测试（core）。

纯 SimpleTestCase，不连接数据库；远程大模型一律 mock，只验证本地规则、
远程解析与降级逻辑。运行：
    python manage.py test core -v 2
"""
from unittest.mock import patch

from django.test import SimpleTestCase

from core import ai_service


@patch('core.ai_service.chat_completion', return_value=None)
class LocalKeywordRuleTests(SimpleTestCase):
    """无远程时的本地关键词规则（强制远程不可用）。"""

    def test_high_risk_keyword_matched(self, _mock):
        result = ai_service.assess_risk('我胸痛得厉害，喘不上气')
        self.assertEqual(result['risk_level'], '高危')
        self.assertIn('胸痛', result['matched_keyword'])
        self.assertEqual(result['source'], '本地规则降级')

    def test_mid_low_risk_without_keyword(self, _mock):
        result = ai_service.assess_risk('轻微咳嗽两天，无其他症状')
        self.assertEqual(result['risk_level'], '中低危')
        self.assertIsNone(result['matched_keyword'])
        self.assertEqual(result['source'], '本地规则降级')

    def test_empty_symptom_does_not_crash(self, _mock):
        result = ai_service.assess_risk('')
        self.assertEqual(result['risk_level'], '中低危')
        self.assertIn('未填写', result['ai_summary'])


class RemoteAssessTests(SimpleTestCase):
    """远程优先：成功用远程，失败/异常降级本地。"""

    @patch('core.ai_service.chat_completion')
    def test_remote_success_uses_remote_result(self, mock_chat):
        mock_chat.return_value = (
            '{"risk_level": "高危", "ai_summary": "胸痛待查，建议急诊", '
            '"matched_keyword": "胸痛"}')
        result = ai_service.assess_risk('胸口疼了一晚上')
        self.assertEqual(result['risk_level'], '高危')
        self.assertEqual(result['ai_summary'], '胸痛待查，建议急诊')
        self.assertEqual(result['source'], ai_service.provider_label())

    @patch('core.ai_service.chat_completion', return_value=None)
    def test_remote_unavailable_falls_back_local(self, _mock):
        result = ai_service.assess_risk('突然剧烈腹痛')
        self.assertEqual(result['risk_level'], '高危')
        self.assertIn('剧烈腹痛', result['matched_keyword'])
        self.assertEqual(result['source'], '本地规则降级')

    @patch('core.ai_service.chat_completion')
    def test_remote_malformed_json_falls_back_local(self, mock_chat):
        mock_chat.return_value = '这不是合法JSON，分级：高危'
        result = ai_service.assess_risk('胸痛胸闷')
        self.assertEqual(result['risk_level'], '高危')
        self.assertEqual(result['source'], '本地规则降级')

    @patch('core.ai_service.chat_completion')
    def test_remote_unknown_level_falls_back_local(self, mock_chat):
        mock_chat.return_value = '{"risk_level": "未知级别", "ai_summary": "x"}'
        result = ai_service.assess_risk('胸痛')
        self.assertEqual(result['risk_level'], '高危')
        self.assertEqual(result['source'], '本地规则降级')


class NormalizeAndParseTests(SimpleTestCase):
    """分级归一化与模型 JSON 解析。"""

    def test_normalize_level_aliases(self):
        self.assertEqual(ai_service._normalize_level('高风险'), '高危')
        self.assertEqual(ai_service._normalize_level('危重急症'), '高危')
        self.assertEqual(ai_service._normalize_level('普通'), '中低危')
        self.assertEqual(ai_service._normalize_level('低风险'), '中低危')
        self.assertIsNone(ai_service._normalize_level('随便说说'))

    def test_parse_model_json_plain_and_code_block(self):
        self.assertEqual(ai_service._parse_model_json('{"a": 1}')['a'], 1)
        self.assertEqual(
            ai_service._parse_model_json('```json\n{"a": 2}\n```')['a'], 2)
        self.assertIsNone(ai_service._parse_model_json('没有任何 JSON'))

    def test_high_risk_keyword_list_not_empty(self):
        self.assertGreaterEqual(len(ai_service.HIGH_RISK_KEYWORDS), 15)
        self.assertIn('胸痛', ai_service.HIGH_RISK_KEYWORDS)

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
AI 链路连通性自测（可复用；不读取、不打印真实 Key）。

运行方式：在工程根目录执行
    python check_ai.py

配置三要素（任选一处；系统环境变量优先于本地文件）：
  - 环境变量：AI_BASE_URL / AI_API_KEY / AI_MODEL
  - medical_platform/local_settings.py（已被 .gitignore 排除，不进仓库）

未配置 Key，或接口超时 / 报错 / 返回结构异常时，三条业务链路会自动降级到本地规则，
流程仍可离线跑通；本脚本逐条标注当前走的是「远程大模型」还是「本地规则降级」。
"""

import os
import sys
import django

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
os.chdir(BASE_DIR)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "medical_platform.settings")
django.setup()

from core import ai_provider, ai_service
from doctor import ai_client


def _masked(key: str) -> str:
    return "已填写（长度 %d）" % len(key) if key else "空"


def main() -> None:
    base_url, api_key, model = ai_provider.get_provider()
    print("=" * 60)
    print("AI 接入配置自检")
    print("=" * 60)
    print("平台识别：%s" % ai_provider.provider_label())
    print("接口地址：%s" % base_url)
    print("模型 ID ：%s" % (model or "（未配置）"))
    print("API Key ：%s" % _masked(api_key))
    print("是否配齐：%s" % ("是" if ai_provider.is_configured() else "否（当前离线降级本地规则）"))
    print("-" * 60)

    # 1) 患者端：风险分级 + 病情摘要（assess_risk 远程优先，失败回退本地关键词规则）
    print("[1] 患者端 · 风险分级与病情摘要")
    case = "头痛两天，轻微鼻塞，精神尚可，无外伤"
    try:
        r = ai_service.assess_risk(case)
        src = r.get("source", "")
        tag = "远程" if src != "本地规则降级" else "本地降级"
        print("    来源：%s" % src)
        print("    分级：%s" % r.get("risk_level"))
        print("    摘要：%s" % (r.get("ai_summary") or ""))
        print("    结论：%s 链路可用" % tag)
    except Exception as exc:  # noqa: BLE001 - 自测脚本需兜底打印任意异常
        print("    异常：%r" % exc)
    print("-" * 60)

    # 2) 医生端：AI 摘要复核建议（采纳 / 修正 / 否决）
    print("[2] 医生端 · 摘要复核建议")
    try:
        s = ai_client.review_suggestion(
            "男，28岁，无过敏史", "头痛两天伴轻微鼻塞",
            "头痛待查，上感可能", "中低危", "")
        if s:
            print("    远程建议：推荐「%s」· %s" % (s.get("recommended"), s.get("reason")))
        else:
            print("    远程未返回（未配置 / 超时 / 异常），视图层已回退人工复核")
    except Exception as exc:  # noqa: BLE001
        print("    异常：%r" % exc)
    print("-" * 60)

    # 3) 医生端：处方草稿 AI 初审（通过 / 风险提示）
    print("[3] 医生端 · 处方草稿预审")
    try:
        p = ai_client.prescription_review(
            "男，28岁，无过敏史", "上呼吸道感染",
            "感冒灵颗粒 1盒 冲服", "本地规则：未见禁忌")
        if p:
            print("    远程结论：%s · %s" % (p.get("result"), p.get("comment")))
        else:
            print("    远程未返回（未配置 / 超时 / 异常），结果页标记「超时降级」")
    except Exception as exc:  # noqa: BLE001
        print("    异常：%r" % exc)
    print("=" * 60)

    if not ai_provider.is_configured():
        print("提示：当前未配齐 Key，三条链路均为本地规则降级。")
        print("填好 AI_API_KEY 后重跑本脚本，对应条目应显示远程平台来源。")
    else:
        print("配置已就绪；若远程条目仍返回 None，请核对接口地址、模型 ID 与网络。")


if __name__ == "__main__":
    main()

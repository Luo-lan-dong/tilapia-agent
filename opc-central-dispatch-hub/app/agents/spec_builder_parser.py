"""Spec Builder Agent 的对话判定标签解析（ADR-046）。"""

import logging

from app.agents.spec_builder_prompts import (
    _DECISION_CLOSE,
    _DECISION_OPEN,
    DECISION_NEED_INFO,
    DECISION_READY,
)

logger = logging.getLogger(__name__)


def parse_decision(content: str) -> tuple[str, str]:
    """解析对话判定轮输出的 ``<decision>`` 标签。

    Args:
        content: 判定轮模型的完整正文输出。

    Returns:
        ``(decision, reply_text)`` 二元组：``decision`` 为
        ``need_info`` / ``ready``；``reply_text`` 为剥离标签与思考标签后
        用户可见的正文。

    兜底策略：标签缺失或取值无法识别时一律视为 ``need_info``（安全侧：
    宁可多一轮追问，不可误判信息足够而生成文档）。
    """
    text = content.strip()
    # 兼容部分模型把推理内容包裹在 <think> 标签内的情况
    if "<think>" in text and "</think>" in text:
        text = text.split("</think>", 1)[-1].strip()

    if _DECISION_OPEN in text and _DECISION_CLOSE in text:
        before, rest = text.split(_DECISION_OPEN, 1)
        decision_raw, _, after = rest.partition(_DECISION_CLOSE)
        decision = decision_raw.strip().lower()
        reply = f"{before}{after}".strip()
        if decision in (DECISION_NEED_INFO, DECISION_READY):
            return decision, reply
        logger.warning(
            "spec_builder_unknown_decision",
            extra={"decision": decision[:50], "fallback": DECISION_NEED_INFO},
        )
        return DECISION_NEED_INFO, reply

    logger.warning(
        "spec_builder_decision_missing",
        extra={"fallback": DECISION_NEED_INFO, "snippet": text[:200]},
    )
    return DECISION_NEED_INFO, text

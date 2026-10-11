"""环境上下文服务（ADR-042）：日期时间 + 集群概况的环境块生成与 Baseline/增量注入。

对齐探查档案章节四（tilacode System Context / Context Epoch）的轻量复刻：

- **环境块**：日期时间（粒度到小时，跨小时才变化）+ 在线节点概况 + 对话模型；
- **Baseline 不可变**：首轮环境块写入 system 并存快照，会话内 system 使用快照
  版本（而非实时），保证 provider cache 前缀稳定；
- **变化增量**：实时块与快照不同时，生成一条 `<environment-update>` 增量
  system 消息追加到历史尾部，并更新快照；环境是可再生事实，增量不落库。
"""

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_node import AgentNode

logger = logging.getLogger(__name__)

#: 环境块包裹标签
ENVIRONMENT_TAG = "environment"
ENVIRONMENT_UPDATE_TAG = "environment-update"

_WEEKDAY_NAMES = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")


def build_environment_block(
    cluster_summary: str | None,
    model_name: str | None,
    now: datetime | None = None,
) -> str:
    """生成环境块文本。

    Args:
        cluster_summary: 集群概况行（调用方查询生成）；None/空时省略该行。
        model_name: 当前对话模型标识；None/空时省略该行。
        now: 当前时间（测试注入用）；缺省取本地现在时刻。

    Returns:
        ``<environment>`` 包裹的多行环境块。
    """
    current = now or datetime.now().astimezone()
    weekday = _WEEKDAY_NAMES[current.weekday()]
    lines = [
        f"当前时间：{current.strftime('%Y-%m-%d')}（{weekday}）"
        f"{current.strftime('%H时')}（{current.tzname() or '本地时区'}）",
    ]
    if cluster_summary:
        lines.append(f"集群概况：{cluster_summary}")
    if model_name:
        lines.append(f"对话模型：{model_name}")
    body = "\n".join(lines)
    return f"<{ENVIRONMENT_TAG}>\n{body}\n</{ENVIRONMENT_TAG}>"


async def query_cluster_summary(session: AsyncSession) -> str | None:
    """查询在线节点概况（starting/idle/busy 按 agent_type 聚合）；失败返回 None。"""
    try:
        result = await session.execute(
            select(
                AgentNode.agent_type,
                AgentNode.status,
                func.count(AgentNode.id),
            )
            .where(AgentNode.status.in_(["starting", "idle", "busy"]))
            .group_by(AgentNode.agent_type, AgentNode.status)
        )
        rows = result.all()
    except Exception:
        logger.warning(
            "environment_cluster_summary_failed",
            extra={"event": "environment_cluster_summary_failed"},
            exc_info=True,
        )
        return None
    if not rows:
        return "0 个节点在线"
    by_type: dict[str, dict[str, int]] = {}
    for agent_type, status, count in rows:
        by_type.setdefault(str(agent_type), {})[str(status)] = int(count)
    total = sum(sum(statuses.values()) for statuses in by_type.values())
    parts = [
        f"{agent_type} 类型 {sum(statuses.values())} 个（"
        + " / ".join(f"{status} {count}" for status, count in sorted(statuses.items()))
        + "）"
        for agent_type, statuses in sorted(by_type.items())
    ]
    return f"{total} 个节点在线——" + "；".join(parts)


def build_environment_update_message(block: str) -> dict[str, Any]:
    """构造环境变化的增量 system 消息（追加到历史尾部，不落库）。"""
    return {
        "role": "system",
        "content": f"<{ENVIRONMENT_UPDATE_TAG}>\n{block}\n</{ENVIRONMENT_UPDATE_TAG}>",
    }

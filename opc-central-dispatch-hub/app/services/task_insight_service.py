"""任务只读洞察：用量曲线、动作时间线与异常告警（ADR-061 M5c）。

**已裁决（用户 2026-09-29）**
- **Q12 镜像粒度 = 动作级摘要时间线**：只记阶段/工具/命令摘要/用量点/校验结果，
  **不存原始工具输出全文**（体积与隐私最小；失败输出尾部已在验收报告里）。
- **Q13 告警 = 按需计算**：任务详情页实时算就出，仪表盘聚合复用现有 alerts 机制，不引入外部通知。

**数据源（全部是既有落库，无需新增节点协议）**
- `tool_call_logs(kind='llm_usage')`：每个统计点一行（M5a），含 input/output/cache_read/cache_write/
  hit_rate/files_touched/tool；
- `tool_call_logs` 其它 kind：`dispatch`（派发）、`steering`（中枢发言）、`agent_task_event`
  （开始/完成/失败/验收阻断/`tool_activity` 动作留痕）。

**告警规则（D5.3 基线，样本不足时不告警以免噪音）**
1. 缓存命中率跌破 **95.2%** 基线（ADR-060 实测基线）；
2. **连续 ≥3 次同签名动作**（工具 + 命令摘要相同 → 疑似打转）；
3. **≥4 个用量点无文件写入**（`files_touched` 全 0 → 长时间没产出）。
"""

import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tool_call_log import ToolCallLog, ToolCallLogKind

logger = logging.getLogger(__name__)

#: 缓存命中率基线（ADR-060 实测 95.2%）；低于它即告警
CACHE_HIT_BASELINE = 0.952
#: 触发「同签名重复」告警的连续次数
REPEATED_ACTIVITY_LIMIT = 3
#: 触发「长时间无文件写入」告警的连续用量点数
NO_FILE_WRITE_STEPS = 4
#: 命中率告警的最小样本数（样本太少不告警，避免噪音）
MIN_HIT_RATE_SAMPLES = 5
#: 单次返回的最大时间线条数（防超长任务拖爆前端）
MAX_TIMELINE_ENTRIES = 300

#: 事件 kind/name → 人类可读标题（时间线展示）
_KIND_TITLES: dict[str, str] = {
    "dispatch": "派发到节点",
    "steering": "中枢发言",
    "compaction": "上下文压缩",
    "skill_call": "中枢技能调用",
    "skill_result": "中枢技能返回",
    "llm_usage": "用量统计点",
}
_EVENT_TITLES: dict[str, str] = {
    "task_started": "开始执行",
    "task_completed": "完成上报",
    "task_failed": "失败上报",
    "task_verify_blocked": "验收阻断",
    "tool_activity": "执行动作",
    "verify_decision_accept": "用户接受现状",
    "verify_decision_retry": "用户要求继续修复",
    "task_steer_sent": "中枢定向发言",
}


def _loads(value: Any) -> dict[str, Any]:
    """把 Text 列里的 JSON 字符串解析为 dict（兼容已是 dict 的情况）。"""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _as_int(value: Any) -> int:
    """宽松取整（非数值/负数按 0）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value) if value > 0 else 0


def _as_float(value: Any) -> float | None:
    """宽松取浮点（非法返回 None）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


async def _fetch_logs(
    session: AsyncSession, task_id: Any, *, kinds: tuple[str, ...] | None = None
) -> list[ToolCallLog]:
    """按时间升序取任务的事件行。"""
    stmt = select(ToolCallLog).where(ToolCallLog.task_id == str(task_id))
    if kinds:
        stmt = stmt.where(ToolCallLog.kind.in_(kinds))
    result = await session.execute(stmt.order_by(ToolCallLog.created_at.asc()))
    return list(result.scalars().all())


def usage_points(rows: list[ToolCallLog]) -> list[dict[str, Any]]:
    """把 `llm_usage` 行转为用量曲线点（按时间升序）。"""
    points: list[dict[str, Any]] = []
    for row in rows:
        summary = _loads(row.result_summary)
        input_tokens = _as_int(summary.get("input_tokens"))
        cache_read = _as_int(summary.get("cache_read_tokens"))
        denominator = input_tokens + cache_read
        points.append(
            {
                "at": row.created_at.isoformat() if isinstance(row.created_at, datetime) else None,
                "input_tokens": input_tokens,
                "output_tokens": _as_int(summary.get("output_tokens")),
                "reasoning_tokens": _as_int(summary.get("reasoning_tokens")),
                "cache_read_tokens": cache_read,
                "cache_write_tokens": _as_int(summary.get("cache_write_tokens")),
                "cache_hit_rate": round(cache_read / denominator, 4) if denominator else 0.0,
                "files_touched": _as_int(summary.get("files_touched")),
                "cost": _as_float(summary.get("cost")),
                "tool": _loads(row.args_summary).get("tool"),
            }
        )
    return points


def summarize_usage(points: list[dict[str, Any]]) -> dict[str, Any]:
    """汇总用量：总 tokens、总缓存命中率与基线对比。"""
    total_input = sum(point["input_tokens"] for point in points)
    total_output = sum(point["output_tokens"] for point in points)
    total_cache_read = sum(point["cache_read_tokens"] for point in points)
    denominator = total_input + total_cache_read
    hit_rate = round(total_cache_read / denominator, 4) if denominator else 0.0
    return {
        "steps": len(points),
        "input_tokens": total_input,
        "output_tokens": total_output,
        "cache_read_tokens": total_cache_read,
        "cache_write_tokens": sum(point["cache_write_tokens"] for point in points),
        "cache_hit_rate": hit_rate,
        "cache_hit_baseline": CACHE_HIT_BASELINE,
        "hit_rate_below_baseline": bool(denominator and hit_rate < CACHE_HIT_BASELINE),
        "files_touched": sum(point["files_touched"] for point in points),
    }


def _activity_signature(summary: dict[str, Any]) -> str | None:
    """动作签名（工具 + 命令摘要）；缺工具名时返回 None（不参与重复判定）。"""
    tool = summary.get("tool")
    if not isinstance(tool, str) or not tool:
        return None
    message = summary.get("message")
    return f"{tool}\u0000{message if isinstance(message, str) else ''}"


def compute_alerts(
    points: list[dict[str, Any]], activities: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """按三条既定规则计算告警（纯函数；样本不足时返回空）。

    Args:
        points: `usage_points()` 的输出（时间升序）。
        activities: 动作摘要列表（时间升序），每项含 ``tool``/``message``/``at``。
    """
    alerts: list[dict[str, Any]] = []

    # 规则 1：缓存命中率跌破基线（需足够样本）
    if len(points) >= MIN_HIT_RATE_SAMPLES:
        summary = summarize_usage(points)
        if summary["hit_rate_below_baseline"]:
            alerts.append(
                {
                    "code": "cache_hit_below_baseline",
                    "level": "warning",
                    "title": "缓存命中率跌破基线",
                    "detail": (
                        f"当前 {summary['cache_hit_rate']:.1%}，低于基线 "
                        f"{CACHE_HIT_BASELINE:.1%}（样本 {summary['steps']} 个统计点）"
                    ),
                }
            )

    # 规则 2：连续同签名动作（疑似打转）
    run_signature: str | None = None
    run_length = 0
    repeated: list[str] = []
    for item in activities:
        signature = _activity_signature(item)
        if signature is None:
            run_signature, run_length = None, 0
            continue
        if signature == run_signature:
            run_length += 1
        else:
            run_signature, run_length = signature, 1
        if run_length == REPEATED_ACTIVITY_LIMIT:
            tool = item.get("tool")
            message = item.get("message")
            repeated.append(f"{tool}：{message or '（无摘要）'}")
    if repeated:
        alerts.append(
            {
                "code": "repeated_activity",
                "level": "warning",
                "title": "疑似原地打转：连续重复同一动作",
                "detail": (
                    f"连续 {REPEATED_ACTIVITY_LIMIT} 次相同动作：" + "；".join(repeated[:3])
                ),
            }
        )

    # 规则 3：连续多个用量点无文件写入（长时间没产出）
    tail = points[-NO_FILE_WRITE_STEPS:]
    if len(tail) == NO_FILE_WRITE_STEPS and all(point["files_touched"] == 0 for point in tail):
        alerts.append(
            {
                "code": "no_file_write",
                "level": "warning",
                "title": "长时间没有文件产出",
                "detail": f"最近 {NO_FILE_WRITE_STEPS} 个统计点均无文件变更，可能在做无效尝试",
            }
        )

    return alerts


def build_timeline(rows: list[ToolCallLog]) -> list[dict[str, Any]]:
    """把事件行转为人类可读时间线条目（时间升序，最多 ``MAX_TIMELINE_ENTRIES`` 条）。"""
    entries: list[dict[str, Any]] = []
    for row in rows:
        summary = _loads(row.result_summary)
        args = _loads(row.args_summary)
        if row.kind == ToolCallLogKind.LLM_USAGE.value:
            points = usage_points([row])
            point = points[0]
            title = "用量统计点"
            detail = (
                f"输入 {point['input_tokens']} / 输出 {point['output_tokens']} / "
                f"缓存命中 {point['cache_hit_rate']:.1%}"
                + (f" · {point['tool']}" if point["tool"] else "")
            )
        elif row.kind == ToolCallLogKind.AGENT_TASK_EVENT.value:
            title = _EVENT_TITLES.get(row.name, row.name)
            if row.name == "tool_activity":
                tool = summary.get("tool") or "（未知工具）"
                title = f"执行动作：{tool}"
                detail = str(summary.get("message") or summary.get("stage") or "")
            else:
                detail = str(
                    summary.get("reason")
                    or summary.get("verify_status")
                    or summary.get("status")
                    or ""
                )
        elif row.kind == ToolCallLogKind.STEERING.value:
            title = _KIND_TITLES["steering"]
            detail = str(args.get("text") or summary.get("text") or "")[:200]
        else:
            title = _KIND_TITLES.get(row.kind, row.kind)
            detail = str(summary.get("dispatch_type") or summary.get("agent_id") or "")
        entries.append(
            {
                "at": row.created_at.isoformat() if isinstance(row.created_at, datetime) else None,
                "kind": row.kind,
                "name": row.name,
                "title": title,
                "detail": detail,
                "status": row.status,
            }
        )
    return entries[-MAX_TIMELINE_ENTRIES:]


async def collect_task_insight(session: AsyncSession, task_id: Any) -> dict[str, Any]:
    """一次性取回任务洞察（用量点/汇总/告警/时间线），供只读端点使用。"""
    rows = await _fetch_logs(session, task_id)
    usage_rows = [row for row in rows if row.kind == ToolCallLogKind.LLM_USAGE.value]
    activity_rows = [
        row
        for row in rows
        if row.kind == ToolCallLogKind.AGENT_TASK_EVENT.value and row.name == "tool_activity"
    ]
    points = usage_points(usage_rows)
    activities = [
        {
            "at": row.created_at.isoformat() if isinstance(row.created_at, datetime) else None,
            "tool": _loads(row.result_summary).get("tool"),
            "message": _loads(row.result_summary).get("message"),
        }
        for row in activity_rows
    ]
    return {
        "points": points,
        "summary": summarize_usage(points),
        "alerts": compute_alerts(points, activities),
        "timeline": build_timeline(rows),
    }

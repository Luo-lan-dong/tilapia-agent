"""节点日志拉取技能（ADR-047）：模型对话中自助拉取开发节点本地日志。

复用中枢 ``request_node_logs``（下发 hub.log_fetch → 节点读本地日志文件
按级别过滤 + tail 截取 → 分块回传，同步等待最长 45s）。典型场景：节点上
任务失败、节点报错或疑似异常退出后，用户问「节点刚才怎么了」，模型拉取
节点日志定位原因。
"""

from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import tool_call_log_service
from app.skills.base import Skill, SkillContext, SkillResult

#: 返回给模型的日志文本上限（防爆上下文；超出取尾部）
_CONTENT_MAX = 6000


class FetchNodeLogsSkill(Skill):
    """按需拉取指定节点的本地日志。"""

    name = "fetch_node_logs"
    display_name = "节点日志拉取"
    description = (
        "拉取某个开发节点的本地日志（最近 N 行，可按级别过滤），用于排查节点上"
        "任务失败、节点报错或异常退出等原因。需要提供节点 client_id（可从节点"
        "列表技能或用户消息获得）；节点不在线或为旧版本时会返回明确错误。"
        "注意：仅拉取节点本地日志文件，不含中枢侧事件（那用 query_trace）。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "client_id": {
                "type": "string",
                "description": "必填，节点 client_id（节点列表/任务详情中可获取）",
            },
            "tail_lines": {
                "type": "integer",
                "description": "可选，回传最近行数（默认 100，上限 2000）",
            },
            "level": {
                "type": "string",
                "enum": ["debug", "info", "warning", "error"],
                "description": "可选，日志级别过滤（缺省=全部）",
            },
        },
        "required": ["client_id"],
    }
    returns = "节点日志文本（时间正序，超长截尾）；或失败原因（离线/超时/旧版本）"

    async def execute(
        self,
        session: AsyncSession,
        context: SkillContext,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """下发日志拉取指令并等待节点回传，产出摘要结果事件。"""
        client_id = str(context.args.get("client_id") or "").strip()
        if not client_id:
            yield {
                "event": "_result",
                "data": SkillResult(
                    success=False,
                    summary="缺少 client_id 参数：请先通过节点列表或询问用户获得目标节点 ID",
                    data={"count": 0},
                ),
            }
            return

        tail_raw = context.args.get("tail_lines")
        try:
            tail_lines = max(1, min(2000, int(tail_raw))) if tail_raw else 100
        except (TypeError, ValueError):
            tail_lines = 100
        level_raw = str(context.args.get("level") or "").strip().lower() or None

        try:
            content = await tool_call_log_service.request_node_logs(
                client_id,
                tail_lines=tail_lines,
                level=level_raw,
            )
        except tool_call_log_service.NodeLogFetchError as exc:
            hints = {
                "offline": "节点不在线：可提示用户检查节点进程/网络，恢复后重试。",
                "timeout": "节点 45s 内未回传：节点可能为旧版本（需更新整体包）或日志过大。",
                "send_failed": "指令下发失败：节点可能刚断线，可稍后重试。",
                "agent_error": "节点读取日志时出错，详见错误信息。",
            }
            summary = f"节点日志拉取失败（{client_id[:8]}）：{exc}。{hints.get(exc.code, '')}"
            yield {
                "event": "_result",
                "data": SkillResult(success=False, summary=summary, data={"count": 0}),
            }
            return

        truncated = False
        if len(content) > _CONTENT_MAX:
            content = content[-_CONTENT_MAX:]
            truncated = True
        header = (
            f"节点 {client_id[:8]} 最近日志（级别={level_raw or '全部'}，"
            f"请求 {tail_lines} 行，共 {len(content)} 字符"
            + ("，超长已截尾保留最新" if truncated else "")
            + "）：\n"
        )
        yield {
            "event": "_result",
            "data": SkillResult(
                success=True,
                summary=header + content,
                data={"count": content.count("\n") + 1, "truncated": truncated},
            ),
        }

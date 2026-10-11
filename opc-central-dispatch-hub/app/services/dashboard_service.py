"""集群仪表盘聚合服务（ADR-038）。

统计指标、告警推导与交付物聚合的全部业务逻辑；路由层只做参数转发。

性能约束：开发库为远程 PostgreSQL，单次往返延迟高（网络抖动时秒级），
因此本服务刻意合并查询——metrics 全程 4 次往返、alerts 2 次往返，
聚合计算全部放在 Python 侧完成（表规模为几十行，全量取回无压力）。
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_node import AgentNode, NodeStatus
from app.models.llm_usage_record import LlmUsageRecord
from app.models.system_settings import SystemSettings
from app.models.task import Task, TaskStatus
from app.schemas.dashboard import (
    AlertItem,
    AlertListResponse,
    ClusterMetricsResponse,
    DeliverableItem,
    DeliverableListResponse,
)
from app.services.artifact_service import artifact_service

logger = logging.getLogger(__name__)

#: pending 任务视为「排队超时」的阈值（分钟）
PENDING_ALERT_THRESHOLD_MINUTES = 10
#: 交付物聚合扫描的最近任务数上限
DELIVERABLE_SCAN_TASKS = 50
#: 每类任务告警的最大条数（与逐类查询时期的 limit 语义一致）
_ALERT_TASK_CAP = 20

#: 告警级别排序键（danger 最前）
_LEVEL_ORDER = {"danger": 0, "warning": 1, "info": 2}

#: 扩展名 → 交付物类型映射
_EXTENSION_TYPES: dict[str, str] = {
    **dict.fromkeys(("png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "ico"), "image"),
    **dict.fromkeys(("mp4", "mov", "avi", "mkv", "webm", "flv"), "video"),
    **dict.fromkeys(("zip", "tar", "gz", "rar", "7z"), "zip"),
    **dict.fromkeys(
        ("md", "txt", "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "csv", "json"),
        "doc",
    ),
    **dict.fromkeys(
        (
            "py",
            "js",
            "ts",
            "tsx",
            "jsx",
            "go",
            "java",
            "rs",
            "html",
            "css",
            "sh",
            "sql",
            "yml",
            "yaml",
        ),
        "code",
    ),
}

#: 在线节点状态集合
_ONLINE_STATUSES = {
    NodeStatus.STARTING.value,
    NodeStatus.IDLE.value,
    NodeStatus.BUSY.value,
}


def _file_type_of(path: str) -> str:
    """按扩展名映射交付物文件类型。"""
    suffix = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return _EXTENSION_TYPES.get(suffix, "other")


def _start_of_today_utc() -> datetime:
    """本地零点（服务器时区）对应的 UTC 时刻，用于「今日」token 统计。"""
    now_local = datetime.now().astimezone()
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_local.astimezone(UTC)


def _cpu_percent_of(snapshot: Any) -> float | None:
    """从节点 metrics 快照提取 CPU 百分比；结构不符返回 None。"""
    if not isinstance(snapshot, dict):
        return None
    cpu = snapshot.get("cpu")
    if isinstance(cpu, dict) and isinstance(cpu.get("percent"), (int, float)):
        return float(cpu["percent"])
    return None


class DashboardService:
    """仪表盘聚合业务能力封装。"""

    async def get_metrics(self, session: AsyncSession) -> ClusterMetricsResponse:
        """聚合仪表盘 8 项统计指标（4 次往返，见模块 docstring）。"""
        task_counts = dict(
            (await session.execute(select(Task.status, func.count()).group_by(Task.status))).all()
        )

        # 节点表全量取回（几十行）：一次查询同时得到计数、CPU 均值与离线告警行
        nodes = (await session.execute(select(AgentNode))).scalars().all()
        node_counts: dict[str, int] = {}
        cpu_values: list[float] = []
        offline_nodes: list[AgentNode] = []
        for node in nodes:
            node_counts[node.status] = node_counts.get(node.status, 0) + 1
            if node.status in _ONLINE_STATUSES:
                cpu = _cpu_percent_of(node.metrics_snapshot)
                if cpu is not None:
                    cpu_values.append(cpu)
            elif node.status == NodeStatus.OFFLINE.value:
                offline_nodes.append(node)

        token_total = await session.scalar(
            select(func.coalesce(func.sum(LlmUsageRecord.total_tokens), 0)).where(
                LlmUsageRecord.created_at >= _start_of_today_utc()
            )
        )

        alerts = await self._collect_alerts(session, offline_nodes=offline_nodes)
        total_nodes = len(nodes)
        return ClusterMetricsResponse(
            total_tasks=sum(task_counts.values()),
            running_tasks=task_counts.get(TaskStatus.RUNNING.value, 0),
            completed_tasks=task_counts.get(TaskStatus.COMPLETED.value, 0),
            queued_tasks=task_counts.get(TaskStatus.PENDING.value, 0),
            online_nodes=total_nodes - node_counts.get(NodeStatus.OFFLINE.value, 0),
            total_nodes=total_nodes,
            cluster_load_percent=(
                round(sum(cpu_values) / len(cpu_values), 1) if cpu_values else None
            ),
            alert_count=len(alerts),
            token_consumed_today=int(token_total or 0),
        )

    async def get_alerts(self, session: AsyncSession, limit: int = 50) -> AlertListResponse:
        """推导告警列表（ADR-038 规则，纯后端推导零协议改动）。

        - danger：节点离线
        - warning：失败任务、待确认（review）任务
        - info：创建超 10 分钟仍 pending 的排队任务

        已读水位线（一键已读）：created_at 早于等于 ``system_settings.
        alerts_read_at`` 的告警不再返回；此后新产生（时间更新）的告警正常出现。
        """
        items = await self._collect_alerts(session)
        # 稳定排序两段式：先同级内按时间倒序，再按级别升序（danger 最前）
        items.sort(key=lambda a: a.created_at, reverse=True)
        items.sort(key=lambda a: _LEVEL_ORDER[a.level])
        return AlertListResponse(total=len(items), items=items[:limit])

    async def mark_all_alerts_read(self, session: AsyncSession) -> datetime:
        """一键已读：将告警已读水位线推进到当前时刻并落库。

        Args:
            session: 异步数据库会话。

        Returns:
            写入的已读水位线（UTC）。
        """
        read_at = datetime.now(UTC)
        settings = await self._get_or_create_settings(session)
        settings.alerts_read_at = read_at
        await session.commit()
        logger.info(
            "dashboard_alerts_read_all",
            extra={"event": "dashboard_alerts_read_all", "read_at": read_at.isoformat()},
        )
        return read_at

    @staticmethod
    async def _get_or_create_settings(session: AsyncSession) -> SystemSettings:
        """获取（必要时创建）系统设置单行记录。"""
        settings = (
            await session.execute(
                select(SystemSettings).order_by(SystemSettings.created_at.asc()).limit(1)
            )
        ).scalar_one_or_none()
        if settings is None:
            settings = SystemSettings()
            session.add(settings)
        return settings

    @staticmethod
    async def _get_alerts_read_at(session: AsyncSession) -> datetime | None:
        """读取告警已读水位线；记录或字段为空时返回 None（不创建行）。"""
        return (
            await session.execute(
                select(SystemSettings.alerts_read_at)
                .order_by(SystemSettings.created_at.asc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def _collect_alerts(
        self,
        session: AsyncSession,
        offline_nodes: list[AgentNode] | None = None,
    ) -> list[AlertItem]:
        """汇集全部告警（metrics 复用；任务类告警合并为单条 SQL）。"""
        alerts: list[AlertItem] = []

        if offline_nodes is None:
            offline_nodes = list(
                (
                    await session.execute(
                        select(AgentNode).where(AgentNode.status == NodeStatus.OFFLINE.value)
                    )
                )
                .scalars()
                .all()
            )
        for node in offline_nodes:
            heartbeat = node.last_heartbeat_at or node.updated_at
            alerts.append(
                AlertItem(
                    id=f"node:{node.id}",
                    level="danger",
                    title=f"{node.name} 离线",
                    description=f"节点状态 offline · 最后心跳 {heartbeat:%m-%d %H:%M}",
                    node_id=str(node.id),
                    created_at=heartbeat,
                )
            )

        # 失败 / 待确认 / 排队超时任务合并一次取回，Python 侧分桶截断
        threshold = datetime.now(UTC) - timedelta(minutes=PENDING_ALERT_THRESHOLD_MINUTES)
        alert_tasks = (
            (
                await session.execute(
                    select(Task).where(
                        (Task.status.in_([TaskStatus.FAILED.value, TaskStatus.REVIEW.value]))
                        | (
                            (Task.status == TaskStatus.PENDING.value)
                            & (Task.created_at < threshold)
                        )
                    )
                )
            )
            .scalars()
            .all()
        )

        failed = sorted(
            (t for t in alert_tasks if t.status == TaskStatus.FAILED.value),
            key=lambda t: t.updated_at,
            reverse=True,
        )[:_ALERT_TASK_CAP]
        review = sorted(
            (t for t in alert_tasks if t.status == TaskStatus.REVIEW.value),
            key=lambda t: t.updated_at,
            reverse=True,
        )[:_ALERT_TASK_CAP]
        pending = sorted(
            (t for t in alert_tasks if t.status == TaskStatus.PENDING.value),
            key=lambda t: t.created_at,
            reverse=True,
        )[:_ALERT_TASK_CAP]

        for task in failed:
            alerts.append(
                AlertItem(
                    id=f"task:{task.id}",
                    level="warning",
                    title=f"任务「{task.title}」执行失败",
                    description=task.change_summary or "任务执行失败，请查看详情并重新派发",
                    task_id=str(task.id),
                    created_at=task.updated_at,
                )
            )
        for task in review:
            alerts.append(
                AlertItem(
                    id=f"task:{task.id}",
                    level="warning",
                    title=f"任务「{task.title}」等待人工确认",
                    description="规划方案已产出，请前往任务详情批准或驳回",
                    task_id=str(task.id),
                    created_at=task.updated_at,
                )
            )
        now = datetime.now(UTC)
        for task in pending:
            waited = max(0, int((now - task.created_at).total_seconds() // 60))
            alerts.append(
                AlertItem(
                    id=f"task:{task.id}",
                    level="info",
                    title=f"任务「{task.title}」排队超时",
                    description=f"已排队 {waited} 分钟仍未分配节点",
                    task_id=str(task.id),
                    created_at=task.created_at,
                )
            )

        # 预算/用量异常任务（ADR-061 M5b/M5c）：已发收尾指令或已按预算强制停止。
        # 只按 `budget_state` 单列过滤（不做跨表聚合），保持仪表盘查询开销可控。
        budget_tasks = (
            (
                await session.execute(
                    select(Task)
                    .where(Task.budget_state.is_not(None))
                    .order_by(Task.updated_at.desc())
                    .limit(_ALERT_TASK_CAP)
                )
            )
            .scalars()
            .all()
        )
        for task in budget_tasks:
            hard = task.budget_state == "hard_stopped"
            alerts.append(
                AlertItem(
                    id=f"task-budget:{task.id}",
                    level="warning" if hard else "info",
                    title=(
                        f"任务「{task.title}」已按预算强制停止"
                        if hard
                        else f"任务「{task.title}」预算接近上限"
                    ),
                    description=(
                        task.change_summary
                        if hard and task.change_summary
                        else "执行预算已用掉约 80%，已发出收尾指令；可在任务详情查看用量曲线"
                    ),
                    task_id=str(task.id),
                    created_at=task.updated_at,
                )
            )

        # 已读水位线过滤（一键已读，ADR-038）：早于等于水位线的告警不再返回
        read_at = await self._get_alerts_read_at(session)
        if read_at is not None:
            alerts = [a for a in alerts if a.created_at > read_at]

        return alerts

    async def get_deliverables(
        self, session: AsyncSession, limit: int = 20
    ) -> DeliverableListResponse:
        """聚合最近任务的产物文件清单（只读缓存，不触发网络拉取）。"""
        tasks = (
            await session.execute(
                select(Task, AgentNode.name)
                .outerjoin(AgentNode, AgentNode.id == Task.assigned_node_id)
                .order_by(Task.updated_at.desc())
                .limit(DELIVERABLE_SCAN_TASKS)
            )
        ).all()

        entries: list[DeliverableItem] = []
        for task, node_name in tasks:
            tree: dict[str, Any] | None = artifact_service.get_tree(str(task.id))
            if not tree:
                continue
            for file in tree.get("files", []):
                path = file.get("path", "")
                if not path:
                    continue
                entries.append(
                    DeliverableItem(
                        task_id=str(task.id),
                        task_title=task.title,
                        node_name=node_name,
                        path=path,
                        file_name=path.rsplit("/", 1)[-1],
                        file_type=_file_type_of(path),
                        size=int(file.get("size", 0)),
                        mtime=float(file.get("mtime", 0)),
                    )
                )
        entries.sort(key=lambda e: e.mtime, reverse=True)
        return DeliverableListResponse(total=len(entries), items=entries[:limit])

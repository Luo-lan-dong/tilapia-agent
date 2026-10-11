"""集群仪表盘聚合端点 Schema（ADR-038）。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ClusterMetricsResponse(BaseModel):
    """集群统计指标（仪表盘 8 卡片数据源）。"""

    total_tasks: int = Field(..., description="总任务数")
    running_tasks: int = Field(..., description="进行中任务数")
    completed_tasks: int = Field(..., description="已完成任务数")
    queued_tasks: int = Field(..., description="排队中（pending）任务数")
    online_nodes: int = Field(..., description="在线节点数")
    total_nodes: int = Field(..., description="节点总数")
    cluster_load_percent: float | None = Field(
        None,
        description="集群负载（在线节点 CPU 均值 %）；无节点指标快照时为 null",
    )
    alert_count: int = Field(..., description="异常事件数（告警总数）")
    token_consumed_today: int = Field(..., description="今日 token 消耗（当前仅中枢侧）")


class AlertItem(BaseModel):
    """告警条目。"""

    id: str = Field(..., description="告警标识（node:{id} / task:{id}）")
    level: Literal["danger", "warning", "info"] = Field(..., description="告警级别")
    title: str = Field(..., description="告警标题")
    description: str = Field(..., description="告警详情")
    task_id: str | None = Field(None, description="关联任务 ID（任务类告警）")
    node_id: str | None = Field(None, description="关联节点 ID（节点类告警）")
    created_at: datetime = Field(..., description="告警时间（事件发生/更新时间）")


class AlertListResponse(BaseModel):
    """告警列表响应。"""

    total: int = Field(..., description="告警总数（未读）")
    items: list[AlertItem] = Field(default_factory=list, description="告警列表")


class AlertReadAllResponse(BaseModel):
    """一键已读响应。"""

    read_at: datetime = Field(..., description="写入的已读水位线（UTC）")


class DeliverableItem(BaseModel):
    """交付物条目（聚合自任务产物缓存清单）。"""

    task_id: str = Field(..., description="任务 ID")
    task_title: str = Field(..., description="任务标题")
    node_name: str | None = Field(None, description="分配节点名称")
    path: str = Field(..., description="工作目录内相对路径")
    file_name: str = Field(..., description="文件名（路径末段）")
    file_type: Literal["image", "video", "zip", "doc", "code", "other"] = Field(
        ...,
        description="文件类型（按扩展名映射）",
    )
    size: int = Field(..., description="文件大小（字节）")
    mtime: float = Field(..., description="最后修改时间（Unix 秒）")


class DeliverableListResponse(BaseModel):
    """交付物聚合列表响应。"""

    total: int = Field(..., description="交付物总数（截断前）")
    items: list[DeliverableItem] = Field(default_factory=list, description="交付物列表")

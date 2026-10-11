"""任务派发与规划方案审批相关的请求/响应 Schema。

从路由文件迁移至此（backend-component-standards §3.1：
请求/响应 Schema 统一存放 app/schemas/）。
"""

import uuid
from typing import Literal

from pydantic import BaseModel, Field


class TaskDispatchRequest(BaseModel):
    """手动派发任务请求。"""

    target: str = Field(
        default="",
        description="目标子系统标识（遗留字段，可与 node_id 二选一）",
    )
    mode: Literal["plan", "build"] | None = Field(
        default=None,
        description="执行模式：plan 先规划（只读）、build 直接开发；缺省沿用任务已有模式",
    )
    node_id: uuid.UUID | None = Field(
        default=None,
        description="手动指定的执行节点 ID（ADR-017 增强）；提供时任务直接指派给该节点",
    )
    force: bool = Field(
        default=False,
        description=(
            "ADR-059 D2 例外：仅当用户**明确要求**无视资源水位强行派送时置真。"
            "false（默认）时若目标节点不满足准入双门槛，任务改为排队等待该节点"
            "（返回 queued_on_node 与 hold_reason）而不派送"
        ),
    )


class TaskDispatchResponse(BaseModel):
    """手动派发任务响应。"""

    task_id: uuid.UUID = Field(..., description="任务 ID")
    target: str = Field(..., description="目标子系统标识")
    status: str = Field(..., description="派发状态")
    dispatch_type: str = Field(..., description="派发模式：auto 调度器 / manual 用户手动")
    queued_on_node: str | None = Field(
        None,
        description="ADR-059：目标节点未达标时改为排队等待，此处为其名称；正常派送时为 null",
    )
    hold_reason: str | None = Field(
        None,
        description="ADR-059：排队原因（含缺口数值）；正常派送时为 null",
    )


class RejectPlanRequest(BaseModel):
    """驳回规划方案请求。"""

    feedback: str = Field(
        ...,
        min_length=1,
        description="驳回反馈意见，将拼入 change_brief 随下一轮 plan 派发下发",
    )


class AcceptTaskRequest(BaseModel):
    """子系统接收任务请求（MVP mock）。"""

    task_id: uuid.UUID = Field(..., description="任务 ID")
    specification: dict = Field(..., description="任务规范三件套")


class AcceptTaskResponse(BaseModel):
    """子系统接收任务响应（MVP mock）。"""

    status: str = Field(..., description="接收状态")
    task_id: uuid.UUID = Field(..., description="任务 ID")

"""任务构建相关 Schema。"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import PaginatedResponse


class TaskSpecification(BaseModel):
    """任务规范三件套 + 验收判据（ADR-061 M4）。

    ``verify`` 为结构化分层验收判据（`verify.json` 的内容），由中枢编排器 LLM 生成、
    经归一化后随派发下发；**省略或为 None 表示「没有判据」**，节点将按「无法验证」上报，
    不得据此判成功（用户已定：不许假成功）。
    """

    spec: str = Field(..., description="spec.md 内容")
    tasks: str = Field(..., description="tasks.md 内容")
    checklist: str = Field(..., description="checklist.md 内容")
    verify: dict[str, Any] | None = Field(
        default=None,
        description=(
            "分层验收判据（ADR-061 M4）：{checks:[{layer:L0~L4,name,cmd,cwd?,timeout_s?,must_pass?}]}；"
            "L0/L1 默认必过、L2 存在即必过、L3/L4 需显式标注；None=无判据（按无法验证处理）"
        ),
    )


class ChatMessage(BaseModel):
    """聊天消息。

    ``content`` 允许空串：思考型回复可能只有 thinking 内容而无正文
    （chunk 为空），历史消息回传时不得因空 content 被 422 拒绝（ADR-028 期修复）。

    ``thinking`` / ``steps`` 仅 assistant 消息携带（ADR-033）：思考过程与
    技能步骤卡片随会话持久化，中枢重启/会话恢复后仍可展示。

    ``tool_calls`` / ``tool_call_id``（ADR-040）：工具轨迹随会话持久化——
    assistant 消息携带 ``tool_calls``（OpenAI 原格式），每个调用对应一条
    ``role:"tool"`` 消息携带 ``tool_call_id``；下一轮随历史回流 LLM，
    支撑跨轮工具结果引用（多轮思考）。
    """

    role: str = Field(..., pattern=r"^(user|assistant|tool)$")
    content: str = Field(default="")
    thinking: str | None = Field(None, description="AI 思考过程（assistant 消息）")
    steps: list[dict] | None = Field(None, description="技能执行步骤（assistant 消息）")
    mention_node: str | None = Field(
        None,
        description="@节点来源（ADR-064）：节点显示名/ID；user 消息（@发给谁）与 assistant 消息（谁的回复）均可携带，历史回传据此渲染来源标注",
    )
    reply_to: str | None = Field(
        None,
        description="续聊回复关联 ID（ADR-064）：assistant 回复与触发它的用户消息配对，前端据此去重与轮询匹配",
    )
    tool_calls: list[dict] | None = Field(
        None,
        description="工具调用清单（assistant 消息，OpenAI 原格式，ADR-040）",
    )
    tool_call_id: str | None = Field(
        None,
        description="对应的工具调用 ID（role=tool 消息，ADR-040）",
    )
    # 图片附件（ADR-039）：user 消息可携带，由中枢按使用中模型视觉能力决定是否注入 LLM
    attachments: list[dict] | None = Field(
        None,
        description="消息携带的图片附件：[{id, file_name, mime}]（仅 user 消息）",
    )


class TaskBuildChatRequest(BaseModel):
    """流式对话请求。"""

    messages: list[ChatMessage] = Field(..., min_length=1)
    current_specification: TaskSpecification | None = Field(None)
    mode: str = Field(
        default="thinking",
        pattern=r"^(fast|thinking)$",
        description="对话模式：fast / thinking",
    )
    session_id: uuid.UUID | None = Field(
        None, description="关联会话 ID，提供时流结束后自动保存本轮对话"
    )
    parent_task_id: uuid.UUID | None = Field(
        None,
        description="任务链父任务 ID（ADR-028）：AI 创建任务时复用迭代链与工作目录",
    )
    output_dir: str | None = Field(None, description="任务工作目录：AI 创建任务时锁定同目录")


class TaskBuildChatResponse(BaseModel):
    """任务构建对话响应。"""

    ai_reply: str = Field(..., description="AI 回复")
    specification: TaskSpecification = Field(..., description="当前三件套草案")
    is_ready_to_create: bool = Field(..., description="是否可以创建任务")
    model: str = Field(..., description="当前使用的模型标识")
    mode: str = Field(..., description="当前使用的对话模式")


class SpecBuilderStreamEvent(BaseModel):
    """SSE 流式事件基础结构。"""

    event: str = Field(..., description="事件类型")
    data: dict = Field(default_factory=dict, description="事件数据")


class TaskBuildChatStreamMeta(BaseModel):
    """SSE 首事件：模型和模式元信息。"""

    model: str = Field(..., description="当前使用的模型标识")
    mode: str = Field(..., description="当前使用的对话模式")


class TaskBuildChatStreamChunk(BaseModel):
    """SSE chunk 事件：AI 回复文本增量。"""

    delta: str = Field(..., description="AI 回复文本增量")


class TaskBuildChatStreamStatus(BaseModel):
    """SSE status 事件：是否可创建任务状态变更。"""

    is_ready_to_create: bool = Field(..., description="是否可以创建任务")


class TaskBuildingSessionCreateRequest(BaseModel):
    """创建任务构建会话请求。"""

    title: str = Field(..., min_length=1, max_length=255, description="会话标题")
    messages: list[ChatMessage] = Field(
        default_factory=list,
        description="初始对话历史",
    )
    specification: TaskSpecification | None = Field(
        None,
        description="初始三件套草案",
    )
    mode: str = Field(
        default="thinking",
        pattern=r"^(fast|thinking)$",
        description="对话模式：fast / thinking",
    )
    model: str | None = Field(None, max_length=64, description="当前使用的模型标识")
    task_id: uuid.UUID | None = Field(
        None,
        description="本会话已创建的任务 ID，恢复会话时继续跟踪方案审批",
    )


class TaskBuildingSessionUpdateRequest(BaseModel):
    """更新任务构建会话请求。"""

    title: str | None = Field(None, min_length=1, max_length=255)
    messages: list[ChatMessage] | None = Field(None)
    specification: TaskSpecification | None = Field(None)
    mode: str | None = Field(None, pattern=r"^(fast|thinking)$")
    model: str | None = Field(None, max_length=64)
    task_id: uuid.UUID | None = Field(
        None,
        description="本会话已创建的任务 ID，恢复会话时继续跟踪方案审批",
    )


class TaskBuildingSessionResponse(BaseModel):
    """任务构建会话响应。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    messages: list[ChatMessage]
    specification: TaskSpecification | None
    mode: str
    model: str | None
    task_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class TaskBuildingSessionSummary(BaseModel):
    """任务构建会话列表项（轻量）。

    列表场景不承载 ``messages`` / ``specification`` 等大字段——每条会话的
    对话历史与三件套可达数百 KB，整列加载（100 条 × 大 JSONB）曾导致历史
    会话列表接口响应体过大、前端长时间无法加载；点开单条时经详情接口
    惰性拉取完整数据。
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    mode: str
    model: str | None
    task_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class TaskBuildingSessionListResponse(PaginatedResponse[TaskBuildingSessionSummary]):
    """任务构建会话列表分页响应模型。"""

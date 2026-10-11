"""技能基类与执行协议（ADR-033）。

技能 = 中枢 AI 对话 Agent（OrchestratorAgent）可自主调用的工具。
协议约定：

- ``Skill.execute`` 为 async generator：执行过程中 yield SSE 中间事件
  （``chunk`` / ``thinking`` / ``specification_delta`` / ``status`` 等，
  由 OrchestratorAgent 原样转发给前端渲染步骤卡片）；
- 执行结束前必须 yield 最后一个事件 ``{"event": "_result", "data": SkillResult}``，
  由 OrchestratorAgent 消费（结果回传 LLM、下发 ``step_finished``），
  ``_result`` 与 ``done`` 事件不会透传到前端。
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

RESULT_EVENT = "_result"


@dataclass
class SkillContext:
    """技能执行上下文：LLM 工具调用参数 + 当前对话状态。"""

    args: dict[str, Any] = field(default_factory=dict)
    messages: list[dict[str, str]] = field(default_factory=list)
    current_specification: dict[str, Any] | None = None
    mode: str = "thinking"
    #: 当前对话关联的构建会话 ID（技能可将产物回写会话，如 smart_dispatch
    #: 派发成功后更新会话 task_id 使前端进入跟踪态）
    session_id: str | None = None
    #: 任务链父任务 ID（ADR-028）：create_task 技能创建任务时复用迭代链
    parent_task_id: str | None = None
    #: 任务工作目录：create_task 技能创建任务时锁定同目录（ADR-028）
    output_dir: str | None = None
    #: 本轮对话中 generate_spec_docs 最近一次执行是否失败（由 Orchestrator
    #: 工具循环跟踪回写）：失败时 create_task 必须拒绝创建，防止基于旧草稿
    #: 误建任务后派发必然失败
    spec_generation_failed: bool = False


@dataclass
class SkillResult:
    """技能执行结果：summary 回传 LLM，data 供审计与调试。"""

    success: bool
    summary: str
    data: dict[str, Any] = field(default_factory=dict)


class Skill(ABC):
    """中枢技能基类。"""

    #: 技能唯一标识（同时为 LLM function name），如 ``find_idle_nodes``
    name: str
    #: 展示名称（技能页/步骤卡片用）
    display_name: str
    #: 功能描述（进入 LLM system prompt，决定 AI 何时调用）
    description: str
    #: 参数 JSON Schema（进入 LLM tools 定义）
    parameters: dict[str, Any]
    #: 返回结构说明（技能页展示）
    returns: str

    @abstractmethod
    def execute(
        self,
        session: AsyncSession,
        context: SkillContext,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """执行技能，产出 SSE 中间事件流，最后一个事件为 ``_result``。"""
        raise NotImplementedError

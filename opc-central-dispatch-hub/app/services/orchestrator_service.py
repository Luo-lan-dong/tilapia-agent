"""Orchestrator 对话服务层（ADR-033）：包装技能循环 Agent 供路由调用。"""

from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.orchestrator import OrchestratorAgent
from app.clients.deepseek import DeepSeekClient


class OrchestratorService:
    """统一对话链路服务：技能调用循环 + SSE 事件流。"""

    def __init__(
        self,
        session: AsyncSession,
        agent: OrchestratorAgent | None = None,
    ) -> None:
        """初始化服务。

        Args:
            session: 异步数据库会话（技能执行与 LLM 配置读取共用）。
            agent: 可注入的 OrchestratorAgent 实例（测试用）。
        """
        self._session = session
        self._agent = agent

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        current_specification: dict[str, Any] | None,
        mode: str | None = None,
        session_id: str | None = None,
        parent_task_id: str | None = None,
        output_dir: str | None = None,
        max_rounds_override: int | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """流式处理一轮对话，产出统一 SSE 事件结构。

        ``max_rounds_override``：唤醒轮等调用方传入的独立轮数上限
        （不传则走全局 orchestrator_max_tool_rounds 默认值）。
        """
        agent = self._agent or OrchestratorAgent(self._session)
        async for event in agent.chat_stream(
            messages,
            current_specification,
            mode=mode,
            session_id=session_id,
            parent_task_id=parent_task_id,
            output_dir=output_dir,
            max_rounds_override=max_rounds_override,
        ):
            yield event

    def with_client(self, client: DeepSeekClient) -> "OrchestratorService":
        """返回注入 DeepSeekClient 后的服务实例（便于测试）。"""
        return OrchestratorService(
            session=self._session,
            agent=OrchestratorAgent(self._session, deepseek_client=client),
        )

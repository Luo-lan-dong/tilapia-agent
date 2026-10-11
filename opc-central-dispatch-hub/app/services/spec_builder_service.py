"""Spec Builder 服务层。"""

from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.spec_builder import SpecBuilderAgent
from app.clients.deepseek import DeepSeekClient


class SpecBuilderService:
    """包装 SpecBuilderAgent，供路由层调用。"""

    def __init__(
        self,
        session: AsyncSession,
        agent: SpecBuilderAgent | None = None,
    ) -> None:
        """初始化服务。

        Args:
            session: 异步数据库会话，用于读取 LLM 配置。
            agent: 可选的 SpecBuilderAgent 实例，未提供时自动创建。
        """
        self._session = session
        self._agent = agent

    async def chat(
        self,
        messages: list[dict[str, str]],
        current_specification: dict[str, str] | None,
        mode: str = "thinking",
    ) -> dict[str, Any]:
        """处理一轮对话，返回 AI 回复和三件套草案。

        Args:
            messages: 用户与 AI 的对话历史。
            current_specification: 当前三件套草案。
            mode: 对话模式，fast 或 thinking。

        Returns:
            包含 ai_reply、specification、is_ready_to_create、model、mode 的字典。
        """
        agent = self._agent
        if agent is None:
            agent = SpecBuilderAgent(self._session)
        return await agent.chat(messages, current_specification, mode=mode)

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        current_specification: dict[str, str] | None,
        mode: str = "thinking",
    ) -> AsyncGenerator[dict[str, Any], None]:
        """流式处理一轮对话，产出 SSE 事件结构。

        Args:
            messages: 用户与 AI 的对话历史。
            current_specification: 当前三件套草案。
            mode: 对话模式，fast 或 thinking。

        Yields:
            包含 event / data 的事件字典。
        """
        agent = self._agent
        if agent is None:
            agent = SpecBuilderAgent(self._session)
        async for event in agent.chat_stream(messages, current_specification, mode=mode):
            yield event

    def with_client(self, client: DeepSeekClient) -> "SpecBuilderService":
        """返回注入 DeepSeekClient 后的服务实例（便于测试）。"""
        return SpecBuilderService(
            session=self._session,
            agent=SpecBuilderAgent(self._session, deepseek_client=client),
        )

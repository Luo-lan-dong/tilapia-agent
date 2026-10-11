"""中枢端点并行竞速探测（ADR-035 第一步）。

从 ``hub_client.py`` 拆出的端点探测职责：维护候选端点环（锁定端点 → 配置
首选 → 同主机 8000-8010 避让范围，ADR-031）、未锁定时对全部候选并行竞速
注册（首个成功者胜出并锁定）、锁定后对锁定端点常规注册。
"""

import asyncio
import logging
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

from app.config import AgentConfig

logger = logging.getLogger(__name__)

# 锁定端点常规注册 HTTP 超时（秒，ADR-031）
REGISTER_TIMEOUT_SECONDS: float = 30.0
# 端点竞速探测单次注册短超时（秒）。
# 教训（2026-10-05 事故）：1s 竞速超时在「中枢与数据库分机 + VPN 高 RTT」环境下
# 永远不够——hub 注册需 3~4 次 DB 往返（~200ms/次），端到端实测 2.6s，节点因此
# 陷入「注册超时 → 离线」死循环且 error 为空（TimeoutError 无消息）难以排查。
# 竞速本就是并行发起，放宽到 10s 不拖慢快端点胜出（首个成功者立即胜出）。
PROBE_TIMEOUT_SECONDS: float = 10.0
# 中枢端口避让探测范围（与 hub launcher find_free_port 范围一致，ADR-031）
PORT_PROBE_RANGE: range = range(8000, 8011)


class EndpointProber:
    """中枢 API 端点探测器：候选环管理 + 并行竞速注册 + 端点锁定。"""

    def __init__(
        self,
        config: AgentConfig,
        build_payload: Callable[[], dict[str, Any]],
    ) -> None:
        """初始化探测器。

        Args:
            config: Agent 配置（读取首选中枢地址）。
            build_payload: 注册请求 payload 构造器（每次探测实时构造）。
        """
        self.config = config
        self._build_payload = build_payload
        self._locked: str | None = None

    @property
    def active_api_url(self) -> str:
        """当前生效的中枢 API 地址：锁定端点优先，否则配置首选地址。"""
        return self._locked or self.config.hub_api_url.rstrip("/")

    def lock(self, url: str) -> None:
        """锁定端点（注册/竞速胜出后调用，WS 地址派生等后续使用）。"""
        self._locked = url.rstrip("/")

    def unlock(self) -> None:
        """解除锁定（会话失败后调用，下一轮改为对全候选并行竞速）。"""
        self._locked = None

    def candidate_api_urls(self) -> list[str]:
        """生成候选地址列表（去重保序）。

        顺序：已锁定端点 → 配置首选地址 → 同主机 8000-8010 避让范围。
        """
        base = self.config.hub_api_url.rstrip("/")
        candidates: list[str] = []
        if self._locked:
            candidates.append(self._locked)
        candidates.append(base)
        parsed = urlparse(base)
        host = parsed.hostname
        if host:
            for port in PORT_PROBE_RANGE:
                candidate = urlunparse(
                    (parsed.scheme, f"{host}:{port}", parsed.path or "", "", "", "")
                ).rstrip("/")
                if candidate not in candidates:
                    candidates.append(candidate)
        return candidates

    async def register(self) -> dict[str, Any]:
        """向中枢注册节点，返回注册响应 data（含 id / registration_token）。

        已锁定端点：直接对锁定端点常规注册（30s 超时，失败由调用方解锁后
        下一轮竞速）；未锁定：对全部候选并行竞速（单次 1s 超时），首个
        成功者胜出并锁定（ADR-035）。

        Returns:
            注册响应 data 字典。

        Raises:
            Exception: 全部候选失败时抛出最后一个异常，由重连循环按间隔
                重试整环。
        """
        if self._locked is not None:
            return await self._post_register(self._locked, REGISTER_TIMEOUT_SECONDS)
        winner, node_data = await self._race_register()
        self.lock(winner)
        logger.info(
            "endpoint_race_won",
            extra={"event": "endpoint_race_won", "endpoint": winner},
        )
        return node_data

    async def _race_register(self) -> tuple[str, dict[str, Any]]:
        """并行竞速全部候选端点，首个注册成功者胜出。

        所有候选同时发起注册探测（asyncio.wait FIRST_COMPLETED 逐个收结果）：
        首个成功者胜出并取消其余探测；慢端点（1s 超时挂起）不阻塞快端点
        胜出（UT-2）。全部失败抛出最后一个异常。

        Returns:
            (胜出端点 URL, 注册响应 data)。

        Raises:
            Exception: 全部候选失败时的最后一个异常。
        """
        tasks = {
            asyncio.create_task(self._post_register(url, PROBE_TIMEOUT_SECONDS)): url
            for url in self.candidate_api_urls()
        }
        winner: str | None = None
        winner_data: dict[str, Any] = {}
        last_error: Exception | None = None
        try:
            while tasks and winner is None:
                done, _ = await asyncio.wait(tasks.keys(), return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    url = tasks.pop(task)
                    if task.cancelled():
                        continue
                    exc = task.exception()
                    if exc is None:
                        winner = url
                        winner_data = task.result()
                        break
                    last_error = exc
            if winner is None:
                if last_error is None:
                    raise RuntimeError("端点竞速探测全部失败且无错误信息")
                raise last_error
            return winner, winner_data
        finally:
            # 取消未完成/落选的探测任务并回收，避免协程泄漏
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _post_register(self, base_url: str, timeout: float) -> dict[str, Any]:
        """对单个端点发起一次注册请求，返回响应 data；失败抛异常。

        Args:
            base_url: 候选端点基础地址。
            timeout: 本次注册 HTTP 超时（秒）。

        Returns:
            注册响应 data 字典。
        """
        url = f"{base_url.rstrip('/')}/api/v1/agent-nodes/register"
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, json=self._build_payload())
            response.raise_for_status()
            body = response.json()
        return body.get("data", {})

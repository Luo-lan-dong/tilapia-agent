"""tilacode server HTTP API 客户端。

封装 Session 创建/复用、agent 切换、prompt 下发、中断、活跃查询、权限回复
以及 Session 级 / 实例级 SSE 事件流订阅，供任务桥接层调用。

协议约定（源码实测）：
- 成功响应形如 ``{"data": ...}``；错误响应形如 ``{"error": {"name": ..., "message": ...}}``
- 所有请求携带 Basic 认证（用户名固定 ``tilacode``）与
  ``x-tilacode-directory`` 头（URL 编码的绝对路径）
"""

import asyncio
import base64
import json
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any
from urllib.parse import quote

import httpx

# MVP 默认模型：MiMo V2.5 Free（见 ADR-012）
_DEFAULT_MODEL_PROVIDER: str = "opencode"
_DEFAULT_MODEL_ID: str = "mimo-v2.5-free"

# SSE 流中数据行的固定前缀
_SSE_DATA_PREFIX: str = "data: "

# 凭据注入重试参数：tilacode 的 integration 注册表按 location（目录）懒初始化，
# deepseek 等内置 provider 的 key method 由 models.dev 插件注册（数据缓存 TTL 5 分钟，
# 过期需网络拉取）。location 初始化未完成时 connect/key 返回 500
# （"Key method not found"），等待注册完成后重试即可恢复。
_INTEGRATION_KEY_MAX_ATTEMPTS: int = 4
_INTEGRATION_KEY_RETRY_BASE_DELAY_SECONDS: float = 2.0


class TilacodeAPIError(Exception):
    """tilacode API 返回非 2xx 响应时抛出的异常。"""

    def __init__(self, url: str, status: int, error_name: str, message: str) -> None:
        """初始化异常上下文。

        Args:
            url: 触发错误的请求 URL。
            status: HTTP 状态码。
            error_name: tilacode 错误响应中的 error.name。
            message: tilacode 错误响应中的 error.message。
        """
        self.url = url
        self.status = status
        self.error_name = error_name
        self.message = message
        super().__init__(f"tilacode API 错误 [{status}] {error_name}: {message} ({url})")


class TilacodeClient:
    """tilacode HTTP API 异步客户端。"""

    def __init__(
        self,
        base_url: str | Callable[[], str],
        password_provider: Callable[[], str | None],
        directory: str,
        timeout: float = 30.0,
    ) -> None:
        """初始化客户端。

        Args:
            base_url: tilacode server 基础 URL（如 ``http://127.0.0.1:4096``），
                或返回该 URL 的回调（ADR-055 P2-11：托管进程重启后真实端口可能
                变化，回调形式避免固化构造期快照导致后续请求打到旧端口）。
            password_provider: Basic 认证密码取值回调（用户名固定 ``tilacode``）。
                tilacode 进程重启会更换随机密码，因此每次请求动态取值，
                避免客户端固化旧密码导致 401。
            directory: 默认工作目录绝对路径，随请求以 ``x-tilacode-directory`` 头携带。
            timeout: 单次请求超时时间（秒）。
        """
        self._password_provider = password_provider
        self._default_directory = directory
        self._logger = logging.getLogger(__name__)
        self._base_url_provider: Callable[[], str] = (
            base_url if callable(base_url) else (lambda: base_url)
        )
        self._client = httpx.AsyncClient(
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )

    @property
    def base_url(self) -> str:
        """返回当前生效的 tilacode server 基础 URL（动态解析，ADR-055 P2-11）。"""
        return self._base_url_provider().rstrip("/")

    def set_base_url(self, base_url: str) -> None:
        """更新基础 URL（托管进程重启后地址变化的兜底入口）。"""
        self._base_url_provider = lambda: base_url

    async def close(self) -> None:
        """关闭底层 HTTP 连接。"""
        await self._client.aclose()

    def _basic_auth(self) -> tuple[str, str]:
        """构造当前有效的 Basic 认证元组（密码每次动态获取）。"""
        return ("tilacode", self._password_provider() or "")

    def _encoded_directory(self, directory: str | None = None) -> str:
        """返回 URL 编码后的工作目录（用于 x-tilacode-directory 头）。"""
        target = directory if directory is not None else self._default_directory
        return quote(target, safe="")

    def _stream_headers(self, directory: str | None = None) -> dict[str, str]:
        """构造 SSE 流式请求所需的显式请求头（认证 + 目录）。

        Args:
            directory: 订阅目录；缺省用客户端默认目录。实例级流按
                ``x-tilacode-directory`` 目录过滤事件（tilacode 侧
                ``event.location.directory === 订阅目录`` 严格匹配）。
        """
        credentials = base64.b64encode(
            f"tilacode:{self._password_provider() or ''}".encode()
        ).decode()
        return {
            "Authorization": f"Basic {credentials}",
            "x-tilacode-directory": self._encoded_directory(directory),
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        directory: str | None = None,
    ) -> dict[str, Any]:
        """发送 HTTP 请求并解析 JSON 响应。

        Args:
            method: HTTP 方法。
            path: 相对 base_url 的路径。
            json_body: 可选的 JSON 请求体。
            params: 可选的 query 参数。
            directory: 请求级工作目录；缺省用客户端默认目录。

        Returns:
            2xx 响应的完整 JSON 字典（含 ``data`` 键）；响应体非 JSON 时返回空字典。

        Raises:
            TilacodeAPIError: 响应状态码非 2xx 时抛出，携带 error.name/message 上下文。
        """
        response = await self._client.request(
            method,
            f"{self.base_url}{path}",
            json=json_body,
            params=params,
            auth=self._basic_auth(),
            headers={"x-tilacode-directory": self._encoded_directory(directory)},
        )
        try:
            payload: Any = response.json()
        except ValueError:
            payload = {}
        if not response.is_success:
            error = payload.get("error") if isinstance(payload, dict) else None
            error = error if isinstance(error, dict) else {}
            raise TilacodeAPIError(
                url=str(response.request.url),
                status=response.status_code,
                error_name=str(error.get("name", "unknown")),
                message=str(error.get("message", response.text)),
            )
        return payload if isinstance(payload, dict) else {}

    async def create_session(
        self,
        *,
        session_id: str,
        agent: str,
        directory: str,
        model: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """创建或复用 Session（传入相同 id 即复用）。

        Args:
            session_id: Session ID（如 ``ses_task_<task_id>``）。
            agent: agent 名称，``plan`` 或 ``build``。
            directory: 本任务的工作目录绝对路径，随请求头传递。
        model: 模型选择（ADR-029），形如 ``{"providerID": ..., "id": ...}``；
            缺省用内置默认（opencode 免费模型）。

        Returns:
            完整响应字典，``data`` 为 Session.Info（含 id、agent、model 等）。
        """
        body = {
            "id": session_id,
            "agent": agent,
            "model": model or {"providerID": _DEFAULT_MODEL_PROVIDER, "id": _DEFAULT_MODEL_ID},
            "location": {"directory": directory},
        }
        result = await self._request("POST", "/api/session", json_body=body, directory=directory)
        self._logger.info(
            "tilacode_session_created",
            extra={
                "session_id": session_id,
                "agent": agent,
                "directory": directory,
                "model": body["model"],
            },
        )
        return result

    async def ensure_integration_key(
        self, integration_id: str, api_key: str, *, directory: str | None = None
    ) -> None:
        """以 API Key 方式幂等注入 provider 凭据（ADR-029）。

        对应 tilacode ``POST /api/integration/{id}/connect/key``；重复注入
        覆盖旧值（幂等）。仅内存传输，不在日志中输出 Key。

        tilacode 凭据为全局存储（跨目录共享），注入请求应使用客户端默认
        目录（location 常驻、integration 已注册）；若目标 location 尚在
        懒初始化（integration 注册未完成），tilacode 返回 500
        "Key method not found"，此时按指数退避重试。

        Args:
            integration_id: tilacode 内置 provider/integration 标识（如 ``deepseek``）。
            api_key: 中枢下发的 API Key。
            directory: 请求级工作目录；缺省用客户端默认目录。

        Raises:
            TilacodeAPIError: 注入失败（非 2xx 且重试耗尽）时抛出。
        """
        for attempt in range(1, _INTEGRATION_KEY_MAX_ATTEMPTS + 1):
            try:
                await self._request(
                    "POST",
                    f"/api/integration/{integration_id}/connect/key",
                    json_body={"key": api_key},
                    directory=directory,
                )
                self._logger.info(
                    "tilacode_integration_key_injected",
                    extra={"integration_id": integration_id, "attempt": attempt},
                )
                return
            except TilacodeAPIError as error:
                if error.status != 500 or attempt == _INTEGRATION_KEY_MAX_ATTEMPTS:
                    raise
                delay = _INTEGRATION_KEY_RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
                self._logger.warning(
                    "tilacode_integration_key_retry",
                    extra={
                        "integration_id": integration_id,
                        "attempt": attempt,
                        "delay_seconds": delay,
                        "status": error.status,
                    },
                )
                await asyncio.sleep(delay)

    async def switch_agent(
        self, session_id: str, agent: str, *, directory: str | None = None
    ) -> None:
        """在同一 Session 内切换 agent（plan/build）。"""
        path = f"/api/session/{session_id}/agent"
        await self._request("POST", path, json_body={"agent": agent}, directory=directory)

    async def send_prompt(
        self,
        session_id: str,
        text: str,
        *,
        directory: str | None = None,
        attachments: list[dict[str, str]] | None = None,
        delivery: str = "steer",
    ) -> dict[str, Any]:
        """向 Session 发送 prompt（默认 delivery=steer）。

        ``attachments`` 为图片 FileParts（ADR-039，执行模型支持视觉时由
        TaskBridge 传入）：``[{type: "file", mime, filename, url}]``，
        url 为 data URL（tilacode FilePart 协议）。

        ``delivery`` 为 tilacode 投递方式（``steer | queue``，ADR-064 M2）：
        ``steer``（默认，保持既有任务行为）打进行动中的执行；``queue``
        空闲即开新轮、活跃则入队（节点续聊轮使用，竞态安全）。
        """
        path = f"/api/session/{session_id}/prompt"
        prompt_part: dict[str, Any] = {"text": text}
        if attachments:
            prompt_part["attachments"] = attachments
        body = {"prompt": prompt_part, "delivery": delivery}
        return await self._request("POST", path, json_body=body, directory=directory)

    async def interrupt(self, session_id: str, *, directory: str | None = None) -> None:
        """中断 Session 当前执行。"""
        await self._request("POST", f"/api/session/{session_id}/interrupt", directory=directory)

    async def invalidate_config(self, *, directory: str) -> None:
        """使目录实例状态失效（PATCH /config 的副作用：markInstanceForDisposal）。

        tilacode 的配置（含 provider 定义）仅在目录 InstanceState 创建时加载；
        运行中 server 不会感知文件系统上的配置变更。本请求以空 payload 触发
        ``PATCH /api/config``，tilacode 更新 ``<目录>/config.json``（空合并
        无实质变化）后将该目录实例标记待销毁——下次访问（create_session）
        重建实例并重新加载目录配置，任务级 provider 注入由此生效
        （2026-09-27：ModelUnavailableError 修复的必要环节）。

        Args:
            directory: 任务工作目录绝对路径。
        """
        await self._request("PATCH", "/api/config", json_body={}, directory=directory)

    async def provider_snapshot(self, *, directory: str) -> dict[str, list[str]]:
        """读取目录实例当前的 provider → 模型 id 清单（派发前预检，ADR-061 C1）。

        对应 tilacode ``GET /api/model``（返回 ``{location, data: [Model.Info]}``，
        每个模型项含 ``providerID``/``id``）。2026-10-01 事故中 tilacode 的
        catalog 装配曾是后台任务，实例刚重建时模型目录为空，任务首轮必然解析
        失败；此处让节点在派发前主动问一次，把「模型不在目录里」变成明确失败
        而不是 ``task_startup_stalled`` 之后一句无法定位的
        ``ModelUnavailableError``。

        契约勘误（2026-10-03 复测③实测）：最初实现读 ``GET /api/provider`` 并
        期望 ``{data: {providers: [...]}}``——但该端点真实返回
        ``{location, data: [Provider.Info]}``（数组、无 models 字段），解析恒
        落空导致节点上「模型目录为空」假阴性；本地测试因 Fake 按错误契约注入
        而全绿。模型目录的正确数据源是 ``/api/model``（含 ``providerID``）。

        Args:
            directory: 任务工作目录绝对路径（随 ``x-tilacode-directory`` 头传递）。

        Returns:
            ``{provider_id: [model_id, ...]}``；响应结构不符合预期时返回空字典
            （调用方据此区分「目录为空」与「目录里缺这一项」）。
        """
        payload = await self._request("GET", "/api/model", directory=directory)
        data = payload.get("data")
        if not isinstance(data, list):
            return {}
        snapshot: dict[str, list[str]] = {}
        for item in data:
            if not isinstance(item, dict):
                continue
            provider_id = item.get("providerID")
            model_id = item.get("id")
            if not isinstance(provider_id, str) or not provider_id:
                continue
            model_ids = snapshot.setdefault(provider_id, [])
            if isinstance(model_id, str) and model_id and model_id not in model_ids:
                model_ids.append(model_id)
        return snapshot

    async def get_history(
        self, session_id: str, *, directory: str | None = None
    ) -> list[dict[str, Any]]:
        """获取 Session durable 事件分页（总结文本回退路径用）。

        注意（ADR-055 P0-5）：该端点返回的是 **SessionEvent.Durable 事件数组**
        （``{data: [...], hasMore}``），不是消息数组；提取 assistant 文本应优先
        使用 :meth:`get_context`。

        Args:
            session_id: Session ID。
            directory: 请求级工作目录；缺省用客户端默认目录。

        Returns:
            事件列表（tilacode 返回的 ``data`` 数组）；响应结构异常时返回空列表。
        """
        payload = await self._request(
            "GET", f"/api/session/{session_id}/history", directory=directory
        )
        data = payload.get("data")
        return data if isinstance(data, list) else []

    async def get_context(
        self, session_id: str, *, directory: str | None = None
    ) -> list[dict[str, Any]]:
        """获取 Session 当前上下文消息数组（ADR-055 P0-5）。

        对应 tilacode ``GET /api/session/{id}/context``，返回
        ``SessionMessage.Message[]``（``role`` + ``content``），是提取最后一条
        assistant 文本的正确端点（``/history`` 返回的是 durable 事件分页）。

        Args:
            session_id: Session ID。
            directory: 请求级工作目录；缺省用客户端默认目录。

        Returns:
            消息列表；响应结构异常时返回空列表。
        """
        payload = await self._request(
            "GET", f"/api/session/{session_id}/context", directory=directory
        )
        data = payload.get("data")
        return data if isinstance(data, list) else []

    async def is_session_active(self, session_id: str, *, directory: str | None = None) -> bool:
        """查询 Session 是否处于运行中（active map 中存在即运行）。"""
        payload = await self._request("GET", "/api/session/active", directory=directory)
        data = payload.get("data")
        return isinstance(data, dict) and session_id in data

    async def permission_reply(
        self,
        session_id: str,
        request_id: str,
        reply: str = "once",
        *,
        message: str | None = None,
        directory: str | None = None,
    ) -> None:
        """回复权限请求（默认 ``once`` 仅本次允许）。

        ``reply="reject"`` 时可带 ``message``：tilacode 会把它作为**可读原因**
        回灌给模型（ADR-061 M2b，Q3；服务端 ``handlers/permission.ts`` 透传
        ``ctx.payload.message``）。
        """
        path = f"/api/session/{session_id}/permission/{request_id}/reply"
        body: dict[str, Any] = {"reply": reply}
        if message:
            body["message"] = message
        await self._request("POST", path, json_body=body, directory=directory)

    async def stream_session_events(
        self, session_id: str, after: int | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        """订阅 Session 级 SSE 事件流。

        Args:
            session_id: Session ID。
            after: 断线重连时的续传游标（上一条事件的 durable.seq）。

        Yields:
            解析后的 SSE 事件 JSON 字典。
        """
        params = {"after": after} if after is not None else None
        path = f"/api/session/{session_id}/event"
        async for event in self._stream_events(path, params):
            yield event

    async def stream_instance_events(
        self, directory: str | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        """订阅实例级 SSE 事件流（live-only，权限与 todo 事件从此流接收）。

        Args:
            directory: 订阅目录。tilacode 实例级流按目录严格过滤事件，
                任务事件（``todo.updated`` 等）的目录为任务工作目录，
                必须用任务目录订阅才能收到；缺省用客户端当前目录。

        Yields:
            解析后的 SSE 事件 JSON 字典。
        """
        async for event in self._stream_events("/api/event", None, directory=directory):
            yield event

    async def _stream_events(
        self,
        path: str,
        params: dict[str, Any] | None,
        directory: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """SSE 流式请求的通用实现。

        逐行解析以 ``data: `` 开头的行并 json.loads；网络异常
        （httpx.RequestError）时静默结束迭代，由调用方处理重连。

        Yields:
            解析后的 SSE 事件 JSON 字典。

        Raises:
            TilacodeAPIError: 流式响应状态码非 2xx 时抛出。
        """
        try:
            async with self._client.stream(
                "GET",
                f"{self.base_url}{path}",
                params=params,
                headers=self._stream_headers(directory),
            ) as response:
                if not response.is_success:
                    body = (await response.aread()).decode("utf-8", errors="replace")
                    try:
                        error = json.loads(body).get("error", {})
                    except ValueError:
                        error = {}
                    error = error if isinstance(error, dict) else {}
                    raise TilacodeAPIError(
                        url=str(response.request.url),
                        status=response.status_code,
                        error_name=str(error.get("name", "unknown")),
                        message=str(error.get("message", body)),
                    )
                async for line in response.aiter_lines():
                    if not line.startswith(_SSE_DATA_PREFIX):
                        continue
                    try:
                        event: Any = json.loads(line[len(_SSE_DATA_PREFIX) :])
                    except ValueError:
                        continue
                    if isinstance(event, dict):
                        yield event
        except httpx.RequestError as e:
            self._logger.warning(
                "tilacode_sse_disconnected",
                extra={"path": path, "error": str(e)},
            )
            return

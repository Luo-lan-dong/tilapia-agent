"""办公 Agent 节点配置模块（ADR-062）。

使用 Pydantic Settings 从环境变量 / .env 文件加载配置，并提供 WebSocket URL
推导、节点身份生成等便捷属性。
"""

import platform
import socket
import uuid
from typing import Any
from urllib.parse import urlparse, urlunparse

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Agent 版本（与 pyproject.toml version 保持一致）
APP_VERSION = "0.1.0"


class AgentConfig(BaseSettings):
    """Linux 办公 Agent 配置。

    环境变量名与字段名大小写不敏感，推荐在 .env 中使用大写下划线形式。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_env: str = Field(
        default="local",
        validation_alias="APP_ENV",
        description="运行环境：local/dev/staging/prod",
    )
    log_level: str = Field(
        default="INFO",
        validation_alias="LOG_LEVEL",
        description="日志级别：DEBUG/INFO/WARNING/ERROR/CRITICAL",
    )
    hub_api_url: str = Field(
        default="http://localhost:8000",
        validation_alias="HUB_API_URL",
        description="中枢 REST API 地址",
    )
    hub_ws_url: str | None = Field(
        default=None,
        validation_alias="HUB_WS_URL",
        description="中枢 WebSocket 地址；为空时由 HUB_API_URL 推导",
    )
    agent_registration_token: str = Field(
        ...,
        validation_alias="AGENT_REGISTRATION_TOKEN",
        description="安装脚本注入的注册 Token（当前保留，WebSocket 认证使用注册接口返回的 Token）",
    )
    agent_client_id: str | None = Field(
        default=None,
        validation_alias="AGENT_CLIENT_ID",
        description="节点客户端 ID；为空时自动生成",
    )
    agent_name: str | None = Field(
        default=None,
        validation_alias="AGENT_NAME",
        description="节点显示名称；为空时使用 hostname + 随机串",
    )
    heartbeat_interval_seconds: int = Field(
        default=5,
        validation_alias="HEARTBEAT_INTERVAL_SECONDS",
        description="心跳上报间隔（秒）",
    )
    reconnect_interval_seconds: int = Field(
        default=5,
        ge=1,
        validation_alias="RECONNECT_INTERVAL_SECONDS",
        description="断线重连重试间隔（秒）；断线后不退出进程，按此间隔重试注册与连接（ADR-020）",
    )
    agent_type: str = Field(
        default="office",
        validation_alias="AGENT_TYPE",
        description="Agent 类型（办公节点固定 office）",
    )
    task_no_progress_timeout_seconds: int = Field(
        default=1800,
        ge=60,
        validation_alias="TASK_NO_PROGRESS_TIMEOUT_SECONDS",
        description=(
            "无进度看门狗阈值（秒，ADR-055）：任何执行活动（LLM 响应/工具调用/进度上报）"
            "都会重置计时，仅完全无活动超过该值时中断任务并上报失败"
        ),
    )
    agent_progress_heartbeat_seconds: int = Field(
        default=20,
        ge=5,
        validation_alias="AGENT_PROGRESS_HEARTBEAT_SECONDS",
        description=(
            "静默期进度心跳间隔（秒，ADR-055 P2-13）：长工具执行期间无事件时，"
            "按该间隔补发进度事件，避免前端看起来卡死"
        ),
    )
    workspace_root: str = Field(
        default="/workspace",
        validation_alias="WORKSPACE_ROOT",
        description="任务工作目录根路径（产物 output_dir 缺省父目录）",
    )
    # ---- 办公执行器（ADR-062 工具循环）----
    office_llm_max_rounds: int = Field(
        default=30,
        ge=1,
        validation_alias="OFFICE_LLM_MAX_ROUNDS",
        description="LLM 工具循环轮次上限；超限终止并尽力产出（Spec 降级链路）",
    )
    office_llm_timeout_seconds: float = Field(
        default=120.0,
        ge=5.0,
        validation_alias="OFFICE_LLM_TIMEOUT_SECONDS",
        description="单次 LLM 请求超时（秒）",
    )
    office_llm_max_retries: int = Field(
        default=2,
        ge=0,
        validation_alias="OFFICE_LLM_MAX_RETRIES",
        description="LLM 请求失败重试次数（指数退避）；仍失败上报 llm_unavailable",
    )
    office_llm_token_budget: int = Field(
        default=200_000,
        ge=10_000,
        validation_alias="OFFICE_LLM_TOKEN_BUDGET",
        description=(
            "单任务累计 token 预算（输入+输出粗估，ADR-065 M7 成本护栏）：达软阈值注入"
            "收尾纪律继续执行，达 100% 硬停并上报明确失败原因"
        ),
    )
    office_llm_budget_soft_ratio: float = Field(
        default=0.8,
        gt=0.0,
        le=1.0,
        validation_alias="OFFICE_LLM_BUDGET_SOFT_RATIO",
        description=(
            "token 预算软阈值比例（ADR-065 M7）：用量达 预算×该比例 时注入「收尾纪律」"
            "并上报 status 轨迹行（任务继续），默认 0.8（对齐 ADR-061 M5b 的 80% 语义）"
        ),
    )
    office_web_search_max_results: int = Field(
        default=8,
        ge=1,
        le=20,
        validation_alias="OFFICE_WEB_SEARCH_MAX_RESULTS",
        description="web_search 返回结果条数上限",
    )
    office_fetch_timeout_seconds: float = Field(
        default=45.0,
        ge=5.0,
        validation_alias="OFFICE_FETCH_TIMEOUT_SECONDS",
        description="web_fetch 单页抓取超时（秒）",
    )
    office_search_backend: str = Field(
        default="auto",
        validation_alias="OFFICE_SEARCH_BACKEND",
        description=(
            "联网检索后端：auto（Bing→DDG 依序尝试，大陆网络 Bing 可达）/ "
            "bing / ddg"
        ),
    )
    office_proxy_url: str | None = Field(
        default=None,
        validation_alias="OFFICE_PROXY_URL",
        description=(
            "联网代理（http://host:port），搜索与抓取经代理出网；"
            "海外节点或需翻墙环境配置，留空直连"
        ),
    )
    office_enable_stealth: bool = Field(
        default=False,
        validation_alias="OFFICE_ENABLE_STEALTH",
        description=(
            "web_fetch 隐身浏览器通道开关（ADR-065 M6）：默认关闭，"
            "SPA/强反爬站点需渲染时置 true（要求已安装 Patchright 浏览器，"
            "会额外占用磁盘与耗时）"
        ),
    )
    agent_max_concurrent_tasks: int = Field(
        default=2,
        ge=1,
        validation_alias="AGENT_MAX_CONCURRENT_TASKS",
        description="节点本地最大并发执行任务数（ADR-032）",
    )
    agent_busy_cpu_threshold_percent: float = Field(
        default=90.0,
        ge=0.0,
        le=100.0,
        validation_alias="AGENT_BUSY_CPU_THRESHOLD_PERCENT",
        description="本地自评 CPU 拒绝阈值（%，ADR-032）",
    )
    agent_busy_mem_threshold_percent: float = Field(
        default=90.0,
        ge=0.0,
        le=100.0,
        validation_alias="AGENT_BUSY_MEM_THRESHOLD_PERCENT",
        description="本地自评内存拒绝阈值（%，ADR-032）",
    )
    agent_api_host: str = Field(
        default="0.0.0.0",
        validation_alias="AGENT_API_HOST",
        description="通知 API 监听地址（仅内网使用，ADR-035）",
    )
    agent_api_port: int = Field(
        default=18081,
        ge=1,
        le=65535,
        validation_alias="AGENT_API_PORT",
        description="通知 API 监听端口（中枢重启反连用，ADR-035；与编程节点错开）",
    )
    api_token: str | None = Field(
        default=None,
        validation_alias="API_TOKEN",
        description="通知 API 认证 Token（安装脚本生成落盘，随首次注册上报，ADR-035）",
    )

    @property
    def reported_address(self) -> dict[str, Any] | None:
        """构造注册上报的通知地址（ADR-035）。

        配置了 ``API_TOKEN`` 时上报 {ip, api_port, api_token}，供中枢落库
        并在重启后主动反连；未配置（旧安装）时返回 None，中枢跳过反连。
        """
        ip = _detect_lan_ip()
        if not ip or not self.api_token:
            return None
        return {
            "ip": ip,
            "api_port": self.agent_api_port,
            "api_token": self.api_token,
        }

    @property
    def websocket_url(self) -> str:
        """计算实际 WebSocket 连接地址。"""
        if self.hub_ws_url:
            return self.hub_ws_url
        return _derive_ws_url(self.hub_api_url)

    @property
    def resolved_client_id(self) -> str:
        """返回显式配置或自动生成的 client_id。"""
        return self.agent_client_id or _generate_client_id()

    @property
    def resolved_name(self) -> str:
        """返回显式配置或自动生成的节点名称。"""
        return self.agent_name or _generate_name()


def _derive_ws_url(api_url: str) -> str:
    """将 REST URL 转换为 WebSocket URL。

    规则：
    - http:// -> ws://
    - https:// -> wss://
    - 路径统一使用 /api/v1/agent-nodes/ws
    """
    parsed = urlparse(api_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunparse((scheme, parsed.netloc, "/api/v1/agent-nodes/ws", "", "", ""))


def _generate_client_id() -> str:
    """生成唯一 client_id：hostname-8位随机串。"""
    hostname = socket.gethostname() or "unknown"
    suffix = uuid.uuid4().hex[:8]
    return f"{hostname}-{suffix}"


def _detect_lan_ip() -> str | None:
    """探测本机局域网 IP（UDP connect 技巧，不实际发包）。

    通过向 8.8.8.8:80 发起 UDP connect 获取出口路由对应的本机地址；
    无网络接口或探测失败时返回 None（注册时不携带 reported_address）。
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        return ip or None
    except OSError:
        return None
    finally:
        sock.close()


def _read_os_release() -> dict[str, str] | None:
    """读取 ``/etc/os-release``（Linux 通用），返回 {NAME, VERSION_ID, ID}。

    Windows / macOS 没有该文件，会被调用方忽略；非致命错误返回 None。
    """
    try:
        with open("/etc/os-release", encoding="utf-8") as fp:
            data: dict[str, str] = {}
            for line in fp:
                line = line.strip()
                if not line or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                # 去引号
                value = value.strip().strip('"').strip("'")
                data[key] = value
        return data or None
    except OSError:
        return None


def _generate_name() -> str:
    """生成默认节点名称：操作系统平台+详细版本（ADR-016）。

    优先级：
    1. ``/etc/os-release`` 的 ``NAME`` + ``VERSION_ID`` → ``Ubuntu 22.04`` / ``CentOS 7.9``
    2. ``platform.platform()`` → ``Linux 5.15.0-...`` / ``macOS-14.4-arm64``
    3. ``socket.gethostname()`` 兜底
    """

    os_release = _read_os_release()
    if os_release:
        name = os_release.get("NAME", "").strip()
        version_id = os_release.get("VERSION_ID", "").strip()
        if name and version_id:
            return f"{name} {version_id}"
        if name:
            return name

    try:
        plat = platform.platform()
        if plat:
            return plat
    except Exception:  # pragma: no cover - defensive
        pass

    return socket.gethostname() or "unknown"

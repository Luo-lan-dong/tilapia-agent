"""Agent 节点配置模块。

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

# Agent 版本（与 pyproject.toml version 保持一致；整体产物版本，见 ADR-024）
APP_VERSION = "0.5.0"


class AgentConfig(BaseSettings):
    """Linux 编程 Agent 配置。

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
        default="code",
        validation_alias="AGENT_TYPE",
        description="Agent 类型",
    )
    task_execution_timeout_seconds: int = Field(
        default=0,
        ge=0,
        validation_alias="TASK_EXECUTION_TIMEOUT_SECONDS",
        description=(
            "任务总时长上限（秒）；0=不限（默认）。ADR-055 起语义由「从未生效的死配置」"
            "改为可选硬上限——编程任务真实耗时远超固定阈值，常规保护依赖"
            " TASK_NO_PROGRESS_TIMEOUT_SECONDS 无进度看门狗"
        ),
    )
    task_no_progress_timeout_seconds: int = Field(
        default=1800,
        ge=60,
        validation_alias="TASK_NO_PROGRESS_TIMEOUT_SECONDS",
        description=(
            "无进度看门狗阈值（秒，ADR-055）：任何执行活动（SSE 事件/工具调用/进度上报/"
            "上下文压缩）都会重置计时，仅完全无活动超过该值时中断任务并上报失败"
        ),
    )
    agent_progress_heartbeat_seconds: int = Field(
        default=20,
        ge=5,
        validation_alias="AGENT_PROGRESS_HEARTBEAT_SECONDS",
        description=(
            "静默期进度心跳间隔（秒，ADR-055 P2-13）：长思考/长命令/压缩期间无事件时，"
            "按该间隔补发进度事件，避免前端看起来卡死"
        ),
    )
    workspace_root: str = Field(
        default="/workspace",
        validation_alias="WORKSPACE_ROOT",
        description="工作目录根路径",
    )
    storage_endpoint: str | None = Field(
        default=None,
        validation_alias="STORAGE_ENDPOINT",
        description="共享存储地址",
    )
    storage_bucket: str | None = Field(
        default=None,
        validation_alias="STORAGE_BUCKET",
        description="存储桶名",
    )
    tilacode_binary_path: str = Field(
        default="/opt/opc-agent-programming/bin/tilacode",
        validation_alias="TILACODE_BINARY_PATH",
        description="tilacode 预编译二进制路径",
    )
    tilacode_port: int = Field(
        default=4096,
        validation_alias="TILACODE_PORT",
        description="tilacode server 监听端口",
    )
    tilacode_host: str = Field(
        default="127.0.0.1",
        validation_alias="TILACODE_HOST",
        description="tilacode server 监听地址",
    )
    tilacode_workspace_root: str = Field(
        default="/opt/opc-agent-code/workspace",
        validation_alias="TILACODE_WORKSPACE_ROOT",
        description="tilacode 任务工作目录根",
    )
    tilacode_health_check_interval: int = Field(
        default=30,
        validation_alias="TILACODE_HEALTH_CHECK_INTERVAL",
        description="tilacode 健康检查间隔（秒）",
    )
    tilacode_auto_restart: bool = Field(
        default=True,
        validation_alias="TILACODE_AUTO_RESTART",
        description="tilacode 进程异常退出是否自动重启",
    )
    tilacode_step_idle_timeout_seconds: float = Field(
        default=30,
        validation_alias="TILACODE_STEP_IDLE_TIMEOUT_SECONDS",
        description="完成判定静默期（秒）：step.ended 后无新 step.started 即判定完成",
    )
    tilacode_dispatch_preflight: bool = Field(
        default=True,
        validation_alias="TILACODE_DISPATCH_PREFLIGHT",
        description=(
            "派发前模型预检（ADR-061 C1）：create_session 前确认中枢下发的 provider/模型"
            "在 tilacode 模型目录中可解析，缺失即明确失败而非卡到 startup_stalled；"
            "置 0 可临时关闭（应急开关，正常保持开启）"
        ),
    )
    tilacode_config_dir: str | None = Field(
        default=None,
        validation_alias="TILACODE_CONFIG_DIR",
        description=(
            "用户级 tilacode 配置目录（ADR-055 D3）：执行模型凭据（apiKey）写入此处，"
            "不落任务工作目录；留空按平台缺省（XDG_CONFIG_HOME/APPDATA/~/.config）+ tilacode"
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
        default=18080,
        ge=1,
        le=65535,
        validation_alias="AGENT_API_PORT",
        description="通知 API 监听端口（中枢重启反连用，ADR-035）",
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
        并在重启后主动反连；未配置（旧安装）时返回 None，中枢跳过反连，
        行为同现状。
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

    @property
    def tilacode_server_url(self) -> str:
        """返回 tilacode server 的 HTTP 基础 URL。"""
        return f"http://{self.tilacode_host}:{self.tilacode_port}"


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

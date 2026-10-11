"""Agent 节点相关 Pydantic 模型。"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import PaginatedResponse


class ReportedAddress(BaseModel):
    """Agent 自报通知地址（ADR-035）。

    Agent 安装配置生成的通知 API 地址与认证 Token，随首次注册上报落库，
    中枢重启后据此主动反连。
    """

    ip: str = Field(..., min_length=1, max_length=64, description="Agent 通知 API 可达 IP")
    api_port: int = Field(..., ge=1, le=65535, description="Agent 通知 API 端口")
    api_token: str = Field(..., min_length=1, max_length=128, description="通知 API 认证 Token")


class AgentNodeRegisterRequest(BaseModel):
    """Agent 节点注册请求模型。"""

    name: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="节点名称，必填",
    )
    agent_type: str = Field(
        ...,
        min_length=1,
        max_length=32,
        description="Agent 类型，如 programming / video / copywriting",
    )
    capabilities: list[str] = Field(
        default=[],
        description='能力标签列表，如 ["react", "fastapi"]',
    )
    client_id: str | None = Field(
        None,
        description="客户端唯一标识，兼容旧版 Agent 可为空",
    )
    hardware_info: dict[str, Any] | None = Field(
        None,
        description="节点硬件信息，如 CPU 核心数、内存、GPU",
    )
    version: str | None = Field(
        None,
        description="Agent 版本号",
    )
    reported_address: ReportedAddress | None = Field(
        None,
        description="自报通知地址 {ip, api_port, api_token}（ADR-035，旧版 Agent 不携带）",
    )


class AgentNodeResponse(BaseModel):
    """Agent 节点响应模型。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="节点 ID")
    name: str = Field(..., description="节点名称")
    agent_type: str = Field(..., description="Agent 类型")
    capabilities: list[str] | None = Field(None, description="能力标签列表")
    status: str = Field(..., description="节点状态")
    load: float = Field(..., description="节点负载")
    registration_token: str = Field(..., description="注册令牌，用于后续 WebSocket 认证")
    last_heartbeat_at: datetime | None = Field(None, description="最后心跳时间")
    reported_ip: str | None = Field(
        None,
        description="节点 IP 地址（WebSocket 连接来源优先，ADR-035 自报地址兜底）",
    )
    metrics_snapshot: dict[str, Any] | None = Field(
        None,
        description="最近一次上报的系统资源监控快照",
    )
    created_at: datetime = Field(..., description="创建时间")
    updated_at: datetime = Field(..., description="更新时间")


class AgentNodeListResponse(PaginatedResponse[AgentNodeResponse]):
    """Agent 节点列表分页响应模型。"""


class InstallScriptResponse(BaseModel):
    """Agent 安装脚本元数据响应模型。"""

    agent_type: str = Field(..., description="Agent 类型")
    os: str = Field(..., description="目标操作系统")
    script_url: str = Field(..., description="安装脚本下载地址")
    hub_url: str = Field(..., description="Agent 应注册到的中枢地址")
    checksum: str = Field(..., description="安装脚本校验和")
    version: str = Field(..., description="安装脚本版本号")


class RegistrationTokenResponse(BaseModel):
    """注册 Token 响应模型。"""

    registration_token: str = Field(..., description="新的注册令牌")


class AgentNodeUpdateRequest(BaseModel):
    """Agent 节点更新请求模型（ADR-016）。

    现阶段仅允许修改 ``name``（节点显示名）。``agent_type`` / ``client_id``
    等调度路由相关字段不在本 schema 中出现，由 Pydantic 直接屏蔽。
    """

    name: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="节点显示名；非空、不允许仅空白，长度 1-128",
    )


class AgentNodeMetricsResponse(BaseModel):
    """Agent 节点监控详情响应模型。"""

    model_config = ConfigDict(from_attributes=True)

    node_id: uuid.UUID = Field(..., description="节点 ID")
    is_online: bool = Field(..., description="节点是否在线")
    last_heartbeat_at: datetime | None = Field(None, description="最后心跳时间")
    metrics: dict[str, Any] | None = Field(
        None,
        description="最近一次上报的系统资源监控快照",
    )

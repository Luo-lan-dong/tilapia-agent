"""宝塔 MCP 服务器管理 Schema（ADR-049）。"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

ADDRESS_MODES = ("local", "public")


class BtMcpServerCreate(BaseModel):
    """创建宝塔 MCP 服务器连接。

    支持两种方式：手填各字段，或传 ``mcp_info``（宝塔插件生成的接入
    JSON，可含注释外的全部字段），后端解析并填充 token/双地址/证书。
    """

    name: str = Field(min_length=1, max_length=100, description="显示名（唯一）")
    local_url: str | None = Field(default=None, description="内网 MCP 地址")
    public_url: str | None = Field(default=None, description="公网 MCP 地址")
    address_mode: str = Field(default="local", description="使用地址：local/public")
    api_token: str | None = Field(default=None, description="Bearer Token")
    ca_cert: str | None = Field(default=None, description="CA 证书链 PEM 全文")
    enabled: bool = Field(default=True, description="是否启用")
    remark: str | None = Field(default=None, max_length=500, description="备注")
    mcp_info: dict[str, Any] | None = Field(
        default=None,
        description="宝塔插件生成的 mcp_info JSON（一键导入，与手填字段互补覆盖）",
    )

    @field_validator("address_mode")
    @classmethod
    def _check_address_mode(cls, value: str) -> str:
        if value not in ADDRESS_MODES:
            raise ValueError(f"address_mode 仅支持 {'/'.join(ADDRESS_MODES)}")
        return value

    def apply_mcp_info(self) -> None:
        """把 mcp_info JSON 中的字段合并进本对象（未知参考字段忽略）。"""
        info = self.mcp_info
        if not info:
            return
        token = info.get("api_token")
        if token and not self.api_token:
            self.api_token = str(token)
        local = info.get("local_host") or info.get("url")
        if local and not self.local_url:
            self.local_url = str(local)
        public = info.get("public_host")
        if public and not self.public_url:
            self.public_url = str(public)
        cert = info.get("tls_cert")
        if cert and not self.ca_cert:
            self.ca_cert = str(cert)

    def validate_completeness(self) -> None:
        """校验必填组合（local_url 与 api_token 至少齐备）。"""
        if not self.local_url and not self.public_url:
            raise ValueError("local_url 与 public_url 至少填写一项（或通过 mcp_info 导入）")
        if not self.api_token:
            raise ValueError("api_token 必填（或通过 mcp_info 导入）")


class BtMcpServerUpdate(BaseModel):
    """更新宝塔 MCP 服务器连接。

    凭据字段（api_token/ca_cert）缺省表示保留现值；传空串表示清除。
    """

    name: str | None = Field(default=None, min_length=1, max_length=100)
    local_url: str | None = None
    public_url: str | None = None
    address_mode: str | None = None
    api_token: str | None = Field(
        default=None,
        description="缺省保留现值；空串清除（清除后连接测试会失败）",
    )
    ca_cert: str | None = Field(default=None, description="缺省保留现值；空串清除")
    enabled: bool | None = None
    remark: str | None = Field(default=None, max_length=500)
    mcp_info: dict[str, Any] | None = Field(default=None, description="重新导入插件接入 JSON")

    @field_validator("address_mode")
    @classmethod
    def _check_address_mode(cls, value: str | None) -> str | None:
        if value is not None and value not in ADDRESS_MODES:
            raise ValueError(f"address_mode 仅支持 {'/'.join(ADDRESS_MODES)}")
        return value


class BtMcpServerResponse(BaseModel):
    """宝塔 MCP 服务器响应（凭据脱敏：只返回是否已配置）。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    local_url: str
    public_url: str | None
    address_mode: str
    has_token: bool
    has_ca_cert: bool
    enabled: bool
    remark: str | None
    created_at: datetime
    updated_at: datetime


class BtMcpServerListResponse(BaseModel):
    """宝塔 MCP 服务器列表响应。"""

    total: int
    items: list[BtMcpServerResponse]


class BtMcpToolInfo(BaseModel):
    """工具清单条目。"""

    name: str
    description: str = ""
    category: str
    risk: str


class BtMcpToolsResponse(BaseModel):
    """工具清单响应。"""

    total: int
    items: list[BtMcpToolInfo]


class BtMcpTestResponse(BaseModel):
    """连接测试响应。"""

    success: bool
    message: str
    server_info: dict[str, Any] | None = None
    tool_count: int = 0
    tool_summary: dict[str, int] | None = None

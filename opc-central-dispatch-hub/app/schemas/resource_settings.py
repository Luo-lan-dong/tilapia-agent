"""Agent 资源管理 DTO（ADR-022 / ADR-025）。"""

from pydantic import BaseModel, Field


class ResourceUpdateRequest(BaseModel):
    """应用更新请求：type=按 agent 类型更新该类型全部条目；keys=按条目；均缺省=全部过时资源。"""

    type: str | None = Field(default=None, description="agent 类型（如 code），更新该类型全部条目")
    keys: list[str] | None = Field(default=None, description="要更新的资源 key 列表")


class ResourceItemInfo(BaseModel):
    """单资源条目信息（本地 vs 云端对比）。"""

    key: str
    cloud_path: str | None = None
    cloud_sha256: str | None = None
    cloud_version: str | None = None
    size: int | None = None
    local_version: str | None = None
    local_sha256: str | None = None
    present: bool = False
    outdated: bool = False
    selected: bool = True


class AgentTypeInfo(BaseModel):
    """按 agent 类型分组的状态（ADR-025）。"""

    type: str
    has_update: bool = False
    resources: list[str] = []


class ResourceStatusResponse(BaseModel):
    """资源管理状态响应。"""

    status: str
    manifest_url: str | None = None
    local_revision: str | None = None
    cloud_revision: str | None = None
    last_checked_at: str | None = None
    last_synced_at: str | None = None
    message: str | None = None
    resources: list[ResourceItemInfo] = []
    agent_types: list[AgentTypeInfo] = []


class ResourceUpdateResponse(BaseModel):
    """应用更新/导入结果响应。"""

    status: str
    updated_keys: list[str] = []
    message: str | None = None

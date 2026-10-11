"""Agent 模板相关 Schema。"""

from pydantic import BaseModel, Field


class AgentTemplateVersion(BaseModel):
    """Agent 模板版本信息。"""

    version: str = Field(..., description="语义化版本号")
    download_path: str = Field(..., description="模板下载路径")
    agent_type: str = Field(..., description="Agent 类型")


class AgentTemplateListResponse(BaseModel):
    """模板列表响应。"""

    templates: list[AgentTemplateVersion] = Field(
        default_factory=list,
        description="模板版本列表",
    )

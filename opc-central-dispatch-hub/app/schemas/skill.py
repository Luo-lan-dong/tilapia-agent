"""技能相关 Pydantic Schema（ADR-033）。"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SkillResponse(BaseModel):
    """技能列表项。"""

    model_config = ConfigDict(from_attributes=True)

    skill_key: str = Field(description="技能唯一标识")
    name: str = Field(description="技能展示名称")
    description: str = Field(description="技能描述")
    parameters_summary: str = Field(description="参数摘要（人类可读）")
    returns: str = Field(description="返回结构说明")
    enabled: bool = Field(description="启用状态")


class SkillDetailResponse(SkillResponse):
    """技能详情（含完整参数 JSON Schema）。"""

    parameters: dict[str, Any] = Field(description="完整参数 JSON Schema")


class SkillUpdateRequest(BaseModel):
    """技能启用开关更新请求。"""

    enabled: bool = Field(description="是否启用")


class AIDispatchSettingsResponse(BaseModel):
    """AI 智能调度设置响应。"""

    enabled: bool = Field(description="AI 智能调度开关状态")
    timeout_seconds: float = Field(description="批量决策 LLM 超时预算（秒）")
    max_tool_rounds: int = Field(description="对话 Agent 最大技能调用轮数")


class AIDispatchSettingsUpdate(BaseModel):
    """AI 智能调度设置更新请求。"""

    enabled: bool = Field(description="是否开启 AI 智能调度")


class AutoWakeSettingsResponse(BaseModel):
    """任务终态自动唤醒编排设置响应（ADR-066）。"""

    enabled: bool = Field(description="任务终态自动唤醒编排开关状态")


class AutoWakeSettingsUpdate(BaseModel):
    """任务终态自动唤醒编排设置更新请求（ADR-066）。"""

    enabled: bool = Field(description="是否开启任务终态自动唤醒编排")

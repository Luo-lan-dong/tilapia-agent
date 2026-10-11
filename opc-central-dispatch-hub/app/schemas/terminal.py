"""终端相关 Pydantic Schema（ADR-053）。"""

from pydantic import BaseModel, Field

TERMINAL_MODES = ("manual", "ai_auto", "full")


class PermissionModeResponse(BaseModel):
    """终端权限模式响应。"""

    mode: str = Field(description="当前权限模式：manual/ai_auto/full")


class PermissionModeUpdate(BaseModel):
    """终端权限模式更新请求。"""

    mode: str = Field(description="目标权限模式：manual/ai_auto/full")


class ApprovalDecisionRequest(BaseModel):
    """终端命令审批决定请求。"""

    approve: bool = Field(description="true=批准执行；false=拒绝")

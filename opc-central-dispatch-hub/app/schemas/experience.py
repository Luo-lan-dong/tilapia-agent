"""经验记忆管理 Schema（ADR-050）。"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

EXPERIENCE_STATUSES = ("pending", "resolved")


class ExperienceUpdate(BaseModel):
    """编辑/回填经验条目（全部可选，缺省不更新）。"""

    tool: str | None = Field(default=None, max_length=64, description="工具名")
    error_signature: str | None = Field(default=None, max_length=255, description="错误签名")
    problem: str | None = Field(default=None, description="现象快照")
    root_cause: str | None = Field(default=None, description="根因（总结回填）")
    solution: str | None = Field(default=None, description="解法（总结回填）")
    status: str | None = Field(default=None, description="pending/resolved")

    @field_validator("status")
    @classmethod
    def _check_status(cls, value: str | None) -> str | None:
        if value is not None and value not in EXPERIENCE_STATUSES:
            raise ValueError(f"status 仅支持 {'/'.join(EXPERIENCE_STATUSES)}")
        return value


class ExperienceResponse(BaseModel):
    """经验条目响应。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tool: str
    error_signature: str
    problem: str | None
    root_cause: str | None
    solution: str | None
    status: str
    session_id: str | None
    hit_count: int
    resolved_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ExperienceListResponse(BaseModel):
    """经验条目分页列表。"""

    total: int
    page: int
    page_size: int
    items: list[ExperienceResponse]

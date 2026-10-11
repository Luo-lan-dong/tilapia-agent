"""开发规则文档 Schema（ADR-048）。"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class StandardDocCreate(BaseModel):
    """创建规则文档请求。"""

    name: str = Field(min_length=1, max_length=100, description="文档名（唯一）")
    category: str | None = Field(
        None, max_length=50, description="分类：coding-standard/decision/custom"
    )
    description: str | None = Field(None, max_length=500, description="用途说明")
    apply_when: str | None = Field(
        None, max_length=500, description="何时必须加载（节点端 AGENTS.md 索引用）"
    )
    content: str = Field(min_length=1, description="Markdown 全文")


class StandardDocUpdate(BaseModel):
    """更新规则文档请求。

    name 不可修改（节点端文件名与历史快照的稳定锚点）；更新时 version 自增。
    """

    category: str | None = Field(None, max_length=50, description="分类")
    description: str | None = Field(None, max_length=500, description="用途说明")
    apply_when: str | None = Field(None, max_length=500, description="何时必须加载")
    content: str | None = Field(None, min_length=1, description="Markdown 全文")


class StandardDocResponse(BaseModel):
    """规则文档条目响应（列表用，不含 content）。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="文档 ID")
    name: str = Field(..., description="文档名")
    category: str | None = Field(None, description="分类")
    description: str | None = Field(None, description="用途说明")
    apply_when: str | None = Field(None, description="何时必须加载")
    version: int = Field(..., description="版本号")
    is_builtin: bool = Field(..., description="是否预置模板")
    created_at: datetime = Field(..., description="创建时间")
    updated_at: datetime = Field(..., description="更新时间")


class StandardDocDetailResponse(StandardDocResponse):
    """规则文档详情响应（含 content）。"""

    content: str = Field(..., description="Markdown 全文")


class StandardDocListResponse(BaseModel):
    """规则文档列表响应（文档数量少，不做分页）。"""

    total: int = Field(..., description="文档总数")
    items: list[StandardDocResponse] = Field(..., description="文档列表")

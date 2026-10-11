"""通用 Pydantic 响应模型。"""

import uuid
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """统一 API 响应格式。

    所有接口无论成功或失败均返回此结构，便于前端统一处理。
    ``request_id`` 由中间件统一注入，用于链路追踪。
    """

    code: int = Field(..., description="业务状态码，0 表示成功，非 0 表示错误")
    message: str = Field(..., description="可读提示信息")
    data: T | None = Field(None, description="业务数据")
    request_id: str = Field(
        default_factory=lambda: f"req_{uuid.uuid4().hex}",
        description="每次请求唯一 ID，便于链路追踪",
    )


class PaginationInfo(BaseModel):
    """分页元信息。"""

    page: int = Field(..., description="当前页码，从 1 开始")
    page_size: int = Field(..., description="每页条数")
    total: int = Field(..., description="总记录数")
    total_pages: int = Field(..., description="总页数")


class PaginatedResponse(BaseModel, Generic[T]):
    """通用分页响应模型。

    列表查询接口统一返回 ``items``（当前页数据）和 ``pagination``（分页元信息），
    便于前端表格/卡片展示与翻页。
    """

    items: list[T] = Field(..., description="当前页数据列表")
    pagination: PaginationInfo = Field(..., description="分页元信息")

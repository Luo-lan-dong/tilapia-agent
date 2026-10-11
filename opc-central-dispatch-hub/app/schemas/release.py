"""产品版本发布与中枢检查更新 DTO（ADR-026）。

中枢 API 命名惯例为 snake_case（与 SystemVersionResponse / ResourceItemInfo 一致），
云端资源仓库下发的 camelCase 字段已在 release_service.fetch_releases 解析层转换。
"""

from pydantic import BaseModel, Field


class ReleaseItemInfo(BaseModel):
    """单条版本发布记录。"""

    product: str
    version: str
    title: str
    content: str
    release_date: str | None = None
    download_url: str | None = None


class ProductGroupInfo(BaseModel):
    """按产品线分组的版本列表。"""

    product: str
    label: str
    releases: list[ReleaseItemInfo] = []


class ReleasesResponse(BaseModel):
    """更新日志分组响应（error 非空表示版本服务不可达）。"""

    products: list[ProductGroupInfo] = []
    error: str | None = None


class HubUpdateCheckResponse(BaseModel):
    """检查中枢更新结果。"""

    status: str
    latest_version: str | None = Field(default=None)
    current_version: str | None = Field(default=None)
    title: str | None = None
    content: str | None = None
    download_url: str | None = None
    message: str | None = None

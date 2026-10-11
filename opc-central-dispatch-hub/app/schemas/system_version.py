"""系统版本相关 Pydantic Schema。"""

import datetime
import uuid

from pydantic import BaseModel, ConfigDict, Field


class SystemVersionResponse(BaseModel):
    """系统版本响应模型。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(description="版本记录 ID")
    version: str = Field(description="版本号，如 v0.1.0")
    title: str = Field(description="版本标题")
    content: str = Field(description="更新内容（纯文本，按行展示）")
    release_date: datetime.date = Field(description="发布日期")
    is_current: bool = Field(description="是否当前版本")

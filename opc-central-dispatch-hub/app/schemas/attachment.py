"""会话附件 Pydantic Schema（ADR-039）。"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class AttachmentResponse(BaseModel):
    """附件上传/查询响应条目。"""

    id: uuid.UUID = Field(..., description="附件 ID")
    file_name: str = Field(..., description="原始文件名")
    mime: str = Field(..., description="MIME 类型")
    size: int = Field(..., description="文件字节数")
    kind: str = Field(..., description="附件类别：image | file | archive")
    created_at: datetime | None = Field(default=None, description="上传时间")


class TaskAttachmentInfo(BaseModel):
    """派发 payload 中的附件信息（Agent 凭 url + token 一次性下载）。"""

    id: uuid.UUID = Field(..., description="附件 ID")
    file_name: str = Field(..., description="原始文件名")
    mime: str = Field(..., description="MIME 类型")
    size: int = Field(..., description="文件字节数")
    url: str = Field(..., description="中枢侧一次性下载相对路径（拼当前中枢地址）")

"""健康检查相关 Schema。"""

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """健康检查响应模型。"""

    status: str = Field(..., description="服务状态")
    service: str = Field(..., description="服务名称")
    version: str = Field(..., description="服务版本")

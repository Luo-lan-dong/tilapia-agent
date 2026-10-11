"""LLM Provider 相关 Pydantic Schema。"""

import uuid

from pydantic import BaseModel, Field, field_serializer


def _mask_api_key(value: str) -> str:
    """对 API Key 进行脱敏展示。

    长度 >= 12 时返回前 4 位 + *** + 后 4 位，否则返回 ***。
    """
    if len(value) >= 12:
        return f"{value[:4]}***{value[-4:]}"
    return "***"


class LLMProviderResponse(BaseModel):
    """LLM Provider 配置响应模型。"""

    provider: str = Field(..., description="Provider 类型")
    api_key: str = Field(..., description="API Key")
    base_url: str = Field(..., description="接口基础 URL")
    model: str = Field(..., description="模型名称")
    is_active: bool = Field(..., description="是否启用")

    @field_serializer("api_key")
    def _serialize_api_key(self, value: str) -> str:
        return _mask_api_key(value)


class LLMProviderUpdate(BaseModel):
    """LLM Provider 配置更新请求模型。"""

    api_key: str = Field(..., min_length=1, description="API Key")
    base_url: str = Field(..., min_length=1, description="接口基础 URL")
    model: str = Field(..., min_length=1, description="模型名称")


class LLMModelCreate(BaseModel):
    """添加自定义模型请求（ADR-030：OpenAI 兼容端点）。"""

    provider: str = Field(
        ...,
        min_length=1,
        max_length=32,
        description="服务商名称（任意可读名，如 DeepSeek / 硅基流动 / OpenRouter）",
    )
    model: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="模型 ID（OpenAI 兼容端点的 model 参数）",
    )
    base_url: str = Field(
        ...,
        min_length=1,
        max_length=256,
        description="OpenAI 兼容 Base URL（如 https://api.siliconflow.cn/v1）",
    )
    api_key: str = Field(..., min_length=1, max_length=512, description="API Key")
    supports_vision: bool = Field(
        False,
        description="是否支持图片输入（ADR-039）；带图任务的自动选模与提示依据",
    )
    context_window: int = Field(
        128000,
        ge=1024,
        le=10_000_000,
        description="模型上下文窗口 token（ADR-041/056）；占用百分比与压缩触发线的分母",
    )


class LLMModelUpdate(BaseModel):
    """编辑自定义模型请求（ADR-041：窗口元数据等）。"""

    supports_vision: bool | None = Field(None, description="是否支持图片输入（ADR-039）")
    context_window: int | None = Field(
        None,
        ge=1024,
        le=10_000_000,
        description="模型上下文窗口 token（ADR-041）",
    )


class LLMModelResponse(BaseModel):
    """自定义模型条目响应模型（api_key 脱敏）。"""

    id: uuid.UUID = Field(..., description="模型配置 ID（执行模型指定用）")
    provider: str = Field(..., description="服务商名称")
    model: str = Field(..., description="模型 ID")
    base_url: str = Field(..., description="OpenAI 兼容 Base URL")
    api_key: str = Field(..., description="API Key（脱敏）")
    is_active: bool = Field(..., description="是否为当前使用中模型")
    supports_vision: bool = Field(..., description="是否支持图片输入（ADR-039）")
    context_window: int = Field(..., description="模型上下文窗口 token（ADR-041）")

    @field_serializer("api_key")
    def _serialize_api_key(self, value: str) -> str:
        return _mask_api_key(value)

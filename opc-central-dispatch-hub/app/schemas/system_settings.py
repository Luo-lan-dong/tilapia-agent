"""系统设置相关 Pydantic Schema。"""

from typing import Literal

from pydantic import BaseModel, Field


class NetworkSettingsResponse(BaseModel):
    """网络配置响应模型。"""

    hub_public_url: str | None = Field(
        default=None,
        description="Agent 安装命令使用的中枢公网/局域网地址，如 http://192.168.1.10:8000",
    )
    frontend_url: str | None = Field(
        default=None,
        description="前端控制台访问地址，如 http://192.168.1.10:5173",
    )
    actual_port: int | None = Field(
        default=None,
        description="后端实际监听端口（ADR-031：端口避让后可能与 Hub URL 端口不同；手动 uvicorn 启动时可能为空）",
    )


class NetworkSettingsUpdate(BaseModel):
    """网络配置更新请求模型。"""

    hub_public_url: str | None = Field(
        default=None,
        description="Agent 安装命令使用的中枢公网/局域网地址",
    )
    frontend_url: str | None = Field(
        default=None,
        description="前端控制台访问地址",
    )


class PermissionPolicyAllowEntry(BaseModel):
    """权限白名单条目（ADR-061 M2b）：``action`` 为工具/动作名，``resource`` 为资源模式。"""

    action: str = Field(..., min_length=1, max_length=64, description="工具/动作名，如 read、bash、edit")
    resource: str = Field(..., min_length=1, max_length=512, description="资源模式，如 /usr/include/**、git *")


class PermissionPolicyResponse(BaseModel):
    """全局权限策略响应（ADR-061 M2b）。"""

    mode: Literal["interactive", "unattended"] = Field(
        ..., description="权限模式；unattended 下「本该询问人」的请求一律按拒绝处理"
    )
    allow: list[PermissionPolicyAllowEntry] = Field(
        default_factory=list, description="allow 白名单（工作区外的显式例外）；不含 blanket allow"
    )


class PermissionPolicyUpdate(BaseModel):
    """全局权限策略更新请求（ADR-061 M2b）。

    仅表达 **allow 白名单**：deny 由 tilacode 内置护栏 + ``unattended`` 模式保证，
    不需要也不允许在此下发 blanket allow（``{"*", "*"}`` 会被服务层丢弃并告警）。
    """

    model_config = {"extra": "forbid"}

    mode: Literal["interactive", "unattended"] = Field(
        default="unattended", description="权限模式；缺省 unattended（无人值守）"
    )
    allow: list[PermissionPolicyAllowEntry] = Field(
        default_factory=list, max_length=200, description="allow 白名单条目"
    )


class BudgetPolicyResponse(BaseModel):
    """全局执行预算响应（ADR-061 M5b）。"""

    max_steps: int = Field(..., description="LLM 轮次上限（steps）")
    max_tokens: int = Field(..., description="token 总量上限（Σ input+output）")
    soft_ratio: float = Field(..., description="软阈值比例（默认 0.8：达到即发收尾指令）")


class BudgetPolicyUpdate(BaseModel):
    """全局执行预算更新请求（ADR-061 M5b，Q9/Q11）。

    度量双维度：steps（LLM 轮次）与 tokens（Σ input+output），**任一超限即触发**；
    80% 只发收尾指令（不停任务），100% 强制停止。任务级可在 `tasks.budget` 按字段覆盖。
    """

    model_config = {"extra": "forbid"}

    max_steps: int = Field(default=60, ge=1, le=10_000, description="LLM 轮次上限")
    max_tokens: int = Field(default=2_000_000, ge=1, description="token 总量上限")
    soft_ratio: float = Field(default=0.8, gt=0.0, lt=1.0, description="软阈值比例")

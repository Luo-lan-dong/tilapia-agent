"""系统设置数据库模型。"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class SystemSettings(Base, TimestampMixin):
    """系统网络与全局设置表。

    使用单行记录存储可被运行时修改的全局配置，避免依赖环境变量重启服务。
    """

    __tablename__ = "system_settings"
    __table_args__ = (
        {
            "comment": "系统全局设置，当前用于存储 Hub 公网地址与前端访问地址",
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="主键 ID",
    )
    hub_public_url: Mapped[str | None] = mapped_column(
        String(256),
        nullable=True,
        comment="Agent 安装命令使用的中枢公网/局域网地址，如 http://192.168.1.10:8000",
    )
    frontend_url: Mapped[str | None] = mapped_column(
        String(256),
        nullable=True,
        comment="前端控制台访问地址，用于展示与跨域配置参考，如 http://192.168.1.10:5173",
    )
    agent_resource_manifest_url: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
        comment="Agent 资源云端仓库 manifest URL（ADR-022）；ADR-025 起为运行期缓存，由检查流程从管理后台自动下发维护",
    )
    ai_dispatch_enabled: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
        comment="AI 智能调度开关（ADR-033）；NULL 时回退环境变量 AI_DISPATCH_ENABLED 初始值",
    )
    orchestrator_auto_wake_enabled: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
        comment="任务终态自动唤醒编排开关（ADR-066）；NULL 时回退环境变量 ORCHESTRATOR_AUTO_WAKE_ENABLED 初始值",
    )
    alerts_read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="仪表盘异常告警已读水位线（ADR-038）：created_at 早于等于该时刻的告警不再返回",
    )
    terminal_permission_mode: Mapped[str | None] = mapped_column(
        String(16),
        nullable=True,
        comment="终端命令执行权限模式（ADR-053）：manual/ai_auto/full；NULL 回退 manual",
    )
    permission_policy: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment=(
            "子 Agent 权限策略（ADR-061 M2b，全局单条）：{mode: unattended|interactive, "
            "allow: [{action, resource}]}；**只表达 allow 白名单**，NULL 视为最严（unattended+空白名单）"
        ),
    )
    budget_policy: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment=(
            "任务执行预算（ADR-061 M5b，全局默认）：{max_steps, max_tokens, soft_ratio}；"
            "80% 发收尾指令、100% 强制停止；NULL 用内置默认"
        ),
    )

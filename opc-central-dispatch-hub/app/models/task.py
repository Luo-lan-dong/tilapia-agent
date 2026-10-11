"""任务数据库模型。"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class TaskStatus(StrEnum):
    """任务状态枚举。"""

    PENDING = "pending"
    ASSIGNED = "assigned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    #: 待人工确认（ADR-038）：plan 模式任务方案产出待审批
    REVIEW = "review"
    #: 待确认改派（ADR-059 D5）：故障后重新排队但**不允许自动派发**，
    #: 等用户确认后回 pending。仅当 AI 智能调度关闭时使用。
    REQUEUE_PENDING = "requeue_pending"
    #: 验收阻断（ADR-061 M4）：代码已完成但**必过验收未通过或无法验证**，
    #: 等用户在任务对话区决定（接受现状 / 继续修复）；24h 无回应由调度器兜底 failed
    VERIFY_BLOCKED = "verify_blocked"


#: 调度器可派发的状态（ADR-059 D5：`requeue_pending` 必须显式排除，
#: 否则「先询问」会退化成「悄悄自动改派」）
DISPATCHABLE_STATUSES: tuple[str, ...] = (TaskStatus.PENDING.value,)

#: 非终态状态集合（可取消、可被用户操作）
ACTIVE_STATUSES: tuple[str, ...] = (
    TaskStatus.PENDING.value,
    TaskStatus.REQUEUE_PENDING.value,
    TaskStatus.REVIEW.value,
    # ADR-061 M4：等用户决定的验收阻断态同样是「活」的（可取消/可决定）
    TaskStatus.VERIFY_BLOCKED.value,
)


class Task(Base, TimestampMixin):
    """任务表模型。"""

    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'assigned', 'running', 'completed', 'failed', "
            "'review', 'requeue_pending')",
            name="status",
        ),
        CheckConstraint(
            "execution_mode IN ('plan', 'build')",
            name="execution_mode",
        ),
        Index(
            "idx_tasks_specification_gin",
            "specification",
            postgresql_using="gin",
        ),
        Index(
            "idx_tasks_parent_task_id",
            "parent_task_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="主键 ID",
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="任务标题",
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="任务描述",
    )
    agent_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
        comment="Agent 类型",
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=TaskStatus.PENDING.value,
        server_default=text("'pending'"),
        index=True,
        comment="任务状态",
    )
    assigned_node_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_nodes.id", name="fk_tasks_agent_nodes_assigned_node_id"),
        nullable=True,
        index=True,
        comment="分配节点 ID",
    )
    # ADR-059 D2：用户/AI 指定的目标节点（排队等待对象）。有值时只等该节点，
    # 不经 AI 决策、不派往他处；节点达标当轮即派送。NULL=无指定，走常规预筛。
    target_node_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_nodes.id", name="fk_tasks_agent_nodes_target_node_id"),
        nullable=True,
        index=True,
        comment="用户/AI 指定的目标节点 ID（ADR-059 排队等待对象）",
    )
    # ADR-059 D3：最近一次节点拒单原因（reason 域见 api-standards §agent.task_rejected）
    last_reject_reason: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="最近一次节点拒单原因（ADR-059）：resource_busy/slots_full/bridge_not_ready/...",
    )
    # ADR-059 D3：当前排队原因（含缺口数值，如「可用内存 0.8GB ≤ 1.0GB」）
    dispatch_hold_reason: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
        comment="当前排队原因（ADR-059 D3，含缺口数值）；NULL=未在排队",
    )
    # ADR-059 D2 例外：用户明确要求越过门槛强行派送时置真（审计用）
    forced_dispatch: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        comment="是否用户明确要求越过准入门槛强行派送（ADR-059 D2 例外）",
    )
    progress: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
        comment="任务进度百分比",
    )
    priority: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        default="P2",
        server_default=text("'P2'"),
        comment="任务优先级 P0|P1|P2（ADR-038）；暂无自动定级，默认 P2",
    )
    specification: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="任务规范三件套(spec/tasks/checklist)",
    )
    source: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="manual",
        server_default=text("'manual'"),
        comment="任务来源",
    )
    execution_target: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        comment="MVP阶段用户手动指定的子系统标识",
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("task_building_sessions.id", name="fk_tasks_task_building_sessions_session_id"),
        nullable=True,
        index=True,
        comment="关联的任务构建会话 ID",
    )
    execution_mode: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="build",
        server_default=text("'build'"),
        comment="执行模式 plan|build",
    )
    dispatch_type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="auto",
        server_default=text("'auto'"),
        comment="派发模式：auto 调度器自动 / manual 用户手动",
    )
    dispatch_hold: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        comment="派发确认门禁（ADR-067）：True=等待用户确认派发，调度器跳过；dispatch_task 执行时清除",
    )
    dispatch_window_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="手动派发窗口截止时间（UTC）；窗口内用户可手动 dispatch，窗口外由调度器接管",
    )
    parent_task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="直接父任务 ID（任务迭代链）",
    )
    root_task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="任务链根任务 ID（冗余存储）",
    )
    change_brief: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="本轮修改说明（中枢拼装）",
    )
    plan_content: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="plan 模式方案全文",
    )
    change_summary: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="build 模式完成时上报的本轮修改说明",
    )
    output_dir: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
        comment="任务产物保存路径（Agent 节点本地目录语义，ADR-028）；空则由 Agent 按默认规则分配",
    )
    executor_model: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="子 Agent 执行模型选择（ADR-029）：{source: hub|opencode_free, model?}；NULL 视为 hub 默认。凭据不落库",
    )
    executor_model_actual: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
        comment="节点上报的实际执行模型（ADR-029 增强）：如 deepseek/deepseek-chat；降级时带原因说明",
    )
    todos: Mapped[list[dict] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Agent 实时上报的待办列表",
    )
    progress_stage: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        comment="Agent 实时上报的执行阶段（ADR-055）：preparing/planning/coding/editing/"
        "running_command/searching/testing/compacting/summarizing/idle；NULL=未上报",
    )
    progress_message: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        comment="Agent 实时上报的当前动作描述（ADR-055，节点侧截断 ≤120 字符）；NULL=未上报",
    )
    progress_tool: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="Agent 实时上报的当前工具名（ADR-061 M5a，UT-M5-4：此前节点上报后被丢弃）；NULL=未上报",
    )
    rule_docs: Mapped[list[dict] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="任务绑定的规则文档（ADR-048）：[{doc_id, version}] 创建时锁定版本；NULL=不下发",
    )
    environment_requirements: Mapped[list[dict] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="环境要求清单（ADR-048 P2）：[{name, check_cmd, min_version?, install_hint?}]；NULL=无环境要求",
    )
    verify_report: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment=(
            "分层验收报告（ADR-061 M4）：{status, source, checks:[{layer,name,cmd,must_pass,"
            "status,exit_code,duration_ms,output_tail}], repair_rounds?, summary?}；NULL=未跑验收"
        ),
    )
    verify_blocked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="验收阻断时刻（ADR-061 M4）：调度器据此做 24h 无回应兜底置 failed；NULL=未阻断",
    )
    budget: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment=(
            "任务级预算 override（ADR-061 M5b）：{max_steps?, max_tokens?, soft_ratio?}；"
            "按字段覆盖全局 budget_policy；NULL=完全用全局"
        ),
    )
    budget_state: Mapped[str | None] = mapped_column(
        String(16),
        nullable=True,
        comment=(
            "预算闸状态（ADR-061 M5b，幂等用）：NULL=未触发 / soft_notified=已发收尾指令 / "
            "hard_stopped=已按预算强制停止"
        ),
    )

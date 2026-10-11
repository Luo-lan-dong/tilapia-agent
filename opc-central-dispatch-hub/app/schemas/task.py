"""任务相关 Pydantic 模型。"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.common import PaginatedResponse
from app.schemas.task_building import TaskSpecification


class ExecutorModelSelection(BaseModel):
    """子 Agent 执行模型选择（ADR-029 / ADR-030 增强）。

    ``source=hub`` 使用中枢系统设置当前使用中的模型（model 可空 = 跟随系统配置）；
    ``source=custom`` 使用 provider_id 指定的自定义模型行（ADR-030）。凭据不随
    本结构存储，派发时由中枢实时注入。
    """

    model_config = ConfigDict(extra="forbid")

    source: Literal["hub", "custom", "auto"] = Field(
        "hub",
        description=(
            "模型来源：hub 当前使用中 / custom 指定自定义模型行（provider_id）/ "
            "auto 由中枢按任务内容自动选择（ADR-039：带图任务优先视觉模型）"
        ),
    )
    provider_id: uuid.UUID | None = Field(
        None,
        description="custom 来源：指向 llm_providers.id 的模型配置 ID",
    )
    model: str | None = Field(
        None,
        max_length=64,
        description="模型 ID；缺省时 hub 来源跟随系统配置",
    )


def normalize_executor_model(selection: ExecutorModelSelection | None) -> dict | None:
    """归一化执行模型选择为落库字典；None 原样返回（视为 hub 默认）。"""
    if selection is None:
        return None
    if selection.source == "custom":
        return {
            "source": "custom",
            "provider_id": str(selection.provider_id) if selection.provider_id else None,
            "model": selection.model,
        }
    return {"source": "hub", "model": selection.model}


class TaskCreateRequest(BaseModel):
    """创建任务请求模型。"""

    requirement: str = Field(
        ...,
        min_length=1,
        description="用户自然语言需求，最少 1 个字符",
    )
    specification: TaskSpecification | None = Field(
        None,
        description="任务规范三件套，提供时直接保存不再调用 Planning Agent",
    )
    budget: dict | None = Field(
        None,
        description=(
            "任务级执行预算 override（ADR-061 M5b，可选）：{max_steps?, max_tokens?, soft_ratio?}，"
            "按字段覆盖全局 budget_policy；NULL=完全用全局"
        ),
    )
    session_id: uuid.UUID | None = Field(
        None,
        description="关联的任务构建会话 ID",
    )
    dispatch_hold: bool = Field(
        False,
        description="派发确认门禁（ADR-067）：True=等待用户确认派发，调度器跳过；编排 AI 创建路径置 True",
    )
    parent_task_id: uuid.UUID | None = Field(
        None,
        description="直接父任务 ID，非空时视为任务迭代链上的新一轮修改",
    )
    change_brief: str | None = Field(
        None,
        description="本轮修改说明，随派发下发给 Agent",
    )
    execution_mode: Literal["plan", "build"] | None = Field(
        None,
        description="执行模式：plan 先规划、build 直接开发；缺省按 build 处理",
    )
    output_dir: str | None = Field(
        None,
        description="任务产物保存路径（Agent 节点本地目录语义，ADR-028）；缺省由 Agent 按默认规则分配",
    )
    executor_model: ExecutorModelSelection | None = Field(
        None,
        description="子 Agent 执行模型选择（ADR-029）；缺省 = 中枢配置模型",
    )
    rule_doc_ids: list[uuid.UUID] | None = Field(
        None,
        description="任务绑定的规则文档 ID 列表（ADR-048）；创建时锁定内容快照，缺省不下发规则包",
    )
    environment_requirements: list[dict] | None = Field(
        None,
        description=(
            "环境要求清单（ADR-048 P2）：[{name, check_cmd, min_version?, install_hint?}]，"
            "由子节点 AI 在项目首次开发时逐条检查、缺失自行安装；缺省无环境要求"
        ),
    )
    priority: Literal["P0", "P1", "P2"] | None = Field(
        None,
        description="任务优先级（ADR-038）；缺省落库为 P2",
    )


class TaskResponse(BaseModel):
    """任务响应模型。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="任务 ID")
    title: str = Field(..., description="任务标题")
    description: str | None = Field(None, description="任务描述")
    agent_type: str = Field(..., description="Agent 类型")
    status: str = Field(..., description="任务状态")
    priority: str = Field(..., description="任务优先级 P0|P1|P2（ADR-038）")
    progress: int = Field(..., description="任务进度百分比")
    rule_docs: list[dict] | None = Field(
        None,
        description="绑定的规则文档快照（ADR-048）；NULL=未绑定",
    )
    environment_requirements: list[dict] | None = Field(
        None,
        description="环境要求清单（ADR-048 P2）；NULL=无环境要求",
    )
    assigned_node_id: uuid.UUID | None = Field(None, description="分配节点 ID")
    assigned_node_name: str | None = Field(
        None,
        description="分配节点名称（JOIN agent_nodes.name；未分配时为 null）",
    )
    target_node_id: uuid.UUID | None = Field(
        None,
        description=(
            "用户/AI 指定的目标节点 ID（ADR-059 D2）：有值时任务排队只等该节点，"
            "节点达标当轮派送且不经 AI；NULL=无指定"
        ),
    )
    target_node_name: str | None = Field(
        None,
        description="目标节点名称（JOIN agent_nodes.name；无指定时为 null）",
    )
    last_reject_reason: str | None = Field(
        None,
        description=(
            "最近一次节点拒单原因（ADR-059 D3）：resource_busy/slots_full/"
            "bridge_not_ready/shutting_down；NULL=未被拒单"
        ),
    )
    dispatch_hold_reason: str | None = Field(
        None,
        description=(
            "当前排队原因（ADR-059 D3，含缺口数值，如「可用内存 0.8GB ≤ 1.0GB」）；NULL=未在排队"
        ),
    )
    forced_dispatch: bool = Field(
        False,
        description="是否用户明确要求越过准入门槛强行派送（ADR-059 D2 例外，审计用）",
    )

    @field_validator("forced_dispatch", mode="before")
    @classmethod
    def _default_forced_dispatch(cls, value: object) -> bool:
        """NULL 归一为 False。

        存量行（迁移前创建）与未 flush 的内存对象可能取到 ``None``，
        而该列为 NOT NULL DEFAULT false——不能因缺值让任务详情整体 500。
        """
        return bool(value) if value is not None else False

    source: str = Field(..., description="任务来源")
    execution_target: str | None = Field(None, description="执行目标子系统")
    specification: dict | None = Field(None, description="任务规范三件套")
    execution_mode: str = Field(..., description="执行模式 plan|build")
    dispatch_type: str = Field(..., description="派发模式 auto|manual")
    dispatch_hold: bool = Field(
        False,
        description="派发确认门禁（ADR-067）：True=等待用户确认派发，调度器跳过",
    )
    dispatch_window_until: datetime | None = Field(
        None,
        description="手动派发窗口截止时间（UTC）；窗口内可手动 dispatch，窗口外调度器接管",
    )
    change_brief: str | None = Field(None, description="本轮修改说明")
    plan_content: str | None = Field(None, description="plan 模式方案全文")
    change_summary: str | None = Field(None, description="build 模式完成时上报的本轮修改说明")
    todos: list[dict] | None = Field(None, description="Agent 实时上报的待办列表")
    progress_stage: str | None = Field(
        None,
        description=(
            "Agent 实时上报的执行阶段（ADR-055）：preparing/planning/coding/editing/"
            "running_command/searching/testing/compacting/summarizing/idle；NULL=未上报"
        ),
    )
    progress_message: str | None = Field(
        None,
        description="Agent 实时上报的当前动作描述（ADR-055）；NULL=未上报",
    )
    progress_tool: str | None = Field(
        None,
        description="Agent 实时上报的当前工具名（ADR-061 M5a）；NULL=未上报",
    )
    verify_report: dict | None = Field(
        None,
        description=(
            "分层验收报告（ADR-061 M4）：{status, source, checks:[...], must_pass_failures, "
            "repair_rounds?, summary?}；NULL=未跑验收"
        ),
    )
    verify_blocked_at: datetime | None = Field(
        None, description="验收阻断时刻（ADR-061 M4）：非空即处于待用户决定状态"
    )
    budget_state: str | None = Field(
        None,
        description=(
            "预算闸状态（ADR-061 M5b）：NULL=未触发 / soft_notified=已发收尾指令 / "
            "hard_stopped=已按预算强制停止"
        ),
    )
    budget: dict | None = Field(
        None, description="任务级预算 override（ADR-061 M5b）：{max_steps?, max_tokens?, soft_ratio?}"
    )
    output_dir: str | None = Field(
        None,
        description="任务产物保存路径（Agent 节点本地目录语义，ADR-028）",
    )
    executor_model: dict | None = Field(
        None,
        description="子 Agent 执行模型选择（ADR-029）：{source, model}；凭据不返回",
    )
    executor_model_actual: str | None = Field(
        None,
        description="节点上报的实际执行模型（ADR-029 增强）：如 deepseek/deepseek-chat；降级时带原因说明",
    )
    parent_task_id: uuid.UUID | None = Field(None, description="直接父任务 ID")
    session_id: uuid.UUID | None = Field(None, description="关联的任务构建会话 ID")
    created_at: datetime = Field(..., description="创建时间")
    updated_at: datetime = Field(..., description="更新时间")


class TaskListResponse(PaginatedResponse[TaskResponse]):
    """任务列表分页响应模型。"""


class TaskSteerRequest(BaseModel):
    """定向发言请求（ADR-061 M5a-steer）。

    把一段话以 steer 方式送进该任务正在跑的 tilacode 会话：**不改变任务状态**，
    只影响其后续行为（与取消的区别）。仅执行中（assigned/running）的任务可发言。
    """

    model_config = ConfigDict(extra="forbid")

    text: str = Field(
        ...,
        min_length=1,
        max_length=4000,
        description="要送给节点会话的话（如纠正方向、补充约束、要求收尾）；上限 4000 字符",
    )


class TaskNodeChatRequest(BaseModel):
    """`@空闲节点` 续聊请求（ADR-064 D1/D2）。

    任务空闲时向指定节点续聊：中枢不建任务，经 ``hub.node_chat`` 下发文本，
    节点对任务链既有 tilacode 会话续发，回复经 ``agent.node_chat_reply``
    回流落会话消息，成功返回 202 ``{reply_to}``。
    任务执行中时应走 ``/steer``（前端按状态分流），此处返回 409。
    """

    model_config = ConfigDict(extra="forbid")

    node_id: uuid.UUID = Field(..., description="目标节点 ID（agent_nodes.id）")
    text: str = Field(
        ...,
        min_length=1,
        max_length=4000,
        description="发给节点的对话文本；上限 4000 字符",
    )


class TaskDispatchPreferenceRequest(BaseModel):
    """派发偏好设置请求（ADR-059 D2）。

    用户/AI 通过本接口表达派发偏好；**偏好参与并决定最终选择**——
    有 ``target_node_id`` 时只等该节点（不经 AI 决策），``force`` 为真时
    才允许越过准入门槛（审计记 ``forced_dispatch=true``）。
    """

    model_config = ConfigDict(extra="forbid")

    target_node_id: uuid.UUID | None = Field(
        None,
        description="指定目标节点 ID；传 null 清除指定（回到常规预筛）。节点离线时保持排队并提示",
    )
    force: bool = Field(
        False,
        description=(
            "**唯一**允许越过准入门槛的开关：true 时即使目标节点不达标/无合格节点也强行派送，"
            "落库 forced_dispatch=true 供审计；false 时严格排队等待"
        ),
    )


class TaskEligibilityCandidate(BaseModel):
    """候选节点准入明细（ADR-059 D3：排队时告知用户「在等哪台、缺什么」）。"""

    node_id: uuid.UUID = Field(..., description="节点 ID")
    name: str = Field(..., description="节点名称")
    online: bool = Field(..., description="是否在线（idle/busy）")
    available_memory_gb: float | None = Field(None, description="绝对可用内存（GB）；null=未上报")
    load_score: float | None = Field(None, description="综合负载分；null=未上报")
    slots_free: int = Field(..., description="剩余并发槽位")
    eligible: bool = Field(..., description="是否可自主派发（双门槛均满足）")
    blocking: str | None = Field(
        None,
        description="不可自主时的**首要**缺口描述（紧凑展示用）；可自主时为 null",
    )
    blocking_reasons: list[str] = Field(
        default_factory=list,
        description=(
            "不可自主时的**全部**缺口描述（内存/负载/CPU 兜底/槽位/回避/离线并列），"
            "避免「解决一项后刷新才发现还有一项」的挤牙膏式反馈；可自主时为空"
        ),
    )


class TaskEligibilityResponse(BaseModel):
    """任务派发准入诊断响应（ADR-059 D3）。"""

    task_id: uuid.UUID = Field(..., description="任务 ID")
    target_node_id: uuid.UUID | None = Field(None, description="当前指定节点 ID")
    forced_dispatch: bool = Field(..., description="是否已要求强行派送")
    eligible_count: int = Field(..., description="可自主派发的候选数")
    candidates: list[TaskEligibilityCandidate] = Field(
        ..., description="全部候选节点（含不可自主者）及其缺口"
    )
    hint: str = Field(..., description="给用户的下一步建议（指定节点 / 等资源释放 / 强行派送）")


class OrchestratorTodoItem(BaseModel):
    """中枢编排器待办项（ADR-061 M5d）：任务摘要**下段**数据源。

    与节点上报的 ``TaskResponse.todos``（上段子 Agent 待办）**物理分离**：
    本清单由中枢编排器经 ``todo_write`` 技能维护，前端只读展示（Q5 裁决）。
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="待办 ID（稳定标识，供编排器后续 start/complete）")
    seq: int = Field(..., description="展示顺序（任务内从 1 递增）")
    content: str = Field(..., description="待办内容")
    status: Literal["pending", "in_progress", "completed"] = Field(..., description="状态")
    source: str = Field(..., description="来源：orchestrator（预留 user）")
    created_at: datetime = Field(..., description="创建时间")
    updated_at: datetime = Field(..., description="更新时间")
    completed_at: datetime | None = Field(None, description="完成时间；未完成为 null")


class OrchestratorTodoListResponse(BaseModel):
    """中枢编排器待办清单响应（ADR-061 M5d）。

    ADR-067 待办先行：任务创建前待办挂会话，故 task_id/session_id 至少其一
    有值（对应任务维度 / 会话维度两个查询端点）。
    """

    task_id: uuid.UUID | None = Field(
        None, description="任务 ID（任务维度查询时有值）"
    )
    session_id: uuid.UUID | None = Field(
        None, description="构建会话 ID（会话维度查询时有值，任务创建前待办挂会话）"
    )
    items: list[OrchestratorTodoItem] = Field(
        default_factory=list, description="按 seq 升序排列的待办项；中枢尚未写待办时为空列表"
    )
    pending_count: int = Field(..., description="未完成条数（pending + in_progress）")
    completed_count: int = Field(..., description="已完成条数")


class TaskVerifyDecisionRequest(BaseModel):
    """验收阻断决策请求（ADR-061 M4，Q7）。

    - ``accept``：接受现状 —— 任务标记 `completed`（并记录验收未通过的审计）；
    - ``retry``：继续修复 —— 任务回到 `running`，中枢把失败摘要 steer 给节点会话再修。
    """

    model_config = ConfigDict(extra="forbid")

    decision: Literal["accept", "retry"] = Field(
        ..., description="accept=接受现状并完成；retry=让子 Agent 继续修复"
    )
    note: str | None = Field(
        None, max_length=1000, description="可选补充说明（retry 时随指令下发给节点）"
    )


class TaskTimelineEntry(BaseModel):
    """时间线条目（ADR-061 M5c，Q12 动作级摘要）。"""

    at: str | None = Field(None, description="发生时刻（ISO8601）")
    kind: str = Field(..., description="事件类别：dispatch/steering/agent_task_event/llm_usage…")
    name: str = Field(..., description="事件名（如 tool_activity）")
    title: str = Field(..., description="人类可读标题")
    detail: str = Field("", description="摘要（命令/原因/发言内容等，已截断）")
    status: str = Field("success", description="该事件状态")


class TaskUsagePoint(BaseModel):
    """用量曲线点（ADR-061 M5c，来自 llm_usage 行）。"""

    at: str | None = Field(None, description="统计时刻")
    input_tokens: int = Field(0, description="输入 tokens")
    output_tokens: int = Field(0, description="输出 tokens")
    reasoning_tokens: int = Field(0, description="推理 tokens")
    cache_read_tokens: int = Field(0, description="缓存读取 tokens")
    cache_write_tokens: int = Field(0, description="缓存写入 tokens")
    cache_hit_rate: float = Field(0.0, description="本点命中率 = cache_read/(input+cache_read)")
    files_touched: int = Field(0, description="本点涉及文件数")
    cost: float | None = Field(None, description="成本（V2 结构性为 0，仅保留字段）")
    tool: str | None = Field(None, description="该点正在使用的工具")


class TaskUsageSummary(BaseModel):
    """用量汇总（ADR-061 M5c）：总量与基线对比。"""

    steps: int = Field(0, description="统计点数（≈ LLM 轮次）")
    input_tokens: int = Field(0, description="输入 tokens 合计")
    output_tokens: int = Field(0, description="输出 tokens 合计")
    cache_read_tokens: int = Field(0, description="缓存读取合计")
    cache_write_tokens: int = Field(0, description="缓存写入合计")
    cache_hit_rate: float = Field(0.0, description="整体命中率")
    cache_hit_baseline: float = Field(0.952, description="基线（ADR-060 实测 95.2%）")
    hit_rate_below_baseline: bool = Field(False, description="是否低于基线")
    files_touched: int = Field(0, description="涉及文件数合计")


class TaskInsightAlert(BaseModel):
    """任务级告警（ADR-061 M5c，Q13 按需计算）。"""

    code: str = Field(..., description="告警码：cache_hit_below_baseline/repeated_activity/no_file_write")
    level: str = Field("warning", description="级别")
    title: str = Field(..., description="标题")
    detail: str = Field("", description="说明")


class TaskInsightResponse(BaseModel):
    """任务只读洞察（ADR-061 M5c）：用量曲线 + 汇总 + 告警 + 时间线。"""

    task_id: uuid.UUID = Field(..., description="任务 ID")
    usage: list[TaskUsagePoint] = Field(default_factory=list, description="用量曲线点（时间升序）")
    summary: TaskUsageSummary = Field(..., description="用量汇总与基线对比")
    alerts: list[TaskInsightAlert] = Field(default_factory=list, description="按需计算的告警")
    timeline: list[TaskTimelineEntry] = Field(
        default_factory=list, description="动作级摘要时间线（时间升序，最多 300 条）"
    )

"""WebSocket 消息 Pydantic 模型。"""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class AgentWebSocketMessage(BaseModel):
    """Agent 节点与中枢之间的 WebSocket 消息统一格式。

    所有入站消息均通过该模型校验，必须包含 ``event`` 字段，``payload``
    缺省为空字典。
    """

    event: str = Field(..., description="事件名，格式为 {模块}.{动作} 的 snake_case")
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description="事件负载字典",
    )
    timestamp: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        description="消息时间戳，ISO 8601 格式",
    )
    request_id: str | None = Field(
        None,
        description="请求追踪 ID，用于端到端链路追踪",
    )


class HeartbeatPayload(BaseModel):
    """节点心跳事件负载。"""

    status: str | None = Field(
        None,
        description="节点上报的状态，如 starting、idle",
    )
    task_id: str | None = Field(
        None,
        description="当前执行中的任务 ID，无任务时可不传",
    )
    load: float | None = Field(
        None,
        description="节点综合负载得分 0~100，metrics 存在时由其中 load_score.score 覆盖",
    )
    metrics: dict[str, Any] | None = Field(
        None,
        description="系统资源监控快照，可选；旧版 Agent 可不携带",
    )


class TaskEventPayload(BaseModel):
    """任务相关事件负载。"""

    task_id: str = Field(..., description="任务 ID")
    progress: int | None = Field(
        None,
        ge=0,
        le=100,
        description="任务进度百分比，progress 事件必填",
    )


#: 允许的执行阶段（ADR-055 封闭枚举，与节点侧 event_translator.STAGES 一致）
TASK_PROGRESS_STAGES: frozenset[str] = frozenset(
    {
        "preparing",
        "planning",
        "coding",
        "editing",
        "running_command",
        "searching",
        "testing",
        "compacting",
        "summarizing",
        "idle",
    }
)

#: 进度上报中「当前动作」描述最大长度（超出截断）
TASK_PROGRESS_MESSAGE_MAX_CHARS = 500


class AgentTaskProgressPayload(TaskEventPayload):
    """agent.task_progress 事件负载（ADR-055：携带执行阶段与当前动作）。

    新增字段均为可选，旧版节点上报的 payload 仍可校验通过；未知 stage 由
    ``_handle_task_progress`` 忽略并告警（不拒绝整条事件）。
    """

    stage: str | None = Field(
        None,
        description=f"执行阶段（ADR-055 封闭枚举）：{'/'.join(sorted(TASK_PROGRESS_STAGES))}",
    )
    message: str | None = Field(
        None,
        max_length=TASK_PROGRESS_MESSAGE_MAX_CHARS,
        description="当前动作描述（人类可读，节点侧已截断 ≤120 字符）",
    )
    tool: str | None = Field(None, max_length=64, description="当前工具名")
    detail: dict | None = Field(
        None,
        description="可观测附加信息（ADR-055）：{files, tokens, cost, compaction}",
    )
    todos: list[dict] | None = Field(None, description="会话待办列表")


class TaskCompletedPayload(TaskEventPayload):
    """任务完成事件负载。"""

    session_id: str | None = Field(
        None,
        description="节点侧 tilacode 会话标识（仅用于日志追溯，不写入 tasks.session_id）",
    )
    plan_content: str | None = Field(
        None,
        description="plan 模式的规划方案全文，节点侧可选上报",
    )
    change_summary: str | None = Field(
        None,
        description="build 模式完成时节点生成的本轮修改说明，可选上报",
    )
    verify_report: dict | None = Field(
        None,
        description=(
            "分层验收报告（ADR-061 M4）：完成闸放行时携带 "
            "{status, source, checks:[{layer,name,cmd,must_pass,status,exit_code,"
            "duration_ms,output_tail}]}"
        ),
    )


class TaskVerifyBlockedPayload(TaskEventPayload):
    """验收阻断事件负载（ADR-061 M4）：必过层红/无法验证且自修用尽。

    与 `TaskFailedPayload` 的区别：任务**没有失败**（代码已完成），只是没通过验收，
    等用户在对话区决定「接受现状 / 继续修复」；24h 无回应由调度器兜底置 `failed`。
    """

    session_id: str | None = Field(None, description="节点侧会话/项目键（仅日志追溯）")
    reason: str | None = Field(None, description="阻断原因摘要（验收报告 summary）")
    report: dict | None = Field(None, description="完整分层验收报告（含输出尾部）")


class TaskFailedPayload(BaseModel):
    """任务失败事件负载。"""

    task_id: str = Field(..., description="任务 ID")
    reason: str | None = Field(
        None,
        description="任务失败原因，节点侧可选上报",
    )


# Agent → 中枢 WebSocket 事件名常量，统一使用 {模块}.{动作} 的 snake_case 格式
AGENT_HEARTBEAT = "agent.heartbeat"
AGENT_TASK_ACCEPTED = "agent.task_accepted"
AGENT_TASK_REJECTED = "agent.task_rejected"
AGENT_TASK_STARTED = "agent.task_started"
AGENT_TASK_PROGRESS = "agent.task_progress"
AGENT_TASK_COMPLETED = "agent.task_completed"
AGENT_TASK_FAILED = "agent.task_failed"
#: 验收阻断上报（ADR-061 M4）：必过层红/无法验证且自修用尽 —— 任务停在
#: `verify_blocked` 等用户在任务对话区决定（接受现状 / 继续修复）
AGENT_TASK_VERIFY_BLOCKED = "agent.task_verify_blocked"

# 产物文件回传事件（ADR-028）
AGENT_FILE_TREE = "agent.file_tree"
AGENT_FILE_CHUNK = "agent.file_chunk"
AGENT_FILE_ERROR = "agent.file_error"

AGENT_TASK_EVENTS: frozenset[str] = frozenset(
    {
        AGENT_TASK_ACCEPTED,
        AGENT_TASK_REJECTED,
        AGENT_TASK_STARTED,
        AGENT_TASK_PROGRESS,
        AGENT_TASK_COMPLETED,
        AGENT_TASK_FAILED,
        AGENT_TASK_VERIFY_BLOCKED,
    },
)

# 中枢 → Agent 下行事件名常量（中枢不校验下行 payload，由调度器直接构造字典下发）
# dispatch payload 字段：task_id / task_name / description / specification / mode /
# base_task_id / change_brief
# 其中 specification 原样透传任务规范三件套（可能为 None），mode 取值 plan|build；
# base_task_id 为任务链根任务 ID（迭代链场景 Agent 复用根任务工作目录与会话，
# 无父任务时等于任务自身 ID），change_brief 为本轮修改说明（可能为 None）
HUB_TASK_DISPATCH = "hub.task_dispatch"
# 任务取消指令（payload 字段：task_id / reason）：节点重连后心跳对账发现其执行中
# 任务已改派他人或已终态时推送，节点本地中断执行（见 ADR-020）
HUB_TASK_CANCEL = "hub.task_cancel"
# 定向发言指令（ADR-061 M5a-steer；payload 字段：task_id / text / reason）：
# 把用户/中枢在任务对话区输入的一段话，以 steer 方式送进该任务正在跑的 tilacode 会话
# （节点侧复用既有 send_prompt(delivery="steer")）；节点收到后回一条 task_progress 作为送达确认。
# 与 hub.task_cancel 的区别：cancel 终结任务，steer 只影响后续行为、任务继续。
HUB_TASK_STEER = "hub.task_steer"
# 节点续聊指令（ADR-064 D1；payload 见 HubNodeChatPayload）：用户 @空闲节点发文本，
# 中枢绕开任务状态机直接把话送进该任务链的既有 tilacode 会话（ses_task_{root_task_id}），
# 节点 send_prompt(delivery="queue") 开新执行轮；trace_task_id/last_seq 供节点为续聊轮
# 临时挂轨迹采集器做 seq 接续（归属链上最后任务）
HUB_NODE_CHAT = "hub.node_chat"
# 产物文件按需拉取指令（ADR-028；payload 字段：request_id / task_id / path / mode）
# mode=file 时 path 为工作目录内相对路径；mode=zip 时打包整个工作目录
HUB_FILE_FETCH = "hub.file_fetch"
# 节点日志按需拉取指令（ADR-047；payload 字段：request_id / tail_lines / level）
# Agent 读本地 TimedRotating 日志文件，按级别过滤 + tail 截取后分块回传
HUB_LOG_FETCH = "hub.log_fetch"
# 日志分块回传（ADR-047；payload 字段：request_id / seq / total / data / done，
# data 为 base64 编码文本；与 Agent 端 send_log_chunk 线上字段一致）
AGENT_LOG_CHUNK = "agent.log_chunk"
# 日志拉取失败回传（ADR-047；payload 字段：request_id / reason）
AGENT_LOG_ERROR = "agent.log_error"
# 节点续聊回复回传（ADR-064 D1；payload 见 AgentNodeChatReplyPayload）：
# 续聊轮结束后节点把 assistant 全文回传，中枢落 task_building_sessions.messages
AGENT_NODE_CHAT_REPLY = "agent.node_chat_reply"
# 节点续聊失败回传（ADR-064；payload 见 AgentNodeChatErrorPayload）：
# 会话丢失/投递失败/超时（默认 300s）时回传，中枢告警并在会话落一条用户可见的错误消息
AGENT_NODE_CHAT_ERROR = "agent.node_chat_error"


class HubNodeChatPayload(BaseModel):
    """hub.node_chat 下行负载（ADR-064 节点续聊）。

    ``task_id`` 为任务链最后任务 ID（续聊轮轨迹归属），``root_task_id`` 供节点
    定位既有会话 ``ses_task_{root_task_id}``；``last_seq`` 为该任务当前轨迹最大
    序号（节点续聊采集器从 ``last_seq + 1`` 续接，无轨迹时为 0）。
    """

    task_id: str = Field(..., description="任务链最后任务 ID（续聊轮轨迹归属）")
    root_task_id: str = Field(..., description="任务链根任务 ID（节点据此定位会话）")
    node_id: str = Field(..., description="目标节点 ID")
    text: str = Field(..., description="用户发给节点的文本")
    reply_to: str = Field(..., description="本次续聊关联 ID（uuid，回复事件原样回传）")
    trace_task_id: str = Field(..., description="续聊轮轨迹采集归属任务 ID（=task_id）")
    last_seq: int = Field(..., ge=0, description="trace_task_id 当前轨迹最大 seq，无轨迹为 0")


class AgentNodeChatReplyPayload(BaseModel):
    """agent.node_chat_reply 上行负载（ADR-064）：节点续聊 assistant 全文回传。"""

    reply_to: str = Field(..., description="发起续聊时的关联 ID")
    task_id: str = Field(..., description="任务链最后任务 ID（与下行 task_id 一致）")
    root_task_id: str = Field(..., description="任务链根任务 ID")
    node_id: str = Field(..., description="回复节点 ID")
    text: str = Field(..., description="节点回复 assistant 全文")
    tokens: dict[str, Any] | None = Field(
        None,
        description="本轮用量统计（可选，当前中枢仅记录日志，不落库）",
    )


class AgentNodeChatErrorPayload(BaseModel):
    """agent.node_chat_error 上行负载（ADR-064）：节点续聊失败回传。"""

    reply_to: str = Field(..., description="发起续聊时的关联 ID")
    task_id: str = Field(..., description="任务链最后任务 ID（与下行 task_id 一致）")
    node_id: str = Field(..., description="回复节点 ID")
    reason: str = Field(..., description="失败原因：session_missing / timeout / deliver_failed 等")

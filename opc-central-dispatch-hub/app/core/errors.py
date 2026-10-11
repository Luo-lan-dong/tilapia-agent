"""业务错误码与自定义业务异常。"""

from fastapi import HTTPException


class ErrorCode:
    """业务错误码常量。

    错误码采用 ``A-BB-CCC`` 格式（语义对齐 docs/standards/api-standards.md §3）：
    - A: 服务模块，1 表示通用，2 表示中央调度中枢
    - BB: 功能域，01 表示任务调度，02 表示节点管理
    - CCC: 具体错误序号
    """

    REQUEST_VALIDATION_FAILED = 100001
    RESOURCE_NOT_FOUND = 100004
    TASK_NOT_FOUND = 201001
    TASK_STATE_NOT_ALLOWED = 201002
    TASK_BUILDING_SESSION_NOT_FOUND = 201004
    OUTPUT_DIR_INVALID = 202012
    AI_PLANNING_FAILED = 201006

    AGENT_NODE_NOT_FOUND = 202001
    AGENT_NODE_OFFLINE = 202002
    INSTALL_SCRIPT_NOT_SUPPORTED = 202004
    AGENT_NODE_NAME_DUPLICATE = 202010
    AGENT_NODE_NAME_INVALID = 202011

    LLM_CONFIG_NOT_FOUND = 203001

    # 技能域（ADR-033）
    SKILL_NOT_FOUND = 204001

    # 附件域（ADR-039）
    ATTACHMENT_INVALID = 205001
    ATTACHMENT_NOT_FOUND = 205002

    # 规则文档域（ADR-048）
    DOC_NOT_FOUND = 206001
    DOC_NAME_DUPLICATE = 206002
    DOC_DOWNLOAD_TOKEN_INVALID = 206003

    # 宝塔 MCP 域（ADR-049）
    BT_MCP_SERVER_NOT_FOUND = 207001
    BT_MCP_SERVER_NAME_DUPLICATE = 207002
    BT_MCP_CONNECT_FAILED = 207003
    BT_MCP_CALL_FAILED = 207004
    BT_MCP_CONFIG_INVALID = 207005

    # 经验记忆域（ADR-050）
    EXPERIENCE_NOT_FOUND = 208001

    # SOP 知识域（ADR-051）
    SOP_NOT_FOUND = 209001
    SOP_NAME_DUPLICATE = 209002

    # 终端域（ADR-053）
    TERMINAL_PTY_UNAVAILABLE = 210001
    TERMINAL_PTY_SPAWN_FAILED = 210002
    TERMINAL_NOT_FOUND = 210003
    TERMINAL_MODE_INVALID = 210004

    RESOURCE_SYNC_FAILED = 401003

    INTERNAL_SERVER_ERROR = 100003


class AppError(Exception):
    """所有业务异常的基类。"""


class TaskError(AppError):
    """任务域异常基类。"""


class TaskNotFoundError(TaskError):
    """指定任务不存在。"""

    def __init__(self, task_id: str) -> None:
        super().__init__(f"任务 {task_id} 不存在")
        self.task_id = task_id


class TaskPlanningError(TaskError):
    """任务规划失败。"""

    def __init__(self, reason: str) -> None:
        super().__init__(f"任务规划失败: {reason}")
        self.reason = reason


class TaskSpecificationError(TaskError):
    """任务规范生成失败。"""

    def __init__(self, reason: str) -> None:
        super().__init__(f"任务规范生成失败: {reason}")
        self.reason = reason


class TaskDispatchError(TaskError):
    """任务分派失败。"""

    def __init__(self, task_id: str | None, reason: str) -> None:
        super().__init__(f"Task {task_id} dispatch failed: {reason}")
        self.task_id = task_id
        self.reason = reason


class TaskTimeoutError(TaskError):
    """任务执行超时。"""

    def __init__(self, task_id: str, agent_id: str | None) -> None:
        super().__init__(f"Task {task_id} timed out on agent {agent_id}")
        self.task_id = task_id
        self.agent_id = agent_id


class AgentError(AppError):
    """Agent 节点域异常基类。"""


class AgentNotFoundError(AgentError):
    """指定 Agent 节点不存在。"""

    def __init__(self, agent_id: str) -> None:
        super().__init__(f"Agent 节点 {agent_id} 不存在")
        self.agent_id = agent_id


class AgentOfflineError(AgentError):
    """Agent 节点离线。"""

    def __init__(self, agent_id: str) -> None:
        super().__init__(f"Agent {agent_id} is offline")
        self.agent_id = agent_id


class AgentRegistrationError(AgentError):
    """Agent 节点注册失败。"""

    def __init__(self, agent_id: str | None, reason: str) -> None:
        super().__init__(f"Agent {agent_id} registration failed: {reason}")
        self.agent_id = agent_id
        self.reason = reason


class ConfigError(AppError):
    """配置域异常基类。"""


class MissingConfigError(ConfigError):
    """缺少指定配置项。"""

    def __init__(self, config_key: str) -> None:
        super().__init__(f"Missing config: {config_key}")
        self.config_key = config_key


class InvalidConfigError(ConfigError):
    """配置项值无效。"""

    def __init__(self, config_key: str, reason: str) -> None:
        super().__init__(f"Invalid config {config_key}: {reason}")
        self.config_key = config_key
        self.reason = reason


class BusinessError(HTTPException):
    """携带业务错误码的 HTTP 异常。

    在 FastAPI 异常处理器中，会根据 ``biz_code`` 返回业务错误码，
    而不是直接使用 HTTP 状态码作为 ``code``。
    """

    def __init__(
        self,
        status_code: int,
        biz_code: int,
        detail: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.biz_code = biz_code


# 兼容性别名：旧代码中使用的 BusinessException 逐步迁移为 BusinessError
BusinessException = BusinessError

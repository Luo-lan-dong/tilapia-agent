"""Agent 端 Pydantic 数据模型。

定义节点注册、任务派发等场景使用的数据结构，与中枢 Schema 保持风格一致。
"""

from pydantic import BaseModel, Field, field_validator


class TaskSpecification(BaseModel):
    """任务规格三件套（markdown 文本）。"""

    spec: str | None = Field(default=None, description="spec 对应的 markdown 文本")
    tasks: str | None = Field(default=None, description="tasks 对应的 markdown 文本")
    checklist: str | None = Field(default=None, description="checklist 对应的 markdown 文本")


class TaskHandoffInfo(BaseModel):
    """跨 Agent 现场迁移信息（ADR-034，中枢派发时可选下发）。"""

    bundle_path: str = Field(..., description="中枢侧一次性下载相对路径（拼当前中枢地址）")
    sha256: str = Field(..., description="bundle 包 sha256 校验值")
    size: int = Field(..., description="bundle 包字节数")
    context_md: str | None = Field(default=None, description="链上最新 project_context.md 全文")


class TaskAttachmentInfo(BaseModel):
    """任务附件信息（ADR-039，中枢派发时随 payload 下发）。"""

    id: str = Field(..., description="附件 ID")
    file_name: str = Field(..., description="原始文件名")
    mime: str = Field(..., description="MIME 类型")
    size: int = Field(..., description="文件字节数")
    url: str = Field(..., description="中枢侧一次性下载相对路径（拼当前中枢地址）")


class TaskRuleDocInfo(BaseModel):
    """任务规则文档信息（ADR-048，中枢派发时随 payload 下发）。

    target 决定落盘位置：
    - ``standard``：规范正文 → ``docs/standards/``（元规则①指向此处按需加载）；
    - ``meta-rule``：元规则（驱动者）→ ``.tilacode/rules/``
      （coding-standards/decision-documents/environment-check）。
    """

    id: str = Field(..., description="文档 ID")
    name: str = Field(..., description="文档名")
    file_name: str = Field(..., description="落盘文件名（不含扩展名，中枢净化生成）")
    version: int | str = Field(..., description="文档版本号")
    apply_when: str | None = Field(None, description="何时必须加载（索引/元规则用）")
    target: str = Field(
        default="standard",
        description="落盘目标：standard=docs/standards/；meta-rule=.tilacode/rules/",
    )
    url: str = Field(..., description="中枢侧下载相对路径（拼当前中枢地址）")
    sha256: str = Field(..., description="文档内容 sha256 校验值")
    size: int = Field(..., description="文档字节数")


class EnvironmentRequirement(BaseModel):
    """环境要求条目（ADR-048 P2，中枢派发时随 payload 内联下发）。

    由子节点执行 AI 在项目首次开发时逐条检查，缺失项自行执行安装；
    同工作目录迭代不重复检查（以 .tilacode/environment.md 是否存在为准）。
    """

    name: str = Field(..., description="环境名，如 Python")
    check_cmd: str = Field(..., description="检查命令，如 python3 --version")
    min_version: str | None = Field(None, description="最低版本，如 3.10")
    install_hint: str | None = Field(None, description="缺失时的安装提示")


class TaskDispatchPayload(BaseModel):
    """中枢下发的任务派发消息 payload。"""

    task_id: str = Field(..., description="任务 ID")
    task_name: str | None = Field(None, description="任务名称")
    description: str | None = Field(None, description="任务描述")
    mode: str = Field(default="build", description="执行模式 plan|build")
    specification: TaskSpecification | None = Field(default=None, description="任务规格三件套")
    base_task_id: str | None = Field(
        default=None, description="任务链根任务 ID，存在时复用其工作目录与 Session"
    )
    change_brief: str | None = Field(
        default=None, description="本轮修改说明（增量/批准方案/驳回反馈）"
    )
    output_dir: str | None = Field(
        default=None,
        description="任务产物保存路径（ADR-028，Agent 节点本地目录语义）；缺省按默认规则分配",
    )
    executor_model: dict | None = Field(
        default=None,
        description=(
            "执行模型选择（ADR-029）：{source: hub|custom, model?, "
            "provider?/api_key?/base_url? 仅 hub 来源}；缺省回退内置免费模型"
        ),
    )
    timeout_seconds: int | None = Field(
        default=None,
        ge=0,
        description=(
            "任务总时长上限（秒，ADR-055）：中枢可覆盖节点默认；None 或 0 表示不限，"
            "常规保护由节点无进度看门狗负责（对齐 agent-node-standards §5.3）"
        ),
    )
    attachments: list[TaskAttachmentInfo] = Field(
        default_factory=list,
        description="任务附件（ADR-039）：执行模型支持视觉时随 prompt 发送图片 parts",
    )
    rule_docs: list[TaskRuleDocInfo] = Field(
        default_factory=list,
        description=(
            "规则文档清单（ADR-048）：非空时下载后落盘 .tilacode/rules/ 并生成 AGENTS.md 索引"
        ),
    )
    environment_requirements: list[EnvironmentRequirement] = Field(
        default_factory=list,
        description=(
            "环境要求清单（ADR-048 P2）：非空且项目首次开发时注入检查/安装指令，"
            "由执行 AI 逐条检查、缺失自行安装"
        ),
    )
    handoff: TaskHandoffInfo | None = Field(
        default=None,
        description="现场迁移信息（ADR-034）：存在时先下载解压产物包到工作目录再执行任务",
    )
    #: 运行时字段：hub_client 下载好的 bundle 原始字节（不序列化、不进日志）
    handoff_bundle: bytes | None = Field(default=None, exclude=True, repr=False)
    #: 运行时字段：hub_client 下载好的附件原始字节（不序列化、不进日志）
    attachment_blobs: list[tuple[TaskAttachmentInfo, bytes]] | None = Field(
        default=None, exclude=True, repr=False
    )
    #: 运行时字段：hub_client 下载好的规则文档原始字节（不序列化、不进日志）
    rule_doc_blobs: list[tuple[TaskRuleDocInfo, bytes]] | None = Field(
        default=None, exclude=True, repr=False
    )

    @field_validator("handoff_bundle", mode="before")
    @classmethod
    def _ignore_inbound_bundle(cls, value: object) -> None:
        """中枢不会下发该运行时字段，防御性忽略入参。"""
        return None

    @field_validator("attachment_blobs", mode="before")
    @classmethod
    def _ignore_inbound_attachment_blobs(cls, value: object) -> None:
        """中枢不会下发该运行时字段，防御性忽略入参。"""
        return None

    @field_validator("rule_doc_blobs", mode="before")
    @classmethod
    def _ignore_inbound_rule_doc_blobs(cls, value: object) -> None:
        """中枢不会下发该运行时字段，防御性忽略入参。"""
        return None


class HardwareInfo(BaseModel):
    """节点硬件信息。"""

    cpu_count: int = Field(..., description="CPU 核心数")
    memory_mb: int = Field(..., description="内存大小（MB）")
    gpu: str | None = Field(None, description="GPU 型号，无 GPU 时为 None")


class RegistrationRequest(BaseModel):
    """节点注册请求体。"""

    name: str = Field(..., description="节点名称")
    agent_type: str = Field(..., description="Agent 类型")
    capabilities: list[str] = Field(default_factory=list, description="能力标签")
    client_id: str = Field(..., description="客户端唯一标识")
    hardware_info: HardwareInfo | None = Field(None, description="硬件信息")
    version: str = Field(..., description="Agent 版本号")

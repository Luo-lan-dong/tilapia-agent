"""Pydantic Settings 全局配置管理。"""

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置模型，所有配置项通过环境变量或 .env 文件注入。"""

    model_config = SettingsConfigDict(
        env_file=".env.local",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # 应用基础配置
    app_env: str = Field(
        default="local",
        validation_alias=AliasChoices("ENVIRONMENT", "APP_ENV"),
    )
    app_name: str = Field(default="opc-central-dispatch-hub", alias="APP_NAME")
    debug: bool = Field(default=False, alias="DEBUG")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_dir: str | None = Field(default=None, alias="LOG_DIR")

    # 服务监听配置
    host: str = Field(default="0.0.0.0", alias="HOST")
    port: int = Field(default=8000, alias="PORT")
    workers: int = Field(default=1, alias="WORKERS")
    hub_public_url: str | None = Field(
        default=None,
        alias="HUB_PUBLIC_URL",
        description="Agent 安装命令使用的中枢公网地址；未设置时从请求头推导",
    )

    # 数据库配置
    database_url: str = Field(alias="DATABASE_URL")
    database_pool_size: int = Field(default=10, alias="DATABASE_POOL_SIZE")

    # Redis 配置
    redis_url: str = Field(alias="REDIS_URL")

    # 对象存储配置
    storage_endpoint: str | None = Field(default=None, alias="STORAGE_ENDPOINT")
    storage_access_key: str | None = Field(default=None, alias="STORAGE_ACCESS_KEY")
    storage_secret_key: str | None = Field(default=None, alias="STORAGE_SECRET_KEY")
    storage_bucket: str | None = Field(default=None, alias="STORAGE_BUCKET")
    storage_use_ssl: bool = Field(default=True, alias="STORAGE_USE_SSL")

    # 安全配置
    secret_key: str | None = Field(default=None, alias="SECRET_KEY")
    agent_registration_token: str | None = Field(default=None, alias="AGENT_REGISTRATION_TOKEN")
    access_token_expire_minutes: int = Field(default=60, alias="ACCESS_TOKEN_EXPIRE_MINUTES")

    # CORS 配置：默认仅放行本地开发前端地址（environment-config-standards §4.1），
    # 生产环境必须显式配置 CORS_ORIGINS 为生产域名，禁止使用 "*"
    # （与 allow_credentials=True 组合属已知反模式，见 §8 禁止事项）
    cors_origins: str = Field(
        default="http://localhost:5173,http://localhost:3000",
        alias="CORS_ORIGINS",
    )

    # 任务卡死检测阈值（秒）：running 任务超过该秒数无任何进度更新
    # 即自动标 failed 并释放节点；最小 60 防止误配为 0
    # （见 ADR-015 / spec/task-stale-detector-spec.md）
    stale_task_timeout_seconds: int = Field(
        default=1800,
        ge=60,
        alias="STALE_TASK_TIMEOUT_SECONDS",
    )

    # 任务派发模式窗口秒数（ADR-017）：任务创建后 dispatch_window_until
    # = now() + 该值；窗口内 dispatch_task 改 dispatch_type=manual 阻断自动派发。
    # 0 = 无窗口，pending 任务下一轮调度器自动派发（保留旧行为）。
    task_dispatch_window_seconds: int = Field(
        default=10,
        ge=0,
        alias="TASK_DISPATCH_WINDOW_SECONDS",
    )

    # 心跳监控阈值（秒）：节点心跳上报间隔与离线判定超时，
    # 运行期阈值经 Pydantic Settings 读取（environment-config-standards §5.2）
    heartbeat_interval_seconds: int = Field(
        default=10,
        ge=1,
        alias="HEARTBEAT_INTERVAL_SECONDS",
    )
    heartbeat_timeout_seconds: int = Field(
        default=30,
        ge=5,
        alias="HEARTBEAT_TIMEOUT_SECONDS",
    )

    # 中枢重启主动反连（ADR-035）：notify API 调用并发上限与单次 HTTP 超时
    reverse_notify_concurrency: int = Field(
        default=5,
        ge=1,
        le=50,
        alias="REVERSE_NOTIFY_CONCURRENCY",
        description="中枢重启后主动反连 Agent notify API 的并发上限（ADR-035）",
    )
    reverse_notify_timeout_seconds: float = Field(
        default=5.0,
        ge=1.0,
        alias="REVERSE_NOTIFY_TIMEOUT_SECONDS",
        description="单次反连 notify API 的 HTTP 超时（秒，ADR-035）",
    )

    # 任务调度器扫描周期（秒），运行期可配，无需重启即生效
    scheduler_interval_seconds: int = Field(
        default=10,
        ge=1,
        alias="SCHEDULER_INTERVAL_SECONDS",
    )

    # 调度资源预筛阈值（ADR-032）：节点心跳 metrics 中 CPU/内存使用率
    # 达到该百分比即不参与本轮派单；Agent 本地自评阈值更严格（默认 90）
    dispatch_cpu_threshold_percent: float = Field(
        default=85.0,
        ge=1.0,
        le=100.0,
        alias="DISPATCH_CPU_THRESHOLD_PERCENT",
    )
    dispatch_mem_threshold_percent: float = Field(
        default=85.0,
        ge=1.0,
        le=100.0,
        alias="DISPATCH_MEM_THRESHOLD_PERCENT",
    )

    # 准入自评门槛（ADR-059 D1）：节点可自主派发 ⇔ 绝对可用内存 > N GB
    # **且** 综合负载分 < M。任一不满足即「不可自主」（可被指定排队，
    # 但不得自动派发）。不用内存百分比作主判据：百分比分母随机器规格变化，
    # 4G 机器 82.7%（仅剩 ~0.7G）会被旧口径误判为「合规」。
    # 上表 DISPATCH_CPU/MEM_THRESHOLD_PERCENT 降级为 metrics 缺失时兜底。
    dispatch_min_available_memory_gb: float = Field(
        default=1.0,
        ge=0.0,
        alias="DISPATCH_MIN_AVAILABLE_MEMORY_GB",
    )
    dispatch_max_load_score: float = Field(
        default=80.0,
        ge=0.0,
        le=100.0,
        alias="DISPATCH_MAX_LOAD_SCORE",
    )
    # 无可自主节点时推送排队提示的最小间隔（秒），避免每 10 秒刷屏
    dispatch_hold_notify_interval_seconds: float = Field(
        default=120.0,
        ge=10.0,
        alias="DISPATCH_HOLD_NOTIFY_INTERVAL_SECONDS",
    )

    # 慢请求告警阈值（毫秒）：单次请求耗时超过该值时日志升级为 WARNING
    # （logging-standards §4.3）
    slow_request_threshold_ms: int = Field(
        default=1000,
        ge=100,
        alias="SLOW_REQUEST_THRESHOLD_MS",
    )

    # AI 智能调度（ADR-033）：自动调度 AI 决策总开关，默认关闭保持纯规则；
    # 运行期可在系统设置页热更新（system_settings.ai_dispatch_enabled 优先）
    ai_dispatch_enabled: bool = Field(
        default=False,
        alias="AI_DISPATCH_ENABLED",
        description="自动调度 AI 决策开关（env 为初始值，运行期以系统设置页为准）",
    )
    # 任务终态自动唤醒编排（ADR-066）：completed/failed 后自动唤醒编排 AI
    # 核对交付物/推进待办/汇报；默认开启（平台自主性核心），
    # 运行期可在系统设置页热更新（system_settings.orchestrator_auto_wake_enabled 优先）
    orchestrator_auto_wake_enabled: bool = Field(
        default=True,
        alias="ORCHESTRATOR_AUTO_WAKE_ENABLED",
        description="任务终态自动唤醒编排开关（env 为初始值，运行期以系统设置页为准）",
    )
    # 批量决策 LLM 调用超时预算（秒）：超时本轮任务回退规则分配，不重试
    ai_dispatch_timeout_seconds: float = Field(
        default=8.0,
        ge=2.0,
        alias="AI_DISPATCH_TIMEOUT_SECONDS",
    )
    # 对话 Agent 单次回复最大技能调用轮数（ADR-058）：**可调安全网**而非唯一防线。
    # 默认 40：旧值 8 已实测误杀正常长任务（部署类任务「配置服务器→查清单→查指引→
    # 定位产物」的侦察阶段就吃掉 6+ 轮）。真正防打转的是 Doom Loop 检测（ADR-043）
    # 与新增的「无新信息」进度检测；本项仅作总预算兜底。达到后仍按三重禁用强制收尾。
    orchestrator_max_tool_rounds: int = Field(
        default=40,
        ge=1,
        alias="ORCHESTRATOR_MAX_TOOL_ROUNDS",
    )
    # 唤醒轮独立轮数上限（2026-10-08 用户实测：部署 13 步远超默认 40 轮，
    # 唤醒轮中途被强制收尾掐断长流程）。未设置时回落全局值。
    orchestrator_wake_max_tool_rounds: int = Field(
        default=120,
        ge=1,
        alias="ORCHESTRATOR_WAKE_MAX_TOOL_ROUNDS",
    )
    # 唤醒轮超时（秒）：部署/多阶段长流程需要远超普通对话的执行窗口
    orchestrator_wake_timeout_seconds: int = Field(
        default=1800,
        ge=60,
        alias="ORCHESTRATOR_WAKE_TIMEOUT_SECONDS",
    )
    # 连续 N 轮未引入任何新技能结果即判定「原地打转」并收尾（ADR-058 D2）：
    # 覆盖 Doom Loop 未覆盖的形态——换了参数但反复查同一批工具且无新结论。
    # 技能返回失败同样算「已返回」（失败也是信息），不累积无进展。
    orchestrator_no_progress_rounds: int = Field(
        default=4,
        ge=2,
        alias="ORCHESTRATOR_NO_PROGRESS_ROUNDS",
    )
    # 逼近轮数上限的警示比例（ADR-058 D3）：rounds_used >= ceil(max × 比例) 时
    # 下发一次 warning，让用户知道「不是卡住，而是在做长任务」。
    orchestrator_rounds_warning_ratio: float = Field(
        default=0.75,
        gt=0.0,
        le=1.0,
        alias="ORCHESTRATOR_ROUNDS_WARNING_RATIO",
    )
    # 单个技能执行超时预算（秒，ADR-043）：超时中止该技能（回传失败 tool 消息），
    # 对话循环继续；兜底死锁场景（LLM 悬挂/DB 死锁），正常技能秒级完成
    skill_execution_timeout_seconds: float = Field(
        default=300.0,
        ge=1.0,
        alias="SKILL_EXECUTION_TIMEOUT_SECONDS",
    )
    # Doom Loop 检测阈值（ADR-043）：连续 N 轮完全相同的技能调用（同名+同参数）
    # 即判定打转，不再执行并强制收尾；窗口滑动，交替/间隔重复不误伤
    orchestrator_doom_loop_threshold: int = Field(
        default=3,
        ge=2,
        alias="ORCHESTRATOR_DOOM_LOOP_THRESHOLD",
    )
    # LLM 请求重试总尝试次数（ADR-044）：可重试错误（429/5xx/网络异常/超时）
    # 的最大尝试次数；其余 4xx 不重试直接抛
    llm_retry_max_attempts: int = Field(
        default=5,
        ge=1,
        alias="LLM_RETRY_MAX_ATTEMPTS",
    )
    # LLM 重试退避基数（秒，ADR-044）：指数退避 base×2^(n-1)，±20% 抖动
    llm_retry_base_delay_seconds: float = Field(
        default=1.0,
        ge=0.0,
        alias="LLM_RETRY_BASE_DELAY_SECONDS",
    )

    # LLM 输出上限默认档（ADR-056）：该模型（deepseek-flash）官方 MAX OUTPUT = 384K，
    # 旧实现硬编码 8192 已实测触顶 2 次（长回答被静默截断）。默认档取 32768——
    # 覆盖实测最长输出，同时避免超长输出带来的超时与成本风险；按场景可降档
    # （摘要 4096 / 三件套 16384）。
    llm_max_output_tokens: int = Field(
        default=32768,
        ge=1024,
        le=393216,
        alias="LLM_MAX_OUTPUT_TOKENS",
        description="LLM 默认输出 token 上限（ADR-056）",
    )
    # 思考强度（ADR-056）：官方仅支持 low/high/max（medium 会被映射为 high，
    # 语义模糊故不提供）；仅在思考模式开启时下发
    llm_reasoning_effort: Literal["low", "high", "max"] = Field(
        default="high",
        alias="LLM_REASONING_EFFORT",
        description="思考强度档位（ADR-056，官方支持 low/high/max）",
    )
    # 编排链路思考开关（ADR-056 决策 D4 路线 B）：显式声明 thinking 开关。
    # 官方「带 tools 时 reasoning_content 必须全量回传」，故开启时编排链路会把
    # 历史 assistant 的 thinking 作为 reasoning_content 回流。
    orchestrator_thinking_enabled: bool = Field(
        default=True,
        alias="ORCHESTRATOR_THINKING_ENABLED",
        description="编排链路是否开启思考模式（ADR-056）",
    )

    # 日志系统（ADR-047）：内存环形缓冲条数（查询 API 直读，重启清空）
    log_ring_buffer_size: int = Field(
        default=5000,
        ge=100,
        alias="LOG_RING_BUFFER_SIZE",
    )
    # 执行链路事件表保留天数（ADR-047）：调度器每日清理超期行
    tool_call_log_retention_days: int = Field(
        default=90,
        ge=1,
        alias="TOOL_CALL_LOG_RETENTION_DAYS",
    )
    # 节点日志按需拉取（ADR-047）：回传等待超时与行数上限
    agent_log_fetch_timeout_seconds: float = Field(
        default=45.0,
        ge=5.0,
        alias="AGENT_LOG_FETCH_TIMEOUT_SECONDS",
    )
    agent_log_fetch_max_lines: int = Field(
        default=2000,
        ge=10,
        le=10000,
        alias="AGENT_LOG_FETCH_MAX_LINES",
    )

    # Agent 资源同步（ADR-022）：本地缓存目录与定时检查间隔
    resource_cache_dir: str = Field(
        default="./data/resources",
        alias="RESOURCE_CACHE_DIR",
        description="Agent 资源本地缓存目录（桌面模式由 launcher 注入绝对路径）",
    )
    # 任务产物本地缓存目录（ADR-028）：Agent 回传文件清单与内容落盘位置
    artifact_cache_dir: str = Field(
        default="./data/artifacts",
        alias="ARTIFACT_CACHE_DIR",
        description="任务产物本地缓存目录（桌面模式由 launcher 注入绝对路径）",
    )
    # 现场迁移 bundle 暂存目录（ADR-034）：跨 Agent 派发时的产物打包暂存
    handoff_cache_dir: str = Field(
        default="./data/handoff",
        alias="HANDOFF_CACHE_DIR",
        description="现场迁移 bundle 暂存目录（桌面模式由 launcher 注入绝对路径）",
    )
    # 会话附件本地存储目录（ADR-039）：对话/任务图片等附件落盘位置
    attachment_storage_dir: str = Field(
        default="./data/attachments",
        alias="ATTACHMENT_STORAGE_DIR",
        description="会话附件本地存储目录（桌面模式由 launcher 注入绝对路径）",
    )
    # 会话附件一次性下载 token 有效期（秒，ADR-039）
    attachment_token_ttl_seconds: int = Field(
        default=600,
        ge=60,
        alias="ATTACHMENT_TOKEN_TTL_SECONDS",
    )
    # 现场迁移一次性下载 token 有效期（秒）
    handoff_bundle_ttl_seconds: int = Field(
        default=600,
        ge=60,
        alias="HANDOFF_BUNDLE_TTL_SECONDS",
    )
    # 现场迁移 bundle 大小上限（字节）：超出拒绝打包，派发退化为无现场迁移
    handoff_max_bundle_bytes: int = Field(
        default=2 * 1024 * 1024 * 1024,
        ge=1024 * 1024,
        alias="HANDOFF_MAX_BUNDLE_BYTES",
    )
    resource_sync_interval_seconds: int = Field(
        default=21600,
        ge=0,
        alias="RESOURCE_SYNC_INTERVAL_SECONDS",
        description="资源云端检查间隔（秒）；0=禁用定时检查",
    )
    # 云端仓库默认 manifest URL：数据库未配置时的内置兜底（开箱即用），
    # 指向作者维护的官方更新源（公共只读，不含任何凭据）；开源用户无需配置即可
    # 拉取更新日志与节点资源。数据库已配置的值优先生效；后台保存空值=恢复此默认。
    # 自建云端资源仓库时改为自己的 manifest 地址。
    agent_resource_manifest_url: str = Field(
        default="https://tilapia-web-1328924353.cos.ap-chengdu.myqcloud.com/code-agent/",
        alias="AGENT_RESOURCE_MANIFEST_URL",
        description="Agent 资源云端仓库默认 manifest URL（数据库未配置时生效；官方更新源）",
    )
    # 服务端配置下发（ADR-025）：检查更新前先从此接口获取最新仓库地址（失败回退缓存）。
    # 默认指向本地开发环境（云端资源仓库与中枢同机）；正式打包 exe 前手动改为生产服务器地址。
    agent_resource_hub_config_url: str = Field(
        default="http://localhost:8080/portal/open/hub/config",
        alias="AGENT_RESOURCE_HUB_CONFIG_URL",
        description="服务端配置下发接口地址（官网公开路径，发布前指向生产服务器）",
    )
    agent_resource_hub_token: str = Field(
        default="",
        alias="AGENT_RESOURCE_HUB_TOKEN",
        description="服务端配置接口对接 Token（X-Hub-Token 认证）",
    )

    # DeepSeek 配置：仅在首次初始化默认记录时使用，运行期不再依赖
    deepseek_api_key: str | None = Field(default=None, alias="DEEPSEEK_API_KEY")
    deepseek_base_url: str | None = Field(default=None, alias="DEEPSEEK_BASE_URL")
    deepseek_model: str | None = Field(default=None, alias="DEEPSEEK_MODEL")

    @computed_field
    @property
    def cors_origins_list(self) -> list[str]:
        """将逗号分隔的 CORS 来源字符串解析为列表。"""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """获取缓存的配置单例。"""
    return Settings()

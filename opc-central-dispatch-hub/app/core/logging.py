"""统一日志配置：structlog + 标准库桥接（ADR-047 日志系统 v1）。

三层输出：控制台 + 按天切割文件（现状）+ 内存环形缓冲（查询 API 直读）
+ WARN+ 分级归档 error.log（保留 30 天）。
"""

import json
import logging
import logging.handlers
import re
import sys
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

DEFAULT_LOCAL_LOG_DIR: str = "./logs"
DEFAULT_PROD_LOG_DIR: str = "/var/log/opc/central-hub"

#: 环形缓冲默认容量（可被 configure_logging 的 ring_buffer_size 覆盖）
DEFAULT_RING_BUFFER_SIZE = 5000

#: 环形缓冲快照携带的结构化字段（来自 record.__dict__ 的 extra kv）
_RING_SNAPSHOT_FIELDS = (
    "request_id",
    "session_id",
    "task_id",
    "event",
    "duration_ms",
    "client_id",
    "agent_id",
    "skill",
    "status_code",
    "path",
    "method",
)


def _resolve_log_dir(environment: str, log_dir: str | None) -> str:
    """根据环境解析日志目录。

    桌面模式（APP_ENV=desktop，launcher 注入）由 launcher 显式传入
    ``data/logs``（launcher/paths.log_dir），此处仅需兜底 local/prod 两分支。
    """
    if log_dir:
        return log_dir
    return DEFAULT_LOCAL_LOG_DIR if environment in ("local", "desktop") else DEFAULT_PROD_LOG_DIR


def _build_shared_processors() -> list[structlog.types.Processor]:
    """构建共享日志处理器。"""
    return [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=False),
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level_number,
        structlog.stdlib.ExtraAdder(),
    ]


def _rotated_file_namer(default_name: str) -> str:
    """将默认滚动文件名 ``app.log.YYYY-MM-DD.log`` 转为 ``app-YYYY-MM-DD.log``。"""
    return re.sub(r"\.log\.(\d{4}-\d{2}-\d{2})\.log$", r"-\1.log", default_name)


class SensitiveQueryMaskFilter(logging.Filter):
    """对访问日志中的敏感 query 参数脱敏（logging-standards §6）。

    Agent 节点 WebSocket 认证凭据经 URL query 传递（``?token=...``），
    uvicorn.access 会记录完整请求路径，若不脱敏将导致注册 Token 明文落盘。
    该过滤器将 message 中 ``token=<值>`` 统一替换为 ``token=***``。
    """

    _TOKEN_QUERY_RE = re.compile(r"(token=)[^&\s]+", re.IGNORECASE)

    def filter(self, record: logging.LogRecord) -> bool:
        """脱敏日志记录中的 token 参数。

        uvicorn access 日志为 msg 模板 + args 五元组形式，且 uvicorn 自带的
        AccessFormatter 会解包 args，因此必须保持 args 结构、只脱敏其中的
        字符串参数；无 args 的记录（已格式化字符串）直接脱敏 msg。

        Args:
            record: 标准库日志记录。

        Returns:
            始终返回 True（不丢弃日志，仅脱敏）。
        """
        if record.args:
            record.args = tuple(
                self._TOKEN_QUERY_RE.sub(r"\1***", arg) if isinstance(arg, str) else arg
                for arg in record.args
            )
        else:
            record.msg = self._TOKEN_QUERY_RE.sub(r"\1***", record.getMessage())
        return True


def _create_file_handler(
    log_dir: str,
    environment: str,
    formatter: structlog.stdlib.ProcessorFormatter,
) -> logging.handlers.TimedRotatingFileHandler:
    """创建按天切割的文件日志处理器。"""
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    handler = logging.handlers.TimedRotatingFileHandler(
        filename=log_path / "app.log",
        when="midnight",
        interval=1,
        backupCount=30 if environment in ("staging", "prod") else 7,
        encoding="utf-8",
    )
    handler.suffix = "%Y-%m-%d.log"
    handler.extMatch = re.compile(r"^\d{4}-\d{2}-\d{2}\.log$")
    handler.namer = _rotated_file_namer
    handler.setFormatter(formatter)
    return handler


def _create_error_file_handler(
    log_dir: str,
    formatter: structlog.stdlib.ProcessorFormatter,
) -> logging.handlers.TimedRotatingFileHandler:
    """创建 WARN+ 分级归档处理器（error.log，按天切割保留 30 天，ADR-047）。"""
    handler = logging.handlers.TimedRotatingFileHandler(
        filename=Path(log_dir) / "error.log",
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
    )
    handler.suffix = "%Y-%m-%d.log"
    handler.extMatch = re.compile(r"^\d{4}-\d{2}-\d{2}\.log$")
    handler.namer = _rotated_file_namer
    handler.setLevel(logging.WARNING)
    handler.setFormatter(formatter)
    return handler


class RingBufferHandler(logging.Handler):
    """内存环形缓冲处理器（ADR-047）。

    emit 时把 LogRecord 快照为普通 dict（防后续 format 修改 record），
    存入 ``deque(maxlen=size)``；查询 API 经 :func:`read_ring_buffer`
    直读，重启清空（持久化职责由文件日志承担）。
    """

    def __init__(self, size: int = DEFAULT_RING_BUFFER_SIZE) -> None:
        super().__init__()
        self.buffer: deque[dict[str, Any]] = deque(maxlen=size)

    def emit(self, record: logging.LogRecord) -> None:  # noqa: A003
        """快照一条日志进缓冲。"""
        try:
            snapshot: dict[str, Any] = {
                # UTC aware ISO（与查询 API 的 start/end 时区语义一致；
                # 前端展示时经 dayjs 转本地时间）
                "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
            for field in _RING_SNAPSHOT_FIELDS:
                value = record.__dict__.get(field)
                if value is not None:
                    snapshot[field] = value
            # 其余 extra kv 归入 metadata（排除标准库保留字段），供展开排查
            standard = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
                "message",
                "asctime",
                "taskName",
            }
            extra = {
                k: v
                for k, v in record.__dict__.items()
                if k not in standard
                and k not in _RING_SNAPSHOT_FIELDS
                and isinstance(v, (str, int, float, bool))
            }
            if extra:
                snapshot["metadata"] = extra
            self.buffer.append(snapshot)
        except Exception:  # noqa: BLE001 — 缓冲失败绝不影响业务日志
            self.handleError(record)


#: 模块级环形缓冲实例（configure_logging 重建，read 函数读取当前实例）
_ring_buffer: RingBufferHandler | None = None


def read_ring_buffer(
    start: datetime | None = None,
    end: datetime | None = None,
    levels: set[str] | None = None,
    keyword: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """读取环形缓冲快照（时间倒序，ADR-047 查询 API 数据源）。

    Args:
        start: 起始时间（含），None 不限。
        end: 结束时间（含），None 不限。
        levels: 级别集合（如 {"ERROR", "WARNING"}），None 不限。
        keyword: 消息/结构化字段子串匹配（大小写不敏感），None 不限。
        limit: 最多返回条数。
    """
    if _ring_buffer is None:
        return []
    levels_upper = {level.upper() for level in levels} if levels else None
    keyword_lower = keyword.lower() if keyword else None
    matched: list[dict[str, Any]] = []
    for snapshot in reversed(_ring_buffer.buffer):
        ts = datetime.fromisoformat(snapshot["timestamp"])
        if start and ts < start:
            continue
        if end and ts > end:
            continue
        if levels_upper and snapshot["level"] not in levels_upper:
            continue
        if keyword_lower:
            haystack = json_dumps_for_search(snapshot)
            if keyword_lower not in haystack:
                continue
        matched.append(snapshot)
        if len(matched) >= limit:
            break
    return matched


def json_dumps_for_search(snapshot: dict[str, Any]) -> str:
    """把快照拍平为小写搜索文本（消息 + 结构化字段）。"""
    return json.dumps(snapshot, ensure_ascii=False, default=str).lower()


def ring_buffer_size() -> int:
    """当前环形缓冲已存条数。"""
    return len(_ring_buffer.buffer) if _ring_buffer else 0


def configure_logging(
    environment: str,
    log_level: str,
    log_dir: str | None = None,
    ring_buffer_size: int = DEFAULT_RING_BUFFER_SIZE,
) -> None:
    """统一配置 structlog 与标准库日志。

    local 环境控制台输出彩色可读文本，文件为无色文本；其余环境控制台与文件均输出 JSON。
    所有环境均写按天切割的文件日志，uvicorn 日志桥接进入统一输出。
    通过标准库桥接保留 ``logging.getLogger(__name__)`` 用法。

    ADR-047：额外挂载内存环形缓冲（查询 API 直读，重启清空）与
    WARN+ 分级归档 error.log（按天切割保留 30 天）。

    Args:
        environment: 运行环境，local/dev/staging/prod/desktop。
        log_level: 日志级别字符串。
        log_dir: 可选，自定义日志目录。
        ring_buffer_size: 环形缓冲容量（条）。
    """
    resolved_log_dir = _resolve_log_dir(environment, log_dir)
    level = getattr(logging, log_level.upper(), logging.INFO)

    shared_processors = _build_shared_processors()
    if environment == "local":
        renderer: structlog.types.Processor = structlog.dev.ConsoleRenderer(colors=True)
        foreign_pre_chain = shared_processors
    else:
        renderer = structlog.processors.JSONRenderer()
        foreign_pre_chain = list(shared_processors)
        foreign_pre_chain.append(structlog.processors.EventRenamer("message"))

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=foreign_pre_chain,
    )

    # 文件日志：local 用无色可读文本（避免 ANSI 颜色码写入文件），其余环境 JSON
    if environment == "local":
        file_formatter = structlog.stdlib.ProcessorFormatter(
            processor=structlog.dev.ConsoleRenderer(colors=False),
            foreign_pre_chain=shared_processors,
        )
    else:
        file_formatter = formatter

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers.clear()

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # 所有环境均输出按天切割的文件日志（logging-standards 第 9 节）
    root_logger.addHandler(_create_file_handler(resolved_log_dir, environment, file_formatter))
    # WARN+ 分级归档（ADR-047）：报错追溯窗口延长至 30 天
    root_logger.addHandler(_create_error_file_handler(resolved_log_dir, file_formatter))
    # 内存环形缓冲（ADR-047）：查询 API 直读数据源
    global _ring_buffer
    _ring_buffer = RingBufferHandler(ring_buffer_size)
    root_logger.addHandler(_ring_buffer)

    # uvicorn 自带 logger 默认 propagate=False 且持有独立 handler，
    # 清理并桥接到 root，使访问/错误日志同样进入控制台与文件；
    # access 日志附加脱敏过滤器，防止 URL query 中的注册 Token 明文落盘
    for uvicorn_logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(uvicorn_logger_name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    logging.getLogger("uvicorn.access").addFilter(SensitiveQueryMaskFilter())

    # structlog 的 processors 只负责字段绑定、上下文、日志级别等预处理，
    # 真正的渲染由标准库 handler 的 ProcessorFormatter 完成，避免同一事件被渲染两次。
    structlog_processors = list(shared_processors)
    if environment != "local":
        structlog_processors.append(structlog.processors.dict_tracebacks)
        structlog_processors.append(structlog.processors.EventRenamer("message"))
    structlog_processors.append(structlog.stdlib.ProcessorFormatter.wrap_for_formatter)

    structlog.configure(
        processors=structlog_processors,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # SQLAlchemy 引擎日志单独降级为 WARNING，避免 DEBUG 环境下 SQL 语句刷屏。
    # echo=True 会在 sqlalchemy.engine.Engine 上添加 StreamHandler，因此需要同时
    # 清理该 logger 的 handlers 并将其级别设为 WARNING。
    for sql_logger_name in (
        "sqlalchemy.engine",
        "sqlalchemy.engine.Engine",
        "sqlalchemy.pool",
        "sqlalchemy.dialects",
    ):
        sql_logger = logging.getLogger(sql_logger_name)
        sql_logger.setLevel(logging.WARNING)
        sql_logger.handlers.clear()

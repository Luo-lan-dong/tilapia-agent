"""Agent 日志配置。

所有环境均输出文件日志（供 ADR-047 log_fetch 按需拉取）：
- local/dev：``./logs/agent.log``（相对工作目录，与中枢 local 策略一致），
  文件为无颜色可读文本、控制台为带颜色可读文本；
- staging/prod：``/var/log/opc/agent-programming/agent.log``，JSON 格式，
  控制台与文件均为 JSON，按天切割保留 7 天。
"""

import logging
import logging.handlers
import re
import sys
from pathlib import Path

import structlog

DEFAULT_PROD_LOG_DIR: str = "/var/log/opc/agent-programming"
#: local/dev 环境日志目录（相对工作目录，与中枢 local 落 ./logs 策略一致）
DEFAULT_LOCAL_LOG_DIR: str = "./logs"
_LOG_BACKUP_COUNT: int = 7


def resolve_log_dir(environment: str) -> Path:
    """按运行环境解析日志目录。

    local/dev 落工作目录 ``logs/``（Windows 开发环境可写）；staging/prod
    落系统日志目录。log_fetch 拉取与文件写入必须使用同一解析结果。
    """
    return (
        Path(DEFAULT_LOCAL_LOG_DIR)
        if environment in ("local", "dev")
        else Path(DEFAULT_PROD_LOG_DIR)
    )


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
    """将默认滚动文件名 ``agent.log.YYYY-MM-DD.log`` 转为 ``agent-YYYY-MM-DD.log``。"""
    return re.sub(r"\.log\.(\d{4}-\d{2}-\d{2})\.log$", r"-\1.log", default_name)


def _create_file_handler(
    formatter: structlog.stdlib.ProcessorFormatter,
    log_dir: Path,
) -> logging.handlers.TimedRotatingFileHandler | None:
    """创建按天切割的文件日志处理器。

    目录创建失败（如只读文件系统）时返回 None，调用方降级为仅控制台输出。
    """
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        logging.warning(
            "file_log_dir_unavailable_fallback_console",
            extra={"log_dir": str(log_dir)},
        )
        return None

    handler = logging.handlers.TimedRotatingFileHandler(
        filename=log_dir / "agent.log",
        when="midnight",
        interval=1,
        backupCount=_LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.suffix = "%Y-%m-%d.log"
    handler.extMatch = re.compile(r"^\d{4}-\d{2}-\d{2}\.log$")
    handler.namer = _rotated_file_namer
    handler.setFormatter(formatter)
    return handler


def setup_logging(environment: str, log_level: str) -> None:
    """配置 structlog：local 输出可读文本（控制台带颜色/文件无颜色），
    其他环境输出 JSON（控制台与文件一致）；所有环境均写文件日志。

    通过标准库桥接保留 ``logging.getLogger(__name__)`` 用法。

    Args:
        environment: 运行环境，local/dev/staging/prod。
        log_level: 日志级别字符串。
    """
    level = getattr(logging, log_level.upper(), logging.INFO)

    shared_processors = _build_shared_processors()
    if environment == "local":
        renderer: structlog.types.Processor = structlog.dev.ConsoleRenderer(colors=True)
        file_renderer: structlog.types.Processor = structlog.dev.ConsoleRenderer(colors=False)
        foreign_pre_chain = shared_processors
    else:
        renderer = structlog.processors.JSONRenderer()
        file_renderer = renderer
        foreign_pre_chain = list(shared_processors)
        foreign_pre_chain.append(structlog.processors.EventRenamer("message"))

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=foreign_pre_chain,
    )
    file_formatter = structlog.stdlib.ProcessorFormatter(
        processor=file_renderer,
        foreign_pre_chain=foreign_pre_chain,
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers.clear()

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # 所有环境均写文件日志（ADR-047 log_fetch 数据源；local 落 ./logs）
    file_handler = _create_file_handler(file_formatter, resolve_log_dir(environment))
    if file_handler is not None:
        root_logger.addHandler(file_handler)

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

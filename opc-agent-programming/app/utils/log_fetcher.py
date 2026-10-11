"""节点日志按需拉取读取器（ADR-047）。

中枢下发 ``hub.log_fetch`` 后，从本地 TimedRotating 日志文件
（目录由 :func:`app.utils.logger.resolve_log_dir` 按环境解析：
local/dev 落 ``./logs``，staging/prod 落 ``/var/log/opc/agent-programming``，
滚动为 ``agent-YYYY-MM-DD.log``）读取尾部行，按级别过滤后回传。

- 兼容两类行格式：JSON 行匹配 ``"level": "ERROR"`` 字段（staging/prod）；
  local 可读文本行匹配 ``[error    ]`` 级别标记（颜色码不影响匹配）；
- 两种格式都不匹配的行（异常输出等）在级别过滤时跳过；
- 当天文件行数不足时从最近的滚动文件补齐。
"""

import os
import re
from pathlib import Path

#: 当前日志文件名
CURRENT_LOG_NAME = "agent.log"
#: 滚动文件名模式（agent-YYYY-MM-DD.log）
_ROTATED_RE = re.compile(r"^agent-(\d{4}-\d{2}-\d{2})\.log$")
#: local 可读文本行的级别标记（ConsoleRenderer：``[info     ]``）
_CONSOLE_LEVEL_RE = re.compile(r"\[\s*(debug|info|warning|error|critical)\s+\]", re.IGNORECASE)
#: 单次读取行数上限（防超大文件载入内存）
_MAX_READ_LINES = 20000


def default_log_dir() -> Path:
    """按运行环境解析默认日志目录（与 setup_logging 写入目录一致）。

    Agent 进程内建议由调用方显式传入 ``resolve_log_dir(config.app_env)``；
    本函数供独立使用/兜底场景（从环境变量 APP_ENV 推断）。
    """
    from app.utils.logger import resolve_log_dir

    return resolve_log_dir(os.environ.get("APP_ENV", "local"))


def _sorted_rotated_files(log_dir: Path) -> list[Path]:
    """按日期倒序返回滚动日志文件。"""
    files: list[tuple[str, Path]] = []
    try:
        for entry in log_dir.iterdir():
            match = _ROTATED_RE.match(entry.name)
            if match and entry.is_file():
                files.append((match.group(1), entry))
    except OSError:
        return []
    files.sort(key=lambda item: item[0], reverse=True)
    return [path for _date, path in files]


def _tail_lines(path: Path, count: int) -> list[str]:
    """读取文件尾部 count 行（全量读入有上限保护）。"""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            return [line.rstrip("\n") for line in fh.readlines()[-count:]]
    except OSError:
        return []


def _line_matches_level(line: str, level_upper: str) -> bool:
    """判断单行日志是否属于目标级别（兼容 JSON 与可读文本两种格式）。

    error 级别同时命中 critical（语义上 critical 属于错误类）。
    """
    targets = {"CRITICAL"} if level_upper == "ERROR" else set()
    targets.add(level_upper)
    if f'"level": "{level_upper.lower()}"' in line or f'"level": "{level_upper}"' in line:
        return True
    match = _CONSOLE_LEVEL_RE.search(line)
    return bool(match and match.group(1).upper() in targets)


def read_recent_log_lines(
    tail_lines: int = 200,
    level: str | None = None,
    log_dir: Path | None = None,
) -> str:
    """读取最近日志行（级别过滤后截取 tail_lines）。

    Args:
        tail_lines: 最多返回的行数（1~20000）。
        level: 级别过滤（info/warning/error/debug，大小写不敏感）；
            None 不过滤。
        log_dir: 日志目录（测试注入用），缺省按 APP_ENV 环境解析。

    Returns:
        日志文本（按时间正序拼接）；文件不存在返回空串。
    """
    tail_lines = max(1, min(tail_lines, _MAX_READ_LINES))
    directory = log_dir or default_log_dir()
    level_upper = level.strip().upper() if level else None

    collected: list[str] = []
    candidates = [directory / CURRENT_LOG_NAME, *_sorted_rotated_files(directory)]
    for path in candidates:
        remaining = tail_lines - len(collected)
        if remaining <= 0:
            break
        lines = (
            _tail_lines(path, min(remaining * 3, _MAX_READ_LINES))
            if level_upper
            else _tail_lines(path, remaining)
        )
        if level_upper:
            lines = [line for line in lines if _line_matches_level(line, level_upper)]
        collected = [*lines[-remaining:], *collected]
    return "\n".join(collected)

"""工作目录文件清单扫描器（ADR-028 产物回传）。

扫描任务工作目录，生成相对路径清单（path/size/mtime），排除依赖与
临时目录；清单超上限时标记 truncated。供执行中周期上报与完成终态上报。

实现要点（ADR-055 P0-1）：
- 使用 ``os.scandir`` 增量遍历而非 ``Path.rglob``：后者会在任何截止检查
  之前把整棵目录树物化并排序，使「硬超时」形同虚设，超大目录（如含
  node_modules）会长时间阻塞调用线程；
- 截止时间在**每个目录**处理前检查，保证超时能真正中断；
- 调用方必须经 ``asyncio.to_thread`` 调用本模块的同步函数，避免阻塞事件循环。
"""

import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

#: 排除的目录/文件名（一级或任意层级匹配）
EXCLUDED_NAMES: frozenset[str] = frozenset(
    {
        ".git",
        ".tilacode",
        ".hub-attachments",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        "dist",
        "build",
    }
)

#: 排除的凭据/敏感配置文件名（ADR-055 P2-12：不得进入产物清单与回传 zip）。
#: ``.env.example`` 为模板文件，无真实凭据，保留在清单中。
SENSITIVE_NAMES: frozenset[str] = frozenset(
    {
        ".mcp.json",
        ".npmrc",
        ".netrc",
        ".pgpass",
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "credentials",
        "credentials.json",
    }
)

#: 清单条目上限（超出后停止扫描并标记 truncated）
MAX_ENTRIES = 5000

#: 单次扫描硬超时（秒）
SCAN_DEADLINE_SECONDS = 10.0


def _excluded_parts(parts: tuple[str, ...]) -> bool:
    """按相对路径逐层判断排除（含敏感文件规则）。

    ``.tilacode`` 例外（ADR-048 修订）：``rules/`` 子树与环境检查记录
    ``environment.md`` 允许回传（元规则对用户可见、可追溯），其余
    （tilacode.jsonc/会话数据等）仍排除。
    """
    for index, part in enumerate(parts):
        if _is_sensitive_name(part):
            return True
        if part not in EXCLUDED_NAMES:
            continue
        if part == ".tilacode":
            rest = parts[index + 1 :]
            if rest and (rest[0] == "rules" or rest == ("environment.md",)):
                continue
        return True
    return False


def _is_sensitive_name(name: str) -> bool:
    """判断单个路径片段是否为敏感文件/目录名（ADR-055 P2-12）。

    ``.env`` 与 ``.env.*`` 视为凭据文件，但 ``.env.example`` 是无凭据的模板，
    保留在产物清单中。
    """
    if name in SENSITIVE_NAMES:
        return True
    if name.startswith(".env."):
        return name != ".env.example"
    return name == ".env"


def _should_descend(parts: tuple[str, ...]) -> bool:
    """判断目录是否还值得继续遍历（ADR-048 例外感知）。

    与 ``_excluded_parts`` 的差别：``.tilacode`` **不可整棵剪枝**——其
    ``rules/**`` 与环境检查记录 ``environment.md`` 需要回传，因此必须进入
    该目录后按层判定。其余排除目录（node_modules/.git/...）可安全剪枝。

    Args:
        parts: 目录的相对路径片段（POSIX 分层）。

    Returns:
        True 表示应继续遍历该目录。
    """
    for part in parts:
        if _is_sensitive_name(part):
            return False
        if part not in EXCLUDED_NAMES:
            continue
        if part == ".tilacode":
            continue
        return False
    return True


def is_excluded_relative_path(relative_path: str) -> bool:
    """判断 POSIX 风格相对路径是否应排除（打包前二次过滤用）。

    Args:
        relative_path: 相对工作目录的路径，如 ``sub/.env``。

    Returns:
        True 表示应从产物清单与回传包中排除。
    """
    return _excluded_parts(tuple(relative_path.split("/")))


def scan_file_tree(
    workspace_dir: Path,
    *,
    deadline_seconds: float = SCAN_DEADLINE_SECONDS,
    max_entries: int = MAX_ENTRIES,
) -> tuple[list[dict], bool]:
    """扫描工作目录生成文件清单。

    Args:
        workspace_dir: 任务工作目录绝对路径。
        deadline_seconds: 单次扫描硬超时（秒）；超时立即返回已收集结果。
        max_entries: 条目上限；超出后标记 truncated 并停止。

    Returns:
        (files, truncated)：files 为 ``[{path, size, mtime}]``（相对路径，
        POSIX 分隔符，按 path 升序）；truncated 表示因超时或超上限截断。
    """
    files: list[dict] = []
    truncated = False
    root = workspace_dir.resolve()
    deadline = time.monotonic() + deadline_seconds
    # 待遍历目录栈：元素为 (绝对路径, 相对 POSIX 路径前缀)
    pending: list[tuple[Path, str]] = [(root, "")]

    while pending:
        if time.monotonic() > deadline:
            truncated = True
            break
        directory, prefix = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError:
            continue
        for entry in entries:
            relative = f"{prefix}/{entry.name}" if prefix else entry.name
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if is_dir:
                if not _should_descend(tuple(relative.split("/"))):
                    continue
                pending.append((Path(entry.path), relative))
                continue
            if _excluded_parts(tuple(relative.split("/"))):
                continue
            try:
                stat = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            files.append({"path": relative, "size": stat.st_size, "mtime": stat.st_mtime})
            if len(files) >= max_entries:
                truncated = True
                break
        if truncated:
            break

    files.sort(key=lambda item: item["path"])
    if truncated:
        logger.warning(
            "file_tree_scan_truncated",
            extra={
                "workspace_dir": str(root),
                "count": len(files),
                "max_entries": max_entries,
                "deadline_seconds": deadline_seconds,
            },
        )
    return files, truncated

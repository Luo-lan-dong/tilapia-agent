"""验收判据防篡改检测（ADR-061 M4 验收 #12 加固）。

verify cmd 常引用工作区内的脚本（如 ``.tilacode/tools/frontend_probe.mjs``），
而整个工作区对 LLM 可写——模型可以在自修轮里改写探针让检查「自己出题自己
答」地通过（2026-10-02 ADR-061 验收实测：M7 任务模型自写探针 + 自带
server.js，repair_1 轮即 passed，绕过「不假成功」防线）。

防线：完成闸启动时对判据相关目录做 mtime 快照；每轮验收 ``passed`` 后
复查——快照期间出现新增、改动或被删除的文件即视为「验收可信度受损」，
按无法验证阻断交用户决定，绝不呈现为正常完成。

边界：只覆盖 verify cmd 最常引用的 ``.tilacode/tools/`` 与三件套判据目录
（``.tilacode/tilacode.jsonc``、``rules/``、会话文件由节点自身写入，不在
监控范围以免误报）；命令行内联逻辑（``python -c ...``）与目录外的脚本
不在此防线上，完全防绕过需要只读执行沙箱（超出本模块范围）。
"""

from __future__ import annotations

from pathlib import Path

#: 监控的判据脚本目录（相对工作区根）。
TOOLS_RELATIVE_DIR = Path(".tilacode") / "tools"

#: 噪音目录：执行探针的必然副产物，不计入篡改（Python 字节码缓存）。
_IGNORED_PARTS = {"__pycache__"}


def _is_watch_file(path: Path) -> bool:
    """排除噪音文件：__pycache__ 缓存等执行副作用不算模型篡改证据。"""
    return not any(part in _IGNORED_PARTS for part in path.parts)


def _watch_dirs(workspace_dir: Path, doc_dir: str | None) -> list[Path]:
    """监控目录清单：探针目录 + 三件套判据目录（绝对化）。"""
    dirs = [workspace_dir / TOOLS_RELATIVE_DIR]
    if doc_dir:
        dirs.append(workspace_dir / doc_dir)
    return dirs


def snapshot(workspace_dir: Path, doc_dir: str | None) -> dict[str, float]:
    """记录监控目录下全部文件的 mtime 快照（相对路径 → mtime_ns）。"""
    baseline: dict[str, float] = {}
    for base in _watch_dirs(workspace_dir, doc_dir):
        if not base.is_dir():
            continue
        for item in base.rglob("*"):
            if item.is_file() and _is_watch_file(item.relative_to(workspace_dir)):
                baseline[item.relative_to(workspace_dir).as_posix()] = item.stat().st_mtime_ns
    return baseline


def changed_paths(
    workspace_dir: Path, doc_dir: str | None, baseline: dict[str, float]
) -> list[str]:
    """返回快照之后新增、改动或被删除的监控文件（相对路径，排序稳定）。"""
    changed: list[str] = []
    seen: set[str] = set()
    for base in _watch_dirs(workspace_dir, doc_dir):
        if not base.is_dir():
            continue
        for item in base.rglob("*"):
            if not item.is_file() or not _is_watch_file(item.relative_to(workspace_dir)):
                continue
            rel = item.relative_to(workspace_dir).as_posix()
            seen.add(rel)
            if baseline.get(rel) != item.stat().st_mtime_ns:
                changed.append(rel)
    missing = sorted(set(baseline) - seen)
    return sorted(set(changed) | set(missing))

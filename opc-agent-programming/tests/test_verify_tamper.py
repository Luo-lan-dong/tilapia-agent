"""verify_tamper 单测（ADR-061 M4 #12：判据/探针防篡改）。"""

from __future__ import annotations

import os
from pathlib import Path

from app.executor.verify_tamper import changed_paths, snapshot


def _make_workspace(tmp_path: Path) -> Path:
    tools = tmp_path / ".tilacode" / "tools"
    tools.mkdir(parents=True)
    specs = tmp_path / "specs" / "demo"
    specs.mkdir(parents=True)
    (tools / "frontend_probe.mjs").write_text("console.log('probe v1')")
    (specs / "spec.md").write_text("# spec")
    return tmp_path


def _touch(path: Path, content: str, stamp: int) -> None:
    path.write_text(content)
    os.utime(path, ns=(stamp, stamp))


def test_no_changes_after_baseline(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path)
    baseline = snapshot(ws, "specs/demo")
    assert changed_paths(ws, "specs/demo", baseline) == []


def test_modified_probe_detected(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path)
    probe = ws / ".tilacode" / "tools" / "frontend_probe.mjs"
    baseline = snapshot(ws, "specs/demo")
    _touch(probe, "console.log('v2')", 2_000_000_000_000)
    assert changed_paths(ws, "specs/demo", baseline) == [".tilacode/tools/frontend_probe.mjs"]


def test_added_and_deleted_files_detected(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path)
    baseline = snapshot(ws, "specs/demo")
    (ws / ".tilacode" / "tools" / "server.js").write_text("serve()")
    (ws / "specs" / "demo" / "spec.md").unlink()
    assert changed_paths(ws, "specs/demo", baseline) == [
        ".tilacode/tools/server.js",
        "specs/demo/spec.md",
    ]


def test_changes_outside_watch_dirs_ignored(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path)
    baseline = snapshot(ws, "specs/demo")
    _touch(ws / "hello.py", "print('business code')", 3_000_000_000_000)
    assert changed_paths(ws, "specs/demo", baseline) == []


def test_doc_dir_none_watches_tools_only(tmp_path: Path) -> None:
    tools = tmp_path / ".tilacode" / "tools"
    tools.mkdir(parents=True)
    (tools / "probe.mjs").write_text("v1")
    other = tmp_path / "specs" / "s"
    other.mkdir(parents=True)
    (other / "spec.md").write_text("# spec")
    baseline = snapshot(tmp_path, None)
    _touch(other / "spec.md", "# changed", 4_000_000_000_000)
    assert changed_paths(tmp_path, None, baseline) == []


def test_pycache_execution_side_effect_ignored(tmp_path: Path) -> None:
    """执行探针的必然副产物（Python 字节码缓存）不算篡改（2026-10-03 复测②实测）。"""
    ws = _make_workspace(tmp_path)
    baseline = snapshot(ws, "specs/demo")
    cache = ws / ".tilacode" / "tools" / "__pycache__"
    cache.mkdir()
    (cache / "check_probe.cpython-311.pyc").write_text("bytecode")
    assert changed_paths(ws, "specs/demo", baseline) == []

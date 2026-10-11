"""file_scanner 元规则可见性 + INDEX 骨架测试（ADR-048 修订）。"""

from pathlib import Path

from app.executor.file_scanner import scan_file_tree
from app.executor.workspace import WorkspaceManager


def test_scan_includes_tilacode_rules_but_excludes_other_tilacode(tmp_path: Path) -> None:
    """`.tilacode/rules/**` 与 environment.md 允许回传；其余 .tilacode 内容仍排除。"""
    ws = tmp_path / "ws"
    (ws / ".tilacode" / "rules").mkdir(parents=True)
    (ws / "docs" / "standards").mkdir(parents=True)
    (ws / ".tilacode" / "rules" / "coding-standards.md").write_text("# 元规则", encoding="utf-8")
    (ws / ".tilacode" / "environment.md").write_text("# 环境记录", encoding="utf-8")
    (ws / ".tilacode" / "tilacode.jsonc").write_text("{}", encoding="utf-8")
    (ws / ".tilacode" / "session.db").write_text("db", encoding="utf-8")
    (ws / "docs" / "standards" / "api-standards.md").write_text("# 规范", encoding="utf-8")

    files, truncated = scan_file_tree(ws)

    assert truncated is False
    paths = {item["path"] for item in files}
    assert ".tilacode/rules/coding-standards.md" in paths
    assert ".tilacode/environment.md" in paths
    assert "docs/standards/api-standards.md" in paths
    assert ".tilacode/tilacode.jsonc" not in paths
    assert ".tilacode/session.db" not in paths


def test_ensure_index_skeleton_creates_once(tmp_path: Path) -> None:
    """INDEX.md 骨架：首次创建，已存在跳过（不覆盖 AI 维护的内容）。"""
    manager = WorkspaceManager(str(tmp_path / "root"))
    ws = tmp_path / "ws"
    ws.mkdir(parents=True)

    created = manager.ensure_index_skeleton(ws)
    assert created == ws / "docs" / "INDEX.md"
    content = (ws / "docs" / "INDEX.md").read_text(encoding="utf-8")
    assert "决策文档清单" in content
    assert "文档更新记录" in content

    # AI 已维护的内容不被覆盖
    (ws / "docs" / "INDEX.md").write_text("# 我自己的索引", encoding="utf-8")
    assert manager.ensure_index_skeleton(ws) is None
    assert (ws / "docs" / "INDEX.md").read_text(encoding="utf-8") == "# 我自己的索引"

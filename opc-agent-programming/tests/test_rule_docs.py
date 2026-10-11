"""规则文档下发链路测试（ADR-048 修订：元规则/规范正文分目录落盘）。"""

import hashlib
from pathlib import Path

from app.executor.workspace import WorkspaceManager, build_agents_md
from app.models.schemas import TaskDispatchPayload, TaskRuleDocInfo


def _make_doc(
    file_name: str = "test-standards",
    apply_when: str | None = "每次写/改代码前",
    target: str = "standard",
) -> TaskRuleDocInfo:
    content = "# 规范正文".encode("utf-8")
    return TaskRuleDocInfo(
        id="doc-1",
        name=file_name,
        file_name=file_name,
        version=1,
        apply_when=apply_when,
        target=target,
        url="/api/v1/docs/download/rule-doc?token=t",
        sha256=hashlib.sha256(content).hexdigest(),
        size=len(content),
    )


def test_dispatch_payload_parses_rule_docs() -> None:
    """payload 解析：含 rule_docs（含 target）正常解析，缺省为空列表。"""
    doc = _make_doc()
    payload = TaskDispatchPayload.model_validate({"task_id": "t1", "rule_docs": [doc.model_dump()]})
    assert payload.rule_docs is not None
    assert len(payload.rule_docs) == 1
    assert payload.rule_docs[0].target == "standard"
    # 运行时字段不出现在序列化结果（不进日志）
    assert "rule_doc_blobs" not in payload.model_dump()

    legacy = TaskDispatchPayload.model_validate({"task_id": "t2"})
    assert legacy.rule_docs == []


def test_build_agents_md_points_to_meta_rules() -> None:
    """AGENTS.md：指向 .tilacode/rules/ 元规则 + 规范正文清单（仅 standard）。"""
    docs = [
        _make_doc("test-standards", "每次写/改代码前"),
        _make_doc("coding-standards", "每次写/改代码前", target="meta-rule"),
        _make_doc("decision-documents", None, target="meta-rule"),
    ]
    content = build_agents_md([(doc, b"x") for doc in docs])
    # 元规则入口（兜底层指引）
    assert ".tilacode/rules/" in content
    assert "coding-standards.md" in content
    assert "decision-documents.md" in content
    # 规范正文清单（docs/standards/ 路径 + apply_when）
    assert "docs/standards/test-standards.md" in content
    assert "每次写/改代码前" in content
    assert "不要全量阅读" in content
    assert "禁止手工改写" in content
    # 元规则自身不出现在规范正文清单表中
    assert "docs/standards/coding-standards.md" not in content


def test_materialize_rule_docs_splits_by_target(tmp_path: Path) -> None:
    """落盘分目录：standard → docs/standards/；meta-rule → .tilacode/rules/。"""
    manager = WorkspaceManager(str(tmp_path / "root"))
    workspace = tmp_path / "ws"
    workspace.mkdir()
    docs = [
        _make_doc("test-standards", "每次写/改代码前", target="standard"),
        _make_doc("coding-standards", "每次写/改代码前", target="meta-rule"),
        _make_doc("decision-documents", "改功能前", target="meta-rule"),
    ]
    blobs = [
        (docs[0], "# 规范正文".encode("utf-8")),
        (docs[1], "# 元规则一".encode("utf-8")),
        (docs[2], "# 元规则二".encode("utf-8")),
    ]

    written = manager.materialize_rule_docs(workspace, blobs)

    assert len(written) == 5  # 3 文档 + AGENTS.md + docs/INDEX.md 骨架
    # 规范正文落 docs/standards/
    assert (workspace / "docs" / "standards" / "test-standards.md").read_text(
        encoding="utf-8"
    ) == "# 规范正文"
    # 元规则落 .tilacode/rules/
    assert (workspace / ".tilacode" / "rules" / "coding-standards.md").read_text(
        encoding="utf-8"
    ) == "# 元规则一"
    assert (workspace / ".tilacode" / "rules" / "decision-documents.md").read_text(
        encoding="utf-8"
    ) == "# 元规则二"
    # AGENTS.md 含规范清单
    agents_md = workspace / "AGENTS.md"
    assert "docs/standards/test-standards.md" in agents_md.read_text(encoding="utf-8")


def test_materialize_upserts_tilacode_instructions(tmp_path: Path) -> None:
    """元规则落盘后同步 tilacode.jsonc instructions（续接会话时补齐）。"""
    manager = WorkspaceManager(str(tmp_path / "root"))
    workspace = tmp_path / "ws"
    (workspace / ".tilacode").mkdir(parents=True)
    # 旧配置（无 instructions，模拟续接会话）
    (workspace / ".tilacode" / "tilacode.jsonc").write_text(
        '{"permission": {"*": "allow"}}', encoding="utf-8"
    )
    docs = [_make_doc("coding-standards", target="meta-rule")]
    manager.materialize_rule_docs(workspace, [(docs[0], "# 元规则".encode("utf-8"))])

    import json

    config = json.loads((workspace / ".tilacode" / "tilacode.jsonc").read_text(encoding="utf-8"))
    assert config["instructions"] == [".tilacode/rules/coding-standards.md"]
    assert config["permission"] == {"*": "allow"}  # 既有字段保留


def test_materialize_rule_docs_blocks_path_traversal(tmp_path: Path) -> None:
    """防越界：file_name 携带路径穿越片段时取 basename。"""
    manager = WorkspaceManager(str(tmp_path / "root"))
    workspace = tmp_path / "ws"
    workspace.mkdir()
    evil = _make_doc(file_name="../../etc/evil", target="meta-rule")
    manager.materialize_rule_docs(workspace, [(evil, b"x")])
    assert (workspace / ".tilacode" / "rules" / "evil.md").is_file()
    assert not (workspace.parent.parent / "etc").exists()


def test_materialize_rule_docs_skips_empty(tmp_path: Path) -> None:
    """空 blobs 时不动工作目录（未下发规则包的回归语义）。"""
    manager = WorkspaceManager(str(tmp_path / "root"))
    workspace = tmp_path / "ws"
    workspace.mkdir()
    assert manager.materialize_rule_docs(workspace, []) == []
    assert not (workspace / "docs" / "standards").exists()
    assert not (workspace / ".tilacode" / "rules").exists()
    assert not (workspace / "AGENTS.md").exists()


def test_rule_docs_materialized_detects_unchanged_content(tmp_path: Path) -> None:
    """ADR-055 P1-7：内容一致时判定已落盘，避免链上重写触发上下文增量注入。"""
    manager = WorkspaceManager(str(tmp_path / "root"))
    workspace = tmp_path / "ws"
    workspace.mkdir()
    meta = _make_doc("coding-standards", target="meta-rule")
    standard = _make_doc("test-standards", target="standard")
    blobs = [(meta, "# 元规则".encode("utf-8")), (standard, "# 规范正文".encode("utf-8"))]

    # 未落盘 → 需要写入
    assert manager.rule_docs_materialized(workspace, blobs) is False

    manager.materialize_rule_docs(workspace, blobs)
    # 内容完全一致 → 复用已有
    assert manager.rule_docs_materialized(workspace, blobs) is True


def test_rule_docs_materialized_false_when_content_changed(tmp_path: Path) -> None:
    """文档版本更新（内容变化）时必须重新落盘。"""
    manager = WorkspaceManager(str(tmp_path / "root"))
    workspace = tmp_path / "ws"
    workspace.mkdir()
    meta = _make_doc("coding-standards", target="meta-rule")
    manager.materialize_rule_docs(workspace, [(meta, b"v1")])

    assert manager.rule_docs_materialized(workspace, [(meta, b"v2")]) is False


def test_rule_docs_materialized_false_when_file_missing(tmp_path: Path) -> None:
    """任一目标文件缺失（如被清理）时判定为需重新落盘。"""
    manager = WorkspaceManager(str(tmp_path / "root"))
    workspace = tmp_path / "ws"
    workspace.mkdir()
    meta = _make_doc("coding-standards", target="meta-rule")
    standard = _make_doc("test-standards", target="standard")
    manager.materialize_rule_docs(
        workspace,
        [(meta, b"same"), (standard, b"same")],
    )
    (workspace / "docs" / "standards" / "test-standards.md").unlink()

    assert (
        manager.rule_docs_materialized(workspace, [(meta, b"same"), (standard, b"same")]) is False
    )

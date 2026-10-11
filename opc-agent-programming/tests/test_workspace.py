"""WorkspaceManager 单元测试。"""

import json
from pathlib import Path
from types import SimpleNamespace

from app.executor import permission_policy
from app.executor import workspace as workspace_module
from app.executor.workspace import (
    WorkspaceManager,
    _sanitize_title_for_dir,
    spec_docs_relative_dir,
)


def _make_specification(
    spec: str | None = None,
    tasks: str | None = None,
    checklist: str | None = None,
) -> SimpleNamespace:
    """构造具有 spec/tasks/checklist 属性的规格对象。"""
    return SimpleNamespace(spec=spec, tasks=tasks, checklist=checklist)


def test_prepare_creates_dirs(tmp_path: Path) -> None:
    """prepare 创建任务目录与 .tilacode 子目录。"""
    manager = WorkspaceManager(str(tmp_path))
    workspace_dir = manager.prepare("task-1")
    assert workspace_dir == tmp_path / "task-1"
    assert workspace_dir.is_dir()
    assert (workspace_dir / ".tilacode").is_dir()


def test_prepare_idempotent(tmp_path: Path) -> None:
    """重复调用 prepare 不报错且返回同一路径。"""
    manager = WorkspaceManager(str(tmp_path))
    first = manager.prepare("task-1")
    second = manager.prepare("task-1")
    assert first == second
    assert first.is_dir()


def test_ensure_tilacode_config_content(tmp_path: Path) -> None:
    """写入的 tilacode 配置含 permission_mode 与 default_agent，不含 model（ADR-029）。

    model 由任务桥接在 session 创建时显式指定（优先级高于配置文件），
    模板写死默认值会误导执行模型排查，故不写入。

    ADR-061 M2a：模板**不得**再写 ``"permission": {"*": "allow"}``——该规则会覆盖
    tilacode 内置的全部权限护栏；无人值守改用 ``permission_mode="unattended"``。
    """
    manager = WorkspaceManager(str(tmp_path))
    workspace_dir = manager.prepare("task-1")
    manager.ensure_tilacode_config(workspace_dir)

    config_path = workspace_dir / ".tilacode" / "tilacode.jsonc"
    content = config_path.read_text(encoding="utf-8")
    data = json.loads(content)
    assert "permission" not in data, "blanket allow 会把内置护栏覆盖成 allow（ADR-061 M2a）"
    assert data["permission_mode"] == "unattended"
    assert "model" not in data
    assert data["default_agent"] == "build"


def test_ensure_tilacode_config_not_overwrite(tmp_path: Path) -> None:
    """配置文件已存在时不覆盖用户内容；**但补全必载指令**（ADR-061 M7）。

    语义更新（2026-09-29）：历史工作区配置缺少新增规则文件条目时会被追加，
    否则节点本地生成的前端验证规则不会被 tilacode 加载。追加**只增不删不重排**。
    """
    manager = WorkspaceManager(str(tmp_path))
    workspace_dir = manager.prepare("task-1")
    config_path = workspace_dir / ".tilacode" / "tilacode.jsonc"
    config_path.write_text('{"custom": true}', encoding="utf-8")

    manager.ensure_tilacode_config(workspace_dir)

    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert data["custom"] is True, "用户自定义内容必须保留"
    assert ".tilacode/rules/frontend-verification.md" in data["instructions"]


def test_ensure_frontend_tooling_writes_probe_and_rule(tmp_path: Path) -> None:
    """UT-M7-1（ADR-061 M7）：前端探针与验证规则落盘，且规则已进 instructions。

    - 探针：从节点 `tools/` 复制到工作区 `.tilacode/tools/`（内容一致，可被覆盖升级）；
    - 规则：`.tilacode/rules/frontend-verification.md`（说明何时必须实测、怎么算通过）。
    """
    manager = WorkspaceManager(str(tmp_path))
    workspace_dir = manager.prepare("task-1")
    manager.ensure_tilacode_config(workspace_dir)

    written = manager.ensure_frontend_tooling(workspace_dir)

    probe = workspace_dir / ".tilacode" / "tools" / "frontend_probe.mjs"
    rule = workspace_dir / ".tilacode" / "rules" / "frontend-verification.md"
    assert probe in written and rule in written
    assert probe.is_file() and rule.is_file()
    assert "frontend_probe" in probe.read_text(encoding="utf-8")
    rule_text = rule.read_text(encoding="utf-8")
    assert "退出码即判据" in rule_text and "--shots" in rule_text

    config = json.loads((workspace_dir / ".tilacode" / "tilacode.jsonc").read_text("utf-8"))
    assert ".tilacode/rules/frontend-verification.md" in config["instructions"]

    # 幂等：内容一致时不重写（mtime 不变），避免每次派发都动文件
    before = probe.stat().st_mtime_ns
    manager.ensure_frontend_tooling(workspace_dir)
    assert probe.stat().st_mtime_ns == before


def test_ensure_frontend_tooling_skips_rule_when_probe_absent(
    tmp_path: Path, monkeypatch
) -> None:
    """UT-M7-5：探针未随包分发时**不写**「指路」规则（避免让 Agent 跑不存在的脚本）。

    场景：`WITHOUT_BROWSER=1` 构建或未上传 `tools/`。此时前端能力由 M4 判据如实报
    「无法验证」，而不是给一份指向空文件的规则。
    """
    manager = WorkspaceManager(str(tmp_path))
    workspace_dir = manager.prepare("task-1")
    monkeypatch.setattr(workspace_module, "_resolve_probe_source", lambda: None)

    written = manager.ensure_frontend_tooling(workspace_dir)

    assert written == []
    assert not (workspace_dir / ".tilacode" / "rules" / "frontend-verification.md").exists()
    assert not (workspace_dir / ".tilacode" / "tools" / "frontend_probe.mjs").exists()


def test_resolve_probe_source_prefers_tools_env(tmp_path: Path, monkeypatch) -> None:
    """UT-M7-6：探针定位优先 `TILAPIA_TOOLS_DIR`（Nuitka onefile 下 `__file__` 不可靠）。"""
    custom = tmp_path / "custom-tools"
    custom.mkdir()
    probe = custom / "frontend_probe.mjs"
    probe.write_text("// probe from env dir", encoding="utf-8")
    monkeypatch.setenv("TILAPIA_TOOLS_DIR", str(custom))

    assert workspace_module._resolve_probe_source() == probe


def test_write_specification_docs(tmp_path: Path) -> None:
    """三件套写入版本目录，None 字段跳过（ADR-037）。"""
    manager = WorkspaceManager(str(tmp_path))
    workspace_dir = manager.prepare("task-1")
    specification = _make_specification(spec="# 规格", tasks="# 任务", checklist=None)
    doc_dir = "specs/登录页面-12345678"

    written = manager.write_specification_docs(workspace_dir, specification, doc_dir)

    assert set(written) == {
        workspace_dir / doc_dir / "spec.md",
        workspace_dir / doc_dir / "tasks.md",
    }
    assert (workspace_dir / doc_dir / "spec.md").read_text(encoding="utf-8") == "# 规格"
    assert (workspace_dir / doc_dir / "tasks.md").read_text(encoding="utf-8") == "# 任务"
    assert not (workspace_dir / doc_dir / "checklist.md").exists()


def test_write_specification_docs_all_none(tmp_path: Path) -> None:
    """全部为 None 时不写任何文件，返回空列表。"""
    manager = WorkspaceManager(str(tmp_path))
    workspace_dir = manager.prepare("task-1")

    written = manager.write_specification_docs(
        workspace_dir, _make_specification(), "specs/登录页面-12345678"
    )

    assert written == []
    assert not (workspace_dir / "docs").exists()


def test_write_specification_docs_idempotent_same_slug(tmp_path: Path) -> None:
    """同 slug 目录二次写入为幂等覆盖（改派重跑语义），不产生新目录。"""
    manager = WorkspaceManager(str(tmp_path))
    workspace_dir = manager.prepare("task-1")
    doc_dir = "specs/登录页面-12345678"

    manager.write_specification_docs(workspace_dir, _make_specification(spec="# 第一版"), doc_dir)
    manager.write_specification_docs(workspace_dir, _make_specification(spec="# 第二版"), doc_dir)

    assert (workspace_dir / doc_dir / "spec.md").read_text(encoding="utf-8") == "# 第二版"
    specs_root = workspace_dir / "specs"
    assert [p.name for p in specs_root.iterdir()] == ["登录页面-12345678"]


def test_sanitize_title_for_dir_rules() -> None:
    """净化规则：非法字符/空白转 -、连续 - 压缩、截断 20、空串兜底 task。"""
    assert _sanitize_title_for_dir('a/b\\c:d*e?f"g<h>i|j') == "a-b-c-d-e-f-g-h-i-j"
    assert _sanitize_title_for_dir("  多  空格 --测试  ") == "多-空格-测试"
    assert len(_sanitize_title_for_dir("超长标题" * 20)) <= 20
    assert _sanitize_title_for_dir("///") == "task"
    assert _sanitize_title_for_dir("") == "task"


def test_spec_docs_relative_dir_format() -> None:
    """版本目录路径 = specs/<净化标题>-<task_id前8>。"""
    assert (
        spec_docs_relative_dir("41f4c828-ec96-4c8a-b20b-bad035c91a1a", "背景改为纯绿色")
        == "specs/背景改为纯绿色-41f4c828"
    )
    # 标题缺失时兜底 task
    assert (
        spec_docs_relative_dir("41f4c828-ec96-4c8a-b20b-bad035c91a1a", None)
        == "specs/task-41f4c828"
    )


def test_read_plan_documents_missing_dir_returns_none(tmp_path: Path) -> None:
    """plans 目录不存在时返回 None。"""
    manager = WorkspaceManager(str(tmp_path))
    workspace_dir = manager.prepare("task-1")

    assert manager.read_plan_documents(workspace_dir) is None


def test_read_plan_documents_empty_dir_returns_none(tmp_path: Path) -> None:
    """plans 目录存在但无 md 文件时返回 None。"""
    manager = WorkspaceManager(str(tmp_path))
    workspace_dir = manager.prepare("task-1")
    (workspace_dir / ".tilacode" / "plans").mkdir(parents=True)

    assert manager.read_plan_documents(workspace_dir) is None


def test_read_plan_documents_joins_sorted_markdown(tmp_path: Path) -> None:
    """多个 plan 文档按文件名排序拼接，标题为文件名，分隔线相连。"""
    manager = WorkspaceManager(str(tmp_path))
    workspace_dir = manager.prepare("task-1")
    plans_dir = workspace_dir / ".tilacode" / "plans"
    plans_dir.mkdir(parents=True)
    (plans_dir / "b-second.md").write_text("第二份方案", encoding="utf-8")
    (plans_dir / "a-first.md").write_text("第一份方案", encoding="utf-8")
    (plans_dir / "notes.txt").write_text("非 md 不读取", encoding="utf-8")

    content = manager.read_plan_documents(workspace_dir)

    assert content is not None
    expected = "## a-first.md\n\n第一份方案\n\n---\n\n## b-second.md\n\n第二份方案"
    assert content == expected
    assert "notes.txt" not in content


# ---------------- ADR-055 D3 修订：provider 段必须留在工作目录配置 ----------------


def test_ensure_tilacode_config_preserves_provider_section(tmp_path: Path) -> None:
    """ensure_tilacode_config 不得删除 provider 段（任务不执行的回归根因）。

    工作目录配置是任务专属 provider（含 apiKey）的**唯一有效载体**：tilacode
    仅在启动时快照用户级配置，运行期只动态加载项目级配置。历史实现曾在
    ``ensure_tilacode_config`` 里「顺手剥离凭据」，而它先于
    ``_resolve_session_model`` 执行，导致复用工作目录的后续任务模型解析失败、
    任务永远不执行。
    """
    manager = WorkspaceManager(str(tmp_path / "root"), config_dir=tmp_path / "cfg")
    workspace_dir = manager.prepare("project-1")
    namespace = manager.upsert_provider_config(
        provider="deepseek",
        workspace_dir=workspace_dir,
        model="deepseek-chat",
        base_url="https://api.deepseek.com/v1",
        api_key="sk-secret",
        namespace="hub-deepseek-abc12345",
    )
    assert namespace == "hub-deepseek-abc12345"

    config_path = workspace_dir / ".tilacode" / "tilacode.jsonc"
    before = json.loads(config_path.read_text(encoding="utf-8"))
    assert before["provider"][namespace]["options"]["apiKey"] == "sk-secret"

    # 第二轮任务：复用同一工作目录再次准备配置
    manager.ensure_tilacode_config(workspace_dir)

    after = json.loads(config_path.read_text(encoding="utf-8"))
    assert "provider" in after, "provider 段被删除 → tilacode 解析不到模型 → 任务不执行"
    assert after["provider"][namespace]["options"]["apiKey"] == "sk-secret"
    # 模板段同时保留（permission_mode/default_agent/instructions 不被覆盖）
    assert "permission" not in after, "旧模板 blanket allow 必须被迁移（ADR-061 M2a）"
    assert after["permission_mode"] == "unattended"
    assert after["default_agent"] == "build"


def test_ensure_tilacode_config_heals_poisoned_keys(tmp_path: Path) -> None:
    """自愈：剔除未知毒键 ``_hub_managed_providers``，复数 providers 归并为单数。"""
    manager = WorkspaceManager(str(tmp_path / "root"), config_dir=tmp_path / "cfg")
    workspace_dir = manager.prepare("project-1")
    config_path = workspace_dir / ".tilacode" / "tilacode.jsonc"
    config_path.write_text(
        json.dumps(
            {
                "permission": {"*": "allow"},
                "_hub_managed_providers": {"x": {"updated_at": 1}},
                "providers": {"ns-1": {"npm": "@ai-sdk/openai-compatible"}},
            }
        ),
        encoding="utf-8",
    )

    manager.ensure_tilacode_config(workspace_dir)

    healed = json.loads(config_path.read_text(encoding="utf-8"))
    assert "_hub_managed_providers" not in healed
    assert "providers" not in healed
    assert healed["provider"]["ns-1"] == {"npm": "@ai-sdk/openai-compatible"}
    # ADR-061 M2a：旧模板的 blanket allow 一并迁移掉
    assert "permission" not in healed
    assert healed["permission_mode"] == "unattended"


def test_ensure_tilacode_config_writes_template_when_missing(tmp_path: Path) -> None:
    """配置文件不存在时写入模板（permission/default_agent/instructions）。"""
    manager = WorkspaceManager(str(tmp_path / "root"), config_dir=tmp_path / "cfg")
    workspace_dir = manager.prepare("project-1")
    config_path = workspace_dir / ".tilacode" / "tilacode.jsonc"
    assert not config_path.exists()

    manager.ensure_tilacode_config(workspace_dir)

    written = json.loads(config_path.read_text(encoding="utf-8"))
    assert "permission" not in written, "模板不得再写 blanket allow（ADR-061 M2a）"
    assert written["permission_mode"] == "unattended"
    assert written["default_agent"] == "build"
    assert written["instructions"]


# ---------------- 启动自检：用户级配置未知键清理（2026-09-27 事故防线）----------------


def test_sanitize_user_config_removes_poison_keys(tmp_path: Path) -> None:
    """用户级配置中的历史毒键（providers/_hub_managed_providers）必须被清除。

    事故背景：这两个键会让 tilacode 判定整个配置文件非法（Unrecognized keys），
    表现为服务启动即退出、或任务专属 provider 从未加载 → 任务永远停在 10%。

    夹具刻意包含 V1 专属键（``permission``）：tilacode 的 ``isV1`` 规则是
    「出现任一 V1 专属键即按 V1 解析」，而 V1 只有单数 ``provider``，
    因此本文件中的复数 ``providers`` 属未知键（这正是现场报错的原因）。
    """
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    config_path = cfg_dir / "tilacode.jsonc"
    config_path.write_text(
        json.dumps(
            {
                "$schema": "https://opencode.ai/config.json",
                "permission": {"*": "allow"},
                "provider": {"hub-deepseek-x": {"npm": "@ai-sdk/openai-compatible"}},
                "providers": {"hub-deepseek-x": {"options": {"apiKey": "sk-secret"}}},
                "_hub_managed_providers": {"hub-deepseek-x": {"updated_at": 1}},
            }
        ),
        encoding="utf-8",
    )
    manager = WorkspaceManager(str(tmp_path / "root"), config_dir=cfg_dir)

    removed = manager.sanitize_user_config()

    assert set(removed) == {"providers", "_hub_managed_providers"}
    cleaned = json.loads(config_path.read_text(encoding="utf-8"))
    # 合法键（含单数 provider 与 V1 permission）必须完整保留
    assert cleaned["permission"] == {"*": "allow"}
    assert cleaned["provider"] == {"hub-deepseek-x": {"npm": "@ai-sdk/openai-compatible"}}
    assert "_hub_managed_providers" not in cleaned
    assert "providers" not in cleaned
    # 幂等：再次清理无残留
    assert manager.sanitize_user_config() == []


def test_sanitize_user_config_keeps_v2_providers(tmp_path: Path) -> None:
    """纯 V2 配置（无 V1 键）里的复数 ``providers`` 是合法键，不得误删。"""
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    config_path = cfg_dir / "tilacode.jsonc"
    payload = {
        "$schema": "https://opencode.ai/config.json",
        "providers": {"mine": {"name": "mine", "models": {"m1": {"name": "m1"}}}},
    }
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    manager = WorkspaceManager(str(tmp_path / "root"), config_dir=cfg_dir)

    assert manager.sanitize_user_config() == []
    assert json.loads(config_path.read_text(encoding="utf-8")) == payload


def test_sanitize_user_config_keeps_known_keys(tmp_path: Path) -> None:
    """合法顶层键（V1/V2 known）必须原样保留，不得误删用户配置。"""
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    config_path = cfg_dir / "tilacode.jsonc"
    payload = {
        "$schema": "https://opencode.ai/config.json",
        "provider": {"mine": {"npm": "@ai-sdk/openai-compatible"}},
        "permission": {"*": "allow"},
        "instructions": [".tilacode/rules/a.md"],
        "model": "opencode/mimo-v2.5-free",
    }
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    manager = WorkspaceManager(str(tmp_path / "root"), config_dir=cfg_dir)

    assert manager.sanitize_user_config() == []
    assert json.loads(config_path.read_text(encoding="utf-8")) == payload


def test_sanitize_user_config_resets_unreadable_file(tmp_path: Path) -> None:
    """配置损坏时重置为空对象（保证 tilacode 能以缺省配置启动）。"""
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    config_path = cfg_dir / "tilacode.jsonc"
    config_path.write_text("{ 坏掉的 JSON", encoding="utf-8")
    manager = WorkspaceManager(str(tmp_path / "root"), config_dir=cfg_dir)

    removed = manager.sanitize_user_config()

    assert removed == ["<unreadable>"]
    assert config_path.read_text(encoding="utf-8") == "{}"


def test_sanitize_user_config_noop_when_absent(tmp_path: Path) -> None:
    """配置文件不存在时不创建文件（避免无谓落盘）。"""
    cfg_dir = tmp_path / "cfg"
    manager = WorkspaceManager(str(tmp_path / "root"), config_dir=cfg_dir)

    assert manager.sanitize_user_config() == []
    assert not (cfg_dir / "tilacode.jsonc").exists()


def test_ensure_tilacode_config_applies_permission_policy(tmp_path: Path) -> None:
    """UT-M2b-2d（ADR-061 M2b）：派发策略写入工作区配置并回读生效策略。

    回归对象：权限策略此前写死在节点代码里（模板硬编码 unattended），
    中枢无法下发白名单；且节点对权限询问无条件放行（见 task_bridge 用例）。
    """
    manager = WorkspaceManager(str(tmp_path))
    workspace_dir = manager.prepare("task-1")
    policy = permission_policy.normalize_policy(
        {"mode": "unattended", "allow": [{"action": "read", "resource": "/usr/include/**"}]}
    )

    effective = manager.ensure_tilacode_config(workspace_dir, policy=policy)

    config = json.loads((workspace_dir / ".tilacode" / "tilacode.jsonc").read_text("utf-8"))
    assert config["permission_mode"] == "unattended"
    assert config["permission"] == {"read": {"/usr/include/**": "allow"}}
    assert effective == policy


def test_ensure_tilacode_config_without_policy_keeps_cache(tmp_path: Path) -> None:
    """UT-M2b-2e（ADR-061 M2b，Q2）：中枢未下发策略时沿用本地已有配置（缓存语义）。"""
    manager = WorkspaceManager(str(tmp_path))
    workspace_dir = manager.prepare("task-1")
    manager.ensure_tilacode_config(
        workspace_dir,
        policy=permission_policy.normalize_policy(
            {"mode": "unattended", "allow": [{"action": "bash", "resource": "git *"}]}
        ),
    )

    effective = manager.ensure_tilacode_config(workspace_dir)

    assert effective == permission_policy.normalize_policy(
        {"mode": "unattended", "allow": [{"action": "bash", "resource": "git *"}]}
    )


def test_ensure_tilacode_config_clears_legacy_blanket_allow_with_policy(
    tmp_path: Path,
) -> None:
    """UT-M2b-2f（ADR-061 M2b）：历史 blanket allow 在下发策略时被清除。"""
    manager = WorkspaceManager(str(tmp_path))
    workspace_dir = manager.prepare("task-1")
    config_path = workspace_dir / ".tilacode" / "tilacode.jsonc"
    config_path.write_text(
        json.dumps({"permission": {"*": "allow"}, "provider": {"hub-x": {}}}), encoding="utf-8"
    )

    manager.ensure_tilacode_config(
        workspace_dir,
        policy=permission_policy.normalize_policy({"mode": "unattended", "allow": []}),
    )

    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert "*" not in config.get("permission", {})
    assert config["provider"] == {"hub-x": {}}, "provider 段必须保留（否则模型解析失败）"

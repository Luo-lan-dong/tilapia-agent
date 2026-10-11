"""节点侧权限策略单元测试（ADR-061 M2b）。

覆盖：归一化（含 blanket allow 丢弃与 fail-closed）、写入工作区配置（含历史
blanket allow 清除）、回读、通配符判定与可读拒绝原因。
"""

from typing import Any

from app.executor import permission_policy


def _policy(*entries: tuple[str, str], mode: str = "unattended") -> dict[str, Any]:
    """构造策略字典（测试辅助）。"""
    return {"mode": mode, "allow": [{"action": a, "resource": r} for a, r in entries]}


def test_normalize_drops_blanket_allow() -> None:
    """UT-M2b-1a：blanket allow（``*``/``*``）必须被丢弃，绝不进入配置。"""
    normalized = permission_policy.normalize_policy(
        {
            "mode": "unattended",
            "allow": [
                {"action": "*", "resource": "*"},
                {"action": "read", "resource": "/opt/data/**"},
                {"action": "read", "resource": "/opt/data/**"},
            ],
        }
    )
    assert normalized == _policy(("read", "/opt/data/**"))


def test_normalize_invalid_inputs_fail_closed() -> None:
    """UT-M2b-1b：未提供 → None（沿用缓存）；非法 → 最严（fail-closed）。"""
    assert permission_policy.normalize_policy(None) is None
    assert permission_policy.normalize_policy("allow") == permission_policy.default_policy()
    assert permission_policy.normalize_policy({"mode": "yolo", "allow": []}) == (
        permission_policy.default_policy()
    )
    # 非法条目逐条丢弃，合法条目保留（不因个别脏数据整条作废）
    normalized = permission_policy.normalize_policy(
        {
            "mode": "unattended",
            "allow": [
                {"action": "read", "resource": ""},
                {"action": 1, "resource": "/x"},
                {"action": "bash", "resource": "x" * 600},
                {"action": "bash", "resource": "pytest *"},
            ],
        }
    )
    assert normalized == _policy(("bash", "pytest *"))


def test_apply_to_config_writes_allowlist_and_preserves_provider() -> None:
    """UT-M2b-2a：策略写入 ``permission_mode`` + allow 条目，且不动 provider 段。"""
    config: dict[str, Any] = {
        "permission_mode": "interactive",
        "provider": {"hub-x": {"npm": "@ai-sdk/openai-compatible"}},
    }
    changed = permission_policy.apply_to_config(
        config, _policy(("read", "/usr/include/**"), ("bash", "git *"))
    )
    assert changed is True
    assert config["permission_mode"] == "unattended"
    assert config["permission"] == {
        "read": {"/usr/include/**": "allow"},
        "bash": {"git *": "allow"},
    }
    assert config["provider"] == {"hub-x": {"npm": "@ai-sdk/openai-compatible"}}


def test_apply_to_config_removes_legacy_blanket_allow() -> None:
    """UT-M2b-2b：三种历史 blanket allow 形式都必须被清除（安全红线）。"""
    # 形式一：工具键 "*" 直接为 "allow"（历史模板默认值）
    first: dict[str, Any] = {"permission": {"*": "allow"}}
    permission_policy.apply_to_config(first, _policy(("read", "/opt/**")))
    assert "*" not in first["permission"]

    # 形式二：{"*": {"*": "allow"}}
    second: dict[str, Any] = {"permission": {"*": {"*": "allow"}}}
    permission_policy.apply_to_config(second, _policy(("read", "/opt/**")))
    assert second["permission"]["*"] == {}

    # 形式三：整个 permission 值为字符串 "allow"
    third: dict[str, Any] = {"permission": "allow"}
    permission_policy.apply_to_config(third, _policy(("read", "/opt/**")))
    assert third["permission"] == {"read": {"/opt/**": "allow"}}


def test_apply_to_config_keeps_existing_string_rule() -> None:
    """UT-M2b-2c：既有整体效果（字符串）被保留为 ``"*"`` 兜底，allow 条目后写生效。"""
    config: dict[str, Any] = {"permission": {"bash": "deny"}}
    permission_policy.apply_to_config(config, _policy(("bash", "git *")))
    assert config["permission"]["bash"] == {"*": "deny", "git *": "allow"}


def test_effective_policy_roundtrip() -> None:
    """UT-M2b-3：写入后回读的策略与归一化策略一致（判定口径来源）。"""
    config: dict[str, Any] = {}
    policy = _policy(("read", "/usr/include/**"), ("bash", "git *"), ("*", "/opt/shared/**"))
    permission_policy.apply_to_config(config, policy)
    assert permission_policy.effective_policy_from_config(config) == policy


def test_is_allowed_matches_like_tilacode() -> None:
    """UT-M2b-4：判定镜像 tilacode ``Wildcard.match``（含 ``git *`` 匹配裸 ``git``）。"""
    policy = _policy(("read", "/usr/include/**"), ("bash", "git *"))
    assert permission_policy.is_allowed(policy, "read", "/usr/include/stdio.h") is True
    assert permission_policy.is_allowed(policy, "bash", "git status") is True
    # tilacode：pattern "git *" → "git( .*)?\"，裸 git 也算命中
    assert permission_policy.is_allowed(policy, "bash", "git") is True
    assert permission_policy.is_allowed(policy, "bash", "rm -rf /") is False
    assert permission_policy.is_allowed(policy, "write", "/usr/include/stdio.h") is False
    # 无白名单 → 一律拒绝（最严兜底）
    assert (
        permission_policy.is_allowed(permission_policy.default_policy(), "read", "/etc/passwd")
        is False
    )


def test_deny_reason_is_readable() -> None:
    """UT-M2b-8：拒绝原因含动作/资源与「不要重复尝试」的指引。"""
    reason = permission_policy.deny_reason("bash", "rm -rf /")
    assert "bash" in reason and "rm -rf /" in reason
    assert "白名单" in reason
    assert "不要重复尝试" in reason

"""验收判据归一化测试（ADR-061 M4）。

覆盖：必过层默认规则（L0/L1 必过、L2 存在即必过、L3/L4 需显式）、层白名单、
每层超时与单条覆盖上限、非法/空白条目剔除、条数与长度上限、空判据 → None，
以及 spec_builder 的容错 JSON 提取。
"""

from app.agents.spec_builder import parse_verify_json
from app.services.verify_plan import (
    LAYER_TIMEOUTS,
    MAX_CHECKS,
    must_pass_checks,
    normalize_verify_plan,
)


def test_must_pass_defaults_by_layer() -> None:
    """UT-M4-2a：必过层规则固化——L0/L1 必过、L2 存在即必过、L3/L4 默认非必过。"""
    plan = normalize_verify_plan(
        {
            "checks": [
                {"layer": "L0", "cmd": "python -m compileall -q ."},
                {"layer": "L1", "cmd": "pytest -q"},
                {"layer": "L2", "cmd": "curl -sf localhost:8000/health"},
                {"layer": "L3", "cmd": "npx playwright test"},
                {"layer": "L4", "cmd": "make bench"},
            ]
        }
    )
    assert plan is not None
    flags = {item["layer"]: item["must_pass"] for item in plan["checks"]}
    assert flags == {"L0": True, "L1": True, "L2": True, "L3": False, "L4": False}
    assert [item["layer"] for item in must_pass_checks(plan)] == ["L0", "L1", "L2"]


def test_explicit_must_pass_overrides_default() -> None:
    """UT-M4-2b：显式标注可把 L3 提为必过、也可把 L1 降为参考。"""
    plan = normalize_verify_plan(
        {
            "checks": [
                {"layer": "L1", "cmd": "pytest -q", "must_pass": False},
                {"layer": "L3", "cmd": "npx playwright test", "must_pass": True},
            ]
        }
    )
    assert plan is not None
    assert [item["layer"] for item in must_pass_checks(plan)] == ["L3"]


def test_layer_timeouts_and_override_clamped() -> None:
    """UT-M4-3a：默认每层超时按用户所定；单条可覆盖但受上限约束。"""
    plan = normalize_verify_plan(
        {
            "checks": [
                {"layer": "L0", "cmd": "a", "timeout_s": 5},
                {"layer": "L2", "cmd": "b"},
                {"layer": "L4", "cmd": "c", "timeout_s": 10**9},
            ]
        }
    )
    assert plan is not None
    by_layer = {item["layer"]: item["timeout_s"] for item in plan["checks"]}
    assert by_layer["L0"] == 5
    assert by_layer["L2"] == LAYER_TIMEOUTS["L2"]
    assert by_layer["L4"] <= 3600


def test_invalid_entries_dropped_and_empty_returns_none() -> None:
    """UT-M4-2c：非法层/空命令/非 dict 条目被剔除；全无有效检查 → None（=无法验证）。"""
    plan = normalize_verify_plan(
        {
            "checks": [
                {"layer": "L9", "cmd": "x"},
                {"layer": "L0", "cmd": "   "},
                "not-a-dict",
                {"layer": "L1", "cmd": "pytest -q", "name": ""},
            ]
        }
    )
    assert plan is not None
    assert len(plan["checks"]) == 1
    assert plan["checks"][0]["name"].startswith("L1")

    assert normalize_verify_plan({"checks": []}) is None
    assert normalize_verify_plan(None) is None
    assert normalize_verify_plan("junk") is None


def test_check_count_cap() -> None:
    """UT-M4-3b：条数上限生效（超出丢弃，不抛异常）。"""
    plan = normalize_verify_plan(
        {"checks": [{"layer": "L0", "cmd": f"cmd-{i}"} for i in range(MAX_CHECKS + 10)]}
    )
    assert plan is not None
    assert len(plan["checks"]) == MAX_CHECKS


def test_parse_verify_json_tolerates_fences_and_garbage() -> None:
    """UT-M4-1：判据轮容错解析——去 ``` 围栏、取首尾花括号；垃圾输入返回 None。"""
    fenced = '```json\n{"checks":[{"layer":"L1","cmd":"pytest -q"}]}\n```'
    plan = parse_verify_json(fenced)
    assert plan is not None and plan["checks"][0]["cmd"] == "pytest -q"

    noisy = '说明如下：{"checks":[{"layer":"L0","cmd":"tsc --noEmit"}]} 以上。'
    plan = parse_verify_json(noisy)
    assert plan is not None and plan["checks"][0]["layer"] == "L0"

    assert parse_verify_json("这部分没有 JSON") is None
    assert parse_verify_json('{"checks": [ 坏掉的 JSON') is None

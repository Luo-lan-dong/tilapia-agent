"""节点分层验收执行器测试（ADR-061 M4）。

覆盖：判据归一化默认必过规则、判据来源优先级（verify.json > 项目配置 > 推导 > 无）、
真实命令执行（通过/失败）、超时按未通过计、非必过失败不阻断、报告结构。
"""

import json
from pathlib import Path

import pytest

from app.executor import verify_runner


def _write_verify_json(workspace: Path, checks: list[dict]) -> None:
    """把判据写入 ``specs/<doc_dir>/verify.json``。"""
    doc_dir = workspace / "specs" / "t1"
    doc_dir.mkdir(parents=True, exist_ok=True)
    (doc_dir / "verify.json").write_text(
        json.dumps({"checks": checks}, ensure_ascii=False), encoding="utf-8"
    )


def test_normalize_defaults_and_invalid_dropped() -> None:
    """UT-M4-2a：必过层默认规则固化；非法条目剔除；空 → None。"""
    plan = verify_runner.normalize_verify_plan(
        {
            "checks": [
                {"layer": "L0", "cmd": "compile"},
                {"layer": "L2", "cmd": "curl"},
                {"layer": "L4", "cmd": "bench"},
                {"layer": "L9", "cmd": "bad-layer"},
                {"layer": "L1", "cmd": "  "},
            ]
        }
    )
    assert plan is not None
    flags = {item["layer"]: item["must_pass"] for item in plan["checks"]}
    assert flags == {"L0": True, "L2": True, "L4": False}
    assert verify_runner.normalize_verify_plan({"checks": []}) is None
    assert verify_runner.normalize_verify_plan(None) is None


def test_load_plan_priority(tmp_path: Path) -> None:
    """UT-M4-2：优先级 verify.json > 项目配置 > 推导；全无 → none。"""
    # 1) verify.json 存在即权威（即使项目里也有 package.json）
    _write_verify_json(tmp_path, [{"layer": "L1", "cmd": "pytest -q", "name": "判据"}])
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "vitest run"}}), encoding="utf-8"
    )
    plan, source = verify_runner.load_plan(tmp_path, "specs/t1")
    assert source == "verify_json" and plan is not None
    assert plan["checks"][0]["name"] == "判据"

    # 2) 无 verify.json → 项目自带配置（package.json scripts.test）
    other = tmp_path / "proj"
    other.mkdir()
    (other / "package.json").write_text(
        json.dumps({"scripts": {"test": "vitest run", "build": "vite build"}}), encoding="utf-8"
    )
    plan, source = verify_runner.load_plan(other, None)
    assert source == "project_config" and plan is not None
    layers = {item["layer"] for item in plan["checks"]}
    assert layers == {"L0", "L1"}

    # 3) 只有 .py 文件 → 推导出语法编译检查
    derived_dir = tmp_path / "derived"
    derived_dir.mkdir()
    (derived_dir / "main.py").write_text("print('hi')\n", encoding="utf-8")
    plan, source = verify_runner.load_plan(derived_dir, None)
    assert source == "derived" and plan is not None
    assert plan["checks"][0]["layer"] == "L0"

    # 4) 什么都没有 → 无法验证
    empty = tmp_path / "empty"
    empty.mkdir()
    plan, source = verify_runner.load_plan(empty, None)
    assert plan is None and source == "none"


async def test_run_plan_passed_and_failed(tmp_path: Path) -> None:
    """UT-M4-4：真实命令——退出码 0 通过、非 0 失败并保留输出尾部。"""
    plan = verify_runner.normalize_verify_plan(
        {
            "checks": [
                {
                    "layer": "L0",
                    "name": "通过项",
                    "cmd": 'python -c "print(\'ok\')"',
                },
                {
                    "layer": "L1",
                    "name": "失败项",
                    "cmd": 'python -c "import sys; print(\'boom\'); sys.exit(3)"',
                },
            ]
        }
    )
    report = await verify_runner.run_plan(tmp_path, plan, "verify_json")

    assert report.status == "failed"
    by_name = {item.name: item for item in report.checks}
    assert by_name["通过项"].status == "passed"
    assert by_name["失败项"].status == "failed"
    assert by_name["失败项"].exit_code == 3
    assert "boom" in by_name["失败项"].output_tail
    assert [item.name for item in report.must_pass_failures] == ["失败项"]


async def test_run_plan_timeout_counts_as_failure(tmp_path: Path) -> None:
    """UT-M4-3：超时**按未通过计**（status=timeout 且计入必过失败）。"""
    plan = verify_runner.normalize_verify_plan(
        {
            "checks": [
                {
                    "layer": "L1",
                    "name": "超时项",
                    "cmd": 'python -c "import time; time.sleep(5)"',
                    "timeout_s": 1,
                }
            ]
        }
    )
    report = await verify_runner.run_plan(tmp_path, plan, "verify_json")

    assert report.status == "failed"
    assert report.checks[0].status == "timeout"
    assert len(report.must_pass_failures) == 1


async def test_non_must_pass_failure_does_not_block(tmp_path: Path) -> None:
    """UT-M4-5：非必过项失败 **不**阻断完成（报告仍为 passed，但如实展示该项失败）。"""
    plan = verify_runner.normalize_verify_plan(
        {
            "checks": [
                {"layer": "L1", "name": "必过项", "cmd": 'python -c "print(1)"'},
                {
                    "layer": "L4",
                    "name": "参考项",
                    "cmd": 'python -c "import sys; sys.exit(1)"',
                },
            ]
        }
    )
    report = await verify_runner.run_plan(tmp_path, plan, "verify_json")

    assert report.status == "passed"
    assert report.must_pass_failures == []
    assert {item.status for item in report.checks} == {"passed", "failed"}


async def test_run_plan_without_plan_is_unverifiable(tmp_path: Path) -> None:
    """UT-M4-4b：无判据 → ``unverifiable``（「无法验证」，不得据此判成功）。"""
    report = await verify_runner.run_plan(tmp_path, None, "none")

    assert report.status == "unverifiable"
    assert report.must_pass_failures == []
    assert "无法验证" in report.summary()


def test_report_payload_shape(tmp_path: Path) -> None:
    """UT-M4-7：报告转 payload 含层/命令/必过/退出码/输出尾部，供落痕与前端展示。"""
    report = verify_runner.VerifyReport(
        status="failed",
        source="verify_json",
        checks=[
            verify_runner.CheckResult(
                layer="L1",
                name="单测",
                cmd="pytest -q",
                cwd=str(tmp_path),
                must_pass=True,
                status="failed",
                exit_code=1,
                duration_ms=12,
                output_tail="1 failed",
            )
        ],
    )
    payload = report.to_payload()

    assert payload["status"] == "failed"
    assert payload["must_pass_failures"][0]["name"] == "单测"
    assert payload["checks"][0]["output_tail"] == "1 failed"


@pytest.mark.parametrize("layer", ["L0", "L1", "L2", "L3", "L4"])
def test_default_timeouts_match_spec(layer: str) -> None:
    """UT-M4-3c：默认超时与用户所定一致（30s/60s/5min/10min/20min）。"""
    expected = {"L0": 30, "L1": 60, "L2": 300, "L3": 600, "L4": 1200}
    assert verify_runner.LAYER_TIMEOUTS[layer] == expected[layer]

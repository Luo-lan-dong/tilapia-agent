"""环境要求清单下发与首次检查注入测试（ADR-048 P2）。"""

from pathlib import Path

from app.executor.task_bridge import TaskBridge
from app.models.schemas import EnvironmentRequirement, TaskDispatchPayload


def _payload(requirements: list[EnvironmentRequirement]) -> TaskDispatchPayload:
    return TaskDispatchPayload.model_validate(
        {
            "task_id": "t-env-1",
            "description": "开发待办事项应用",
            "environment_requirements": [req.model_dump() for req in requirements],
        }
    )


def test_payload_parses_environment_requirements() -> None:
    """payload 解析：含 environment_requirements 正常解析，缺省为空列表。"""
    payload = _payload(
        [
            EnvironmentRequirement(
                name="Python",
                check_cmd="python3 --version",
                min_version="3.10",
                install_hint="apt install python3.11",
            )
        ]
    )
    assert payload.environment_requirements is not None
    assert payload.environment_requirements[0].min_version == "3.10"

    legacy = TaskDispatchPayload.model_validate({"task_id": "t2"})
    assert legacy.environment_requirements == []


def test_first_run_injects_check_and_install_instructions(tmp_path: Path) -> None:
    """项目首次开发（无 environment.md）：注入逐条检查指令与安装规则。"""
    payload = _payload(
        [
            EnvironmentRequirement(
                name="Python",
                check_cmd="python3 --version",
                min_version="3.10",
                install_hint="apt install python3.11",
            ),
            EnvironmentRequirement(name="Node", check_cmd="node --version"),
        ]
    )
    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()

    part = TaskBridge._environment_prompt_part(payload, ws_dir)

    assert part is not None
    assert "开工第一步" in part
    # 首要指令：读取中枢下发的环境检查文档（元规则目录）
    assert ".tilacode/rules/environment-check.md" in part
    # 内联清单兜底
    assert "`python3 --version`" in part
    assert "≥ 3.10" in part
    assert "apt install python3.11" in part
    assert "`node --version`" in part
    assert ".tilacode/environment.md" in part  # 结果写入指示
    assert "不要静默跳过" in part


def test_ready_directory_skips_recheck(tmp_path: Path) -> None:
    """同项目迭代（environment.md 已存在）：只提示已就绪，不再注入检查清单。"""
    payload = _payload([EnvironmentRequirement(name="Python", check_cmd="python3 --version")])
    ws_dir = tmp_path / "ws"
    (ws_dir / ".tilacode").mkdir(parents=True)
    (ws_dir / ".tilacode" / "environment.md").write_text("# 检查记录", encoding="utf-8")

    part = TaskBridge._environment_prompt_part(payload, ws_dir)

    assert part is not None
    assert "无需重复探测" in part
    assert "开工第一步" not in part


def test_no_requirements_returns_none(tmp_path: Path) -> None:
    """任务无环境要求清单时不注入任何环境块（回归语义）。"""
    payload = TaskDispatchPayload.model_validate({"task_id": "t3", "description": "d"})
    part = TaskBridge._environment_prompt_part(payload, tmp_path)
    assert part is None

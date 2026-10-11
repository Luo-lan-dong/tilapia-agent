"""部署规划技能测试（deploy_plan：待办清单写入任务清单面板）。"""

from __future__ import annotations

from app.skills.base import SkillContext
from app.skills.deploy_plan import DeployPlanSkill


async def _collect(args: dict, current_specification: dict | None = None):
    """执行技能并收集事件。"""
    context = SkillContext(args=args, current_specification=current_specification)
    return [event async for event in DeployPlanSkill().execute(None, context)]  # type: ignore[arg-type]


def _result(events: list[dict]):
    """取技能结果事件。"""
    return events[-1]["data"]


async def test_plan_writes_tasks_specification() -> None:
    """清单渲染为 Markdown 并经 specification 事件更新任务清单面板。"""
    events = await _collect(
        {"target": "宝塔服务器-101.13", "steps": ["确认端口", "创建站点", "核验访问"]}
    )
    spec_event = next(event for event in events if event["event"] == "specification")
    assert "部署待办清单" in spec_event["data"]["tasks"]
    assert "**目标**：宝塔服务器-101.13" in spec_event["data"]["tasks"]
    assert "1. 确认端口" in spec_event["data"]["tasks"]
    assert "3. 核验访问" in spec_event["data"]["tasks"]
    result = _result(events)
    assert result.success is True
    assert "3 步" in result.summary


async def test_plan_preserves_spec_and_checklist() -> None:
    """未提供的 spec/checklist 保留会话现有值（三件套不覆盖）。"""
    events = await _collect(
        {"steps": ["步骤 A"]},
        current_specification={"spec": "# 现有规范", "tasks": "旧清单", "checklist": "# 现有验收"},
    )
    spec_event = next(event for event in events if event["event"] == "specification")
    assert spec_event["data"]["spec"] == "# 现有规范"
    assert spec_event["data"]["checklist"] == "# 现有验收"
    assert "步骤 A" in spec_event["data"]["tasks"]


async def test_plan_missing_steps_fails() -> None:
    """缺 steps 参数返回失败指引而非异常。"""
    result = _result(await _collect({}))
    assert result.success is False
    assert "steps" in result.summary


async def test_plan_empty_steps_fails() -> None:
    """steps 全空返回失败。"""
    result = _result(await _collect({"steps": ["", "  "]}))
    assert result.success is False

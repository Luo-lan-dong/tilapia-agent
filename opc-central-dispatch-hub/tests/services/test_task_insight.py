"""任务只读洞察测试（ADR-061 M5c）：用量曲线、汇总基线对比、三条告警规则、时间线。

覆盖用户已定基线：**命中率 < 95.2%** / **连续 ≥3 次同签名动作** / **≥4 个统计点无文件写入**；
以及「样本不足不告警」「动作不同不告警」「有文件产出不告警」等**不误报**路径。
"""

import json
import uuid

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Task, TaskStatus
from app.models.tool_call_log import ToolCallLog, ToolCallLogKind
from app.services import task_insight_service as insight


def _usage_row(
    input_tokens: int,
    cache_read: int,
    *,
    files: int = 1,
    tool: str = "edit",
    output_tokens: int = 0,
) -> ToolCallLog:
    """构造一条未落库的 llm_usage 行（测试辅助）。"""
    return ToolCallLog(
        kind=ToolCallLogKind.LLM_USAGE.value,
        name="agent_llm_usage",
        task_id=str(uuid.uuid4()),
        args_summary=json.dumps({"tool": tool}),
        result_summary=json.dumps(
            {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_tokens": cache_read,
                "files_touched": files,
            }
        ),
    )


def test_usage_points_and_summary_hit_rate() -> None:
    """UT-M5c-1：曲线点与汇总——命中率 = cache_read/(input+cache_read)，并与基线对比。"""
    rows = [_usage_row(100, 900), _usage_row(200, 800)]

    points = insight.usage_points(rows)
    summary = insight.summarize_usage(points)

    assert points[0]["cache_hit_rate"] == pytest.approx(0.9)
    assert summary["steps"] == 2
    assert summary["input_tokens"] == 300
    assert summary["cache_read_tokens"] == 1700
    assert summary["cache_hit_rate"] == pytest.approx(round(1700 / 2000, 4))
    assert summary["hit_rate_below_baseline"] is True


def test_alert_hit_rate_requires_enough_samples() -> None:
    """UT-M5c-2：命中率告警——样本不足**不告警**；样本足够且低于基线才告警。"""
    low_rows = [_usage_row(100, 0) for _ in range(insight.MIN_HIT_RATE_SAMPLES - 1)]
    assert insight.compute_alerts(insight.usage_points(low_rows), []) == []

    enough_rows = [_usage_row(100, 0) for _ in range(insight.MIN_HIT_RATE_SAMPLES)]
    alerts = insight.compute_alerts(insight.usage_points(enough_rows), [])
    assert [alert["code"] for alert in alerts] == ["cache_hit_below_baseline"]


def test_alert_repeated_activity_signature() -> None:
    """UT-M5c-3：连续 3 次同签名动作告警；动作不同（或不足 3 次）不告警。"""
    same = [{"tool": "bash", "message": "正在执行 npm test"} for _ in range(3)]
    alerts = insight.compute_alerts([], same)
    assert [alert["code"] for alert in alerts] == ["repeated_activity"]
    assert "npm test" in alerts[0]["detail"]

    assert insight.compute_alerts([], same[:2]) == []
    distinct = [
        {"tool": "bash", "message": "npm test"},
        {"tool": "edit", "message": "改 main.py"},
        {"tool": "bash", "message": "npm test"},
    ]
    assert insight.compute_alerts([], distinct) == []
    # 无工具名的动作不参与重复判定（避免误报）
    assert insight.compute_alerts([], [{"tool": None, "message": "x"}] * 4) == []


def test_alert_no_file_write() -> None:
    """UT-M5c-4：连续 4 个统计点无文件写入告警；有产出则不告警。"""
    idle_points = insight.usage_points([_usage_row(10, 0, files=0) for _ in range(4)])
    alerts = insight.compute_alerts(idle_points, [])
    assert [alert["code"] for alert in alerts] == ["no_file_write"]

    productive = insight.usage_points(
        [_usage_row(10, 0, files=0) for _ in range(3)] + [_usage_row(10, 0, files=2)]
    )
    assert insight.compute_alerts(productive, []) == []


def test_timeline_titles_and_details() -> None:
    """UT-M5c-5：时间线把各类事件转成人读标题（动作/发言/用量/完成）。"""
    task_id = str(uuid.uuid4())
    rows = [
        ToolCallLog(
            kind=ToolCallLogKind.DISPATCH.value,
            name="node-a",
            task_id=task_id,
            result_summary=json.dumps({"dispatch_type": "auto"}),
        ),
        ToolCallLog(
            kind=ToolCallLogKind.AGENT_TASK_EVENT.value,
            name="tool_activity",
            task_id=task_id,
            result_summary=json.dumps({"tool": "bash", "message": "正在执行 npm test"}),
        ),
        ToolCallLog(
            kind=ToolCallLogKind.STEERING.value,
            name="task_steer_sent",
            task_id=task_id,
            result_summary=json.dumps({"text": "请先跑测试"}),
        ),
        _usage_row(10, 90),
        ToolCallLog(
            kind=ToolCallLogKind.AGENT_TASK_EVENT.value,
            name="task_completed",
            task_id=task_id,
            result_summary=json.dumps({"status": "completed"}),
        ),
    ]

    entries = insight.build_timeline(rows)
    titles = [entry["title"] for entry in entries]

    assert titles[0] == "派发到节点"
    assert titles[1] == "执行动作：bash"
    assert "npm test" in entries[1]["detail"]
    assert titles[2] == "中枢发言" and entries[2]["detail"] == "请先跑测试"
    assert titles[3] == "用量统计点"
    assert titles[4] == "完成上报"


@pytest.fixture
async def insight_task(db_session: AsyncSession) -> Task:
    """落库一个任务（用例后清理任务与其事件行）。"""
    task = Task(
        id=uuid.uuid4(),
        title="洞察测试任务",
        agent_type="programming",
        status=TaskStatus.RUNNING.value,
    )
    db_session.add(task)
    await db_session.commit()
    yield task
    await db_session.execute(delete(ToolCallLog).where(ToolCallLog.task_id == str(task.id)))
    await db_session.execute(delete(Task).where(Task.id == task.id))
    await db_session.commit()


async def test_collect_task_insight_end_to_end(
    db_session: AsyncSession, insight_task: Task
) -> None:
    """UT-M5c-6：一次性取回——曲线点/汇总/告警/时间线都来自既有落库，无需新协议。"""
    for _ in range(4):
        db_session.add(
            ToolCallLog(
                kind=ToolCallLogKind.LLM_USAGE.value,
                name="agent_llm_usage",
                task_id=str(insight_task.id),
                args_summary=json.dumps({"tool": "bash"}),
                result_summary=json.dumps(
                    {
                        "input_tokens": 100,
                        "output_tokens": 10,
                        "cache_read_tokens": 0,
                        "files_touched": 0,
                    }
                ),
            )
        )
    db_session.add(
        ToolCallLog(
            kind=ToolCallLogKind.AGENT_TASK_EVENT.value,
            name="tool_activity",
            task_id=str(insight_task.id),
            result_summary=json.dumps({"tool": "bash", "message": "正在执行 npm test"}),
        )
    )
    await db_session.commit()

    result = await insight.collect_task_insight(db_session, str(insight_task.id))

    assert result["summary"]["steps"] == 4
    codes = {alert["code"] for alert in result["alerts"]}
    assert "no_file_write" in codes  # 4 个统计点都无文件产出
    assert "cache_hit_below_baseline" not in codes  # 样本不足 5 个 → 不误报命中率
    assert any(entry["title"] == "执行动作：bash" for entry in result["timeline"])

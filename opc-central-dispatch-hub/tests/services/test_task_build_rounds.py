"""任务构建回合收集单测（问题修复：跨回合步骤收尾）。"""

from app.services.task_build_rounds import BuildRoundCollector, finalize_running_steps


def test_finish_step_crosses_round_split() -> None:
    """回合分割后 step_finished 仍更新旧回合的步骤（不再卡执行中）。"""
    collector = BuildRoundCollector()
    collector.add_thinking("第一轮")
    collector.start_step({"step_id": "s1", "seq": 1, "skill_name": "skill_a"})
    # 步骤未结束时的思考增量 = 开启下一回合
    collector.add_thinking("第二轮")
    collector.finish_step(
        {"step_id": "s1", "status": "success", "result_summary": "完成", "duration_ms": 5}
    )

    assert [rnd["thinking"] for rnd in collector.rounds] == ["第一轮", "第二轮"]
    step = collector.rounds[0]["steps"][0]
    assert step["status"] == "success"
    assert step["result_summary"] == "完成"
    assert step["duration_ms"] == 5


def test_close_round_opens_new_round_for_next_thinking() -> None:
    """轨迹事件闭合回合后，后续思考/步骤进入新回合。"""
    collector = BuildRoundCollector()
    collector.add_thinking("a")
    collector.close_round()
    collector.add_thinking("b")
    collector.start_step({"step_id": "s1"})

    assert [rnd["thinking"] for rnd in collector.rounds] == ["a", "b"]
    assert len(collector.rounds[1]["steps"]) == 1


def test_finalize_running_steps_marks_failed_and_keeps_summary() -> None:
    """落库收尾：running → failed；已有 result_summary 不被覆盖。"""
    messages = [
        {
            "role": "assistant",
            "steps": [
                {"step_id": "s1", "status": "running"},
                {"step_id": "s2", "status": "running", "result_summary": "已有说明"},
                {"step_id": "s3", "status": "success"},
            ],
        }
    ]

    assert finalize_running_steps(messages) == 2
    steps = messages[0]["steps"]
    assert steps[0]["status"] == "failed"
    assert "流中断" in steps[0]["result_summary"]
    assert steps[1]["result_summary"] == "已有说明"
    assert steps[2]["status"] == "success"

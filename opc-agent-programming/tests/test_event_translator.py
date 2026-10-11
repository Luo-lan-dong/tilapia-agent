"""EventTranslator 单元测试（ADR-055：对齐真实 session.next.* schema）。"""

from app.executor.event_translator import (
    EventTranslator,
    ProgressUpdate,
    is_permission_event,
)


def make_translator() -> EventTranslator:
    """构造标记为 session 就绪的翻译器。"""
    translator = EventTranslator("task-1")
    translator.mark_session_ready()
    return translator


def test_mark_session_ready_sets_initial_progress() -> None:
    """mark_session_ready 应将初始进度设为 10。"""
    translator = EventTranslator("task-1")
    assert translator.progress == 0
    translator.mark_session_ready()
    assert translator.progress == 10


def test_first_step_started_after_ready_gets_20() -> None:
    """就绪后首个 step.started 进度应为 20，阶段为 coding（build）。"""
    translator = make_translator()
    update = translator.translate({"type": "session.next.step.started", "data": {}})
    assert update is not None
    assert update.progress == 20
    assert update.message == "开始执行阶段"
    assert update.stage == "coding"


def test_step_started_plan_agent_maps_to_planning() -> None:
    """plan 模式（agent=plan）的 step.started 应映射 planning 阶段。"""
    translator = make_translator()
    update = translator.translate({"type": "session.next.step.started", "data": {"agent": "plan"}})
    assert update is not None
    assert update.stage == "planning"


def test_progress_monotonic_and_capped_at_90() -> None:
    """连续 100 个工具事件进度应单调递增且封顶 90。"""
    translator = make_translator()
    translator.translate({"type": "session.next.step.started", "data": {}})
    last = translator.progress
    for i in range(100):
        update = translator.translate(
            {"type": "session.next.tool.called", "data": {"tool": f"tool_{i}"}}
        )
        assert update is not None
        assert update.progress >= last
        assert update.progress <= 90
        last = update.progress
    assert last == 90


def test_step_started_never_regresses_progress() -> None:
    """进度推进后再次 step.started 不应回退到 20。"""
    translator = make_translator()
    translator.translate({"type": "session.next.step.started", "data": {}})
    for _ in range(20):
        translator.translate({"type": "session.next.tool.success", "data": {"tool": "bash"}})
    update = translator.translate({"type": "session.next.step.started", "data": {}})
    assert update is not None
    assert update.progress == 20 + 20 * 2  # 60，未回退


def test_tool_called_extracts_tool_name() -> None:
    """tool.called 应从 data.tool（真实 schema）提取工具名并映射阶段。

    ADR-055 P1-8：原实现读 ``data.name``/``data.tool.title``，而真实事件
    字段是 ``tool: string``，导致工具名恒为 unknown。
    """
    translator = make_translator()
    update = translator.translate(
        {
            "type": "session.next.tool.called",
            "data": {"tool": "bash", "input": {"command": "npm test"}},
        }
    )
    assert update is not None
    assert update.tool_name == "bash"
    assert update.stage == "running_command"
    assert update.message == "正在执行 npm test"


def test_tool_called_edit_message_uses_relative_path() -> None:
    """edit 工具的当前动作应展示相对路径并映射 editing 阶段。"""
    translator = make_translator()
    update = translator.translate(
        {
            "type": "session.next.tool.called",
            "data": {"tool": "edit", "input": {"filePath": "src/app/main.py"}},
        }
    )
    assert update is not None
    assert update.stage == "editing"
    assert update.message == "正在处理 src/app/main.py"


def test_tool_called_absolute_path_is_shortened() -> None:
    """绝对路径只展示末两段，避免泄露节点目录结构。"""
    translator = make_translator()
    update = translator.translate(
        {
            "type": "session.next.tool.called",
            "data": {
                "tool": "write",
                "input": {"filePath": "/opt/opc-agent-code/workspace/t1/src/a.py"},
            },
        }
    )
    assert update is not None
    assert update.message == "正在处理 src/a.py"


def test_tool_name_missing_fields_no_exception() -> None:
    """data 缺字段时不应抛异常，工具名回退为 unknown。"""
    translator = make_translator()
    update = translator.translate({"type": "session.next.tool.called", "data": {}})
    assert update is not None
    assert update.tool_name == "unknown"


def test_tool_success_and_failed_messages() -> None:
    """tool.success 推进进度；tool.failed 只上报不推进进度。"""
    translator = make_translator()
    translator.translate({"type": "session.next.step.started", "data": {}})

    success = translator.translate({"type": "session.next.tool.success", "data": {"tool": "read"}})
    assert success is not None
    assert success.message == "完成 read"
    assert success.progress == 22

    failed = translator.translate(
        {
            "type": "session.next.tool.failed",
            "data": {"tool": "bash", "error": {"message": "exit 1"}},
        }
    )
    assert failed is not None
    assert failed.message == "工具失败 bash：exit 1"
    assert failed.progress == 22  # 失败不推进进度


def test_tool_progress_keeps_progress_and_reports_message() -> None:
    """tool.progress 不推进进度，仅作为「仍在执行」信号（ADR-055 P2-13）。"""
    translator = make_translator()
    translator.translate({"type": "session.next.step.started", "data": {}})
    translator.translate({"type": "session.next.tool.called", "data": {"tool": "bash"}})
    before = translator.progress

    update = translator.translate(
        {"type": "session.next.tool.progress", "data": {"tool": "bash", "content": []}}
    )
    assert update is not None
    assert update.progress == before
    assert update.tool_name == "bash"


def test_compaction_events_map_stage_and_restore() -> None:
    """compaction.started 映射 compacting，ended 恢复原阶段。"""
    translator = make_translator()
    translator.translate({"type": "session.next.step.started", "data": {}})
    started = translator.translate(
        {"type": "session.next.compaction.started", "data": {"reason": "auto"}}
    )
    assert started is not None
    assert started.stage == "compacting"
    assert started.message == "正在压缩上下文"

    ended = translator.translate({"type": "session.next.compaction.ended", "data": {}})
    assert ended is not None
    assert ended.stage == "coding"


def test_retried_event_message_contains_attempt() -> None:
    """retried 事件应产生含重试次数的当前动作。"""
    translator = make_translator()
    update = translator.translate(
        {"type": "session.next.retried", "data": {"attempt": 2, "error": {"message": "429"}}}
    )
    assert update is not None
    assert "重试" in update.message
    assert "2" in update.message


def test_step_ended_detail_carries_tokens_cost_files() -> None:
    """step.ended 应提取 tokens/cost/files 作为可观测明细（ADR-055 P1-8）。"""
    translator = make_translator()
    update = translator.translate(
        {
            "type": "session.next.step.ended",
            "data": {
                "finish": "stop",
                "cost": 0.12,
                "tokens": {
                    "input": 1200,
                    "output": 300,
                    "reasoning": 0,
                    "cache": {"read": 900, "write": 0},
                },
                "files": ["src/a.py"],
            },
        }
    )
    assert update is not None
    assert update.detail is not None
    assert update.detail["tokens"]["cache_read"] == 900
    assert update.detail["cost"] == 0.12
    assert update.detail["files"] == ["src/a.py"]


def test_text_ended_summary_truncated_to_120() -> None:
    """text.ended 应输出最后文本片段摘要并截断 120 字符。"""
    translator = make_translator()
    long_text = "字" * 200
    update = translator.translate({"type": "session.next.text.ended", "data": {"text": long_text}})
    assert update is not None
    assert update.message == "字" * 120
    assert len(update.message) == 120


def test_step_ended_advances_progress() -> None:
    """step.ended 应推进进度并上报“阶段完成”。"""
    translator = make_translator()
    translator.translate({"type": "session.next.step.started", "data": {}})
    update = translator.translate({"type": "session.next.step.ended", "data": {}})
    assert update is not None
    assert update.message == "阶段完成"
    assert update.progress == 22


def test_todo_updated_maps_message_from_in_progress_item() -> None:
    """todo.updated 的当前动作取 in_progress 项内容（任务摘要据此展示）。"""
    translator = make_translator()
    update = translator.translate(
        {
            "type": "todo.updated",
            "data": {
                "sessionID": "ses_task_t1",
                "todos": [
                    {"content": "搭骨架", "status": "completed", "priority": "high"},
                    {"content": "实现登录接口", "status": "in_progress", "priority": "high"},
                ],
            },
        }
    )
    assert update is not None
    assert update.message == "实现登录接口"
    assert update.progress == 50


def test_tool_called_todowrite_extracts_input_todos() -> None:
    """兜底提取（2026-10-06 修复③）：tilacode 升级后实例流不再送 todo.updated，
    todowrite 工具调用的 input.todos 即完整清单——从中提取并随进度上报。"""
    translator = make_translator()
    update = translator.translate(
        {
            "type": "session.next.tool.called",
            "data": {
                "tool": "todowrite",
                "input": {
                    "todos": [
                        {"content": "搭骨架", "status": "completed", "priority": "high"},
                        {"content": "实现登录接口", "status": "in_progress", "priority": "high"},
                    ]
                },
            },
        }
    )
    assert update is not None
    assert update.todos is not None and len(update.todos) == 2
    assert update.message == "实现登录接口"
    assert update.progress == 50
    # 后续其他工具事件携带已提取的清单（中枢据此写 tasks.todos）
    follow = translator.translate(
        {"type": "session.next.tool.called", "data": {"tool": "bash"}}
    )
    assert follow is not None
    assert follow.todos is not None and len(follow.todos) == 2


def test_tool_called_todowrite_completed_reaches_100() -> None:
    """todowrite 全部完成后 todo 进度应为 100（不受事件进度 90 封顶限制）。"""
    translator = make_translator()
    translator.translate({"type": "session.next.step.started", "data": {}})
    for _ in range(40):
        translator.translate({"type": "session.next.tool.called", "data": {"tool": "bash"}})
    update = translator.translate(
        {
            "type": "session.next.tool.called",
            "data": {
                "tool": "todowrite",
                "input": {
                    "todos": [
                        {"content": "a", "status": "completed", "priority": "high"},
                        {"content": "b", "status": "completed", "priority": "high"},
                    ]
                },
            },
        }
    )
    assert update is not None
    assert update.progress == 100


def test_permission_event_returns_none() -> None:
    """权限事件应由 bridge 兜底处理，translate 返回 None。"""
    translator = make_translator()
    event = {
        "type": "permission.v2.asked",
        "data": {
            "id": "per_x",
            "sessionID": "ses_x",
            "action": "bash",
            "resources": ["rm -rf /"],
        },
    }
    assert is_permission_event(event) is True
    assert translator.translate(event) is None


def test_is_permission_event_negative() -> None:
    """非权限事件 is_permission_event 应返回 False。"""
    assert is_permission_event({"type": "session.next.step.started", "data": {}}) is False
    assert is_permission_event({}) is False


def test_unknown_type_returns_none() -> None:
    """未知事件类型应返回 None。"""
    translator = make_translator()
    assert translator.translate({"type": "session.next.reasoning.delta", "data": {}}) is None
    assert translator.translate({"type": "whatever.else", "data": {}}) is None
    assert translator.translate({}) is None


def test_mark_complete_returns_100() -> None:
    """mark_complete 应将进度置为 100 并返回。"""
    translator = make_translator()
    translator.translate({"type": "session.next.step.started", "data": {}})
    for _ in range(30):
        translator.translate({"type": "session.next.tool.success", "data": {"tool": "bash"}})
    assert translator.mark_complete() == 100
    assert translator.progress == 100


def test_progress_update_defaults() -> None:
    """ProgressUpdate 默认 tool_name 为 None、stage 为 executing、detail 为 None。"""
    update = ProgressUpdate(progress=10, message="测试")
    assert update.tool_name is None
    assert update.stage == "executing"
    assert update.detail is None

"""任务事件处理器单元测试。

通过 mock ``AsyncSessionLocal`` 与数据库会话，验证 agent.task_failed 与
agent.task_completed（含 plan_content；session_id 不写入外键字段）的
状态流转与节点释放逻辑，不依赖真实数据库。
"""

import contextlib
import uuid
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import CheckConstraint

from app.models.agent_node import AgentNode, NodeStatus
from app.models.task import Task, TaskStatus
from app.models.task_building_session import TaskBuildingSession
from app.models.tool_call_log import ToolCallLog, ToolCallLogKind
from app.schemas.ws_messages import (
    AGENT_TASK_FAILED,
    AGENT_TASK_PROGRESS,
    TASK_PROGRESS_MESSAGE_MAX_CHARS,
    AgentTaskProgressPayload,
    TaskCompletedPayload,
    TaskFailedPayload,
)
from app.services.task_event_handler import (
    _EVENT_HANDLERS,
    REPORTED_TEXT_MAX_LENGTH,
    _handle_node_chat_error,
    _handle_node_chat_reply,
    _handle_task_completed,
    _handle_task_failed,
    _handle_task_progress,
    _handle_task_rejected,
    _handle_task_started,
)


def _make_task(node_id: uuid.UUID, status: str, progress: int = 40) -> Task:
    """构造未落库的 Task 实例，用于 mock 查询返回。"""
    return Task(
        id=uuid.uuid4(),
        title="测试任务",
        agent_type="programming",
        status=status,
        progress=progress,
        assigned_node_id=node_id,
    )


def _make_node(node_id: uuid.UUID) -> AgentNode:
    """构造未落库的忙碌节点实例，用于 mock 查询返回。"""
    return AgentNode(
        id=node_id,
        name="测试节点",
        agent_type="programming",
        capabilities=["pytest"],
        status=NodeStatus.BUSY.value,
        load=1.0,
        registration_token="test-token",
    )


@contextlib.contextmanager
def _patch_db_session(task: Task, node: AgentNode) -> Iterator[AsyncMock]:
    """将处理器内部的 AsyncSessionLocal 替换为 mock 会话工厂。

    mock 会话的 ``get`` 方法按模型类型返回预制的 task / node 实例，
    ``commit`` 为 AsyncMock，便于断言是否发生落库。
    """
    session = AsyncMock()

    async def fake_get(model: Any, _: Any) -> Any:
        if model is Task:
            return task
        if model is AgentNode:
            return node
        return None

    session.get.side_effect = fake_get

    # AsyncSessionLocal() 返回 async context manager，其 __aenter__ 返回 mock 会话
    session_ctx = AsyncMock()
    session_ctx.__aenter__.return_value = session
    factory = MagicMock(return_value=session_ctx)

    with patch("app.services.task_event_handler.AsyncSessionLocal", factory):
        yield session


async def test_task_failed_running_task_marks_failed_and_releases_node() -> None:
    """running 任务上报失败：状态置 failed、progress 保留、节点释放 idle。"""
    node_id = uuid.uuid4()
    task = _make_task(node_id, TaskStatus.RUNNING.value, progress=60)
    node = _make_node(node_id)

    with _patch_db_session(task, node) as session:
        await _handle_task_failed(
            str(node_id),
            {"task_id": str(task.id), "reason": "执行器崩溃"},
        )

    assert task.status == TaskStatus.FAILED.value
    assert task.progress == 60
    # ADR-032：节点状态/负载由心跳派生，任务失败不再改写节点行
    assert node.status == NodeStatus.BUSY.value
    assert node.load == 1.0
    session.commit.assert_awaited_once()


async def test_task_started_persists_executor_model_actual() -> None:
    """task_started 携带 executor_model_actual 时落库（ADR-029 增强）。"""
    node_id = uuid.uuid4()
    task = _make_task(node_id, TaskStatus.ASSIGNED.value)
    node = _make_node(node_id)

    with _patch_db_session(task, node) as session:
        await _handle_task_started(
            str(node_id),
            {
                "task_id": str(task.id),
                "executor_model_actual": "deepseek/deepseek-chat",
            },
        )

    assert task.status == TaskStatus.RUNNING.value
    assert task.executor_model_actual == "deepseek/deepseek-chat"
    session.commit.assert_awaited_once()


async def test_task_started_without_model_field_keeps_null() -> None:
    """旧版节点 task_started 不带 model 字段：字段保持 NULL，不报错。"""
    node_id = uuid.uuid4()
    task = _make_task(node_id, TaskStatus.ASSIGNED.value)
    node = _make_node(node_id)

    with _patch_db_session(task, node):
        await _handle_task_started(str(node_id), {"task_id": str(task.id)})

    assert task.status == TaskStatus.RUNNING.value
    assert task.executor_model_actual is None


async def test_task_started_with_oversized_model_truncated() -> None:
    """超长 model 描述按 200 字符截断，避免异常上报撑爆列。"""
    node_id = uuid.uuid4()
    task = _make_task(node_id, TaskStatus.ASSIGNED.value)
    node = _make_node(node_id)

    with _patch_db_session(task, node):
        await _handle_task_started(
            str(node_id),
            {
                "task_id": str(task.id),
                "executor_model_actual": "x" * 500,
            },
        )

    assert task.executor_model_actual == "x" * 200


async def test_task_failed_assigned_task_marks_failed() -> None:
    """assigned 任务（尚未 started）上报失败同样置为 failed。"""
    node_id = uuid.uuid4()
    task = _make_task(node_id, TaskStatus.ASSIGNED.value, progress=0)
    node = _make_node(node_id)

    with _patch_db_session(task, node):
        await _handle_task_failed(str(node_id), {"task_id": str(task.id)})

    assert task.status == TaskStatus.FAILED.value


async def test_task_failed_completed_task_ignored() -> None:
    """completed 任务上报失败：不处理、不落库。"""
    node_id = uuid.uuid4()
    task = _make_task(node_id, TaskStatus.COMPLETED.value, progress=100)
    node = _make_node(node_id)

    with _patch_db_session(task, node) as session:
        await _handle_task_failed(str(node_id), {"task_id": str(task.id)})

    assert task.status == TaskStatus.COMPLETED.value
    assert node.status == NodeStatus.BUSY.value
    session.commit.assert_not_awaited()


async def test_task_failed_pending_task_ignored() -> None:
    """pending 任务上报失败：不处理、不落库。"""
    node_id = uuid.uuid4()
    task = _make_task(node_id, TaskStatus.PENDING.value, progress=0)
    node = _make_node(node_id)

    with _patch_db_session(task, node) as session:
        await _handle_task_failed(str(node_id), {"task_id": str(task.id)})

    assert task.status == TaskStatus.PENDING.value
    session.commit.assert_not_awaited()


async def test_task_completed_with_session_id_not_written() -> None:
    """task_completed 携带 session_id：不写入 Task.session_id。

    节点上报的 tilacode 会话标识与 ``task_building_sessions`` 外键语义不符，
    写入必然外键违规；该字段由创建任务流程确定。
    """
    node_id = uuid.uuid4()
    session_id = uuid.uuid4()
    task = _make_task(node_id, TaskStatus.RUNNING.value, progress=80)
    node = _make_node(node_id)

    with _patch_db_session(task, node) as session:
        await _handle_task_completed(
            str(node_id),
            {"task_id": str(task.id), "session_id": str(session_id)},
        )

    assert task.status == TaskStatus.COMPLETED.value
    assert task.progress == 100
    assert task.session_id is None
    # ADR-032：节点状态由心跳派生，任务完成不再改写节点行
    assert node.status == NodeStatus.BUSY.value
    session.commit.assert_awaited_once()


async def test_task_completed_without_session_id_untouched() -> None:
    """task_completed 不带 session_id：Task.session_id 保持不变。"""
    node_id = uuid.uuid4()
    task = _make_task(node_id, TaskStatus.RUNNING.value, progress=80)
    node = _make_node(node_id)

    with _patch_db_session(task, node):
        await _handle_task_completed(str(node_id), {"task_id": str(task.id)})

    assert task.status == TaskStatus.COMPLETED.value
    assert task.session_id is None


async def test_task_completed_with_plan_content_persists() -> None:
    """task_completed 携带 plan_content：写入 Task.plan_content。"""
    node_id = uuid.uuid4()
    task = _make_task(node_id, TaskStatus.RUNNING.value, progress=80)
    node = _make_node(node_id)
    plan_content = "# 规划方案\n1. 重构登录模块\n2. 新增记住我功能"

    with _patch_db_session(task, node) as session:
        await _handle_task_completed(
            str(node_id),
            {"task_id": str(task.id), "plan_content": plan_content},
        )

    assert task.status == TaskStatus.COMPLETED.value
    assert task.plan_content == plan_content
    # ADR-032：节点状态由心跳派生，任务完成不再改写节点行
    assert node.status == NodeStatus.BUSY.value
    session.commit.assert_awaited_once()


async def test_task_completed_with_oversized_plan_content_truncated() -> None:
    """task_completed 携带超长 plan_content：截断到阈值后存储。"""
    node_id = uuid.uuid4()
    task = _make_task(node_id, TaskStatus.RUNNING.value, progress=80)
    node = _make_node(node_id)
    plan_content = "x" * (REPORTED_TEXT_MAX_LENGTH + 1)

    with _patch_db_session(task, node):
        await _handle_task_completed(
            str(node_id),
            {"task_id": str(task.id), "plan_content": plan_content},
        )

    assert task.status == TaskStatus.COMPLETED.value
    assert task.plan_content is not None
    assert len(task.plan_content) == REPORTED_TEXT_MAX_LENGTH


async def test_task_completed_with_invalid_plan_content_ignored() -> None:
    """task_completed 携带非法类型 plan_content：仅记 warning 不落该字段。"""
    node_id = uuid.uuid4()
    task = _make_task(node_id, TaskStatus.RUNNING.value, progress=80)
    node = _make_node(node_id)

    with _patch_db_session(task, node):
        await _handle_task_completed(
            str(node_id),
            {"task_id": str(task.id), "plan_content": {"not": "a string"}},
        )

    assert task.status == TaskStatus.COMPLETED.value
    assert task.plan_content is None


async def test_task_completed_with_change_summary_persists() -> None:
    """task_completed 携带 change_summary：写入 Task.change_summary。"""
    node_id = uuid.uuid4()
    task = _make_task(node_id, TaskStatus.RUNNING.value, progress=80)
    node = _make_node(node_id)
    change_summary = "# 本轮修改说明\n- 新增用户导出接口\n- 修复登录超时"

    with _patch_db_session(task, node) as session:
        await _handle_task_completed(
            str(node_id),
            {"task_id": str(task.id), "change_summary": change_summary},
        )

    assert task.status == TaskStatus.COMPLETED.value
    assert task.change_summary == change_summary
    # ADR-032：节点状态由心跳派生，任务完成不再改写节点行
    assert node.status == NodeStatus.BUSY.value
    session.commit.assert_awaited_once()


async def test_task_completed_with_invalid_change_summary_ignored() -> None:
    """task_completed 携带非法类型 change_summary：仅记 warning 不落该字段。"""
    node_id = uuid.uuid4()
    task = _make_task(node_id, TaskStatus.RUNNING.value, progress=80)
    node = _make_node(node_id)

    with _patch_db_session(task, node):
        await _handle_task_completed(
            str(node_id),
            {"task_id": str(task.id), "change_summary": 12345},
        )

    assert task.status == TaskStatus.COMPLETED.value
    assert task.change_summary is None


async def test_task_progress_updates_progress_and_todos() -> None:
    """task_progress 携带 progress 与 todos：更新任务进度和待办列表。"""
    node_id = uuid.uuid4()
    task = _make_task(node_id, TaskStatus.RUNNING.value, progress=20)
    node = _make_node(node_id)
    todos = [
        {"id": "1", "content": "验证 smali 文件修改", "status": "completed"},
        {"id": "2", "content": "验证 APK 签名", "status": "pending"},
    ]

    with _patch_db_session(task, node) as session:
        await _handle_task_progress(
            str(node_id),
            {"task_id": str(task.id), "progress": 50, "todos": todos},
        )

    assert task.progress == 50
    assert task.todos == todos
    session.commit.assert_awaited_once()


async def test_task_progress_ignores_invalid_todos() -> None:
    """task_progress 携带非法类型 todos：仅记 warning 不更新该字段。"""
    node_id = uuid.uuid4()
    task = _make_task(node_id, TaskStatus.RUNNING.value, progress=20)
    node = _make_node(node_id)

    with _patch_db_session(task, node) as session:
        await _handle_task_progress(
            str(node_id),
            {"task_id": str(task.id), "progress": 50, "todos": "not-a-list"},
        )

    assert task.progress == 50
    assert task.todos is None
    session.commit.assert_awaited_once()


async def test_task_progress_without_todos_keeps_existing() -> None:
    """task_progress 不带 todos 字段：进度更新，已有 todos 保持不变。"""
    node_id = uuid.uuid4()
    task = _make_task(node_id, TaskStatus.RUNNING.value, progress=20)
    task.todos = [{"id": "1", "content": "已有待办", "status": "completed"}]
    node = _make_node(node_id)

    with _patch_db_session(task, node) as session:
        await _handle_task_progress(
            str(node_id),
            {"task_id": str(task.id), "progress": 60},
        )

    assert task.progress == 60
    assert task.todos == [{"id": "1", "content": "已有待办", "status": "completed"}]
    session.commit.assert_awaited_once()


async def test_task_progress_invalid_progress_ignored() -> None:
    """task_progress 携带越界 progress：不更新任务。"""
    node_id = uuid.uuid4()
    task = _make_task(node_id, TaskStatus.RUNNING.value, progress=20)
    node = _make_node(node_id)

    with _patch_db_session(task, node) as session:
        await _handle_task_progress(
            str(node_id),
            {"task_id": str(task.id), "progress": 150, "todos": []},
        )

    assert task.progress == 20
    session.commit.assert_not_awaited()


async def test_task_progress_event_registered() -> None:
    """agent.task_progress 已注册到事件分发表。"""
    assert _EVENT_HANDLERS.get(AGENT_TASK_PROGRESS) is _handle_task_progress


async def test_task_progress_persists_stage_and_message() -> None:
    """ADR-055：新增 stage/message 落库，进度与待办语义不变。"""
    node_id = uuid.uuid4()
    task = _make_task(node_id, TaskStatus.RUNNING.value, progress=20)
    node = _make_node(node_id)

    with _patch_db_session(task, node) as session:
        await _handle_task_progress(
            str(node_id),
            {
                "task_id": str(task.id),
                "progress": 42,
                "stage": "editing",
                "message": "正在编辑 src/main.py",
                "tool": "edit",
                "detail": {"files": ["src/main.py"]},
                "todos": [{"content": "改接口", "status": "in_progress"}],
            },
        )

    assert task.progress == 42
    assert task.progress_stage == "editing"
    assert task.progress_message == "正在编辑 src/main.py"
    assert task.todos == [{"content": "改接口", "status": "in_progress"}]
    session.commit.assert_awaited_once()


async def test_task_progress_unknown_stage_ignored_but_progress_updated() -> None:
    """非封闭枚举的 stage 只告警忽略，进度仍要落库（不拒绝整条事件）。"""
    node_id = uuid.uuid4()
    task = _make_task(node_id, TaskStatus.RUNNING.value, progress=20)
    node = _make_node(node_id)

    with _patch_db_session(task, node) as session:
        await _handle_task_progress(
            str(node_id),
            {"task_id": str(task.id), "progress": 55, "stage": "not-a-stage"},
        )

    assert task.progress == 55
    assert task.progress_stage is None
    session.commit.assert_awaited_once()


async def test_task_progress_long_message_truncated() -> None:
    """超长 message 截断到上限后落库（不拒绝整条事件）。"""
    node_id = uuid.uuid4()
    task = _make_task(node_id, TaskStatus.RUNNING.value, progress=20)
    node = _make_node(node_id)

    with _patch_db_session(task, node) as session:
        await _handle_task_progress(
            str(node_id),
            {"task_id": str(task.id), "progress": 60, "message": "x" * 900},
        )

    assert task.progress_message is not None
    assert len(task.progress_message) == TASK_PROGRESS_MESSAGE_MAX_CHARS
    session.commit.assert_awaited_once()


async def test_task_progress_legacy_payload_keeps_new_columns_null() -> None:
    """旧版节点 payload（无新键）行为不变，新列保持 NULL（向后兼容）。"""
    node_id = uuid.uuid4()
    task = _make_task(node_id, TaskStatus.RUNNING.value, progress=20)
    node = _make_node(node_id)

    with _patch_db_session(task, node) as session:
        await _handle_task_progress(str(node_id), {"task_id": str(task.id), "progress": 70})

    assert task.progress == 70
    assert task.progress_stage is None
    assert task.progress_message is None
    session.commit.assert_awaited_once()


async def test_task_progress_logs_cache_metrics(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """UT-86/87（ADR-060 P0）：节点携带 detail.tokens 时中枢记录缓存命中率。

    回归对象：中枢此前**完全忽略** `detail` 字段，节点采集的 cache read/write
    在到达中枢后被丢弃，导致节点侧缓存命中率不可观测。
    """
    node_id = uuid.uuid4()
    task = _make_task(node_id, TaskStatus.RUNNING.value, progress=20)
    node = _make_node(node_id)

    with caplog.at_level("INFO"), _patch_db_session(task, node):
        await _handle_task_progress(
            str(node_id),
            {
                "task_id": str(task.id),
                "progress": 60,
                "detail": {
                    "tokens": {
                        "input": 200,
                        "output": 30,
                        "reasoning": 5,
                        "cache_read": 800,
                        "cache_write": 0,
                    },
                    "cost": 0.0012,
                },
            },
        )

    records = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "task_progress_cache_metrics"
    ]
    assert len(records) == 1, "应记录一条缓存指标"
    record = records[0]
    assert record.input_tokens == 200
    assert record.cache_read_tokens == 800
    assert record.output_tokens == 30
    assert record.cache_hit_rate == pytest.approx(0.8)
    assert record.cost == pytest.approx(0.0012)


async def test_task_progress_without_cache_detail_skips_metrics(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """UT-89（ADR-060 P0 守卫）：旧版节点（无 detail）行为不变、不产生指标噪音。"""
    node_id = uuid.uuid4()
    task = _make_task(node_id, TaskStatus.RUNNING.value, progress=20)
    node = _make_node(node_id)

    with caplog.at_level("INFO"), _patch_db_session(task, node) as session:
        await _handle_task_progress(str(node_id), {"task_id": str(task.id), "progress": 70})

    assert task.progress == 70
    session.commit.assert_awaited_once()
    assert not [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "task_progress_cache_metrics"
    ]


async def test_task_progress_zero_token_detail_skips_metrics(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """UT-87b：input 与 cache_read 皆为 0（首轮无缓存）时不产生指标、不除零。"""
    node_id = uuid.uuid4()
    task = _make_task(node_id, TaskStatus.RUNNING.value, progress=20)
    node = _make_node(node_id)

    with caplog.at_level("INFO"), _patch_db_session(task, node):
        await _handle_task_progress(
            str(node_id),
            {
                "task_id": str(task.id),
                "progress": 30,
                "detail": {"tokens": {"input": 0, "cache_read": 0}},
            },
        )

    assert not [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "task_progress_cache_metrics"
    ]


async def test_task_progress_malformed_detail_is_safe(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """UT-89b：detail/tokens 结构异常（非 dict）时静默跳过，不影响进度落库。"""
    node_id = uuid.uuid4()
    task = _make_task(node_id, TaskStatus.RUNNING.value, progress=20)
    node = _make_node(node_id)

    with caplog.at_level("INFO"), _patch_db_session(task, node) as session:
        await _handle_task_progress(
            str(node_id),
            {"task_id": str(task.id), "progress": 80, "detail": "not-a-dict"},
        )

    assert task.progress == 80
    session.commit.assert_awaited_once()
    assert not [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "task_progress_cache_metrics"
    ]


async def test_task_progress_persists_llm_usage_row() -> None:
    """UT-M5a-3（ADR-061 M5a）：节点上报 detail.tokens 时中枢落库 llm_usage 行。

    回归对象：用量/缓存指标此前**只写日志**（task_progress_cache_metrics），
    落库缺失导致用量曲线、预算闸统计均无数据源。
    """
    node_id = uuid.uuid4()
    task = _make_task(node_id, TaskStatus.RUNNING.value, progress=20)
    node = _make_node(node_id)
    record = AsyncMock()

    with _patch_db_session(task, node), patch("app.services.tool_call_log_service.record", record):
        await _handle_task_progress(
            str(node_id),
            {
                "task_id": str(task.id),
                "progress": 55,
                "tool": "edit",
                "detail": {
                    "tokens": {
                        "input": 200,
                        "output": 30,
                        "reasoning": 5,
                        "cache_read": 800,
                        "cache_write": 0,
                    },
                    "cost": 0.0012,
                    "files": ["a.py", "b.py"],
                },
            },
        )

    # ADR-061 M5c 起，工具/阶段变化还会额外写一条 tool_activity；
    # 这里按 kind 精确定位 llm_usage 行（保持本用例原意图：用量字段逐项正确）
    usage_calls = [
        call for call in record.await_args_list if call.kwargs.get("kind") == "llm_usage"
    ]
    assert len(usage_calls) == 1
    kwargs = usage_calls[0].kwargs
    assert kwargs["kind"] == "llm_usage"
    assert kwargs["task_id"] == str(task.id)
    assert kwargs["args_summary"] == {"agent_id": str(node_id), "tool": "edit"}
    summary = kwargs["result_summary"]
    assert summary["input_tokens"] == 200
    assert summary["cache_read_tokens"] == 800
    assert summary["cache_write_tokens"] == 0
    assert summary["output_tokens"] == 30
    assert summary["reasoning_tokens"] == 5
    assert summary["cache_hit_rate"] == pytest.approx(0.8)
    assert summary["cost"] == pytest.approx(0.0012)
    assert summary["files_touched"] == 2


async def test_task_progress_skips_llm_usage_without_valid_sample() -> None:
    """UT-M5a-4（ADR-061 M5a 守卫）：无 detail / 零 token 空样本时不落噪音行。"""
    node_id = uuid.uuid4()
    task = _make_task(node_id, TaskStatus.RUNNING.value, progress=20)
    node = _make_node(node_id)

    for detail in (None, {"tokens": {"input": 0, "cache_read": 0}}, "not-a-dict"):
        record = AsyncMock()
        payload: dict[str, Any] = {"task_id": str(task.id), "progress": 61}
        if detail is not None:
            payload["detail"] = detail

        with _patch_db_session(task, node), patch(
            "app.services.tool_call_log_service.record", record
        ):
            await _handle_task_progress(str(node_id), payload)

        record.assert_not_awaited()


async def test_task_progress_persists_current_tool() -> None:
    """UT-M5a-6（ADR-061 M5a / UT-M5-4）：节点上报的当前工具名不再被丢弃。"""
    node_id = uuid.uuid4()
    task = _make_task(node_id, TaskStatus.RUNNING.value, progress=20)
    node = _make_node(node_id)

    with _patch_db_session(task, node):
        await _handle_task_progress(
            str(node_id),
            {"task_id": str(task.id), "progress": 44, "tool": "edit"},
        )

    assert task.progress_tool == "edit"
    assert task.progress == 44


async def test_task_progress_sanitizes_tool(caplog: pytest.LogCaptureFixture) -> None:
    """UT-M5a-7（ADR-061 M5a）：tool 非法类型忽略、超长截断，均不影响进度落库。"""
    node_id = uuid.uuid4()
    task = _make_task(node_id, TaskStatus.RUNNING.value, progress=20)
    node = _make_node(node_id)

    with caplog.at_level("INFO"), _patch_db_session(task, node):
        await _handle_task_progress(
            str(node_id),
            {"task_id": str(task.id), "progress": 45, "tool": {"name": "edit"}},
        )

    assert task.progress_tool is None
    assert task.progress == 45
    assert [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "task_progress_invalid_tool"
    ]

    with caplog.at_level("INFO"), _patch_db_session(task, node):
        await _handle_task_progress(
            str(node_id),
            {"task_id": str(task.id), "progress": 46, "tool": "x" * 80},
        )

    assert task.progress_tool == "x" * 64
    assert [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "task_progress_tool_truncated"
    ]


def test_tool_call_log_kind_constraint_covers_enum_values() -> None:
    """UT-M5a-5（ADR-061 M5a）：CHECK 白名单必须覆盖枚举全部取值。

    回归对象：kind 枚举与 ``chk_tool_call_logs_kind`` 约束历史上是两处手写
    字符串，新增 kind 漏改任一处都会在运行期抛 IntegrityError。
    """
    constraint = next(
        item
        for item in ToolCallLog.__table__.constraints
        if isinstance(item, CheckConstraint) and "llm_call" in str(item.sqltext)
    )
    sqltext = str(constraint.sqltext)
    missing = {
        member.value for member in ToolCallLogKind if f"'{member.value}'" not in sqltext
    }
    assert not missing, f"chk_tool_call_logs_kind 缺少枚举值：{sorted(missing)}"
    # 历史遗留值：写入过但无枚举成员，约束需继续容忍
    assert "'bt_mcp_call'" in sqltext


async def test_task_progress_schema_rejects_invalid_values() -> None:
    """AgentTaskProgressPayload 校验：progress 越界拒绝，缺 task_id 拒绝。"""
    from pydantic import ValidationError

    assert AgentTaskProgressPayload(task_id="t", progress=0).progress == 0
    assert AgentTaskProgressPayload(task_id="t", progress=100).progress == 100
    with pytest.raises(ValidationError):
        AgentTaskProgressPayload(task_id="t", progress=101)
    with pytest.raises(ValidationError):
        AgentTaskProgressPayload(progress=50)


async def test_task_progress_schema_accepts_legacy_without_new_keys() -> None:
    """新增字段全部可选：旧 payload 仍可校验通过。"""
    payload = AgentTaskProgressPayload(task_id="t", progress=30)
    assert payload.stage is None
    assert payload.message is None
    assert payload.tool is None
    assert payload.detail is None
    assert payload.todos is None


async def test_task_failed_event_registered() -> None:
    """agent.task_failed 已注册到事件分发表。"""
    assert _EVENT_HANDLERS.get(AGENT_TASK_FAILED) is _handle_task_failed


async def test_task_completed_plan_mode_with_plan_content_enters_review() -> None:
    """ADR-038：plan 模式任务上报方案 → 状态置 review（待人工确认）。"""
    node_id = uuid.uuid4()
    task = _make_task(node_id, TaskStatus.RUNNING.value, progress=80)
    task.execution_mode = "plan"
    node = _make_node(node_id)
    plan_content = "# 规划方案\n1. 拆分模块"

    with _patch_db_session(task, node) as session:
        await _handle_task_completed(
            str(node_id),
            {"task_id": str(task.id), "plan_content": plan_content},
        )

    assert task.status == TaskStatus.REVIEW.value
    assert task.progress == 100
    assert task.plan_content == plan_content
    session.commit.assert_awaited_once()


async def test_task_completed_plan_mode_without_plan_content_stays_completed() -> None:
    """ADR-038：plan 模式任务未上报方案 → 仍为 completed（降级兜底）。"""
    node_id = uuid.uuid4()
    task = _make_task(node_id, TaskStatus.RUNNING.value, progress=80)
    task.execution_mode = "plan"
    node = _make_node(node_id)

    with _patch_db_session(task, node):
        await _handle_task_completed(str(node_id), {"task_id": str(task.id)})

    assert task.status == TaskStatus.COMPLETED.value


def test_payload_schemas() -> None:
    """payload schema 字段校验：TaskFailedPayload 与 TaskCompletedPayload。"""
    failed = TaskFailedPayload.model_validate({"task_id": "abc", "reason": "boom"})
    assert failed.task_id == "abc"
    assert failed.reason == "boom"

    completed = TaskCompletedPayload.model_validate({"task_id": "abc", "session_id": "sid-1"})
    assert completed.session_id == "sid-1"


# ---------------- ADR-059 D3：拒单原因可见 ----------------


async def test_task_rejected_persists_reason_and_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    """UT-19：拒单原因落库到 last_reject_reason，并写明排队原因（含节点名）。"""
    from app.services import dispatch_backoff

    dispatch_backoff.reset_all()
    node_id = uuid.uuid4()
    task = _make_task(node_id, TaskStatus.ASSIGNED.value, progress=0)
    node = _make_node(node_id)

    with _patch_db_session(task, node):
        await _handle_task_rejected(
            str(node_id),
            {"task_id": str(task.id), "reason": "resource_busy"},
        )

    assert task.status == TaskStatus.PENDING.value
    assert task.assigned_node_id is None
    assert task.progress == 0
    assert task.last_reject_reason == "resource_busy"
    assert task.dispatch_hold_reason is not None
    assert "测试节点" in task.dispatch_hold_reason
    assert "resource_busy" in task.dispatch_hold_reason
    # 该任务在 TTL 内回避此节点
    assert str(node_id) in dispatch_backoff.excluded_nodes(str(task.id))
    dispatch_backoff.reset_all()


async def test_task_rejected_legacy_payload_defaults_to_slots_full() -> None:
    """UT-20：旧节点无 reason 字段 → 按 slots_full 处理（协议向后兼容不回归）。"""
    node_id = uuid.uuid4()
    task = _make_task(node_id, TaskStatus.ASSIGNED.value, progress=0)
    node = _make_node(node_id)

    with _patch_db_session(task, node):
        await _handle_task_rejected(str(node_id), {"task_id": str(task.id)})

    assert task.last_reject_reason == "slots_full"
    assert task.status == TaskStatus.PENDING.value


async def test_task_accepted_clears_reject_and_hold_reason() -> None:
    """派发成功后清除历史拒单/排队痕迹，避免旧原因持续展示误导用户。"""
    from app.services.task_event_handler import _handle_task_accepted

    node_id = uuid.uuid4()
    task = _make_task(node_id, TaskStatus.ASSIGNED.value, progress=0)
    task.last_reject_reason = "resource_busy"
    task.dispatch_hold_reason = "旧排队原因"
    node = _make_node(node_id)

    with _patch_db_session(task, node):
        await _handle_task_accepted(str(node_id), {"task_id": str(task.id)})

    assert task.status == TaskStatus.RUNNING.value
    assert task.last_reject_reason is None
    assert task.dispatch_hold_reason is None
    assert TaskCompletedPayload.model_validate({"task_id": "abc"}).session_id is None


# ---------------- ADR-064：节点续聊 reply/error 上行处理 ----------------


def _make_building(messages: list[dict] | None = None) -> TaskBuildingSession:
    """构造未落库的构建会话实例（mock 查询返回）。"""
    return TaskBuildingSession(id=uuid.uuid4(), title="续聊会话", messages=messages or [])


@contextlib.contextmanager
def _patch_node_chat_session(
    task: Task | None,
    node: AgentNode,
    building: TaskBuildingSession | None,
) -> Iterator[AsyncMock]:
    """替换处理器内部的 AsyncSessionLocal（task 传 None 模拟任务不存在）。"""
    session = AsyncMock()

    async def fake_get(model: Any, _: Any) -> Any:
        if model is Task:
            return task
        if model is AgentNode:
            return node
        if model is TaskBuildingSession:
            return building
        return None

    session.get.side_effect = fake_get
    session_ctx = AsyncMock()
    session_ctx.__aenter__.return_value = session
    factory = MagicMock(return_value=session_ctx)
    with patch("app.services.task_event_handler.AsyncSessionLocal", factory):
        yield session


async def test_node_chat_reply_persists_assistant_message() -> None:
    """UT-H4：node_chat_reply 落 assistant 消息（带 reply_to/mention_node/ts）。"""
    node_id = uuid.uuid4()
    task = _make_task(node_id, TaskStatus.COMPLETED.value, progress=100)
    node = _make_node(node_id)
    building = _make_building(
        [{"role": "user", "content": "把登录按钮改成绿色", "mention_node": "测试节点", "ts": "t0"}]
    )
    task.session_id = building.id
    reply_to = uuid.uuid4()

    with _patch_node_chat_session(task, node, building):
        await _handle_node_chat_reply(
            str(node_id),
            {
                "reply_to": str(reply_to),
                "task_id": str(task.id),
                "root_task_id": str(task.id),
                "node_id": str(node_id),
                "text": "已把登录按钮改成绿色",
                "tokens": {"input": 10, "output": 5},
            },
        )

    assert len(building.messages) == 2
    appended = building.messages[-1]
    assert appended["role"] == "assistant"
    assert appended["content"] == "已把登录按钮改成绿色"
    assert appended["mention_node"] == "测试节点"
    assert appended["reply_to"] == str(reply_to)
    assert appended.get("ts")


async def test_node_chat_reply_invalid_payload_ignored() -> None:
    """UT-H4 守卫：缺 reply_to / text 非法的 payload 只告警，不落库不抛。"""
    node_id = uuid.uuid4()
    task = _make_task(node_id, TaskStatus.COMPLETED.value)
    node = _make_node(node_id)
    building = _make_building()
    task.session_id = building.id

    for payload in (
        {"task_id": str(task.id), "text": "缺 reply_to"},
        {"reply_to": str(uuid.uuid4()), "task_id": str(task.id)},
    ):
        with _patch_node_chat_session(task, node, building):
            await _handle_node_chat_reply(str(node_id), payload)

    assert building.messages == []


async def test_node_chat_reply_task_not_found_ignored() -> None:
    """UT-H4 守卫：任务不存在（已删除）时忽略回复，不落库不抛。"""
    node_id = uuid.uuid4()
    task = _make_task(node_id, TaskStatus.COMPLETED.value)
    node = _make_node(node_id)
    building = _make_building()
    task.session_id = building.id

    with _patch_node_chat_session(None, node, building):
        await _handle_node_chat_reply(
            str(node_id),
            {
                "reply_to": str(uuid.uuid4()),
                "task_id": str(task.id),
                "root_task_id": str(task.id),
                "node_id": str(node_id),
                "text": "回复",
            },
        )

    assert building.messages == []


async def test_node_chat_error_persists_visible_message() -> None:
    """UT-H4：node_chat_error 落用户可见错误消息（assistant + [节点续聊失败] 前缀）。"""
    node_id = uuid.uuid4()
    task = _make_task(node_id, TaskStatus.COMPLETED.value)
    node = _make_node(node_id)
    building = _make_building(
        [{"role": "user", "content": "在吗", "mention_node": "测试节点", "ts": "t0"}]
    )
    task.session_id = building.id
    reply_to = uuid.uuid4()

    with _patch_node_chat_session(task, node, building):
        await _handle_node_chat_error(
            str(node_id),
            {
                "reply_to": str(reply_to),
                "task_id": str(task.id),
                "node_id": str(node_id),
                "reason": "session_missing",
            },
        )

    assert len(building.messages) == 2
    appended = building.messages[-1]
    assert appended["role"] == "assistant"
    assert appended["content"] == "[节点续聊失败] session_missing"
    assert appended["mention_node"] == "测试节点"
    assert appended["reply_to"] == str(reply_to)


async def test_node_chat_error_unknown_task_ignored() -> None:
    """UT-H4 守卫：任务不存在/非法 task_id 时仅告警，不落库不抛。"""
    node_id = uuid.uuid4()
    node = _make_node(node_id)
    building = _make_building()

    for task_id in (str(uuid.uuid4()), "not-a-uuid"):
        with _patch_node_chat_session(None, node, building):
            await _handle_node_chat_error(
                str(node_id),
                {"reply_to": str(uuid.uuid4()), "task_id": task_id, "node_id": str(node_id)},
            )

    assert building.messages == []


async def test_node_chat_events_registered() -> None:
    """node_chat_reply / node_chat_error 已注册到事件分发表。"""
    from app.schemas.ws_messages import AGENT_NODE_CHAT_ERROR, AGENT_NODE_CHAT_REPLY

    assert _EVENT_HANDLERS.get(AGENT_NODE_CHAT_REPLY) is _handle_node_chat_reply
    assert _EVENT_HANDLERS.get(AGENT_NODE_CHAT_ERROR) is _handle_node_chat_error

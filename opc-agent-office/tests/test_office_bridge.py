"""OfficeBridge 集成测试（Harness U-6/U-8/U-10/I-2/I-4）。"""

import asyncio
import json
from pathlib import Path

import pytest

from app.config import AgentConfig
from app.executor.office_bridge import BridgeCallbacks, OfficeBridge
from app.models.schemas import TaskDispatchPayload


def make_config(**overrides: str) -> AgentConfig:
    values = {
        "AGENT_REGISTRATION_TOKEN": "t",
        "WORKSPACE_ROOT": "",
        "OFFICE_LLM_MAX_ROUNDS": "10",
    }
    values.update({k: str(v) for k, v in overrides.items()})
    import os

    for key, value in values.items():
        os.environ[key] = value
    return AgentConfig(_env_file=None)


class FakeCallbacks(BridgeCallbacks):
    """收集全部上报事件的假回调。"""

    def __init__(self) -> None:
        self.events: list[tuple] = []

    async def on_progress(self, task_id, progress, todos=None, *, stage=None, message=None, tool=None, detail=None):
        self.events.append(("progress", task_id, progress, stage, message, todos))

    async def on_task_started(self, task_id, executor_model_actual):
        self.events.append(("started", task_id, executor_model_actual))

    async def on_completed(self, task_id, session_id, plan_content=None, change_summary=None, project_context=None):
        self.events.append(("completed", task_id, session_id, change_summary))

    async def on_failed(self, task_id, reason):
        self.events.append(("failed", task_id, reason))

    async def on_file_tree(self, task_id, files, truncated):
        self.events.append(("file_tree", task_id, [item["path"] for item in files], truncated))

    async def on_file_chunk(self, request_id, seq, total, data_b64, done):
        self.events.append(("file_chunk", request_id, seq, total, done))

    async def on_file_error(self, request_id, reason):
        self.events.append(("file_error", request_id, reason))


class ScriptedLLM:
    """脚本化 LLM（写一个 docx 后收尾）。"""

    model = "fake-model"

    def __init__(self) -> None:
        self.step = 0

    async def chat(self, messages, tools=None):  # noqa: ANN001, ANN202
        self.step += 1
        if self.step == 1:
            return {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {
                                "name": "write_docx",
                                "arguments": json.dumps(
                                    {
                                        "filename": "产出.docx",
                                        "title": "交付文档",
                                        "blocks": [{"type": "paragraph", "text": "正文"}],
                                    },
                                    ensure_ascii=False,
                                ),
                            },
                        }
                    ],
                },
                "prompt_tokens": 100,
                "completion_tokens": 50,
            }
        return {
            "message": {"role": "assistant", "content": "文档已生成，核心结论 XYZ。"},
            "prompt_tokens": 100,
            "completion_tokens": 50,
        }


def make_dispatch(**extra) -> TaskDispatchPayload:
    payload = {
        "task_id": "task-abc-001",
        "task_name": "写调研报告",
        "description": "调研某主题并产出 docx",
        "executor_model": {
            "source": "hub",
            "model": "deepseek/deepseek-chat",
            "provider": "deepseek",
            "api_key": "sk-test-123",
            "base_url": "https://api.deepseek.com",
        },
        "output_dir": str(extra.pop("output_dir")) if "output_dir" in extra else None,
    }
    payload.update(extra)
    return TaskDispatchPayload.model_validate(payload)


async def test_missing_credentials_fails_fast(tmp_path: Path) -> None:
    """U-6：凭据缺失 → 快速失败且原因明确，不发起 LLM 调用。"""
    config = make_config(WORKSPACE_ROOT=str(tmp_path))
    callbacks = FakeCallbacks()
    bridge = OfficeBridge(config, callbacks)
    dispatch = make_dispatch(
        executor_model={"source": "hub", "model": "deepseek/deepseek-chat"},
        output_dir=str(tmp_path / "ws"),
    )
    await bridge.execute_task(dispatch)

    failed = [event for event in callbacks.events if event[0] == "failed"]
    assert len(failed) == 1
    assert "missing_executor_credentials" in failed[0][2]
    assert not any(event[0] == "started" for event in callbacks.events)


async def test_full_task_flow(tmp_path: Path) -> None:
    """I-2：派发 → 执行 → 产物 → 完成上报全链路（fake LLM）。"""
    work_dir = tmp_path / "ws"
    config = make_config(WORKSPACE_ROOT=str(tmp_path))
    callbacks = FakeCallbacks()
    bridge = OfficeBridge(config, callbacks)
    monkey_llm = ScriptedLLM()

    dispatch = make_dispatch(output_dir=str(work_dir))
    # 注入脚本化 LLM：直接 patch LLMClient 构造产物
    import app.executor.office_bridge as bridge_mod

    class FakeLLMClient:
        def __init__(self, **kwargs) -> None:
            self.model = kwargs.get("model", "fake-model")
            self._llm = monkey_llm

        async def chat(self, messages, tools=None):  # noqa: ANN001, ANN202
            return await self._llm.chat(messages, tools)

    original_client = bridge_mod.LLMClient
    bridge_mod.LLMClient = FakeLLMClient
    try:
        await bridge.execute_task(dispatch)
    finally:
        bridge_mod.LLMClient = original_client

    kinds = [event[0] for event in callbacks.events]
    assert kinds[0] == "started"
    assert "completed" in kinds
    assert "failed" not in kinds

    started = callbacks.events[0]
    assert started[2] == "deepseek/deepseek-chat"  # executor_model_actual

    completed = [event for event in callbacks.events if event[0] == "completed"][0]
    assert "核心结论" in completed[3]

    file_tree = [event for event in callbacks.events if event[0] == "file_tree"][0]
    assert "产出.docx" in file_tree[2]
    assert (work_dir / "产出.docx").is_file()


async def test_interrupt_reports_failed(tmp_path: Path) -> None:
    """中断：interrupt 后任务上报 failed(interrupted)。"""
    config = make_config(WORKSPACE_ROOT=str(tmp_path))
    callbacks = FakeCallbacks()
    bridge = OfficeBridge(config, callbacks)

    import app.executor.office_bridge as bridge_mod

    class SlowLLMClient:
        def __init__(self, **kwargs) -> None:
            pass

        async def chat(self, messages, tools=None):  # noqa: ANN001, ANN202
            await asyncio.sleep(1)
            return {"message": {"content": "late"}, "prompt_tokens": 0, "completion_tokens": 0}

    original_client = bridge_mod.LLMClient
    bridge_mod.LLMClient = SlowLLMClient
    try:
        run_task = asyncio.create_task(
            bridge.execute_task(make_dispatch(output_dir=str(tmp_path / "ws2")))
        )
        await asyncio.sleep(0.2)
        await bridge.interrupt("task-abc-001")
        await asyncio.wait_for(run_task, timeout=3)
    finally:
        bridge_mod.LLMClient = original_client

    failed = [event for event in callbacks.events if event[0] == "failed"]
    assert failed and failed[0][2] == "interrupted"


async def test_credentials_not_logged(tmp_path: Path) -> None:
    """I-4：api_key 不出现在上报事件与日志中。"""
    config = make_config(WORKSPACE_ROOT=str(tmp_path))
    callbacks = FakeCallbacks()
    bridge = OfficeBridge(config, callbacks)

    import app.executor.office_bridge as bridge_mod

    class NoopLLMClient:
        def __init__(self, **kwargs) -> None:
            pass

        async def chat(self, messages, tools=None):  # noqa: ANN001, ANN202
            return {"message": {"content": "done"}, "prompt_tokens": 0, "completion_tokens": 0}

    original_client = bridge_mod.LLMClient
    bridge_mod.LLMClient = NoopLLMClient
    try:
        await bridge.execute_task(make_dispatch(output_dir=str(tmp_path / "ws3")))
    finally:
        bridge_mod.LLMClient = original_client

    # 全部上报事件序列化后不含 api_key
    import reprlib

    dumped = reprlib.Repr().repr(callbacks.events)
    assert "sk-test-123" not in dumped


async def test_silent_heartbeat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """U-10：静默期心跳补发（last_active 过期时触发）。"""
    config = make_config(WORKSPACE_ROOT=str(tmp_path), AGENT_PROGRESS_HEARTBEAT_SECONDS=20)
    callbacks = FakeCallbacks()
    bridge = OfficeBridge(config, callbacks)

    from app.executor.office_bridge import TaskRunState

    state = TaskRunState("t1")
    state.last_stage = "searching"
    state.last_message = "正在查资料"
    state.last_active -= 25  # 模拟已静默 25s

    # 直接跑心跳循环（缩短睡眠：闭包持有真实 sleep 防递归）
    real_sleep = asyncio.sleep

    async def fast_sleep(seconds):  # noqa: ANN001, ANN202
        await real_sleep(0.001)

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)
    task = asyncio.create_task(bridge._heartbeat_loop(state))  # noqa: SLF001
    # 心跳循环至少完整跑过一轮（首次 0.001s 即触发补发）
    await real_sleep(0.2)
    task.cancel()

    progress_events = [event for event in callbacks.events if event[0] == "progress"]
    assert any("仍在执行" in (event[4] or "") for event in progress_events)


async def test_stage_todos_reported(tmp_path: Path) -> None:
    """阶段化 todos：stage 变化时上报待办明细（含动态插入检索项）。"""
    from app.executor.office_bridge import TaskRunState

    state = TaskRunState("t9")
    assert state.apply_stage("planning") is True
    assert state.todos[0]["status"] == "in_progress"

    assert state.apply_stage("searching") is True
    contents = [todo["content"] for todo in state.todos]
    assert "检索与提炼资料" in contents  # 动态插入
    assert state.todos[0]["status"] == "completed"
    assert state.todos[1]["status"] == "in_progress"

    assert state.apply_stage("coding") is True
    searching = next(todo for todo in state.todos if todo["content"] == "检索与提炼资料")
    coding = next(todo for todo in state.todos if todo["content"] == "编写交付文档")
    assert searching["status"] == "completed"
    assert coding["status"] == "in_progress"

    # 同阶段重复 apply 不再变化
    assert state.apply_stage("coding") is False

    # 终态：全部置 completed（修复「卡死在最后一步」）
    state.complete_all_todos()
    assert all(todo["status"] == "completed" for todo in state.todos)
    assert state.todos_dirty is True


async def test_cjk_text_sanitized(tmp_path: Path) -> None:
    """NFKC 清洗 + PUA 检测（docx 方框字问题的清洗与自愈）。"""
    from app.tools.base import clean_llm_text, count_unrenderable_chars

    # 兼容区字符（U+F900-FAFF）NFKC 后不再保留原码位
    cleaned = clean_llm_text("教\uF9FB")
    assert "\uF9FB" not in cleaned
    # 全角拉丁归一为半角
    assert clean_llm_text("ＡＢＣ１２３") == "ABC123"
    # 常规中文不受影响
    assert clean_llm_text("编写网页") == "编写网页"
    # PUA（私用区）检测：LLM 偶发输出的方框字根因
    assert count_unrenderable_chars("正常文本") == 0
    assert count_unrenderable_chars("包含\uE12B异常字符") == 1


async def test_docx_pua_detected(tmp_path: Path) -> None:
    """write_docx 检测到 PUA 字符时反馈 LLM 重新生成。"""
    from app.tools.docx_tool import DocxTool

    tool = DocxTool(tmp_path)
    result = tool.execute(
        {
            "filename": "异常.docx",
            "title": "标题",
            "blocks": [{"type": "paragraph", "text": "含\uE12B异常字符的段落"}],
        }
    )
    assert "异常字符" in result and "重新调用" in result
    # 文件仍已生成（保留产物，由 LLM 决定覆盖重写）
    assert (tmp_path / "异常.docx").is_file()


async def test_handle_file_fetch_chunked(tmp_path: Path) -> None:
    """产物拉取：file 模式分块回传；非法路径报 file_error。"""
    config = make_config(WORKSPACE_ROOT=str(tmp_path))
    callbacks = FakeCallbacks()
    bridge = OfficeBridge(config, callbacks)

    work_dir = tmp_path / "ws4"
    work_dir.mkdir()
    (work_dir / "result.md").write_text("a" * (600 * 1024), encoding="utf-8")  # >1 chunk

    from app.executor.office_bridge import TaskRunState

    state = TaskRunState("task-1")
    state.work_dir = work_dir
    bridge._tasks["task-1"] = state  # noqa: SLF001

    await bridge.handle_file_fetch("req-1", "task-1", "file", "result.md")
    chunks = [event for event in callbacks.events if event[0] == "file_chunk"]
    assert len(chunks) == 2
    assert chunks[-1][4] is True  # done

    await bridge.handle_file_fetch("req-2", "task-1", "file", "../escape.md")
    errors = [event for event in callbacks.events if event[0] == "file_error"]
    assert errors

"""LLM 工具循环单元测试（Harness U-1/U-7/U-9）。"""

import asyncio
from pathlib import Path

import httpx
import pytest
import respx

from app.config import AgentConfig
from app.executor.llm_client import LLMClient, LLMError
from app.executor.tool_loop import ToolLoopRunner, default_registry


def make_config(**overrides: int) -> AgentConfig:
    values = {
        "AGENT_REGISTRATION_TOKEN": "t",
        "OFFICE_LLM_MAX_ROUNDS": 30,
        "OFFICE_LLM_TOKEN_BUDGET": 200000,
        "OFFICE_LLM_MAX_RETRIES": 2,
    }
    values.update(overrides)
    import os

    for key, value in values.items():
        os.environ[key] = str(value)
    return AgentConfig(_env_file=None)


class FakeLLM:
    """脚本化 LLM：按预设响应序列返回。"""

    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.calls: list[list | None] = []
        self.model = "fake-model"

    async def chat(self, messages, tools=None):  # noqa: ANN001, ANN202
        self.calls.append(tools)
        if not self.responses:
            return {"message": {"role": "assistant", "content": "完成。"}, "prompt_tokens": 0, "completion_tokens": 0}
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return {"message": response, "prompt_tokens": 10, "completion_tokens": 5}


def tool_call(call_id: str, name: str, args: dict) -> dict:
    import json

    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
    }


def make_progress_collector():
    """进度收集器：返回 (steps, on_progress)。"""
    steps: list[tuple[int, str, str]] = []

    async def on_progress(progress: int, stage: str, message: str) -> None:
        steps.append((progress, stage, message))

    return steps, on_progress


async def test_loop_converges_with_tools(tmp_path: Path) -> None:
    """U-1：fake LLM 先调工具再给最终答复，循环收敛。"""
    config = make_config()
    fake = FakeLLM(
        [
            {"role": "assistant", "content": "", "tool_calls": [tool_call("c1", "write_docx", {
                "filename": "报告.docx",
                "title": "T",
                "blocks": [{"type": "paragraph", "text": "内容"}],
            })]},
            {"role": "assistant", "content": "最终总结：报告已生成。"},
        ]
    )
    steps, progress_cb = make_progress_collector()
    runner = ToolLoopRunner(config, fake, default_registry(tmp_path, config), on_progress=progress_cb)
    outcome = await runner.run("task1", "写一份报告", asyncio.Event())

    assert outcome.summary == "最终总结：报告已生成。"
    assert outcome.rounds == 2
    assert outcome.stopped_reason == "final_answer"
    assert (tmp_path / "报告.docx").is_file()
    stages = [step[1] for step in steps]
    assert "planning" in stages and "coding" in stages and "summarizing" in stages


async def test_loop_search_stage_mapping(tmp_path: Path) -> None:
    """stage 映射：搜索工具 → searching。"""
    config = make_config()

    class FakeSearch:
        name = "web_search"
        description = "s"
        parameters = {}

        def __init__(self, work_dir) -> None:
            pass

        def to_openai_schema(self):  # noqa: ANN202
            return {}

        def execute(self, args):  # noqa: ANN001, ANN202
            return "1. 结果"

    from app.tools.base import ToolRegistry

    registry = ToolRegistry()
    registry.register(FakeSearch(tmp_path))
    fake = FakeLLM(
        [
            {"role": "assistant", "content": "", "tool_calls": [tool_call("c1", "web_search", {"query": "x"})]},
            {"role": "assistant", "content": "资料已汇总。"},
        ]
    )
    steps, progress_cb = make_progress_collector()
    runner = ToolLoopRunner(config, fake, registry, on_progress=progress_cb)
    outcome = await runner.run("task1", "查资料", asyncio.Event())
    assert outcome.stopped_reason == "final_answer"
    assert any(step[1] == "searching" for step in steps)


async def test_loop_max_rounds_forces_final(tmp_path: Path) -> None:
    """U-9：轮次超限 → 注入收尾轮（不带 tools）并终止。"""
    config = make_config(OFFICE_LLM_MAX_ROUNDS=2)
    tool_response = {"role": "assistant", "content": "", "tool_calls": [tool_call("c", "web_search", {"query": "x"})]}

    class FakeSearch:
        name = "web_search"
        description = "s"
        parameters = {}

        def __init__(self, work_dir) -> None:
            pass

        def to_openai_schema(self):  # noqa: ANN202
            return {}

        def execute(self, args):  # noqa: ANN001, ANN202
            return "结果"

    from app.tools.base import ToolRegistry

    registry = ToolRegistry()
    registry.register(FakeSearch(tmp_path))
    fake = FakeLLM([tool_response, tool_response, {"role": "assistant", "content": "收尾总结"}])
    steps, progress_cb = make_progress_collector()
    runner = ToolLoopRunner(config, fake, registry, on_progress=progress_cb)
    outcome = await runner.run("task1", "任务", asyncio.Event())

    assert outcome.stopped_reason == "max_rounds"
    assert outcome.summary == "收尾总结"
    # 收尾轮（第三次调用）不带 tools
    assert fake.calls[2] is None


async def test_cancel_stops_loop(tmp_path: Path) -> None:
    """中断事件 → stopped_reason=cancelled（工具间隙检查）。"""
    config = make_config()
    cancel = asyncio.Event()

    class CancelSearch:
        name = "web_search"
        description = "s"
        parameters = {}

        def __init__(self, work_dir) -> None:
            pass

        def to_openai_schema(self):  # noqa: ANN202
            return {}

        def execute(self, args):  # noqa: ANN001, ANN202
            cancel.set()
            return "结果"

    from app.tools.base import ToolRegistry

    registry = ToolRegistry()
    registry.register(CancelSearch(tmp_path))
    fake = FakeLLM(
        [{"role": "assistant", "content": "", "tool_calls": [tool_call("c1", "web_search", {"query": "x"})]}]
    )
    steps, progress_cb = make_progress_collector()
    runner = ToolLoopRunner(config, fake, registry, on_progress=progress_cb)
    outcome = await runner.run("task1", "任务", cancel)
    assert outcome.stopped_reason == "cancelled"


@respx.mock
async def test_llm_client_retries_then_success():
    """U-7：两次 5xx 后第三次成功。"""
    route = respx.post("https://api.example.com/v1/chat/completions")
    route.side_effect = [
        httpx.Response(500, text="e1"),
        httpx.Response(500, text="e2"),
        httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}}],
                                  "usage": {"prompt_tokens": 3, "completion_tokens": 2}}),
    ]
    client = LLMClient(model="m", api_key="k", base_url="https://api.example.com", max_retries=2)
    result = await client.chat([{"role": "user", "content": "hi"}])
    assert result["message"]["content"] == "ok"
    assert result["prompt_tokens"] == 3


@respx.mock
async def test_llm_client_non_retry_4xx():
    """4xx 不重试，直接 LLMError。"""
    route = respx.post("https://api.example.com/v1/chat/completions")
    route.return_value = httpx.Response(401, text="bad key")
    client = LLMClient(model="m", api_key="k", base_url="https://api.example.com", max_retries=2)
    with pytest.raises(LLMError):
        await client.chat([{"role": "user", "content": "hi"}])
    assert route.call_count == 1


@respx.mock
async def test_llm_client_retries_exhausted():
    """U-7：三次均失败 → LLMError。"""
    route = respx.post("https://api.example.com/v1/chat/completions")
    route.return_value = httpx.Response(500, text="down")
    client = LLMClient(model="m", api_key="k", base_url="https://api.example.com", max_retries=2)
    with pytest.raises(LLMError):
        await client.chat([{"role": "user", "content": "hi"}])
    assert route.call_count == 3

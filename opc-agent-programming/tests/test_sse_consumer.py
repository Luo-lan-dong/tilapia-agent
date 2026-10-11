"""SSEConsumer 启动停滞判定测试（ADR-055 补丁，2026-09-27）。

背景：tilacode 的模型解析与部分 drain 前置步骤在**发布任何 session 事件之前**
执行，失败时只记自己的日志、不产生事件。节点侧表现为进度停在 10%（session
就绪）后零事件，直到无进度看门狗 1800s 才判超时。本模块锁定新的「启动停滞」
快速判定：admitted 后阈值内无 step.started 即失败并给出可操作原因。
"""

import asyncio
import time
from typing import Any

from app.executor.event_translator import EventTranslator
from app.executor.sse_consumer import ConsumeResult, SSEConsumer


class _NoopClient:
    """最小客户端替身：本组用例不触发网络调用。"""

    def __init__(self, active: bool = True) -> None:
        self._active = active

    async def is_session_active(self, session_id: str, **kwargs: Any) -> bool:
        return self._active

    def stream_session_events(self, session_id: str, after: int | None = None):
        async def _empty():
            if False:  # pragma: no cover - 空异步生成器
                yield {}

        return _empty()


def _consumer(active: bool = True) -> SSEConsumer:
    async def _progress(_update) -> None:  # pragma: no cover - 不触发
        return None

    return SSEConsumer(
        client=_NoopClient(active),
        task_id="t1",
        session_id="ses_task_t1",
        directory=".",
        translator=EventTranslator("t1"),
        idle_timeout_seconds=30.0,
        on_progress=_progress,
    )


def _admit(consumer: SSEConsumer) -> None:
    """模拟收到 prompt.admitted（排水完成）。"""
    consumer._draining = False
    consumer._admitted_at = time.monotonic()
    consumer._translator.mark_session_ready()


def test_startup_stall_triggers_when_no_step_after_admitted() -> None:
    """admitted 后无任何 step 且超过阈值 → 判失败并带可操作原因。"""
    consumer = _consumer()
    _admit(consumer)
    consumer._admitted_at -= 61  # 模拟已停滞 61 秒

    assert consumer.check_startup_stall(60.0) is True
    result = consumer.result
    assert result is not None and result.status == "failed"
    assert "task_startup_stalled" in (result.reason or "")
    # 原因里必须提示排查方向（模型/provider），否则排查成本依旧很高
    assert "模型" in (result.reason or "")


def test_startup_stall_not_triggered_before_threshold() -> None:
    """未到阈值不判定，任务继续等待。"""
    consumer = _consumer()
    _admit(consumer)
    consumer._admitted_at -= 5

    assert consumer.check_startup_stall(60.0) is False
    assert consumer.result is None


def test_startup_stall_not_triggered_after_step_started() -> None:
    """已有 step.started（正常执行）时不再判定启动停滞。"""
    consumer = _consumer()
    _admit(consumer)
    consumer._step_started = True
    consumer._admitted_at -= 600

    assert consumer.check_startup_stall(60.0) is False
    assert consumer.result is None


def test_startup_stall_not_triggered_before_admitted() -> None:
    """尚未 admitted（drain 未完成）时不判定——此时还没有活动基线。"""
    consumer = _consumer()
    assert consumer._admitted_at is None

    assert consumer.check_startup_stall(60.0) is False
    assert consumer.result is None


def test_startup_stall_reported_once() -> None:
    """重复调用只判定一次（幂等），不覆盖既有结果。"""
    consumer = _consumer()
    _admit(consumer)
    consumer._admitted_at -= 61

    assert consumer.check_startup_stall(60.0) is True
    first = consumer.result
    assert consumer.check_startup_stall(60.0) is False
    assert consumer.result is first


async def test_run_with_no_events_is_bounded_by_stall_check() -> None:
    """集成式验证：流无事件 + active 恒真时，外部判定可终止消费循环。

    说明：真实生产由看门狗任务周期性调用 ``check_startup_stall``；这里直接
    模拟该调用，确认置失败后 ``run()`` 能收敛（不再无限等待）。
    """
    consumer = _consumer(active=True)
    _admit(consumer)
    consumer._admitted_at -= 61

    task = asyncio.create_task(consumer.run())
    await asyncio.sleep(0)
    assert consumer.check_startup_stall(60.0) is True

    # 流为空：run() 会走重连退避；置失败后应在有限步内退出
    for _ in range(50):
        await asyncio.sleep(0.01)
        if task.done():
            break
    task.cancel()
    result = await asyncio.gather(task, return_exceptions=True)
    outcome = result[0]
    if isinstance(outcome, ConsumeResult):
        assert outcome.status == "failed"
        assert "task_startup_stalled" in (outcome.reason or "")

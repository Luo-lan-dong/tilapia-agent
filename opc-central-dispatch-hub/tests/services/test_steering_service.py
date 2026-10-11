"""Steering 队列注册表单元测试（ADR-045）。"""

from app.services import steering_service


def setup_function() -> None:
    """每条测试前清空注册表，避免跨测试污染。"""
    steering_service._queues.clear()


def test_enqueue_without_active_stream_rejected() -> None:
    """UT-1：未 open 时 enqueue 返回 False（无活跃流拒绝入队）。"""
    assert steering_service.enqueue("s1", "插话") is False


def test_open_then_enqueue_and_drain_fifo() -> None:
    """UT-2：open 后入队成功，drain 按投递顺序取空。"""
    steering_service.open_stream("s1")
    assert steering_service.enqueue("s1", "第一条") is True
    assert steering_service.enqueue("s1", "第二条") is True
    assert steering_service.drain("s1") == ["第一条", "第二条"]


def test_close_rejects_and_drains_empty() -> None:
    """UT-3：close 后 enqueue 返回 False、drain 为空。"""
    steering_service.open_stream("s1")
    steering_service.close_stream("s1")
    assert steering_service.enqueue("s1", "插话") is False
    assert steering_service.drain("s1") == []


def test_drain_idempotent() -> None:
    """UT-4：drain 幂等——第二次调用返回空。"""
    steering_service.open_stream("s1")
    steering_service.enqueue("s1", "插话")
    assert steering_service.drain("s1") == ["插话"]
    assert steering_service.drain("s1") == []


def test_reopen_clears_stale_queue() -> None:
    """重复 open 清空旧队列（防上轮异常残留串话）。"""
    steering_service.open_stream("s1")
    steering_service.enqueue("s1", "残留")
    steering_service.open_stream("s1")
    assert steering_service.drain("s1") == []

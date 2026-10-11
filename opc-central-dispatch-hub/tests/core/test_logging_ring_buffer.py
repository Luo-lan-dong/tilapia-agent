"""内存环形缓冲与分级归档测试（ADR-047 UT-1/UT-2）。"""

import json
import logging

from app.core.logging import (
    RingBufferHandler,
    _resolve_log_dir,
    configure_logging,
    read_ring_buffer,
    ring_buffer_size,
)


def _reset_root() -> None:
    """清理 root handlers，防用例间互相泄漏。"""
    logging.getLogger().handlers.clear()


def test_ring_buffer_capacity_drops_oldest() -> None:
    """UT-1：超过 maxlen 丢弃最旧条目。"""
    handler = RingBufferHandler(size=3)
    test_logger = logging.getLogger("ring_buffer_test")
    test_logger.handlers = [handler]
    test_logger.propagate = False
    test_logger.setLevel(logging.INFO)
    for index in range(5):
        test_logger.info("event_%d", index)
    messages = [snap["message"] for snap in handler.buffer]
    assert messages == ["event_2", "event_3", "event_4"]
    _reset_root()


def test_ring_buffer_snapshot_fields() -> None:
    """UT-1：快照携带级别/logger/extra 结构化字段。"""
    handler = RingBufferHandler(size=10)
    test_logger = logging.getLogger("ring_buffer_fields_test")
    test_logger.handlers = [handler]
    test_logger.propagate = False
    test_logger.setLevel(logging.INFO)
    test_logger.info(
        "skill_executed",
        extra={"event": "skill_executed", "request_id": "req-1", "skill": "find_idle_nodes"},
    )
    snapshot = handler.buffer[-1]
    assert snapshot["level"] == "INFO"
    assert snapshot["message"] == "skill_executed"
    assert snapshot["request_id"] == "req-1"
    # skill 在快照白名单字段内，作为顶层字段呈现
    assert snapshot["skill"] == "find_idle_nodes"
    _reset_root()


def test_read_ring_buffer_filters(monkeypatch) -> None:
    """UT-1：级别/关键字/时间过滤与倒序返回。"""
    import time as time_mod

    import app.core.logging as logging_module

    handler = RingBufferHandler(size=50)
    test_logger = logging.getLogger("ring_buffer_filter_test")
    test_logger.handlers = [handler]
    test_logger.propagate = False
    test_logger.setLevel(logging.DEBUG)
    test_logger.info("任务创建成功 task_id=1")
    test_logger.warning("节点心跳超时")
    test_logger.error("数据库连接异常")
    time_mod.sleep(0.01)
    boundary = handler.buffer[-1]["timestamp"]
    monkeypatch.setattr(logging_module, "_ring_buffer", handler)

    # 倒序：最新在前
    all_entries = read_ring_buffer()
    assert all_entries[0]["level"] == "ERROR"

    errors = read_ring_buffer(levels={"ERROR"})
    assert len(errors) == 1
    assert "数据库连接异常" in errors[0]["message"]

    warn_plus = read_ring_buffer(levels={"WARNING", "ERROR"})
    assert len(warn_plus) == 2

    keyword_hits = read_ring_buffer(keyword="心跳")
    assert len(keyword_hits) == 1

    from datetime import datetime

    start = datetime.fromisoformat(boundary)
    recent = read_ring_buffer(start=start)
    assert recent and recent[0]["level"] == "ERROR"
    _reset_root()

def test_read_ring_buffer_empty_without_configure() -> None:
    """未配置时读取返回空列表且不抛异常。"""
    import app.core.logging as logging_module
    original = logging_module._ring_buffer
    logging_module._ring_buffer = None
    try:
        assert read_ring_buffer() == []
        assert ring_buffer_size() == 0
    finally:
        logging_module._ring_buffer = original


def test_resolve_log_dir_desktop_fallback() -> None:
    """桌面模式未显式传目录时回落 local 相对目录（launcher 正常会显式注入）。"""
    assert _resolve_log_dir("local", None) == "./logs"
    assert _resolve_log_dir("desktop", None) == "./logs"
    assert _resolve_log_dir("prod", None) == "/var/log/opc/central-hub"
    assert _resolve_log_dir("prod", "/custom") == "/custom"


def test_configure_logging_installs_handlers(tmp_path) -> None:
    """configure 后 root 挂载环形缓冲与分级 error handler。"""
    configure_logging(
        environment="dev",
        log_level="INFO",
        log_dir=str(tmp_path),
        ring_buffer_size=100,
    )
    root = logging.getLogger()
    handler_types = [type(h).__name__ for h in root.handlers]
    assert "RingBufferHandler" in handler_types
    assert ring_buffer_size() == 0

    logging.getLogger("configure_test").warning("警告条目")
    logging.getLogger("configure_test").info("信息条目")
    assert ring_buffer_size() == 2

    # error.log 已创建且仅含 WARNING 条目（dev 环境 JSON 行）
    error_log = tmp_path / "error.log"
    assert error_log.is_file()
    lines = [
        line for line in error_log.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert lines, "error.log 应有 WARNING 条目"
    messages = [json.loads(line)["message"] for line in lines]
    assert "警告条目" in messages
    assert "信息条目" not in messages
    _reset_root()

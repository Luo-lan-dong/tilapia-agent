"""节点日志读取器测试（ADR-047 AT-1/AT-2）。

read_recent_log_lines：tail 截取、级别 JSON 行过滤、滚动文件补齐、
空目录/缺失文件降级。
"""

import base64
import json
from pathlib import Path

from app.utils.log_fetcher import read_recent_log_lines


def _write_json_log(path: Path, entries: list[tuple[str, str]]) -> None:
    """写 JSON 日志行文件（与 Agent 生产格式一致：level 小写字段+大写值）。"""
    lines = [
        json.dumps(
            {
                "level": level.lower(),
                "level_number": 20,
                "timestamp": f"2026-09-24T10:00:{index:02d}",
                "logger": "test",
                "message": message,
            },
            ensure_ascii=False,
        )
        for index, (level, message) in enumerate(entries)
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_tail_lines_basic(tmp_path: Path) -> None:
    """AT-1：无级别过滤时取最后 N 行。"""
    log = tmp_path / "agent.log"
    entries = [(level, f"msg-{index}") for index, level in enumerate(["info"] * 10)]
    _write_json_log(log, entries)
    result = read_recent_log_lines(tail_lines=3, log_dir=tmp_path)
    assert result.splitlines()[-1].find("msg-9") >= 0
    assert len(result.splitlines()) == 3


def test_level_filter_matches_json_field(tmp_path: Path) -> None:
    """AT-1：级别过滤只命中 JSON level 字段（消息含 error 字样的 info 行不误命中）。"""
    log = tmp_path / "agent.log"
    _write_json_log(
        log,
        [
            ("info", "任务执行失败 error occurred"),  # 消息含 error，级别 info
            ("error", "真正的错误行"),
            ("warning", "警告行"),
        ],
    )
    result = read_recent_log_lines(tail_lines=10, level="error", log_dir=tmp_path)
    lines = result.splitlines()
    assert len(lines) == 1
    assert "真正的错误行" in lines[0]


def test_level_filter_matches_console_lines(tmp_path: Path) -> None:
    """AT-1：local 可读文本行按 [level] 标记过滤（文件为无颜色格式）。"""
    log = tmp_path / "agent.log"
    log.write_text(
        "\n".join(
            [
                "2026-09-24T10:00:01 [info     ] 任务开始 [app.main]",
                "2026-09-24T10:00:02 [error    ] 节点执行失败 [app.executor]",
                "2026-09-24T10:00:03 [warning  ] 警告行 [app.executor]",
                "2026-09-24T10:00:04 无级别标记的裸行（异常输出，过滤时跳过）",
            ]
        ),
        encoding="utf-8",
    )
    result = read_recent_log_lines(tail_lines=10, level="error", log_dir=tmp_path)
    lines = result.splitlines()
    assert len(lines) == 1
    assert "节点执行失败" in lines[0]
    # error 级别同时命中 critical 行
    log.write_text(
        "2026-09-24T10:00:05 [critical ] 致命错误 [app.main]\n",
        encoding="utf-8",
    )
    assert "致命错误" in read_recent_log_lines(tail_lines=10, level="error", log_dir=tmp_path)


def test_rotated_file_backfill(tmp_path: Path) -> None:
    """AT-1：当天文件行数不足时从最近滚动文件补齐（时间正序拼接）。"""
    current = tmp_path / "agent.log"
    rotated = tmp_path / "agent-2026-09-23.log"
    older = tmp_path / "agent-2026-09-22.log"
    _write_json_log(current, [("error", "今天-1"), ("error", "今天-2")])
    _write_json_log(rotated, [("error", "昨天-1"), ("error", "昨天-2")])
    _write_json_log(older, [("error", "前天-1")])

    result = read_recent_log_lines(tail_lines=5, level="error", log_dir=tmp_path)
    text = result
    assert "今天-1" in text and "今天-2" in text
    assert "昨天-1" in text and "昨天-2" in text
    assert "前天-1" in text
    # 滚动文件在前（旧），当前文件在后（新）
    assert text.find("昨天-1") < text.find("今天-1")
    assert text.find("前天-1") < text.find("昨天-1")


def test_missing_files_returns_empty(tmp_path: Path) -> None:
    """AT-3：目录无日志文件返回空串（调用方回传 log_error）。"""
    assert read_recent_log_lines(tail_lines=10, log_dir=tmp_path) == ""


def test_chunk_encoding_roundtrip(tmp_path: Path) -> None:
    """AT-2：内容经 base64 分块后按 seq 拼接可还原（512KB 分块参数语义）。"""
    log = tmp_path / "agent.log"
    content = "\n".join(f"日志行 {index}" for index in range(500))
    _write_json_log(log, [("error", f"日志行 {index}") for index in range(500)])
    result = read_recent_log_lines(tail_lines=500, level="error", log_dir=tmp_path)

    encoded = base64.b64encode(result.encode("utf-8")).decode("ascii")
    chunk_size = 512 * 1024
    total = max(1, -(-len(encoded) // chunk_size))
    joined = "".join(encoded[seq * chunk_size : (seq + 1) * chunk_size] for seq in range(total))
    assert base64.b64decode(joined).decode("utf-8") == result
    assert content.splitlines()[0] in result

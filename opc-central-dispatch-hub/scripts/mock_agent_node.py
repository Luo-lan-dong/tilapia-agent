#!/usr/bin/env python3
"""OPC Mock Agent 节点脚本。

本地开发和手动验证完整调度链路：注册 -> WebSocket 连接 -> 心跳 ->
接收任务 -> 模拟执行 -> 上报完成。

用法：python opc-central-dispatch-hub/scripts/mock_agent_node.py \
    --hub-url http://localhost:8000 --name mock-01 --agent-type programming
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import urllib.parse
from datetime import UTC, datetime
from typing import Any

import httpx
import websockets

DEFAULT_HUB_URL = "http://localhost:8000"
HEARTBEAT_INTERVAL_SECONDS = 5
PROGRESS_INTERVAL_SECONDS = 2
MAX_RECONNECT_RETRIES = 5
RECONNECT_DELAY_SECONDS = 3
PROGRESS_STEP = 20
_LOGGER_NAME = "mock_agent_node"


class JsonFormatter(logging.Formatter):
    """JSON 结构化日志格式化器。"""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "event": getattr(record, "event", record.getMessage()),
        }
        for key in ("node_id", "task_id", "agent_type", "metadata"):
            value = getattr(record, key, None)
            if value is not None:
                entry[key] = value
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def _setup_logging() -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    return logger


logger = _setup_logging()


def _log(event: str, **kwargs: Any) -> None:
    """输出结构化 INFO 日志。"""
    logger.info(event, extra={"event": event, **kwargs})


def _parse_capabilities(raw: list[str] | None) -> list[str]:
    if not raw:
        return []
    result: list[str] = []
    for item in raw:
        result.extend(cap.strip() for cap in item.split(",") if cap.strip())
    return result


def _build_ws_url(hub_url: str) -> str:
    parsed = urllib.parse.urlparse(hub_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urllib.parse.urlunparse(
        parsed._replace(scheme=scheme, path="/api/v1/agent-nodes/ws"),
    )


async def _register_node(
    client: httpx.AsyncClient,
    hub_url: str,
    name: str,
    agent_type: str,
    capabilities: list[str],
) -> tuple[str, str]:
    response = await client.post(
        urllib.parse.urljoin(hub_url, "/api/v1/agent-nodes/register"),
        json={"name": name, "agent_type": agent_type, "capabilities": capabilities},
        timeout=10.0,
    )
    response.raise_for_status()
    body = response.json()
    if body.get("code") != 0:
        raise RuntimeError(f"注册失败: {body}")
    data = body["data"]
    node_id = str(data["id"])
    token = data["registration_token"]
    _log(
        "agent_registered",
        node_id=node_id,
        agent_type=agent_type,
        metadata={"capabilities": capabilities},
    )
    return node_id, token


async def _send_progress(
    websocket: websockets.WebSocketClientProtocol,
    node_id: str,
    task_id: str,
) -> None:
    progress = 0
    while progress <= 100:
        await websocket.send(
            json.dumps(
                {"event": "agent.task_progress", "payload": {"task_id": task_id, "progress": progress}},
            ),
        )
        _log(
            "task_progress_reported",
            node_id=node_id,
            task_id=task_id,
            metadata={"progress_percent": progress},
        )
        if progress == 100:
            break
        await asyncio.sleep(PROGRESS_INTERVAL_SECONDS)
        progress += PROGRESS_STEP


async def _handle_task_dispatch(
    websocket: websockets.WebSocketClientProtocol,
    node_id: str,
    payload: dict[str, Any],
) -> None:
    task_id = payload.get("task_id")
    _log(
        "task_received",
        node_id=node_id,
        task_id=task_id,
        metadata={"task_name": payload.get("task_name"), "description": payload.get("description")},
    )
    await websocket.send(
        json.dumps({"event": "agent.task_accepted", "payload": {"task_id": task_id}}),
    )
    _log("task_accepted_sent", node_id=node_id, task_id=task_id)
    await _send_progress(websocket, node_id, task_id)
    await websocket.send(
        json.dumps({"event": "agent.task_completed", "payload": {"task_id": task_id}}),
    )
    _log("task_completed_sent", node_id=node_id, task_id=task_id)


async def _heartbeat_loop(
    websocket: websockets.WebSocketClientProtocol,
    node_id: str,
    stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=HEARTBEAT_INTERVAL_SECONDS)
        except TimeoutError:
            await websocket.send(json.dumps({"event": "agent.heartbeat"}))


async def _receive_loop(
    websocket: websockets.WebSocketClientProtocol,
    node_id: str,
    stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        try:
            message_raw = await websocket.recv()
        except websockets.exceptions.ConnectionClosed:
            _log("websocket_connection_closed", node_id=node_id)
            stop_event.set()
            return
        try:
            message = json.loads(message_raw)
        except json.JSONDecodeError:
            _log("invalid_message_received", node_id=node_id)
            continue
        event = message.get("event")
        payload = message.get("payload") or {}
        if event == "hub.task_dispatch":
            asyncio.create_task(_handle_task_dispatch(websocket, node_id, payload))
        else:
            _log("message_received", node_id=node_id, metadata={"ws_event": event})


async def _run_single_session(
    hub_url: str,
    node_id: str,
    token: str,
    stop_event: asyncio.Event,
) -> None:
    ws_url = f"{_build_ws_url(hub_url)}?node_id={node_id}&token={token}"
    async with websockets.connect(ws_url) as websocket:
        _log("websocket_connected", node_id=node_id)
        heartbeat_task = asyncio.create_task(_heartbeat_loop(websocket, node_id, stop_event))
        receive_task = asyncio.create_task(_receive_loop(websocket, node_id, stop_event))
        await asyncio.gather(heartbeat_task, receive_task, return_exceptions=True)


async def _run_agent(args: argparse.Namespace, stop_event: asyncio.Event) -> None:
    client = httpx.AsyncClient()
    node_id: str | None = None
    token: str | None = None
    retry_count = 0
    while not stop_event.is_set() and retry_count <= MAX_RECONNECT_RETRIES:
        try:
            if node_id is None or token is None:
                node_id, token = await _register_node(
                    client,
                    args.hub_url,
                    args.name,
                    args.agent_type,
                    args.capabilities,
                )
            await _run_single_session(args.hub_url, node_id, token, stop_event)
        except websockets.exceptions.InvalidStatusCode as exc:
            _log(
                "websocket_auth_failed",
                node_id=node_id,
                metadata={"status_code": exc.status_code},
            )
            node_id, token = None, None
        except Exception:
            logger.exception(
                "agent_session_error",
                extra={"event": "agent_session_error", "node_id": node_id},
            )
        if stop_event.is_set():
            break
        retry_count += 1
        if retry_count <= MAX_RECONNECT_RETRIES:
            _log(
                "agent_reconnecting",
                node_id=node_id,
                metadata={"retry_count": retry_count, "delay_seconds": RECONNECT_DELAY_SECONDS},
            )
            await asyncio.sleep(RECONNECT_DELAY_SECONDS)
    await client.aclose()
    _log("agent_shutdown", node_id=node_id)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OPC Mock Agent 节点")
    parser.add_argument("--hub-url", default=os.getenv("HUB_URL", DEFAULT_HUB_URL), help="中枢 API 地址")
    parser.add_argument("--name", default=os.getenv("AGENT_NAME", "mock-agent"), help="节点名称")
    parser.add_argument("--agent-type", default=os.getenv("AGENT_TYPE", "programming"), help="Agent 类型")
    parser.add_argument(
        "--capabilities",
        nargs="*",
        default=_parse_capabilities(os.getenv("AGENT_CAPABILITIES", "").split(",")),
        help="能力标签，逗号分隔",
    )
    parser.add_argument("--verbose", action="store_true", help="输出 DEBUG 日志")
    return parser.parse_args()


async def main() -> int:
    args = _parse_args()
    if args.verbose:
        logger.setLevel(logging.DEBUG)
    stop_event = asyncio.Event()

    def _signal_handler(signum: int, frame: Any) -> None:
        _log("shutdown_signal_received", metadata={"signal": signum})
        stop_event.set()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)
    await _run_agent(args, stop_event)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

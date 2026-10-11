"""部署后验证：任务执行状态 + 中枢日志 + 节点日志 + 配置污染检查。"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.models.base import AsyncSessionLocal  # noqa: E402
from app.models.task import Task  # noqa: E402
from app.models.bt_mcp_server import BtMcpServer  # noqa: E402
from app.services.bt_mcp_client import BtMcpError, timed_call  # noqa: E402
from node_diag_20260927 import _build  # noqa: E402

HUB = "http://127.0.0.1:8000"
SERVER_ID = "3fc19d1d-2a75-4a78-8ef8-28ba1d87822b"

NODE_COMMANDS = [
    (
        "节点 Agent 日志尾部",
        "tail -60 /opt/opc-agent-code/logs/agent.log 2>&1 | grep -viE 'api_key|sk-' | tail -45",
    ),
    (
        "用户级配置是否被再写（应为仅 $schema）",
        "cat /root/.config/tilacode/tilacode.jsonc 2>&1",
    ),
    (
        "工作目录配置（最新任务目录的 .tilacode/tilacode.jsonc 应含 providers）",
        "latest=$(ls -t /opt/opc-agent-code/workspace/ | head -1); echo \"dir=$latest\"; "
        "cat \"/opt/opc-agent-code/workspace/$latest/.tilacode/tilacode.jsonc\" 2>&1 | grep -vE 'sk-|apiKey' | head -30",
    ),
]


async def hub_side() -> None:
    async with AsyncSessionLocal() as session:
        tasks = (
            await session.execute(select(Task).order_by(Task.created_at.desc()).limit(2))
        ).scalars().all()
        print("===== 最新任务 =====")
        for task in tasks:
            todos = task.todos or []
            print(
                f"[{str(task.id)[:8]}] status={task.status} progress={task.progress} "
                f"stage={task.progress_stage} actual={task.executor_model_actual} "
                f"todos={len(todos)}"
            )
            print(f"  title: {task.title[:56]}")
            print(f"  msg: {task.progress_message}")
            for todo in todos[:6]:
                print(f"    - [{todo.get('status')}] {str(todo.get('content'))[:56]}")

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(f"{HUB}/api/v1/logs?keyword=task_progress_updated&limit=30")
        events = [
            m.group(1)
            for m in __import__("re").finditer(r'"message":"([^"]+)"', resp.text)
        ]
        real = [e for e in events if e != "ok"]
        print(f"\n===== 中枢 task_progress_updated（最近 {len(real)} 条） =====")
        for e in real[-8:]:
            print(f"  {e[:120]}")


async def node_side(server) -> None:
    for title, command in NODE_COMMANDS:
        print(f"\n===== {title} =====")
        client = _build(server)
        try:
            result, _ms = await timed_call(client, "Bash", {"command": command})
            data = result.get("data") or {}
            print(str(data.get("stdout") or result.get("msg") or "")[:2600])
        except BtMcpError as exc:
            print(f"[MCP 错误] {exc}")
        finally:
            await client.close()


async def main() -> None:
    await hub_side()
    async with AsyncSessionLocal() as session:
        server = (
            await session.execute(
                select(BtMcpServer).where(BtMcpServer.id == __import__("uuid").UUID(SERVER_ID))
            )
        ).scalars().first()
    await node_side(server)


if __name__ == "__main__":
    asyncio.run(main())

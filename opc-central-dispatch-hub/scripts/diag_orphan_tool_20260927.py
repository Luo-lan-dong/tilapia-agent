"""诊断：会话消息序列中的孤儿 tool 消息（400 根因取证）。"""

import asyncio
import json
import sys

sys.path.insert(0, ".")

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings

SESSION_ID = "83ca6bf0-e8bc-41f6-8171-dcf82d086f73"


async def main() -> None:
    engine = create_async_engine(get_settings().database_url, echo=False)
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT messages FROM task_building_sessions WHERE id = :sid"),
                {"sid": SESSION_ID},
            )
        ).fetchone()
        if row is None:
            print("会话不存在")
            return
        messages = row[0]
        print(f"总消息数：{len(messages)}")
        prev_role = None
        prev_has_calls = False
        for index, msg in enumerate(messages):
            role = msg.get("role")
            has_calls = bool(msg.get("tool_calls"))
            tool_call_id = msg.get("tool_call_id")
            flag = ""
            if role == "tool" and not (prev_role == "assistant" and prev_has_calls):
                flag = "  <<<< 孤儿 tool 消息（前置非 assistant(tool_calls)）"
            content = str(msg.get("content") or "")[:60].replace("\n", " ")
            print(
                f"[{index}] {role} calls={has_calls} tid={tool_call_id} "
                f"thinking={'Y' if msg.get('thinking') else 'N'} "
                f"steps={len(msg.get('steps') or [])} | {content}{flag}"
            )
            prev_role, prev_has_calls = role, has_calls
    await engine.dispose()


asyncio.run(main())

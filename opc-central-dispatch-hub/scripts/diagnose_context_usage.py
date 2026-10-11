"""诊断：压缩后上下文占用的构成分解（ADR-041 验证辅助）。

查最新会话的压缩点与消息量，分解 system / tools / checkpoint / 剩余消息
各占多少 token，判断「压缩后依然超窗口」是投影失效还是物理下限。
"""
import asyncio
import json
import sys

import asyncpg

DSN = "postgresql://opc:xBHXSwscrLPHF3G6@192.168.101.101:5432/opc"


def est(text: str) -> int:
    return len(text) // 4


async def main() -> None:
    conn = await asyncpg.connect(DSN)
    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )
    try:
        row = await conn.fetchrow(
            """
            SELECT s.id, s.title, s.model, s.messages, s.context_compaction,
                   p.context_window
            FROM task_building_sessions s
            LEFT JOIN llm_providers p ON p.model = s.model
            ORDER BY s.updated_at DESC
            LIMIT 1
            """
        )
        if row is None:
            print("无会话")
            return
        messages = row["messages"] or []
        compaction = row["context_compaction"]
        window = row["context_window"] or 0
        print(f"会话 {str(row['id'])[:8]}… {row['title'][:20]!r}")
        print(f"模型 {row['model']} 窗口 {window} | 消息 {len(messages)} 条")
        if compaction and compaction.get("summary_text"):
            print(
                f"压缩点: 覆盖前 {compaction.get('message_count')} 条, "
                f"摘要 {len(compaction['summary_text'])} 字 ≈ {est(compaction['summary_text'])} tok"
            )
        else:
            print("压缩点: 无")

        # 消息体量分解
        total_msg_chars = 0
        user_chars = 0
        tool_chars = 0
        assistant_chars = 0
        for m in messages:
            content = m.get("content") or ""
            total_msg_chars += len(content)
            if m.get("tool_calls"):
                total_msg_chars += len(json.dumps(m["tool_calls"], ensure_ascii=False))
            role = m.get("role")
            if role == "user":
                user_chars += len(content)
            elif role == "tool":
                tool_chars += len(content)
            else:
                assistant_chars += len(content)
        msg_total = total_msg_chars // 4
        print(
            f"消息原文合计 ≈ {msg_total} tok "
            f"(user {user_chars // 4} / tool {tool_chars // 4} / assistant {assistant_chars // 4})"
        )

        # system + tools 固定开销（与业务库一致地构造）
        sys.path.insert(0, ".")
        from app.agents.orchestrator_prompts import build_system_prompt
        from app.skills.registry import SKILL_REGISTRY, to_openai_tools

        skills = [cls() for cls in SKILL_REGISTRY]
        system_text = build_system_prompt(
            enabled=[(s.name, s.description) for s in skills],
            disabled=[],
        )
        tools = to_openai_tools(skills)
        tools_text = json.dumps(tools, ensure_ascii=False)
        print(f"system prompt ≈ {est(system_text)} tok")
        print(f"tools 定义（{len(tools)} 个技能） ≈ {est(tools_text)} tok")
        fixed = est(system_text) + est(tools_text)
        print(f"固定开销合计 ≈ {fixed} tok（不含历史，压缩无法缩减）")
        if window:
            print(f"→ 固定开销占窗口 {fixed / window * 100:.0f}%")
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())

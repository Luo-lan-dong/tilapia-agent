"""存量数据 agent_type 迁移：programming → code（ADR-016）。

ADR-016 决策：agent_type 以 code 替换 programming，列名不变。
节点注册侧已切换 code，但任务创建（spec_chat 硬编码 / DeepSeek 规划）
与早期注册的存量行仍为 programming，导致调度器与手动派发按类型过滤时
永远匹配不到在线的 code 节点。本脚本幂等：仅更新 programming 行。

用法：
    cd opc-central-dispatch-hub
    python scripts\\migrate_agent_type_to_code.py
"""

import asyncio
import sys
from pathlib import Path

# 将后端根目录加入 sys.path，便于在项目根目录下直接执行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select, update

from app.models.agent_node import AgentNode
from app.models.base import AsyncSessionLocal
from app.models.task import Task

OLD_TYPE = "programming"
NEW_TYPE = "code"


async def _count(session, model) -> int:
    """统计模型下 agent_type=programming 的行数。"""
    stmt = select(func.count()).select_from(model).where(model.agent_type == OLD_TYPE)
    return (await session.execute(stmt)).scalar_one()


async def main() -> None:
    """将 tasks 与 agent_nodes 中存量 programming 迁移为 code（幂等）。"""
    async with AsyncSessionLocal() as session:
        task_count = await _count(session, Task)
        node_count = await _count(session, AgentNode)
        print(f"迁移前：tasks.programming={task_count}，agent_nodes.programming={node_count}")

        if task_count:
            await session.execute(
                update(Task).where(Task.agent_type == OLD_TYPE).values(agent_type=NEW_TYPE)
            )
        if node_count:
            await session.execute(
                update(AgentNode)
                .where(AgentNode.agent_type == OLD_TYPE)
                .values(agent_type=NEW_TYPE)
            )
        await session.commit()

        task_left = await _count(session, Task)
        node_left = await _count(session, AgentNode)
        print(f"迁移后：tasks.programming={task_left}，agent_nodes.programming={node_left}")
        print(
            "迁移完成（programming → code）"
            if (task_left == 0 and node_left == 0)
            else "仍有残留，请检查"
        )


if __name__ == "__main__":
    asyncio.run(main())

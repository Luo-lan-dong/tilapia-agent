"""清理重启风暴产生的孤儿 Agent 节点记录。

背景：0.3.0 及之前的 Agent 每次重启都会以随机身份重新注册，且中枢未存
client_id，导致 agent_nodes 表堆积大量重复记录（同一台机器几十条）。
0.3.1 起 Agent 固定 AGENT_CLIENT_ID，中枢注册按 client_id 幂等复用，
不再产生新垃圾。本脚本用于一次性清理历史孤儿记录。

判定标准：client_id 为空的 programming 节点 = 升级前注册的历史记录，
升级后的新注册均携带 client_id，不受影响。

用法（在中枢项目根目录）：
    # 预览（默认，不删除）
    .venv/Scripts/python.exe scripts/cleanup_orphan_agent_nodes.py
    # 实际删除
    .venv/Scripts/python.exe scripts/cleanup_orphan_agent_nodes.py --execute
"""

import argparse
import asyncio

from sqlalchemy import delete, func, select, update

from app.models.agent_node import AgentNode
from app.models.base import AsyncSessionLocal
from app.models.task import Task


async def _count_orphans(session) -> int:
    """统计待清理的孤儿节点数量。"""
    result = await session.execute(
        select(func.count())
        .select_from(AgentNode)
        .where(AgentNode.client_id.is_(None), AgentNode.agent_type == "programming")
    )
    return int(result.scalar_one())


async def main() -> None:
    parser = argparse.ArgumentParser(description="清理孤儿 Agent 节点记录")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="实际执行删除；缺省仅预览数量",
    )
    args = parser.parse_args()

    async with AsyncSessionLocal() as session:
        total_result = await session.execute(select(func.count()).select_from(AgentNode))
        total = int(total_result.scalar_one())
        orphans = await _count_orphans(session)
        print(f"节点总数: {total}，其中孤儿记录（client_id 为空）: {orphans}")

        if not args.execute:
            print("预览模式，未删除。确认无误后加 --execute 执行。")
            return

        # 部分孤儿节点仍被历史任务的 assigned_node_id 引用（外键约束），
        # 先解除引用再删除：历史任务记录保留，仅清空对已废弃节点的分配
        orphan_ids = select(AgentNode.id).where(
            AgentNode.client_id.is_(None),
            AgentNode.agent_type == "programming",
        )
        unassigned = await session.execute(
            update(Task)
            .where(Task.assigned_node_id.in_(orphan_ids))
            .values(assigned_node_id=None)
        )
        print(f"已解除 {unassigned.rowcount} 条历史任务的节点分配引用")

        await session.execute(
            delete(AgentNode).where(
                AgentNode.client_id.is_(None),
                AgentNode.agent_type == "programming",
            )
        )
        await session.commit()
        remaining = int(
            (await session.execute(select(func.count()).select_from(AgentNode))).scalar_one()
        )
        print(f"已删除 {orphans} 条孤儿记录，剩余节点总数: {remaining}")


if __name__ == "__main__":
    asyncio.run(main())

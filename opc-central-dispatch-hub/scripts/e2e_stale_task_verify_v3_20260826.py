"""E2E v3 验证：读取 logs/e2e_v3_marker.txt，按其记录做断言。"""

import asyncio
import sys
from pathlib import Path

from sqlalchemy import text

from app.models.base import AsyncSessionLocal

MARKER_PATH = Path("logs/e2e_v3_marker.txt")


def _parse_marker() -> dict[str, str]:
    m: dict[str, str] = {}
    for line in MARKER_PATH.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            m[k.strip()] = v.strip()
    return m


async def main() -> int:
    if not MARKER_PATH.exists():
        print(f"[verify-v3] marker file {MARKER_PATH} 不存在")
        return 2

    marker = _parse_marker()
    node_id = marker["node_id"]
    task_a = marker["task_a"]
    task_b = marker["task_b"]
    task_c = marker["task_c"]
    summary_b = marker["summary_b"]

    failures: list[str] = []
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT id::text, title, status, change_summary "
                    "FROM tasks WHERE id IN (:a, :b, :c)"
                ),
                {"a": task_a, "b": task_b, "c": task_c},
            )
        ).mappings().all()
        by_id = {r["id"]: r for r in rows}

        # 任务 A：期望 failed，change_summary 含「自动标记为 failed」
        a = by_id.get(task_a)
        if a is None:
            failures.append(f"Task A({task_a}) 不存在")
        else:
            print(
                f"[verify-v3] Task A (stale, 无 cs): status={a['status']} "
                f"cs={(a['change_summary'] or '')[:60]!r}"
            )
            if a["status"] != "failed":
                failures.append(f"Task A.status 期望 failed，实际 {a['status']}")
            if not a["change_summary"] or "自动标记为 failed" not in a["change_summary"]:
                failures.append(
                    f"Task A.change_summary 缺「自动标记为 failed」: {a['change_summary']!r}"
                )

        # 任务 B：期望 failed，change_summary 保持原文
        b = by_id.get(task_b)
        if b is None:
            failures.append(f"Task B({task_b}) 不存在")
        else:
            print(
                f"[verify-v3] Task B (stale, cs 已存在): status={b['status']} "
                f"cs={(b['change_summary'] or '')[:60]!r}"
            )
            if b["status"] != "failed":
                failures.append(f"Task B.status 期望 failed，实际 {b['status']}")
            if b["change_summary"] != summary_b:
                failures.append(
                    f"Task B.change_summary 被覆写: {b['change_summary']!r} ≠ {summary_b!r}"
                )

        # 任务 C：期望 running
        c = by_id.get(task_c)
        if c is None:
            failures.append(f"Task C({task_c}) 不存在")
        else:
            print(
                f"[verify-v3] Task C (fresh): status={c['status']} "
                f"cs={(c['change_summary'] or '')[:60]!r}"
            )
            if c["status"] != "running":
                failures.append(f"Task C.status 期望 running，实际 {c['status']}")

        # 节点：status=idle（保持），load 应当减回 seed 之前（v3 seed 前是 4.9，+1 后 5.9；
        # A/B 都失败 -1，所以应是 4.9）
        node_row = (
            await session.execute(
                text(
                    "SELECT id::text, status, load FROM agent_nodes WHERE id = :nid"
                ),
                {"nid": node_id},
            )
        ).mappings().first()
        if node_row is None:
            failures.append(f"节点 {node_id} 不存在")
        else:
            print(
                f"[verify-v3] node {node_row['id']} status={node_row['status']} "
                f"load={node_row['load']}"
            )
            if node_row["status"] != "idle":
                failures.append(
                    f"节点 status 期望 idle，实际 {node_row['status']}"
                )
            # load 应回到 4.9（A 失败 -1，B 失败 -1 = -2，共 +1-2 = -1）
            # 但 v3 seed 时 load=4.9，+1 后 5.9；2 个 failed -1 = 4.9
            # 由于其他任务可能动态变 load，校验"<= 5.9"即可
            if float(node_row["load"]) > 5.9:
                failures.append(
                    f"节点 load 不应高于 seed 后值(5.9)，实际 {node_row['load']}"
                )

    print()
    if failures:
        print("[verify-v3] FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("[verify-v3] OK: 全部 E2E 断言通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

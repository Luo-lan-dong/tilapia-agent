"""E2E 验证：读取任务与节点状态并断言兜底行为。

按 spec/task-stale-detector-spec.md §10 验收：
1. Task A（stale，无 change_summary）→ status=failed，change_summary 含「自动标记为 failed」
2. Task B（stale，change_summary 已存在）→ status=failed，change_summary 保持原文
3. Task C（fresh）→ status=running
4. 节点 f267f65c 状态：status=idle 且 load 减回（busy→idle 是兜底效果；load 需 <= 占用前）
"""

import asyncio
import sys

from sqlalchemy import text

from app.models.base import AsyncSessionLocal

EXPECTED_NODE_ID = "f267f65c-6594-4562-8b1e-7ba1166ae478"
EXPECTED_A_PREFIX = "e05386ff-5b0e-4376-ad54-5486edca8b1c"
EXPECTED_B_PREFIX = "9de28f63-49a3-43e4-8364-45ad1ae48dda"
EXPECTED_C_PREFIX = "579a4880-9d84-47dd-b8b1-153949694d7d"


async def main() -> int:
    failures: list[str] = []
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT id::text, title, status, change_summary "
                    "FROM tasks WHERE id IN (:a, :b, :c)"
                ),
                {"a": EXPECTED_A_PREFIX, "b": EXPECTED_B_PREFIX, "c": EXPECTED_C_PREFIX},
            )
        ).mappings().all()
        by_id = {r["id"]: r for r in rows}

        for prefix, expected_status, label in (
            (EXPECTED_A_PREFIX, "failed", "Task A (stale, 无 change_summary)"),
            (EXPECTED_B_PREFIX, "failed", "Task B (stale, change_summary 已存在)"),
            (EXPECTED_C_PREFIX, "running", "Task C (fresh)"),
        ):
            row = by_id.get(prefix)
            if row is None:
                failures.append(f"{label}: 任务不存在")
                continue
            actual = row["status"]
            print(f"[verify] {label}: status={actual} title={row['title']}")
            if actual != expected_status:
                failures.append(
                    f"{label}: 期望 status={expected_status}，实际 {actual}"
                )

        # 任务 A：change_summary 写入「自动标记为 failed」字样
        a = by_id.get(EXPECTED_A_PREFIX)
        if a and a["change_summary"]:
            if "自动标记为 failed" not in a["change_summary"]:
                failures.append(
                    f"Task A.change_summary 缺「自动标记为 failed」: {a['change_summary']!r}"
                )
        elif a:
            failures.append("Task A.change_summary 未写入")

        # 任务 B：change_summary 保持原文
        b = by_id.get(EXPECTED_B_PREFIX)
        if b:
            expected_summary = "本轮修改：fix bug #456（E2E 预置）"
            if b["change_summary"] != expected_summary:
                failures.append(
                    f"Task B.change_summary 被覆写: {b['change_summary']!r}"
                )

        # 节点状态：busy → idle（兜底释放）；load 应回到 0.0
        node_row = (
            await session.execute(
                text(
                    "SELECT id::text, status, load FROM agent_nodes WHERE id = :nid"
                ),
                {"nid": EXPECTED_NODE_ID},
            )
        ).mappings().first()
        if node_row is None:
            failures.append(f"节点 {EXPECTED_NODE_ID} 不存在")
        else:
            print(
                f"[verify] node {node_row['id']} status={node_row['status']} load={node_row['load']}"
            )
            if node_row["status"] != "idle":
                failures.append(
                    f"节点 status 期望 idle，实际 {node_row['status']}"
                )
            if float(node_row["load"]) > 0.0:
                # load 应该被减回 0（脚本预置时 +1；A/B 任务共同占用；每个失败 -1；共 -2 → 0）
                # 但 A 和 B 都指向该节点，每个 -1 应该是 0；如果 load > 0 说明兜底没跑
                failures.append(
                    f"节点 load 期望 0.0，实际 {node_row['load']}"
                )

    print()
    if failures:
        print("[verify] FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("[verify] OK: 全部 E2E 断言通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

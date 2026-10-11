"""rename_agent_type_to_code_and_add_unique_name

按 ADR-016 将 agent_nodes.agent_type 的存量值从 'programming' 改为 'code'，
并新增 partial unique index 约束同 agent_type 下 name 唯一。
迁移期间自动处理存量 (agent_type, name) 重复行：在 name 后追加 -dup1 / -dup2 ...

Revision ID: 20260826_node
Revises: 7a8b9c0d1e2f
Create Date: 2026-08-26 12:00:00.000000

"""
from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = "20260826_node"
down_revision: str | None = "7a8b9c0d1e2f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """执行 agent_type 值迁移 + name 唯一性约束。

    步骤：
    1) 找出 (agent_type, name) 重复行，给非首条追加 -dupN 后缀。
    2) UPDATE 所有 agent_type='programming' 行为 'code'。
    3) 新增 partial unique index ux_agent_nodes_agent_type_name。
    """
    conn = op.get_bind()

    # 1) 处理 (agent_type, name) 重复行：按 (agent_type, name) 分组，
    #    每组保留第一条，其余按 created_at 升序追加 -dup1、-dup2 ...
    dup_rows = conn.execute(
        text(
            """
            SELECT id, agent_type, name, created_at
            FROM agent_nodes
            WHERE (agent_type, name) IN (
                SELECT agent_type, name
                FROM agent_nodes
                GROUP BY agent_type, name
                HAVING COUNT(*) > 1
            )
            ORDER BY agent_type, name, created_at ASC
            """
        )
    ).fetchall()

    rename_count = 0
    seen: set[tuple[str, str]] = set()
    for row in dup_rows:
        key = (row.agent_type, row.name)
        if key not in seen:
            seen.add(key)
            # 这一组的第一条保持原 name；后续同组行才需要改名
            continue
        # 找下一个可用的 -dupN 后缀
        suffix = 1
        new_name = f"{row.name}-dup{suffix}"
        while conn.execute(
            text(
                "SELECT 1 FROM agent_nodes WHERE agent_type=:a AND name=:n"
            ),
            {"a": row.agent_type, "n": new_name},
        ).first() is not None:
            suffix += 1
            new_name = f"{row.name}-dup{suffix}"
        conn.execute(
            text("UPDATE agent_nodes SET name=:n WHERE id=:i"),
            {"n": new_name, "i": row.id},
        )
        rename_count += 1

    if rename_count:
        print(
            f"[agent_node_display_name] renamed {rename_count} "
            "legacy duplicate (agent_type, name) rows"
        )

    # 2) 把 agent_type='programming' 改成 'code'
    updated = conn.execute(
        text("UPDATE agent_nodes SET agent_type='code' WHERE agent_type='programming'")
    ).rowcount
    if updated:
        print(
            f"[agent_node_display_name] migrated {updated} "
            "agent_type rows from 'programming' to 'code'"
        )

    # 3) partial unique index：同 agent_type 下 name 唯一
    op.create_index(
        "ux_agent_nodes_agent_type_name",
        "agent_nodes",
        ["agent_type", "name"],
        unique=True,
        postgresql_where=text("name IS NOT NULL"),
    )


def downgrade() -> None:
    """回滚：删除 partial unique index，把 code 改回 programming。

    重复行重命名不回滚（没有记录原始映射），需人工核对。
    """
    op.drop_index("ux_agent_nodes_agent_type_name", table_name="agent_nodes")
    op.execute("UPDATE agent_nodes SET agent_type='programming' WHERE agent_type='code'")

"""跨 Agent 现场迁移服务测试（ADR-034）。"""

from __future__ import annotations

import uuid
import zipfile
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_node import AgentNode
from app.models.dispatch_decision import DispatchDecision
from app.models.system_settings import SystemSettings
from app.models.task import Task, TaskStatus
from app.models.task_building_session import TaskBuildingSession
from app.models.task_chain_context import TaskChainContext
from app.services.task_handoff_service import (
    build_handoff_payload,
    get_chain_context,
    resolve_bundle_download,
    upsert_chain_context,
)


@pytest_asyncio.fixture(autouse=True)
async def _clean_tables(db_session: AsyncSession) -> AsyncGenerator[None, None]:
    """清理表（bundle 暂存目录由 pytest tmp_path 自动回收）。"""
    await db_session.execute(delete(DispatchDecision))
    await db_session.execute(delete(TaskChainContext))
    await db_session.execute(TaskBuildingSession.__table__.update().values(task_id=None))
    await db_session.execute(delete(TaskBuildingSession))
    await db_session.execute(delete(Task))
    await db_session.execute(delete(AgentNode))
    await db_session.execute(delete(SystemSettings))
    yield


async def _make_completed_task(
    db_session: AsyncSession,
    *,
    title: str,
    root_task_id: uuid.UUID | None,
    export_files: dict[str, bytes] | None = None,
) -> Task:
    """创建已完成任务；export_files 非空时写入产物目录 export.zip。"""
    task = Task(
        title=title,
        agent_type="code",
        status=TaskStatus.COMPLETED.value,
        progress=100,
        root_task_id=root_task_id,
        specification={"spec": "# s", "tasks": "# t", "checklist": "# c"},
    )
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)

    if export_files is not None:
        from app.core.config import get_settings

        task_dir = Path(get_settings().artifact_cache_dir) / str(task.id)
        task_dir.mkdir(parents=True, exist_ok=True)
        zip_path = task_dir / "export.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, content in export_files.items():
                archive.writestr(name, content)
    return task


async def test_upsert_and_get_chain_context(db_session: AsyncSession) -> None:
    """上下文按链覆盖更新：同 root 二次 upsert 覆盖为最新内容。"""
    root_task = Task(
        title="根任务",
        agent_type="code",
        status=TaskStatus.COMPLETED.value,
        progress=100,
        specification={"spec": "# s", "tasks": "# t", "checklist": "# c"},
    )
    db_session.add(root_task)
    await db_session.commit()
    await db_session.refresh(root_task)

    source_1 = uuid.uuid4()
    source_2 = uuid.uuid4()

    await upsert_chain_context(db_session, root_task.id, "第一版上下文", source_1)
    await upsert_chain_context(db_session, root_task.id, "第二版上下文", source_2)

    context = await get_chain_context(db_session, root_task.id)
    assert context == "第二版上下文"
    records = list(
        (await db_session.execute(__import__("sqlalchemy").select(TaskChainContext))).scalars()
    )
    assert len(records) == 1
    assert records[0].source_task_id == source_2


async def test_build_handoff_payload_with_context_and_site(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """链上有完成任务的现场包与上下文 → bundle 生成并注入 project_context.md。"""
    from app.core.config import get_settings

    monkeypatch.setattr(
        "app.services.task_handoff_service._bundle_dir", lambda: tmp_path / "handoff"
    )

    root_task = Task(
        title="根任务",
        agent_type="code",
        status=TaskStatus.COMPLETED.value,
        progress=100,
        specification={"spec": "# s", "tasks": "# t", "checklist": "# c"},
    )
    db_session.add(root_task)
    await db_session.commit()
    await db_session.refresh(root_task)

    completed = await _make_completed_task(
        db_session,
        title="旧任务",
        root_task_id=root_task.id,
        export_files={"login.html": "<html></html>", "docs/spec.md": "# 规范"},
    )
    await upsert_chain_context(db_session, root_task.id, "项目上下文记忆内容", completed.id)

    new_task = Task(
        title="迭代任务",
        agent_type="code",
        status=TaskStatus.PENDING.value,
        root_task_id=root_task.id,
        specification={"spec": "# s", "tasks": "# t", "checklist": "# c"},
    )
    db_session.add(new_task)
    await db_session.commit()
    await db_session.refresh(new_task)

    handoff = await build_handoff_payload(db_session, new_task)

    assert handoff is not None
    assert handoff["context_md"] == "项目上下文记忆内容"
    assert handoff["bundle_path"].startswith(f"/api/v1/tasks/{new_task.id}/handoff-bundle?token=")

    # 从 bundle_path 提取 token 验证一次性下载与包内容
    token = handoff["bundle_path"].split("token=")[1]
    bundle_file = resolve_bundle_download(str(new_task.id), token)
    assert bundle_file is not None
    # 一次性：二次下载返回 None
    assert resolve_bundle_download(str(new_task.id), token) is None

    with zipfile.ZipFile(bundle_file) as archive:
        names = set(archive.namelist())
    assert "login.html" in names
    assert "docs/spec.md" in names
    assert "project_context.md" in names
    with zipfile.ZipFile(bundle_file) as archive:
        assert archive.read("project_context.md").decode() == "项目上下文记忆内容"

    _ = get_settings()  # 保持 settings 引用（目录断言经由 monkeypatch 生效）


async def test_build_handoff_payload_no_history_returns_none(
    db_session: AsyncSession,
) -> None:
    """链上无历史现场且无上下文（首个任务）→ 不构造 handoff。"""
    task = Task(
        title="首个任务",
        agent_type="code",
        status=TaskStatus.PENDING.value,
        specification={"spec": "# s", "tasks": "# t", "checklist": "# c"},
    )
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)

    handoff = await build_handoff_payload(db_session, task)
    assert handoff is None


async def test_bundle_download_invalid_token(db_session: AsyncSession) -> None:
    """错误 token / 任务不匹配 → 拒绝下载。"""
    task = Task(
        title="t",
        agent_type="code",
        status=TaskStatus.PENDING.value,
        specification={"spec": "# s", "tasks": "# t", "checklist": "# c"},
    )
    db_session.add(task)
    await db_session.commit()

    assert resolve_bundle_download(str(task.id), "bad-token") is None

"""测试共享 fixtures。"""

import asyncio
import os
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import get_settings
from app.models.agent_node import AgentNode
from app.models.base import engine
from app.models.dispatch_decision import DispatchDecision
from app.models.llm_provider import LLMProvider
from app.models.task import Task
from app.models.task_building_session import TaskBuildingSession
from app.models.task_chain_context import TaskChainContext
from app.models.task_orchestrator_todo import TaskOrchestratorTodo

# ---------------------------------------------------------------------------
# 测试库隔离（修复：此前测试直接清开发库，用户配置的模型/任务被误删）
# 优先级：TEST_DATABASE_URL 环境变量（独立测试库）> 开发库 + 会话级数据守卫
# 必须在任何 app 模块加载前生效（base.py 模块级即创建引擎）。
# ---------------------------------------------------------------------------
if os.environ.get("TEST_DATABASE_URL"):
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]


async def _noop_background_loop(stop_event: asyncio.Event) -> None:
    """占位后台循环，立即等待停止事件，用于替换真实的调度器/心跳任务。"""
    await stop_event.wait()


def _noop_start_background_task() -> tuple[asyncio.Event, asyncio.Task[None]]:
    """创建占位后台任务，避免测试事件循环被真实后台任务占用。"""
    stop_event = asyncio.Event()
    task = asyncio.create_task(_noop_background_loop(stop_event))
    return stop_event, task


def _noop_start_reverse_notify() -> asyncio.Task[None]:
    """占位中枢重启反连任务（ADR-035），避免测试期间真实反连调用。"""
    return asyncio.create_task(asyncio.sleep(0))


def _run_alembic_upgrade_head() -> None:
    """通过 alembic CLI 把测试库 schema 升级到最新版本。

    比 ``Base.metadata.create_all`` 更可靠：create_all 不会 ALTER 已存在
    的表，而 alembic 迁移是幂等且按 version 增量。
    """
    backend_dir = Path(__file__).resolve().parent.parent
    env = os.environ.copy()
    # 强制把 DATABASE_URL 注入子进程，避免测试与生产配置串
    env["DATABASE_URL"] = get_settings().database_url
    env["REDIS_URL"] = get_settings().redis_url
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=str(backend_dir),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"alembic upgrade head failed:\nstdout={result.stdout}\nstderr={result.stderr}"
        )


@pytest.fixture(scope="session", autouse=True)
def _ensure_test_schema() -> None:
    """测试会话级 fixture：pytest 启动时跑一次 ``alembic upgrade head``。

    走 Alembic 比 ``Base.metadata.create_all`` 可靠：create_all 不会 ALTER
    已存在表，会导致老表缺新列（典型表现：INSERT 时报 UndefinedColumnError）。
    """
    _run_alembic_upgrade_head()
    yield


# ---------------------------------------------------------------------------
# 会话级数据守卫：测试前备份 / 结束后恢复（开发库保护）
# ---------------------------------------------------------------------------

#: 与 db_session 清理序列对应的守卫表（恢复按外键安全顺序：被引用者先行）
#: 注：删除按 ``reversed(_GUARDED_TABLES)`` 执行，故**引用他表的子表要排在被引用表之后**：
#: - task_orchestrator_todos 引用 tasks（ADR-061 M5d）→ 排在 Task 之后；
#: - ADR-067 修订：todos 还可挂构建会话（session_id FK）→ 删除序须先 todos 后
#:   sessions，即 forward 序 sessions 排在 todos **之前**；恢复插入（forward）
#:   同序天然满足：Task 先于 sessions/todos，sessions 先于 todos。
_GUARDED_TABLES: list[type[Any]] = [
    AgentNode,
    Task,
    DispatchDecision,
    TaskChainContext,
    TaskBuildingSession,
    TaskOrchestratorTodo,
    LLMProvider,
]


async def _snapshot_guarded_tables() -> dict[type[Any], list[dict[str, Any]]]:
    """备份守卫表全量行（独立引擎，不影响测试连接池）。"""
    snapshot: dict[type[Any], list[dict[str, Any]]] = {}
    guard_engine = create_async_engine(get_settings().database_url, echo=False)
    try:
        async with guard_engine.connect() as conn:
            for model in _GUARDED_TABLES:
                result = await conn.execute(model.__table__.select())
                snapshot[model] = [dict(row._mapping) for row in result]
    finally:
        await guard_engine.dispose()
    return snapshot


async def _restore_guarded_tables(
    snapshot: dict[type[Any], list[dict[str, Any]]],
) -> None:
    """清空守卫表并按备份原样恢复（含测试期间被清理的用户数据）。

    tasks ↔ task_building_sessions 存在双向外键（session_id / task_id），
    单趟按序插入必然有一个方向先于被引用行：先剥离关联字段插入全部行，
    再回填关联字段（ADR-041 修复：真实库存在关联时恢复报外键冲突）。

    恢复完成后逐表校验行数：与快照不一致直接抛错——守卫静默失效会让
    用户配置（模型密钥/节点身份）在多次跑测试后永久丢失（2026-10-02
    事故：qwen 两条 LLM 配置在两次回归后消失，恢复失败无人知晓）。
    """
    guard_engine = create_async_engine(get_settings().database_url, echo=False)
    try:
        async with guard_engine.begin() as conn:
            await conn.execute(update(TaskBuildingSession).values(task_id=None))
            await conn.execute(update(Task).values(session_id=None))
            for model in reversed(_GUARDED_TABLES):
                await conn.execute(model.__table__.delete())
            for model in _GUARDED_TABLES:
                rows = snapshot.get(model) or []
                if not rows:
                    continue
                if model is Task:
                    rows = [{**r, "session_id": None} for r in rows]
                elif model is TaskBuildingSession:
                    rows = [{**r, "task_id": None} for r in rows]
                await conn.execute(model.__table__.insert(), rows)
            # 回填双向关联字段（两表行均已就位）
            for model, field_name in ((Task, "session_id"), (TaskBuildingSession, "task_id")):
                for row in snapshot.get(model) or []:
                    linked = row.get(field_name)
                    if linked is None:
                        continue
                    await conn.execute(
                        update(model)
                        .where(model.id == row["id"])
                        .values(**{field_name: linked})
                    )
        # 恢复行数校验（独立连接，确认已提交状态）
        async with guard_engine.connect() as conn:
            for model in _GUARDED_TABLES:
                expected = len(snapshot.get(model) or [])
                actual = (await conn.execute(select(func.count()).select_from(model.__table__))).scalar() or 0
                if expected != actual:
                    raise RuntimeError(
                        f"守卫表 {model.__tablename__} 恢复不完整："
                        f"备份 {expected} 行，实际 {actual} 行——请检查恢复逻辑"
                    )
    finally:
        await guard_engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def _guard_dev_data() -> None:
    """会话级数据守卫：测试前备份业务表，会话结束后原样恢复。

    背景：db_session 每个测试会清空 llm_providers/tasks 等表，而当前
    测试与开发共用数据库（opc 用户无建库权限，暂无法独立测试库）——
    不加守卫时多次跑测试会清掉用户配置的模型/任务（事故：2026-09-21
    用户添加的模型被全量测试清空）。配置 TEST_DATABASE_URL 指向独立
    测试库后，本守卫自动退化为备份/恢复空表的空转。
    """
    snapshot = asyncio.run(_snapshot_guarded_tables())
    yield
    asyncio.run(_restore_guarded_tables(snapshot))


@pytest.fixture(autouse=True)
def _stub_tool_call_log_record(monkeypatch) -> list[dict[str, Any]]:
    """默认将执行链路采集 ``tool_call_log_service.record`` 替换为内存 spy。

    record 内部自开独立会话写库，单测中若放行会直连开发库写入测试垃圾行
    并拖慢执行（ADR-047）；需要真实写库行为的用例（服务层集成测试）可在
    用例内重新 patch ``AsyncSessionLocal`` 后恢复原实现。
    """
    from app.services import tool_call_log_service

    calls: list[dict[str, Any]] = []

    async def fake_record(**kwargs: Any) -> None:
        calls.append(kwargs)

    # 保留原实现供需要真实写库行为的用例恢复（见 tests/services/test_tool_call_log_service.py）
    monkeypatch.setattr(
        tool_call_log_service,
        "_real_record",
        tool_call_log_service.record,
        raising=False,
    )
    monkeypatch.setattr(tool_call_log_service, "record", fake_record)
    return calls


@pytest.fixture(scope="module")
def ws_client() -> TestClient:
    """创建模块级同步 WebSocket 测试客户端。

    所有 WebSocket 测试复用同一个 TestClient 与事件循环，避免跨
    TestClient 实例的 asyncpg 连接循环冲突。

    为避免真实调度器/心跳后台任务干扰测试并导致事件循环争用，创建应用前
    使用 ``unittest.mock.patch`` 将 ``app.main`` 中的启动函数替换为占位实现，
    确保 lifespan 中引用到的是 noop 版本。
    """
    with (
        patch("app.main.start_scheduler", _noop_start_background_task),
        patch("app.main.start_heartbeat_monitor", _noop_start_background_task),
        patch("app.main.start_reverse_notify", _noop_start_reverse_notify),
    ):
        # 延迟导入 create_app，确保 patch 在 lifespan 闭包绑定前生效
        from app.main import create_app

        app = create_app()
        # 模块级 fixture 跨测试模块复用全局引擎连接池：上一模块（已关闭的
        # 事件循环）残留的池化连接会在 TestClient lifespan 启动触达 DB 时
        # 抛 'NoneType' has no attribute 'send'——必须在进入 TestClient
        # 之前清空连接池（asyncio.run 提供一次性事件循环执行 dispose）
        asyncio.run(engine.dispose())
        with TestClient(app) as client:
            yield client
            client.portal.call(engine.dispose)


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    """创建测试数据库会话，函数结束后回滚所有变更。

    使用 savepoint 模式：被测函数内部调用 session.commit() 时只会释放 savepoint，
    外层事务在测试结束时统一 rollback，避免污染开发数据库。

    Schema 同步：会话级 ``_ensure_test_schema`` 已 alembic upgrade head，
    本 fixture 仅负责按测试清理数据 + 提供 savepoint 事务。
    """
    settings = get_settings()
    engine = create_async_engine(settings.database_url, echo=False)

    async with engine.connect() as conn:
        await conn.begin()
        # 清理历史数据，确保每个测试在干净状态下运行。
        # dispatch_decisions / task_chain_contexts / task_building_sessions
        # 均外键引用 tasks：先清审计与链上下文表、解除双向会话关联，
        # 再按 tasks -> sessions 顺序清理。
        await conn.execute(delete(DispatchDecision))
        await conn.execute(delete(TaskChainContext))
        # 中枢编排器待办外键引用 tasks（ADR-061 M5d）：必须先于 tasks 删除
        await conn.execute(delete(TaskOrchestratorTodo))
        await conn.execute(update(TaskBuildingSession).values(task_id=None))
        await conn.execute(update(Task).values(session_id=None))
        await conn.execute(delete(Task))
        await conn.execute(delete(TaskBuildingSession))
        await conn.execute(delete(AgentNode))
        await conn.execute(delete(LLMProvider))

        session = AsyncSession(
            conn,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        async with session:
            yield session
            await session.rollback()

        await conn.rollback()

    await engine.dispose()

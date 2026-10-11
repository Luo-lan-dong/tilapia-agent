"""AI 智能调度决策测试（ADR-033）：白名单校验、回退与审计。"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_node import AgentNode, NodeStatus
from app.models.dispatch_decision import DispatchDecision
from app.models.system_settings import SystemSettings
from app.models.task import Task, TaskStatus
from app.services import dispatch_decision_service
from app.services.dispatch_decision_service import (
    blocking_reasons,
    build_hold_reason,
    decide_assignments,
    diagnose_eligibility,
    eligibility_gap,
    is_node_eligible,
    list_available_nodes,
    node_available_memory_gb,
    node_cpu_percent,
    node_load_score,
    pick_lowest_load_eligible,
)
from app.services.scheduler import dispatch_pending_tasks


def _unique_token() -> str:
    """生成唯一的注册令牌。"""
    return f"token-{uuid.uuid4().hex}"


async def _create_agent_node(
    session: AsyncSession,
    agent_type: str,
    load: float = 0.0,
) -> AgentNode:
    """创建测试用在线节点。"""
    node = AgentNode(
        name=f"test-agent-{uuid.uuid4().hex}",
        agent_type=agent_type,
        status=NodeStatus.IDLE.value,
        load=load,
        registration_token=_unique_token(),
    )
    session.add(node)
    await session.commit()
    await session.refresh(node)
    return node


async def _create_task(
    session: AsyncSession,
    agent_type: str = "code",
) -> Task:
    """创建测试用 pending 任务。"""
    task = Task(
        title=f"test-task-{uuid.uuid4().hex}",
        agent_type=agent_type,
        status=TaskStatus.PENDING.value,
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)
    return task


async def _set_ai_dispatch_enabled(session: AsyncSession, enabled: bool) -> None:
    """设置 AI 智能调度开关（system_settings 单行）。"""
    result = await session.execute(
        select(SystemSettings).order_by(SystemSettings.created_at.asc()).limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = SystemSettings(ai_dispatch_enabled=enabled)
        session.add(row)
    else:
        row.ai_dispatch_enabled = enabled
    await session.commit()


@pytest_asyncio.fixture(autouse=True)
async def _clean_tables(db_session: AsyncSession) -> AsyncGenerator[None, None]:
    """每个测试前清空任务、节点与决策审计表。"""
    await db_session.execute(delete(DispatchDecision))
    await db_session.execute(delete(Task))
    await db_session.execute(delete(AgentNode))
    await db_session.execute(delete(SystemSettings))
    yield


@pytest.fixture(autouse=True)
def _ai_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """环境变量兜底值固定为关闭，隔离宿主 .env 影响。"""
    monkeypatch.setenv("AI_DISPATCH_ENABLED", "false")


async def test_ai_disabled_multi_candidates_uses_lowest_load(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """开关关闭时多候选任务走规则路径（负载最低），LLM 零调用。"""
    calls: list[str] = []

    async def _fail_llm(*args: object, **kwargs: object) -> str:
        calls.append("called")
        raise AssertionError("AI 关闭时不应调用 LLM")

    monkeypatch.setattr(dispatch_decision_service, "_call_llm_json", _fail_llm)
    low = await _create_agent_node(db_session, "code", load=0.0)
    await _create_agent_node(db_session, "code", load=5.0)
    task = await _create_task(db_session)

    dispatched = await dispatch_pending_tasks(db_session)

    assert dispatched == 1
    refreshed = await db_session.get(Task, task.id)
    assert refreshed is not None
    assert refreshed.assigned_node_id == low.id
    assert calls == []


async def test_ai_enabled_single_candidate_skips_llm(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """开关开启但候选唯一时走快速路径：零 LLM 调用 + rule 审计。"""
    await _set_ai_dispatch_enabled(db_session, True)

    async def _fail_llm(*args: object, **kwargs: object) -> str:
        raise AssertionError("候选唯一时不应调用 LLM")

    monkeypatch.setattr(dispatch_decision_service, "_call_llm_json", _fail_llm)
    node = await _create_agent_node(db_session, "code", load=3.0)
    task = await _create_task(db_session)

    dispatched = await dispatch_pending_tasks(db_session)

    assert dispatched == 1
    refreshed = await db_session.get(Task, task.id)
    assert refreshed is not None
    assert refreshed.assigned_node_id == node.id
    rows = list((await db_session.execute(select(DispatchDecision))).scalars().all())
    assert len(rows) == 1
    assert rows[0].decision_source == "rule"
    assert rows[0].validated is False
    assert rows[0].fallback is False


async def test_ai_enabled_adopts_valid_choice(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AI 输出白名单内节点时被采纳，审计记录 validated=True/source=ai。"""
    await _set_ai_dispatch_enabled(db_session, True)
    low = await _create_agent_node(db_session, "code", load=0.0)
    preferred = await _create_agent_node(db_session, "code", load=4.0)
    task = await _create_task(db_session)

    async def _fake_llm(session: AsyncSession, user_prompt: str) -> str:
        return json.dumps(
            {
                "decisions": [
                    {
                        "task_id": str(task.id),
                        "node_id": str(preferred.id),
                        "reason": "任务需要高配节点",
                    }
                ]
            }
        )

    monkeypatch.setattr(dispatch_decision_service, "_call_llm_json", _fake_llm)
    dispatched = await dispatch_pending_tasks(db_session)

    assert dispatched == 1
    refreshed = await db_session.get(Task, task.id)
    assert refreshed is not None
    # 采纳 AI 选择（非负载最低节点）
    assert refreshed.assigned_node_id == preferred.id
    assert low.id != preferred.id
    rows = list((await db_session.execute(select(DispatchDecision))).scalars().all())
    assert len(rows) == 1
    assert rows[0].decision_source == "ai"
    assert rows[0].validated is True
    assert rows[0].fallback is False
    assert rows[0].ai_choice_node_id == preferred.id
    assert rows[0].final_node_id == preferred.id
    assert rows[0].reason is not None and "高配" in rows[0].reason


async def test_ai_hallucinated_node_falls_back_without_retry(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AI 幻觉（白名单外节点）时回退负载最低节点，且只调用一次 LLM（不重试）。"""
    await _set_ai_dispatch_enabled(db_session, True)
    low = await _create_agent_node(db_session, "code", load=0.0)
    await _create_agent_node(db_session, "code", load=2.0)
    task = await _create_task(db_session)
    call_count = {"n": 0}

    async def _hallucinate_llm(session: AsyncSession, user_prompt: str) -> str:
        call_count["n"] += 1
        return json.dumps(
            {
                "decisions": [
                    {"task_id": str(task.id), "node_id": str(uuid.uuid4()), "reason": "幻觉"}
                ]
            }
        )

    monkeypatch.setattr(dispatch_decision_service, "_call_llm_json", _hallucinate_llm)
    dispatched = await dispatch_pending_tasks(db_session)

    assert dispatched == 1
    refreshed = await db_session.get(Task, task.id)
    assert refreshed is not None
    assert refreshed.assigned_node_id == low.id
    assert call_count["n"] == 1  # 不重试
    rows = list((await db_session.execute(select(DispatchDecision))).scalars().all())
    assert len(rows) == 1
    assert rows[0].fallback is True
    assert rows[0].validated is False
    assert rows[0].decision_source == "rule"


async def test_ai_unparseable_output_falls_back(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AI 输出无法解析 JSON 时回退规则分配。"""
    await _set_ai_dispatch_enabled(db_session, True)
    low = await _create_agent_node(db_session, "code", load=0.0)
    await _create_agent_node(db_session, "code", load=1.0)
    task = await _create_task(db_session)

    async def _garbage_llm(session: AsyncSession, user_prompt: str) -> str:
        return "这不是 JSON"

    monkeypatch.setattr(dispatch_decision_service, "_call_llm_json", _garbage_llm)
    dispatched = await dispatch_pending_tasks(db_session)

    assert dispatched == 1
    refreshed = await db_session.get(Task, task.id)
    assert refreshed is not None
    assert refreshed.assigned_node_id == low.id
    rows = list((await db_session.execute(select(DispatchDecision))).scalars().all())
    assert len(rows) == 1
    assert rows[0].fallback is True


async def test_ai_llm_exception_falls_back(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM 调用异常时本轮回退规则分配，调度循环不中断。"""
    await _set_ai_dispatch_enabled(db_session, True)
    low = await _create_agent_node(db_session, "code", load=0.0)
    await _create_agent_node(db_session, "code", load=1.5)
    task = await _create_task(db_session)

    async def _boom_llm(session: AsyncSession, user_prompt: str) -> str:
        raise RuntimeError("provider down")

    monkeypatch.setattr(dispatch_decision_service, "_call_llm_json", _boom_llm)
    dispatched = await dispatch_pending_tasks(db_session)

    assert dispatched == 1
    refreshed = await db_session.get(Task, task.id)
    assert refreshed is not None
    assert refreshed.assigned_node_id == low.id
    rows = list((await db_session.execute(select(DispatchDecision))).scalars().all())
    assert len(rows) == 1
    assert rows[0].fallback is True
    assert rows[0].reason is not None and "provider down" in rows[0].reason


async def test_decide_assignments_context_is_slim(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """决策 prompt 仅包含候选节点关键字段（上下文瘦身）。"""
    node = await _create_agent_node(db_session, "code", load=1.0)
    task = await _create_task(db_session)
    captured: dict[str, str] = {}

    async def _capture_llm(session: AsyncSession, user_prompt: str) -> str:
        captured["prompt"] = user_prompt
        return json.dumps(
            {"decisions": [{"task_id": str(task.id), "node_id": str(node.id), "reason": "ok"}]}
        )

    monkeypatch.setattr(dispatch_decision_service, "_call_llm_json", _capture_llm)
    candidates = await list_available_nodes(db_session, "code")
    decisions = await decide_assignments(db_session, {str(task.id): candidates})

    assert str(node.id) in captured["prompt"]
    assert node.name in captured["prompt"]
    assert "metrics_snapshot" not in captured["prompt"]
    decision = decisions[str(task.id)]
    assert decision.validated is True
    assert decision.final_node is not None and decision.final_node.id == node.id
    # 审计快照不含全量 metrics；ADR-059 起含准入判据字段（eligible/gap 可见性）
    assert decision.candidates_snapshot[0].keys() == {
        "node_id",
        "name",
        "agent_type",
        "load",
        "cpu_percent",
        "memory_percent",
        "available_memory_gb",
        "load_score",
        "eligible",
    }


# ---------------- ADR-059 D1：准入双门槛（纯函数，无需数据库）----------------


def _node_with_metrics(
    *,
    available_gb: float | None = 4.0,
    load_score: float | None = 20.0,
    cpu_percent: float | None = 10.0,
    memory_percent: float | None = 40.0,
) -> AgentNode:
    """构造仅带 metrics 的节点对象（不落库，供准入判定单测使用）。"""
    snapshot: dict = {}
    if available_gb is not None or memory_percent is not None:
        snapshot["memory"] = {"percent": memory_percent, "available_gb": available_gb}
    if load_score is not None:
        snapshot["load_score"] = {"score": load_score}
    if cpu_percent is not None:
        snapshot["cpu"] = {"percent": cpu_percent}
    return AgentNode(
        id=uuid.uuid4(),
        name=f"node-{uuid.uuid4().hex[:6]}",
        agent_type="code",
        status=NodeStatus.IDLE.value,
        load=0.0,
        registration_token=_unique_token(),
        metrics_snapshot=snapshot,
    )


def test_eligible_true_when_both_gates_pass() -> None:
    """可用内存 2.5G 且负载 45 → 可自主。"""
    assert is_node_eligible(_node_with_metrics(available_gb=2.5, load_score=45.0)) is True
    assert eligibility_gap(_node_with_metrics(available_gb=2.5, load_score=45.0)) is None


def test_ineligible_when_available_memory_at_or_below_1gb() -> None:
    """可用内存 ≤1G 即不可自主（即使负载很低）——任一不满足即不可自主。"""
    node = _node_with_metrics(available_gb=0.9, load_score=10.0)
    assert is_node_eligible(node) is False
    assert "可用内存" in (eligibility_gap(node) or "")

    boundary = _node_with_metrics(available_gb=1.0, load_score=10.0)
    assert is_node_eligible(boundary) is False


def test_ineligible_when_load_score_at_or_above_80() -> None:
    """综合负载 ≥80 即不可自主（即使内存充足）。"""
    node = _node_with_metrics(available_gb=8.0, load_score=80.0)
    assert is_node_eligible(node) is False
    assert "综合负载" in (eligibility_gap(node) or "")

    assert is_node_eligible(_node_with_metrics(available_gb=8.0, load_score=79.9)) is True


def test_blocking_reasons_lists_all_gaps() -> None:
    """UT-04：两项都不达标时**并列**给出两个缺口（不是只报首要那个）。

    这是 ADR-059 卫生项修复的行为：避免用户「解决内存后刷新才发现负载也不达标」。
    """
    node = _node_with_metrics(available_gb=0.5, load_score=95.0)

    reasons = blocking_reasons(node)

    assert len(reasons) == 2
    assert any("可用内存" in r for r in reasons)
    assert any("综合负载" in r for r in reasons)
    # 首要缺口仍是第一条（与 eligibility_gap 对齐，供紧凑展示）
    assert eligibility_gap(node) == reasons[0]


def test_blocking_reasons_empty_when_eligible() -> None:
    """可自主节点无缺口。"""
    assert blocking_reasons(_node_with_metrics(available_gb=3.0, load_score=20.0)) == []
    assert blocking_reasons(_node_with_metrics(available_gb=1.5, load_score=79.9)) == []


def test_load_score_missing_falls_back_to_cpu() -> None:
    """UT-08：`load_score` 缺失但 CPU 可读 → 用 CPU 百分比兜底（口径降级）。"""
    from app.core.config import get_settings

    threshold = get_settings().dispatch_cpu_threshold_percent

    busy = _node_with_metrics(available_gb=4.0, load_score=None, cpu_percent=threshold + 5)
    assert node_load_score(busy) is None
    assert is_node_eligible(busy) is False
    gap = eligibility_gap(busy)
    assert gap is not None and "CPU" in gap and "口径降级" in gap
    assert blocking_reasons(busy) == [gap]

    idle = _node_with_metrics(available_gb=4.0, load_score=None, cpu_percent=threshold - 5)
    assert is_node_eligible(idle) is True


def test_missing_load_score_and_cpu_passes() -> None:
    """指标全缺（旧节点）→ 无法判定即放行，不因缺字段拒派。"""
    node = _node_with_metrics(available_gb=4.0, load_score=None, cpu_percent=None)
    assert is_node_eligible(node) is True
    assert blocking_reasons(node) == []


def test_memory_gate_still_wins_when_load_score_missing() -> None:
    """内存不足时即使负载分缺失也判不可自主（内存门槛不被兜底绕过）。"""
    node = _node_with_metrics(available_gb=0.4, load_score=None, cpu_percent=5.0)
    assert is_node_eligible(node) is False
    assert node_cpu_percent(node) == 5.0


def test_absolute_memory_not_percentage() -> None:
    """ADR-059 核心诉求：判据是绝对可用内存，与内存百分比无关。

    4G 机器占用 82.7%（剩 ~0.7G）→ 不可自主；
    8 核机器占用 43%（剩 2.3G）→ 可自主。
    """
    small = _node_with_metrics(available_gb=0.7, load_score=31.6, memory_percent=82.7)
    larger = _node_with_metrics(available_gb=2.3, load_score=12.3, memory_percent=43.0)
    assert is_node_eligible(small) is False
    assert is_node_eligible(larger) is True


def test_missing_metrics_legacy_node_passes() -> None:
    """旧节点无新指标 → 无法判定即放行（兼容，不因缺字段拒派）。"""
    legacy = _node_with_metrics(available_gb=None, load_score=None)
    assert node_available_memory_gb(legacy) is None
    assert node_load_score(legacy) is None
    assert is_node_eligible(legacy) is True


def test_metrics_type_tolerance() -> None:
    """metrics 字段为字符串/异常值时按缺失处理，不抛异常。"""
    node = _node_with_metrics()
    node.metrics_snapshot = {"memory": {"available_gb": "3.5"}, "load_score": {"score": "12.0"}}
    assert node_available_memory_gb(node) == 3.5
    assert node_load_score(node) == 12.0

    node.metrics_snapshot = {"memory": {"available_gb": "abc"}, "load_score": {"score": {}}}
    assert node_available_memory_gb(node) is None
    assert node_load_score(node) is None


def test_build_hold_reason_reports_gap_and_target() -> None:
    """排队原因含缺口数值；指定节点时聚焦该节点。"""
    blocked = {
        "node_id": "n1",
        "name": "笔记本",
        "online": True,
        "available_memory_gb": 0.8,
        "load_score": 31.6,
        "slots_free": 2,
        "eligible": False,
        "blocking": "可用内存 0.8GB ≤ 1.0GB",
    }
    ok = {
        "node_id": "n2",
        "name": "code",
        "online": True,
        "available_memory_gb": 3.0,
        "load_score": 12.0,
        "slots_free": 1,
        "eligible": True,
        "blocking": None,
    }
    reason = build_hold_reason([blocked, ok])
    assert "笔记本" in reason and "0.8GB" in reason

    targeted = build_hold_reason([blocked, ok], target_node_id="n1")
    assert "指定节点「笔记本」" in targeted and "0.8GB" in targeted

    offline_target = build_hold_reason([blocked, ok], target_node_id="n1", target_offline=True)
    assert "离线" in offline_target

    assert build_hold_reason([]) == "无匹配该类型的 Agent 节点在线"


def test_pick_lowest_load_eligible_skips_blocked() -> None:
    """规则回退只在可自主节点中取负载最低者。"""
    blocked = {
        "node_id": "n1",
        "name": "a",
        "online": True,
        "available_memory_gb": 0.5,
        "load_score": 1.0,
        "slots_free": 2,
        "eligible": False,
        "blocking": "可用内存 0.5GB ≤ 1.0GB",
    }
    ok_high = {
        "node_id": "n2",
        "name": "b",
        "online": True,
        "available_memory_gb": 3.0,
        "load_score": 50.0,
        "slots_free": 2,
        "eligible": True,
        "blocking": None,
    }
    ok_low = {
        "node_id": "n3",
        "name": "c",
        "online": True,
        "available_memory_gb": 3.0,
        "load_score": 10.0,
        "slots_free": 2,
        "eligible": True,
        "blocking": None,
    }
    assert pick_lowest_load_eligible([blocked, ok_high, ok_low]) == "n3"
    assert pick_lowest_load_eligible([blocked]) is None


async def test_diagnose_eligibility_marks_slots_full(
    db_session: AsyncSession,
) -> None:
    """槽位已满的节点在诊断中标 slots_full（缺口描述可解释）。"""
    node = await _create_agent_node(db_session, "code")
    node.max_concurrent_tasks = 1
    running = await _create_task(db_session, "code")
    running.status = TaskStatus.RUNNING.value
    running.assigned_node_id = node.id
    await db_session.commit()

    diagnostics = await diagnose_eligibility(db_session, "code")
    row = next(d for d in diagnostics if d["node_id"] == str(node.id))
    assert row["eligible"] is False
    assert row["slots_free"] == 0
    assert row["blocking"] is not None and "槽位" in row["blocking"]


async def test_list_eligible_nodes_filters_by_gate(
    db_session: AsyncSession,
) -> None:
    """双门槛在候选预筛之上再收一层：内存/负载不达标者不出现在 eligible 中。"""
    from app.services.dispatch_decision_service import list_eligible_nodes

    healthy = await _create_agent_node(db_session, "code")
    healthy.metrics_snapshot = {"memory": {"available_gb": 4.0}, "load_score": {"score": 10.0}}
    starved = await _create_agent_node(db_session, "code")
    starved.metrics_snapshot = {"memory": {"available_gb": 0.5}, "load_score": {"score": 10.0}}
    busy = await _create_agent_node(db_session, "code")
    busy.metrics_snapshot = {"memory": {"available_gb": 4.0}, "load_score": {"score": 92.0}}
    await db_session.commit()

    eligible = await list_eligible_nodes(db_session, "code")
    ids = {node.id for node in eligible}
    assert healthy.id in ids
    assert starved.id not in ids
    assert busy.id not in ids

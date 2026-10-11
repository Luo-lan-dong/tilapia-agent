"""任务执行模型选择与凭据下发测试（ADR-029 / ADR-030）。

覆盖创建链路落库（U1/I1/I2）、schema 归一化（U2）与派发 payload
凭据组装（U3-U6，含日志脱敏红线）。
"""

import logging
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Task, TaskStatus
from app.schemas.task import ExecutorModelSelection, TaskCreateRequest, normalize_executor_model
from app.services.llm_provider_service import LLMProviderConfig
from app.services.scheduler import _build_executor_model_entry
from app.services.task_service import TaskService

_SPEC = {
    "spec": "# Spec\n开发登录接口",
    "tasks": "# Tasks\n1. 设计数据库",
    "checklist": "# Checklist\n- 接口可访问",
}


# ---------------------------------------------------------------------------
# Schema 归一化（U2）
# ---------------------------------------------------------------------------


def test_executor_model_rejects_invalid_source() -> None:
    """非法 source 在 schema 层即被拒绝（U2）。"""
    with pytest.raises(ValidationError):
        ExecutorModelSelection.model_validate({"source": "evil"})


def test_normalize_executor_model_none_passthrough() -> None:
    """缺省选择原样返回 None（视为 hub 默认）。"""
    assert normalize_executor_model(None) is None


# ---------------------------------------------------------------------------
# 创建链路落库（U1/I1/I2）
# ---------------------------------------------------------------------------


async def test_create_task_executor_model_defaults_to_none(db_session: AsyncSession) -> None:
    """缺省 executor_model 落库为 NULL，响应亦为 None（U1）。"""
    request = TaskCreateRequest(requirement="开发登录接口", specification=_SPEC)

    created = await TaskService().create_task(db_session, request)

    assert created.executor_model is None
    task = await db_session.get(Task, created.id)
    assert task is not None and task.executor_model is None


async def test_create_task_with_hub_model_override(db_session: AsyncSession) -> None:
    """hub 来源覆盖模型 ID 时按请求落库（I2）。"""
    request = TaskCreateRequest(
        requirement="开发登录接口",
        specification=_SPEC,
        executor_model={"source": "hub", "model": "deepseek-reasoner"},
    )

    created = await TaskService().create_task(db_session, request)

    task = await db_session.get(Task, created.id)
    assert task is not None
    assert task.executor_model == {"source": "hub", "model": "deepseek-reasoner"}


# -----------------------------------------------------------------------------
# 派发 payload 凭据组装（U3-U6）
# -----------------------------------------------------------------------------


def _make_task_with_executor_model(executor_model: dict | None) -> Task:
    """构造携带执行模型选择的内存 Task 对象（无需落库）。"""
    return Task(
        id=uuid.uuid4(),
        title="executor-model-task",
        agent_type="code",
        status=TaskStatus.ASSIGNED.value,
        executor_model=executor_model,
    )


def _hub_config() -> LLMProviderConfig:
    """中枢活跃 LLM 配置样本。"""
    return LLMProviderConfig(
        provider="deepseek",
        api_key="sk-test-secret-123",
        base_url="https://api.deepseek.com",
        model="deepseek-chat",
    )


async def test_build_executor_model_entry_hub_injects_credentials(caplog) -> None:
    """hub 来源派发 payload 注入实时读取的中枢凭据，日志不含 Key 明文（U3）。"""
    task = _make_task_with_executor_model({"source": "hub", "model": None})
    provider_mock = AsyncMock(return_value=_hub_config())

    with (
        patch("app.services.scheduler.LLMProviderService.get_active_provider", provider_mock),
        caplog.at_level(logging.DEBUG),
    ):
        entry = await _build_executor_model_entry(MagicMock(), task, {})

    provider_mock.assert_awaited_once()
    assert entry["source"] == "hub"
    assert entry["provider"] == "deepseek"
    assert entry["model"] == "deepseek-chat"  # 未指定时跟随系统配置
    assert entry["api_key"] == "sk-test-secret-123"
    assert entry["base_url"] == "https://api.deepseek.com"
    # 安全红线：派发链路日志不得出现 api_key 明文（U3）
    assert "sk-test-secret-123" not in caplog.text


async def test_build_executor_model_entry_hub_config_missing_degrades() -> None:
    """中枢 LLM 配置缺失时降级：hub 来源但不带凭据（U4）。"""
    task = _make_task_with_executor_model({"source": "hub", "model": None})

    with patch(
        "app.services.scheduler.LLMProviderService.get_active_provider",
        AsyncMock(side_effect=ValueError("LLM Provider 配置不存在，请先完成配置")),
    ):
        entry = await _build_executor_model_entry(MagicMock(), task, {})

    assert entry["source"] == "hub"
    assert "api_key" not in entry and "base_url" not in entry
    assert "provider" not in entry


async def test_build_executor_model_entry_reuses_llm_cache() -> None:
    """同一调度循环内复用 LLM 配置缓存，多个任务只读取一次配置。"""
    cache: dict = {}
    provider_mock = AsyncMock(return_value=_hub_config())

    with patch("app.services.scheduler.LLMProviderService.get_active_provider", provider_mock):
        first = await _build_executor_model_entry(
            MagicMock(), _make_task_with_executor_model({"source": "hub"}), cache
        )
        second = await _build_executor_model_entry(
            MagicMock(), _make_task_with_executor_model({"source": "hub"}), cache
        )

    provider_mock.assert_awaited_once()
    assert first["api_key"] == second["api_key"] == "sk-test-secret-123"


async def test_build_executor_model_entry_auto_resolves_to_hub() -> None:
    """ADR-039：source=auto 且任务无附件时解析为使用中模型（entry.source 归一 hub）。"""
    task = _make_task_with_executor_model({"source": "auto"})
    provider_mock = AsyncMock(return_value=_hub_config())

    with patch("app.services.scheduler.LLMProviderService.get_active_provider", provider_mock):
        entry = await _build_executor_model_entry(MagicMock(), task, {})

    provider_mock.assert_awaited_once()
    assert entry["source"] == "hub"
    assert entry["provider"] == "deepseek"
    assert entry["api_key"] == "sk-test-secret-123"


async def test_build_executor_model_entry_auto_with_attachments_picks_vision_row(
    db_session: AsyncSession,
) -> None:
    """ADR-039：auto + 带图任务优先派给勾选支持图片的自定义模型行。

    entry.source 归一为 custom 并携带该行凭据与 supports_vision=True。
    """
    from app.models.llm_provider import LLMProvider

    vision_row = LLMProvider(
        provider="qwen-vl",
        api_key="sk-vision-key",
        base_url="https://vl.example.com/v1",
        model="qwen-vl-max",
        is_active=False,
        supports_vision=True,
    )
    db_session.add(vision_row)
    await db_session.commit()
    await db_session.refresh(vision_row)

    task = _make_task_with_executor_model({"source": "auto"})
    entry = await _build_executor_model_entry(db_session, task, {}, has_attachments=True)

    assert entry["source"] == "custom"
    assert entry["model"] == "qwen-vl-max"
    assert entry["api_key"] == "sk-vision-key"
    assert entry["base_url"] == "https://vl.example.com/v1"
    assert entry["supports_vision"] is True


async def test_build_executor_model_entry_auto_with_attachments_no_candidate(
    db_session: AsyncSession,
) -> None:
    """ADR-039：带图任务但无视觉模型候选时降级使用中模型（supports_vision=False）。"""
    task = _make_task_with_executor_model({"source": "auto"})
    provider_mock = AsyncMock(return_value=_hub_config())

    with patch("app.services.scheduler.LLMProviderService.get_active_provider", provider_mock):
        entry = await _build_executor_model_entry(db_session, task, {}, has_attachments=True)

    assert entry["source"] == "hub"
    assert entry["supports_vision"] is False
    assert entry["api_key"] == "sk-test-secret-123"


async def test_build_executor_model_entry_custom_row_carries_vision(
    db_session: AsyncSession,
) -> None:
    """ADR-039：custom 来源行凭据附带 supports_vision 标记（Agent 决定是否带图）。"""
    from app.models.llm_provider import LLMProvider

    row = LLMProvider(
        provider="vl-provider",
        api_key="sk-k",
        base_url="https://api.example.com/v1",
        model="vl-model",
        is_active=False,
        supports_vision=True,
    )
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)

    task = _make_task_with_executor_model(
        {"source": "custom", "provider_id": str(row.id), "model": None}
    )
    entry = await _build_executor_model_entry(db_session, task, {})

    assert entry["supports_vision"] is True


# ---------------- ADR-030：custom 来源（指定自定义模型行） ----------------


async def test_build_executor_model_entry_custom_row(db_session: AsyncSession) -> None:
    """custom 来源按 provider_id 查行注入凭据（U7）。"""
    from app.models.llm_provider import LLMProvider

    row = LLMProvider(
        provider="硅基流动",
        api_key="sk-sf-key",
        base_url="https://api.siliconflow.cn/v1",
        model="deepseek-ai/DeepSeek-V3",
        is_active=False,
    )
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)

    task = _make_task_with_executor_model(
        {"source": "custom", "provider_id": str(row.id), "model": None}
    )
    entry = await _build_executor_model_entry(db_session, task, {})

    assert entry["source"] == "custom"
    assert entry["provider"] == "硅基流动"
    assert entry["model"] == "deepseek-ai/DeepSeek-V3"
    assert entry["api_key"] == "sk-sf-key"
    assert entry["base_url"] == "https://api.siliconflow.cn/v1"


async def test_build_executor_model_entry_custom_row_missing(db_session: AsyncSession) -> None:
    """custom 指定的模型行已删除：降级不带凭据 + 告警（U8）。"""
    task = _make_task_with_executor_model(
        {"source": "custom", "provider_id": str(uuid.uuid4()), "model": "deepseek-chat"}
    )
    entry = await _build_executor_model_entry(db_session, task, {})

    assert entry == {"source": "custom", "model": "deepseek-chat"}


async def test_build_executor_model_entry_custom_id_missing(db_session: AsyncSession) -> None:
    """custom 缺少 provider_id：降级不带凭据。"""
    task = _make_task_with_executor_model({"source": "custom", "provider_id": None})
    entry = await _build_executor_model_entry(db_session, task, {})

    assert entry == {"source": "custom", "model": None}


def test_normalize_executor_model_custom() -> None:
    """custom 来源归一化落库 provider_id。"""
    pid = uuid.uuid4()
    normalized = normalize_executor_model(
        ExecutorModelSelection(source="custom", provider_id=pid, model="m-1")
    )
    assert normalized == {"source": "custom", "provider_id": str(pid), "model": "m-1"}

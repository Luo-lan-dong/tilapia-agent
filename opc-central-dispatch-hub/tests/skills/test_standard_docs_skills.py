"""规则文档技能测试（ADR-048 P1）：generate_standard_doc 生成入库。"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, patch

import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.standard_doc import StandardDoc
from app.skills.base import SkillContext
from app.skills.standard_docs import GenerateStandardDocSkill, ListStandardDocsSkill

_GENERATED_CONTENT = "# 技术栈规范\n\n## 通用编码要求\n\n- 命名清晰"


@pytest_asyncio.fixture(autouse=True)
async def _clean_docs(db_session: AsyncSession) -> AsyncGenerator[None, None]:
    """清理文档表（保留预置模板）。"""
    await db_session.execute(delete(StandardDoc).where(StandardDoc.is_builtin.is_(False)))
    yield
    await db_session.execute(delete(StandardDoc).where(StandardDoc.is_builtin.is_(False)))


def _mock_llm(content: str) -> AsyncMock:
    """构造 LLM 客户端 mock：get_active_provider 返回配置对象、chat_completion 返回内容。"""
    provider = AsyncMock()
    provider.model = "deepseek-chat"
    with patch(
        "app.services.llm_provider_service.LLMProviderService.get_active_provider",
        new=AsyncMock(return_value=provider),
    ):
        yield provider


async def test_generate_standard_doc_saves_to_library(db_session: AsyncSession) -> None:
    """降级分支：LLM 未按 <doc> 标签输出时全文作为单文档入库。"""
    skill = GenerateStandardDocSkill()
    context = SkillContext(
        args={
            "name": "gen-fastapi-standards",
            "tech_stack": "FastAPI + PostgreSQL",
            "apply_when": "写/改后端 Python 代码前",
        },
        messages=[],
    )

    with (
        patch(
            "app.services.llm_provider_service.LLMProviderService.get_active_provider",
            new_callable=AsyncMock,
        ) as provider_mock,
        patch("app.clients.deepseek.DeepSeekClient") as client_cls,
    ):
        provider_mock.return_value = AsyncMock()
        client_cls.return_value.chat_completion = AsyncMock(return_value=_GENERATED_CONTENT)
        client_cls.return_value.close = AsyncMock()
        events = [event async for event in skill.execute(db_session, context)]

    result = next(event["data"] for event in events if event["event"] == "_result")
    assert result.success is True
    assert result.data["docs"] == ["gen-fastapi-standards"]

    row = (
        await db_session.execute(
            select(StandardDoc).where(StandardDoc.name == "gen-fastapi-standards")
        )
    ).scalar_one()
    assert row.content == _GENERATED_CONTENT
    assert row.category == "coding-standard"
    assert row.is_builtin is False


async def test_generate_standard_doc_rejects_duplicate_name(db_session: AsyncSession) -> None:
    """同名文档已存在时失败并提示换名。"""
    db_session.add(StandardDoc(name="dup-doc", content="# 已存在", is_builtin=False))
    await db_session.commit()

    skill = GenerateStandardDocSkill()
    context = SkillContext(
        args={"name": "dup-doc", "tech_stack": "FastAPI"},
        messages=[],
    )

    with (
        patch(
            "app.services.llm_provider_service.LLMProviderService.get_active_provider",
            new_callable=AsyncMock,
        ) as provider_mock,
        patch("app.clients.deepseek.DeepSeekClient") as client_cls,
    ):
        provider_mock.return_value = AsyncMock()
        client_cls.return_value.chat_completion = AsyncMock(return_value="# 新内容")
        client_cls.return_value.close = AsyncMock()
        events = [event async for event in skill.execute(db_session, context)]

    result = next(event["data"] for event in events if event["event"] == "_result")
    assert result.success is False
    assert "已存在" in result.summary


async def test_generate_standard_doc_requires_args(db_session: AsyncSession) -> None:
    """缺 name/tech_stack 时直接失败，不调用 LLM。"""
    skill = GenerateStandardDocSkill()
    context = SkillContext(args={"name": "only-name"}, messages=[])
    events = [event async for event in skill.execute(db_session, context)]

    result = next(event["data"] for event in events if event["event"] == "_result")
    assert result.success is False
    assert "参数不完整" in result.summary
    rows = list((await db_session.execute(select(StandardDoc))).scalars().all())
    assert all(doc.is_builtin for doc in rows)


async def test_generate_standard_doc_splits_into_multiple_docs(
    db_session: AsyncSession,
) -> None:
    """按类别拆分：LLM 输出 <doc> 标签序列时逐份入库。"""
    llm_output = (
        '<doc name="fastapi-react" apply_when="每次写/改代码前"># 通用规范\n正文A</doc>'
        '<doc name="fastapi-react-backend" apply_when="写/改后端代码前"># 后端规范\n正文B</doc>'
        '<doc name="fastapi-react-frontend" apply_when="写/改前端代码前"># 前端规范\n正文C</doc>'
    )
    skill = GenerateStandardDocSkill()
    context = SkillContext(
        args={"name": "fastapi-react", "tech_stack": "FastAPI + React"},
        messages=[],
    )

    with (
        patch(
            "app.services.llm_provider_service.LLMProviderService.get_active_provider",
            new_callable=AsyncMock,
        ) as provider_mock,
        patch("app.clients.deepseek.DeepSeekClient") as client_cls,
    ):
        provider_mock.return_value = AsyncMock()
        client_cls.return_value.chat_completion = AsyncMock(return_value=llm_output)
        client_cls.return_value.close = AsyncMock()
        events = [event async for event in skill.execute(db_session, context)]

    result = next(event["data"] for event in events if event["event"] == "_result")
    assert result.success is True
    assert result.data["docs"] == [
        "fastapi-react",
        "fastapi-react-backend",
        "fastapi-react-frontend",
    ]
    rows = list(
        (
            await db_session.execute(
                select(StandardDoc).where(StandardDoc.category == "coding-standard")
            )
        ).scalars()
    )
    assert {row.name for row in rows} == {
        "fastapi-react",
        "fastapi-react-backend",
        "fastapi-react-frontend",
    }
    backend = next(row for row in rows if row.name == "fastapi-react-backend")
    assert backend.apply_when == "写/改后端代码前"


async def test_list_standard_docs_returns_inventory(db_session: AsyncSession) -> None:
    """list_standard_docs 返回文档清单（含预置与自定义）。"""
    db_session.add(StandardDoc(name="custom-a", content="# a", is_builtin=False))
    await db_session.commit()

    skill = ListStandardDocsSkill()
    context = SkillContext(args={}, messages=[])
    events = [event async for event in skill.execute(db_session, context)]

    result = next(event["data"] for event in events if event["event"] == "_result")
    assert result.success is True
    names = {doc["name"] for doc in result.data["docs"]}
    assert "custom-a" in names

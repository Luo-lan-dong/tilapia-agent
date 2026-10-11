"""规则文档技能：查询文档库 / 按技术栈生成规范集（ADR-048 修订）。"""

import re
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.standard_doc import StandardDoc
from app.skills.base import Skill, SkillContext, SkillResult

#: 生成文档的 <doc> 标签解析（name/apply_when 属性 + Markdown 正文）
_DOC_TAG_PATTERN = re.compile(
    r'<doc\s+name="(?P<name>[^"]+)"(?:\s+apply_when="(?P<apply_when>[^"]*)")?\s*>\s*'
    r"(?P<content>.*?)</doc\s*>",
    re.DOTALL,
)


class ListStandardDocsSkill(Skill):
    """查询中枢规则文档库清单。"""

    name = "list_standard_docs"
    display_name = "查询规则文档库"
    description = (
        "查询中枢规则文档库中有哪些开发规范文档（名称/用途/何时加载/版本）。"
        "用户询问有哪些规范、编排任务考虑是否下发开发规则、为任务挑选规则文档时调用。"
    )
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    returns = "文档清单：[{name, category, description, apply_when, version}]"

    async def execute(
        self,
        session: AsyncSession,
        context: SkillContext,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """查询文档库并产出结果事件。"""
        result = await session.execute(select(StandardDoc).order_by(StandardDoc.name))
        docs = list(result.scalars().all())
        if docs:
            lines = []
            for doc in docs:
                apply_when = doc.apply_when or "（未标注）"
                lines.append(
                    f"- {doc.name}（v{doc.version}，{'预置' if doc.is_builtin else '自定义'}）："
                    f"{doc.description or '无说明'}；何时加载：{apply_when}"
                )
            summary = (
                f"规则文档库当前共 {len(docs)} 份文档：\n"
                + "\n".join(lines)
                + "\n创建任务时可通过 rule_doc_ids 选择下发；不选择则节点不加载任何规范。"
            )
        else:
            summary = "规则文档库为空，可在「文档」管理页创建或上传规范文档。"
        yield {
            "event": "_result",
            "data": SkillResult(
                success=True,
                summary=summary,
                data={
                    "docs": [
                        {
                            "id": str(doc.id),
                            "name": doc.name,
                            "category": doc.category,
                            "description": doc.description,
                            "apply_when": doc.apply_when,
                            "version": doc.version,
                            "is_builtin": doc.is_builtin,
                        }
                        for doc in docs
                    ]
                },
            ),
        }


_GENERATE_DOC_SYSTEM_PROMPT = """你是「OPC 超级个体」平台的开发规范撰写专家。
请为执行开发任务的 AI Agent 节点撰写**一套**开发规范文档（Markdown），
面向 AI Agent 执行者：指令明确、可判定、无废话。

## 拆分要求（重要）

**必须按职责类别拆分为多份独立文档**，禁止把所有规范写在一份里——子节点
按"动工哪部分读哪份"按需加载，一份大杂烩会导致全量阅读浪费 token。按
本次技术栈实际涉及面选择类别，每类一份，不涉及的类别不要凑数：

- 通用编码规范（命名/结构/错误处理/变更纪律，任何项目都需要，必出）；
- 后端规范（目录结构/路由与模型约定/配置管理，涉及后端时出，文件名含 backend）；
- 前端规范（组件组织/状态管理/样式约定，涉及前端时出，文件名含 frontend）；
- API 接口规范（URL 设计/请求响应结构/错误码/分页约定，有对外接口时出，文件名含 api）；
- 日志规范（级别/结构化格式/脱敏，后端服务类项目出，文件名含 logging）；
- 数据库规范（表/字段/索引/迁移约定，涉及数据库时出，文件名含 database）。

## 输出格式（严格遵守）

每份文档用一个 doc 标签包裹，依次输出全部文档：

<doc name="文档名（英文小写连字符，统一以调用方给定的前缀开头）" apply_when="何时必须加载（如：写/改后端 Python 代码前）">
# 文档标题
（Markdown 正文）
</doc>

标签外不要输出任何解释、开场白、结尾语或代码块围栏。

## 内容要求

1. 面向 AI 执行者：指令明确、可判定，每份控制在 80 行以内。
2. 技术栈特定规范要具体（目录结构、命名、配置管理、测试要求、错误处理约定）。
3. 通用编码规范中包含决策纪律：与任务规范出现重大偏离（环境/依赖冲突、
   需引入新技术栈、发现设计缺陷）时停下上报；小修小补不写决策文档。
4. 全文中文。
"""


def _parse_doc_tags(raw_output: str) -> list[dict]:
    """解析 <doc> 标签序列；无标签时降级为单文档（全文，apply_when=None）。"""
    docs = [
        {
            "name": match.group("name").strip(),
            "apply_when": (match.group("apply_when") or "").strip() or None,
            "content": match.group("content").strip(),
        }
        for match in _DOC_TAG_PATTERN.finditer(raw_output)
    ]
    return docs


class GenerateStandardDocSkill(Skill):
    """按技术栈生成一套拆分后的开发规范文档集并保存到规则文档库。"""

    name = "generate_standard_doc"
    display_name = "生成开发规范"
    description = (
        "根据用户确认的技术栈生成一套开发规范文档（按职责类别自动拆分为多份"
        "独立文档：通用编码/后端/前端/API/日志/数据库等）并保存到规则文档库"
        "（供 create_task 绑定后随任务下发给节点，节点按需读取）。文档库中没有"
        "合适规范时调用；生成前先与用户确认技术栈。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "文档名前缀（英文小写连字符，如 fastapi-react），"
                    "拆分后的各份文档以此为前缀命名（如 fastapi-react-backend）"
                ),
            },
            "tech_stack": {
                "type": "string",
                "description": "已与用户确认的技术栈描述，如 '后端 FastAPI + PostgreSQL 16，前端 React + Ant Design'",
            },
            "apply_when": {
                "type": "string",
                "description": "可选，各份文档统一的'何时必须加载'默认值（LLM 可按份覆盖）",
            },
            "focus": {
                "type": "string",
                "description": "可选，用户对规范的额外要求或侧重点",
            },
        },
        "required": ["name", "tech_stack"],
    }
    returns = "生成结果：成功入库的文档清单与跳过清单；LLM 失败时说明原因"

    async def execute(
        self,
        session: AsyncSession,
        context: SkillContext,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """调用 LLM 生成规范文档集并逐份入库。"""
        from app.clients.deepseek import DeepSeekClient
        from app.core.errors import BusinessError
        from app.schemas.standard_doc import StandardDocCreate
        from app.services.llm_provider_service import LLMProviderService
        from app.services.standard_doc_service import StandardDocService

        name_prefix = str(context.args.get("name") or "").strip()
        tech_stack = str(context.args.get("tech_stack") or "").strip()
        default_apply_when = str(context.args.get("apply_when") or "").strip() or None
        focus = str(context.args.get("focus") or "").strip()
        if not name_prefix or not tech_stack:
            yield {
                "event": "_result",
                "data": SkillResult(
                    success=False,
                    summary="参数不完整：必须提供文档名前缀（name）与已确认的技术栈（tech_stack）。",
                ),
            }
            return

        user_prompt = (
            f"文档名前缀：{name_prefix}\n"
            f"技术栈：{tech_stack}\n"
            f"额外要求：{focus or '无'}\n"
            "请按拆分要求生成该技术栈的开发规范文档集。"
        )
        yield {
            "event": "status",
            "data": {"message": f"正在生成规范文档集（前缀 {name_prefix}）…"},
        }
        try:
            config = await LLMProviderService().get_active_provider(session)
            client = DeepSeekClient(config, scene="standard_doc_gen")
            try:
                raw_output = await client.chat_completion(_GENERATE_DOC_SYSTEM_PROMPT, user_prompt)
            finally:
                await client.close()
        except Exception as exc:  # noqa: BLE001 — 失败如实回传 LLM
            yield {
                "event": "_result",
                "data": SkillResult(
                    success=False,
                    summary=f"规范文档生成失败：{exc}",
                ),
            }
            return

        parsed = _parse_doc_tags(raw_output)
        if not parsed:
            # 降级：LLM 未按标签输出时全文作为单文档入库
            fallback_content = raw_output.strip()
            if not fallback_content:
                yield {
                    "event": "_result",
                    "data": SkillResult(success=False, summary="规范文档生成失败：LLM 返回为空。"),
                }
                return
            parsed = [
                {
                    "name": name_prefix,
                    "apply_when": default_apply_when,
                    "content": fallback_content,
                }
            ]

        service = StandardDocService()
        saved: list[str] = []
        skipped: list[str] = []
        for index, item in enumerate(parsed, start=1):
            doc_name = item["name"] or f"{name_prefix}-{index}"
            try:
                doc = await service.create_doc(
                    session,
                    StandardDocCreate(
                        name=doc_name,
                        category="coding-standard",
                        description=f"{tech_stack} 开发规范（AI 生成，{doc_name}）",
                        apply_when=item["apply_when"] or default_apply_when,
                        content=item["content"],
                    ),
                )
            except BusinessError:
                skipped.append(doc_name)
                continue
            saved.append(doc.name)
        if not saved:
            yield {
                "event": "_result",
                "data": SkillResult(
                    success=False,
                    summary=(
                        "规范文档保存失败：全部文档名已存在"
                        f"（{', '.join(skipped)}）。请换前缀重试，"
                        f"或提示用户在「文档」页编辑现有文档。"
                    ),
                ),
            }
            return
        summary = f"已生成并保存 {len(saved)} 份规范文档：{', '.join(saved)}。"
        if skipped:
            summary += f"同名已存在跳过：{', '.join(skipped)}。"
        summary += "可在 create_task 时通过 rule_doc_names 绑定。"
        yield {
            "event": "_result",
            "data": SkillResult(
                success=True,
                summary=summary,
                data={"docs": saved, "skipped": skipped},
            ),
        }

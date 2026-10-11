"""提示词关键内容断言（ADR-048 P1）：编排流程 + 后端先行。"""

from app.agents.orchestrator_prompts import ORCHESTRATOR_BASE_PROMPT
from app.agents.spec_builder_prompts import (
    DOC_CHECKLIST_SYSTEM_PROMPT,
    DOC_SPEC_SYSTEM_PROMPT,
    DOC_TASKS_SYSTEM_PROMPT,
)


def test_orchestrator_prompt_contains_engineering_flow() -> None:
    """编排提示词包含工程化询问（利弊）、技术栈推荐与规则包绑定引导。"""
    assert "技术栈" in ORCHESTRATOR_BASE_PROMPT
    assert "工程化" in ORCHESTRATOR_BASE_PROMPT
    assert "token" in ORCHESTRATOR_BASE_PROMPT  # 利弊说明需提及 token 消耗
    assert "list_standard_docs" in ORCHESTRATOR_BASE_PROMPT
    assert "rule_doc_names" in ORCHESTRATOR_BASE_PROMPT
    assert "generate_standard_doc" in ORCHESTRATOR_BASE_PROMPT


def test_orchestrator_prompt_requires_todo_before_dispatch() -> None:
    """问题修复：创建任务后、派发前必须先列中枢待办（todo_write）。"""
    assert "todo_write" in ORCHESTRATOR_BASE_PROMPT
    assert "action=add" in ORCHESTRATOR_BASE_PROMPT
    assert "smart_dispatch" in ORCHESTRATOR_BASE_PROMPT


def test_spec_prompt_enforces_api_contract_first() -> None:
    """spec 提示词：带后端项目先定义接口契约。"""
    assert "接口契约" in DOC_SPEC_SYSTEM_PROMPT


def test_tasks_prompt_enforces_backend_first() -> None:
    """tasks 提示词：后端任务在前，接口调通前不安排前端对接。"""
    assert "后端先行" in DOC_TASKS_SYSTEM_PROMPT
    assert "接口" in DOC_TASKS_SYSTEM_PROMPT
    assert "前端对接" in DOC_TASKS_SYSTEM_PROMPT


def test_checklist_prompt_enforces_joint_debug_gate() -> None:
    """checklist 提示词：后端接口联调通过是前端对接验收的前置检查点。"""
    assert "所有后端接口联调通过" in DOC_CHECKLIST_SYSTEM_PROMPT

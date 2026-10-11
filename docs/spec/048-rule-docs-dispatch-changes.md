# 开发规则文档库与可选规则包下发 Changes（ADR-048）

> 按规则要求：编码前仅创建骨架，**边开发边实时登记**，不得完成后一次性补记。

## 关联决策文档

- ADR：[048-rule-docs-dispatch.md](../adr/048-rule-docs-dispatch.md)
- Spec：[048-rule-docs-dispatch-spec.md](048-rule-docs-dispatch-spec.md)
- Harness：[048-rule-docs-dispatch-harness.md](048-rule-docs-dispatch-harness.md)

## 变更清单

| 日期 | 文件 | 类型（增/修/删） | 函数/类级明细 | 作用说明 |
| --- | --- | --- | --- | --- |
| 2026-09-25 | docs/adr/048-rule-docs-dispatch.md | 增 | - | ADR 决策文档 |
| 2026-09-25 | docs/spec/048-rule-docs-dispatch-spec.md | 增 | - | Spec |
| 2026-09-25 | docs/spec/048-rule-docs-dispatch-harness.md | 增 | - | Harness |
| 2026-09-25 | docs/spec/048-rule-docs-dispatch-changes.md | 增 | - | 本清单骨架 |
| 2026-09-25 | docs/INDEX.md | 修 | 1.1/1.2/1.3/六 四处登记 | 决策文档索引 |
| 2026-09-25 | hub/app/models/standard_doc.py | 增 | `StandardDoc` | 规则文档表模型（name 唯一/version/is_builtin） |
| 2026-09-25 | hub/app/models/task.py | 修 | `Task.rule_docs` | 新增 JSONB 字段（内容快照，NULL=不下发） |
| 2026-09-25 | hub/app/models/__init__.py | 修 | 导出 `StandardDoc` | 模型注册 |
| 2026-09-25 | hub/app/core/rule_doc_templates.py | 增 | `RuleDocTemplate`/`BUILTIN_RULE_DOC_TEMPLATES` | 两份节点裁剪版模板 seed 内容 |
| 2026-09-25 | hub/alembic/versions/20260925_rule_docs.py | 增 | upgrade/downgrade/_seed_builtin_templates | 建表+tasks 加列+幂等 seed |
| 2026-09-25 | hub/app/schemas/standard_doc.py | 增 | Create/Update/Response/Detail/ListResponse | 文档 CRUD Schema |
| 2026-09-25 | hub/app/schemas/task.py | 修 | `TaskCreateRequest.rule_doc_ids` | 创建任务绑定文档参数 |
| 2026-09-25 | hub/app/services/standard_doc_service.py | 增 | `StandardDocService`(CRUD/`snapshot_rule_docs`)、`build_rule_docs_payload`、`resolve_rule_doc_download`、`sanitize_file_name` | CRUD+内容快照+token 注册表+payload 构造 |
| 2026-09-25 | hub/app/api/v1/endpoints/standard_docs.py | 增 | list/get/create/update/delete + `download_rule_doc` | /docs CRUD 与 token 下载端点 |
| 2026-09-25 | hub/app/api/v1/api.py | 修 | 注册 `/docs` 前缀 | 路由接入 |
| 2026-09-25 | hub/app/api/deps.py | 修 | `get_standard_doc_service` | 依赖注入 |
| 2026-09-25 | hub/app/core/errors.py | 修 | `DOC_NOT_FOUND/DOC_NAME_DUPLICATE/DOC_DOWNLOAD_TOKEN_INVALID`(206001-3) | 错误码 |
| 2026-09-25 | hub/app/services/task_service.py | 修 | `create_task` 快照写入 chain_fields；`dispatch_task` 注入 rule_docs | 创建锁定+手动派发下发 |
| 2026-09-25 | hub/app/services/scheduler.py | 修 | `_assign_plan` 注入 rule_docs | 自动派发下发 |
| 2026-09-25 | hub/app/skills/standard_docs.py | 增 | `ListStandardDocsSkill` | AI 查询文档库技能 |
| 2026-09-25 | hub/app/skills/registry.py | 修 | 注册 `ListStandardDocsSkill` | 技能注册表 |
| 2026-09-25 | hub/tests/services/test_standard_docs.py | 增 | 7 项 UT/IT | service/快照/payload/下载闭环 |
| 2026-09-25 | hub/tests/api/v1/test_standard_docs.py | 增 | 5 项 IT | API CRUD/409/下载 401/200 |
| 2026-09-25 | agent/app/models/schemas.py | 修 | `TaskRuleDocInfo`、`TaskDispatchPayload.rule_docs/rule_doc_blobs` | payload schema 扩展 |
| 2026-09-25 | agent/app/client/hub_client.py | 修 | `_download_rule_docs` + `_handle_task_dispatch` 接入 | 下载+sha256 校验+失败降级 |
| 2026-09-25 | agent/app/executor/workspace.py | 修 | `RuleDocEntry`/`build_agents_md`/`materialize_rule_docs` | 落盘 .tilacode/rules/ + AGENTS.md 索引生成 |
| 2026-09-25 | agent/app/executor/task_bridge.py | 修 | `execute_task` 接入落盘 | 执行链路接入 |
| 2026-09-25 | agent/tests/test_rule_docs.py | 增 | 5 项 UT | schema/索引/落盘/防越界/空回归 |
| 2026-09-25 | web/src/types/standardDoc.ts | 增 | - | 类型定义 |
| 2026-09-25 | web/src/api/standardDoc.ts | 增 | fetch/create/update/delete | API 封装 |
| 2026-09-25 | web/src/pages/Docs.tsx | 增 | `Docs` | 文档管理页（表格+编辑 Modal+预览+上传） |
| 2026-09-25 | web/src/App.tsx | 修 | `/docs` 路由 | 路由注册 |
| 2026-09-25 | web/src/components/AppLayout.tsx | 修 | 菜单项「文档」 | 侧边栏入口 |
| 2026-09-25 | hub/app/agents/orchestrator_prompts.py | 修 | `ORCHESTRATOR_BASE_PROMPT` 第 7 条 | P1 编排流程：需求讨论→技术栈确认推荐→工程化询问（含 token 利弊）→list_standard_docs/generate_standard_doc 绑定规则包 |
| 2026-09-25 | hub/app/skills/task_creation.py | 修 | `CreateTaskSkill` 参数 `rule_doc_names`+execute 按名解析/缺失提示/summary | create_task 绑定规则包（按文档名，缺省不绑定） |
| 2026-09-25 | hub/app/skills/standard_docs.py | 修 | 增 `GenerateStandardDocSkill`+`_GENERATE_DOC_SYSTEM_PROMPT` | 按技术栈生成规范文档并入库（同名冲突/缺参/LLM 失败降级） |
| 2026-09-25 | hub/app/skills/registry.py | 修 | 注册 `GenerateStandardDocSkill` | 技能注册表 |
| 2026-09-25 | hub/app/agents/spec_builder_prompts.py | 修 | DOC_SPEC/TASKS/CHECKLIST 三个 system prompt | "后端先行"：spec 接口契约先行、tasks 后端任务前置于前端对接（接口全调通为闸）、checklist 联调通过前置检查点 |
| 2026-09-25 | hub/tests/skills/test_create_task_skill.py | 修 | 增 `test_create_task_binds_rule_docs_by_name`/`test_create_task_without_rule_docs_keeps_null`+fixture 清 standard_docs | 规则包绑定传参与回归 |
| 2026-09-25 | hub/tests/skills/test_standard_docs_skills.py | 增 | 4 项（生成入库/同名冲突/缺参/清单查询，LLM mock） | P1 技能测试 |
| 2026-09-25 | hub/tests/agents/test_orchestrator_prompts_backend_first.py | 增 | 4 项提示词断言 | 编排流程+后端先行入 prompt 的守卫 |
| 2026-09-25 | hub/app/models/task.py | 修 | `Task.environment_requirements` | 环境要求清单 JSONB（P2，用户修正设计：由 AI 检查安装而非 Python 硬检查拒单） |
| 2026-09-25 | hub/alembic/versions/20260925_task_env_requirements.py | 增 | upgrade/downgrade | tasks.environment_requirements 迁移 |
| 2026-09-25 | hub/app/schemas/task.py | 修 | `TaskCreateRequest.environment_requirements`/`TaskResponse.rule_docs+environment_requirements` | 创建参数与响应字段 |
| 2026-09-25 | hub/app/services/task_service.py | 修 | create_task chain_fields 透传环境清单 | 创建链路 |
| 2026-09-25 | hub/app/services/scheduler.py | 修 | `_build_dispatch_message` 内联 `environment_requirements` | 派发下发（自动+手动共用） |
| 2026-09-25 | hub/app/skills/task_creation.py | 修 | `CreateTaskSkill` 参数 `environment` 透传 | AI 编排时按技术栈生成环境清单 |
| 2026-09-25 | hub/app/agents/orchestrator_prompts.py | 修 | 第 7 条 b：技术栈确认后生成环境要求清单 | 编排引导 |
| 2026-09-25 | agent/app/models/schemas.py | 修 | `EnvironmentRequirement`+payload `environment_requirements` | 内联清单 schema |
| 2026-09-25 | agent/app/executor/task_bridge.py | 修 | `_environment_prompt_part`（首次判断 `.tilacode/environment.md`）+`_build_prompt` 注入 | 首次注入检查/安装指令，迭代与同目录重跑跳过，改派新节点新目录自动重查 |
| 2026-09-25 | agent/tests/test_environment_requirements.py | 增 | 4 项（解析/首次指令/已就绪跳过/无清单回归） | P2 Agent 测试 |
| 2026-09-25 | hub/tests/services/test_task_env_requirements.py | 增 | 3 项（落库/NULL 回归/payload 内联） | P2 中枢测试 |
| 2026-09-25 | hub/tests/skills/test_create_task_skill.py | 修 | 增 `test_create_task_passes_environment_requirements` | 技能透传用例 |
| 2026-09-25 | hub/app/core/rule_doc_templates.py | 修 | 三份模板全重写 | 元规则①②逐节以 .trae/rules 为模板替换路径（含 {{STANDARDS_TABLE}} 占位）+ 环境检查文档模板（{{ENV_TABLE}}） |
| 2026-09-25 | hub/alembic/versions/20260925_meta_rules_v2.py | 增 | upsert seed | 内置元规则内容覆盖更新 |
| 2026-09-25 | hub/app/services/standard_doc_service.py | 修 | `build_rule_docs_payload` async+session 重构（元规则自动附加/清单渲染/target 字段/环境检查文档）；`snapshot_rule_docs` 过滤 meta-rule；辅助 `_render_standards_table/_render_env_table/_register_entry` | payload 分层：规范正文(standard)+元规则(meta-rule) |
| 2026-09-25 | hub/app/services/scheduler.py | 修 | `build_rule_docs_payload` await | async 签名适配 |
| 2026-09-25 | hub/app/services/task_service.py | 修 | 同上（手动派发） | 同上 |
| 2026-09-25 | agent/app/models/schemas.py | 修 | `TaskRuleDocInfo.target`（standard/meta-rule） | 落盘目标字段 |
| 2026-09-25 | agent/app/executor/workspace.py | 修 | `TILACODE_CONFIG_TEMPLATE.instructions`、`spec_docs_relative_dir`→specs/、`materialize_rule_docs` target 分目录+`_upsert_instructions`、`build_agents_md` 重写 | 目录结构修订落地 |
| 2026-09-25 | agent/app/executor/task_bridge.py | 修 | specs/ 提示文本×3、环境块指向 environment-check.md | 路径与文档指向同步 |
| 2026-09-25 | agent/tests/test_rule_docs.py | 修 | 重写（分目录/instructions/AGENTS.md 断言） | 测试适配 |
| 2026-09-25 | agent/tests/test_workspace.py、tests/test_task_bridge.py | 修 | specs/ 路径断言 | ADR-037 路径修订适配 |
| 2026-09-25 | agent/tests/test_environment_requirements.py | 修 | 首次分支断言 environment-check.md 指向 | 测试适配 |
| 2026-09-25 | hub/tests/services/test_standard_docs.py | 修 | payload 重构断言（渲染/target/元规则过滤）+ `test_snapshot_filters_meta_rule_docs` | 测试适配 |
| 2026-09-25 | hub/tests/api/v1/test_standard_docs.py | 修 | 按 target 取 standard 条目 | 测试适配 |
| 2026-09-25 | agent/app/executor/file_scanner.py | 修 | `_excluded` 路径感知：.tilacode 例外放行 rules/ 子树与 environment.md | 问题一：元规则与环境记录对用户可见（产物回传），其余 .tilacode 仍排除 |
| 2026-09-25 | agent/app/executor/workspace.py | 修 | 增 `ensure_index_skeleton`（docs/INDEX.md 骨架，已存在跳过），materialize 时调用 | 问题一：决策文档体系索引骨架可见 |
| 2026-09-25 | hub/app/skills/standard_docs.py | 修 | `generate_standard_doc` 重构：按类别拆分多份文档（<doc> 标签解析 `_parse_doc_tags`，无标签降级单文档），逐份入库+跳过清单 | 问题二：规范按类别拆分（通用/后端/前端/API/日志/数据库），节点按需读取 |
| 2026-09-25 | web/src/hooks/useTaskBuildingChat.ts | 修 | thinking case 回合分割：正文已开始的回合再收到思考→新建 assistant 消息 | 问题三：多轮思考/工具/正文按回合层叠 |
| 2026-09-25 | web/src/hooks/useSkillSteps.ts | 修 | step_started 同回合分割逻辑 | 问题三：步骤卡归入对应回合 |
| 2026-09-25 | agent/tests/test_file_scanner_rules.py | 增 | 2 项（.tilacode 例外/INDEX 骨架一次性） | 新行为测试 |
| 2026-09-25 | hub/tests/skills/test_standard_docs_skills.py | 修 | 增多文档拆分用例，单文档用例改降级分支断言 | 测试适配 |
| 2026-09-25 | web/src/hooks/useSkillSteps.ts | 修 | step_finished 改全局按 step_id 查找更新 | 实测问题：回合分割后 step_finished 只更新最后一条导致步骤卡卡"执行中" |
| 2026-09-25 | web/src/components/TaskNewChatPanel.tsx | 修 | 连续 assistant 消息只显示一次"AI"标签；ThinkingCollapse 卡片化（图标+边框+默认收起） | 实测问题：AI 名字反复出现/思考过程样式 |
| 2026-09-25 | web/src/api/taskBuilding.ts | 修 | taskBuildingChatStream 加 signal（AbortSignal） | 中枢回答可打断基础 |
| 2026-09-25 | web/src/hooks/useTaskBuildingChat.ts | 修 | abortRef + stop 回调 + AbortError 静默处理（保留已生成内容） | 中枢回答可打断 |
| 2026-09-25 | web/src/components/TaskNewInputBar.tsx | 修 | 回答中按钮改停止按钮（onStop） | 中枢回答打断入口 |
| 2026-09-25 | web/src/pages/TaskNew.tsx | 修 | 解构 stop 传 onStop；onCancelTask 接 PlanReviewPanel | 打断入口接线 |
| 2026-09-25 | hub/app/services/task_service.py | 修 | 增 `cancel_task`（pending 直接取消；assigned/running 置 failed+推送 hub.task_cancel；终态 409） | 子节点任务取消 API |
| 2026-09-25 | hub/app/api/v1/endpoints/tasks.py | 修 | 增 POST /{task_id}/cancel 端点 | 取消 API 暴露 |
| 2026-09-25 | web/src/api/tasks.ts + PlanReviewPanel.tsx + TaskNewRightPanel.tsx | 修 | cancelTask API + 执行卡"取消任务"按钮（Popconfirm）+ 透传 | 子节点打断入口 |
| 2026-09-25 | hub/tests/services/test_task_cancel.py | 增 | 4 项（pending 取消/终态 409/running 推送/404） | 取消链路测试 |
| 2026-09-25 | web/src/pages/TaskNew.tsx | 修 | 恢复 `message` 原名（改名 antdMessage 导致页面 ReferenceError 崩溃），取消回调统一用 message | 实测崩溃修复（浏览器复现定位） |
| 2026-09-25 | hub/app/api/v1/endpoints/client_logs.py + api.py | 增 | POST /client-logs 端点（WARNING 级写运行日志，恒 204） | 存量缺口：前端 errorReporter 上报端点从未实现（405），前端崩溃可进中枢日志排查 |

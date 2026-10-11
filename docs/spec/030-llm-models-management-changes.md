# 多模型管理与自定义接入 Changes 变更清单

关联决策：[ADR-030](../adr/030-llm-models-management.md) / [Spec](./030-llm-models-management-spec.md)

> 按规则：每修改/新增/删除一个文件立即登记本清单（修/增/减 + 函数级明细），禁止开发完成后一次性补记。

## 变更清单

| # | 文件 | 类型 | 函数/类级明细 | 作用说明 | 日期 |
|---|------|------|--------------|---------|------|
| 1 | docs/adr/030-llm-models-management.md | 增 | — | 决策：自定义 OpenAI 兼容接入 + 模型列表管理 + 按任务指定模型 | 2026-09-11 |
| 2 | docs/spec/030-llm-models-management-spec.md | 增 | — | 功能规格：用户故事/流程/降级/验收 | 2026-09-11 |
| 3 | docs/spec/030-llm-models-management-harness.md | 增 | — | 测试清单与 CI 门禁 | 2026-09-11 |
| 4 | docs/spec/030-llm-models-management-changes.md | 增 | — | 本清单骨架 | 2026-09-11 |
| 5 | docs/INDEX.md | 修 | 1.1/1.2/1.3 表 +ADR-030 行 | 登记决策文档 | 2026-09-11 |
| 6 | hub/app/services/llm_provider_service.py | 修 | get_active_provider/update_provider 去 provider 限制；+LLMModelNotFoundError/LLMModelInUseError；+list_models/add_model/activate_model/delete_model | 自定义模型 CRUD 与激活互斥切换（表结构零迁移） | 2026-09-11 |
| 7 | hub/app/schemas/llm_provider.py | 修 | +LLMModelCreate/LLMModelResponse（api_key 脱敏序列化） | models API 请求/响应模型 | 2026-09-11 |
| 8 | hub/app/api/v1/endpoints/settings.py | 修 | +GET/POST /llm/models、PUT /llm/models/{id}/activate、DELETE /llm/models/{id}；+_model_not_found | 模型管理 4 端点 | 2026-09-11 |
| 9 | hub/app/core/errors.py | 修 | ErrorCode +LLM_MODEL_IN_USE(203002) | 使用中删除 409 业务码 | 2026-09-11 |
| 10 | hub/app/schemas/task.py | 修 | ExecutorModelSelection source +custom、+provider_id；normalize_executor_model +custom 分支 | 执行模型支持按任务指定自定义模型行 | 2026-09-11 |
| 11 | hub/app/services/scheduler.py | 修 | _build_executor_model_entry +custom 分支（按 provider_id 查行注入，行缺失降级告警）；import LLMProvider | 派发凭据按指定模型行注入 | 2026-09-11 |
| 12 | hub/tests/services/test_llm_provider_service.py | 修 | +7 个用例（非默认 provider 激活/首行自动激活/次行不激活/互斥切换/not found/使用中拒删/非使用中可删） | U1-U6 测试 | 2026-09-11 |
| 13 | hub/tests/services/test_task_executor_model.py | 修 | +4 个用例（custom 行注入/行缺失降级/id 缺失降级/normalize） | U7/U8 测试 | 2026-09-11 |
| 14 | web/src/types/llmProvider.ts | 修 | +LLMModelItem/LLMModelCreateRequest | 前端类型 | 2026-09-11 |
| 15 | web/src/api/llmProvider.ts | 修 | +fetchLLMModels/addLLMModel/activateLLMModel/deleteLLMModel | 模型管理 API 封装 | 2026-09-11 |
| 16 | web/src/components/LLMModelsPanel.tsx | 增 | LLMModelsPanel（列表 Table + 添加 Modal + 启用/删除 + 空态引导） | 设置页「模型管理」面板（图一布局） | 2026-09-11 |
| 17 | web/src/pages/Settings.tsx | 修 | 移除旧 LLM 表单（llmForm/handleLlmSubmit/llmSaving），Card 内嵌 LLMModelsPanel | 设置页改版 | 2026-09-11 |
| 18 | web/src/components/TaskExecutorModelSelect.tsx | 修 | 重写：加载模型列表，分组下拉（中枢模型/自定义模型/免费通道），value 改 ExecutorModelSelection | 新建对话执行模型选择器列出自定义模型 | 2026-09-11 |
| 19 | web/src/types/task.ts、web/src/components/TaskNewToolbar.tsx、web/src/pages/TaskNew.tsx | 修 | ExecutorModelSelection +custom/provider_id；Toolbar/TaskNew state 与 handleCreate 传参适配 | 执行模型选择接线 | 2026-09-11 |

（验证记录 2026-09-11：hub llm 相关测试 27 passed；前端 lint/tsc/vitest 31 passed/build+copy_web_dist 同步；8001 临时实例 API 实测：列表脱敏 ✓、添加第二个模型不抢激活 ✓、激活互斥切换 ✓、删除使用中 409 ✓、切回后删除成功 ✓，测试数据已清理；Agent 端零改动——payload 仍走 provider/api_key/model 注入通路）

| # | 文件 | 类型 | 函数/类级明细 | 作用说明 | 日期 |
|---|------|------|--------------|---------|------|
| 20 | hub/app/services/llm_provider_service.py | 修 | delete_model：移除使用中 409 拒绝逻辑，改为删除后自动激活 created_at 最早的其他模型（无则回到未配置态），记 llm_model_deleted 事件（含 was_active/successor_id） | 问题1修复：使用中模型可删除且主模型自动切换 | 2026-09-12 |
| 21 | hub/tests/services/test_llm_provider_service.py | 修 | 使用中删除用例改断言：删除后激活最早剩余模型；+无剩余模型回退用例 | 同步删除策略变更 | 2026-09-12 |
| 22 | web/src/components/LLMModelsPanel.tsx | 修 | 操作列：使用中行恢复删除按钮 + Popconfirm 提示「自动切换到最早的其他模型」 | 问题1前端配套 | 2026-09-12 |
| 23 | web/src/components/TaskNewToolbar.tsx | 修 | +当前模型下拉（chatModels 加载/使用中标记/onClick 调 activateLLMModel 后刷新），单模型时禁用 | 问题2：对话页顶部随时热切换主模型 | 2026-09-12 |

（验证记录 2026-09-12：hub 目标测试 34 passed（含 llm service/settings/launcher/reconcile）；agent hub_client 12 passed；前端 tsc + eslint 通过。截图「操作列为 —」系用户中枢实例未重启仍在跑旧代码所致，重启后生效）

## 变更记录

| 日期 | 变更内容 | 变更人 |
|------|---------|--------|
| 2026-09-11 | 初始创建（ADR-030 骨架） | AI助手 |

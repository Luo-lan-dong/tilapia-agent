# 跨 Agent 任务现场迁移与项目上下文记忆 Changes（变更清单）

> 关联：[ADR-034](../adr/034-task-handoff-context.md) / [Spec](./034-task-handoff-context-spec.md) / [Harness](./034-task-handoff-context-harness.md)
>
> 按 `.trae/rules/decision-documents.md` 要求：边开发边实时登记，禁止开发完成后一次性补记。

## 变更清单

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| docs/adr/034-task-handoff-context.md | 增 | - | 决策文档 |
| docs/spec/034-task-handoff-context-spec.md | 增 | - | 功能 Spec |
| docs/spec/034-task-handoff-context-harness.md | 增 | - | 测试清单 |
| docs/spec/034-task-handoff-context-changes.md | 增 | - | 本变更清单 |
| docs/INDEX.md | 修 | 1.1/1.2/1.3/六 | 登记本次决策 |
| docs/standards/api-standards.md | 修 | §4.2 事件 payload 扩展 / §7 变更记录 | 协议规范登记 |
| opc-central-dispatch-hub/alembic/versions/20260915_task_chain_contexts.py | 增 | upgrade/downgrade | task_chain_contexts 表（root_task_id 唯一） |
| opc-central-dispatch-hub/app/models/task_chain_context.py | 增 | TaskChainContext | 项目上下文记忆模型 |
| opc-central-dispatch-hub/app/models/__init__.py | 修 | __all__ | 导出 TaskChainContext |
| opc-central-dispatch-hub/app/core/config.py | 修 | Settings | 新增 HANDOFF_CACHE_DIR / HANDOFF_BUNDLE_TTL_SECONDS / HANDOFF_MAX_BUNDLE_BYTES |
| opc-central-dispatch-hub/app/services/task_handoff_service.py | 增 | upsert_chain_context / get_chain_context / build_handoff_payload / resolve_bundle_download / _resolve_source_bundle / _inject_context / _pack_directory | 链上下文存储 + 现场打包（链上最新 export.zip 注入 context md）+ 一次性 token 下载注册表 |
| opc-central-dispatch-hub/app/services/task_event_handler.py | 修 | _handle_task_completed 增 project_context 入库与 _schedule_artifact_sync 后台全量产物同步 | 任务完成即同步完整现场到中枢（handoff 数据来源，触发场景为 Agent 离线故必须在线时预同步） |
| opc-central-dispatch-hub/app/services/scheduler.py | 修 | _assign_plan 构造 message 增 handoff 字段 | 自动派发附带现场迁移 |
| opc-central-dispatch-hub/app/services/task_service.py | 修 | dispatch_task 构造消息增 handoff 字段 | 手动/智能派发同样附带现场迁移 |
| opc-central-dispatch-hub/app/api/v1/endpoints/task_artifacts.py | 增 | GET /handoff-bundle（一次性下载）/ GET /context（链上下文查询） | 新端点 |
| opc-agent-programming/app/models/schemas.py | 修 | TaskDispatchPayload 增 handoff: TaskHandoffInfo 与 handoff_bundle 运行时字段；增 TaskHandoffInfo | 派发消息承载现场迁移信息 |
| opc-agent-programming/app/client/hub_client.py | 修 | 增 _download_handoff_bundle（sha256/大小校验，失败重试 1 次后降级）；_handle_task_dispatch 接单前下载；report_completed/_send_task_completed 增 project_context | Agent 端 bundle 下载与上下文上报 |
| opc-agent-programming/app/executor/task_bridge.py | 修 | _prepare_session 增 handoff_bundle 解压（路径安全过滤）；_build_prompt 增 ws_dir 参数（project_context.md 阅读提示 + build 模式完成前生成要求）；_dispatch_result 读取 project_context 随完成上报 | 现场恢复 + 上下文注入 + 记忆生成 |
| opc-agent-programming/tests/test_task_bridge.py | 修 | on_completed 回调签名加 project_context；_build_prompt 调用加 ws_dir | 测试适配 |
| opc-central-dispatch-hub/tests/services/test_task_handoff_service.py | 增 | 4 项用例：上下文 upsert 覆盖 / bundle 生成+注入+一次性下载 / 首个任务无 handoff / 无效 token 拒绝 | handoff 服务回归 |

<!-- 开发过程中在此实时追加：文件 → 变更类型（修/增/减） → 函数/类级明细 → 作用说明 -->

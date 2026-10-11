# ADR-029: 任务执行模型选择与凭据中枢下发

## 状态

Accepted

## 日期

2026-09-10

## 问题背景

Agent 节点执行编程任务依赖 tilacode（OpenCode 内核）。当前 Agent 端硬编码使用 OpenCode
官方免费模型 `opencode/mimo-v2.5-free`（ADR-012 MVP 默认），未配置任何自有 LLM 凭据。
OpenCode 官方已限制免费通道（实测报错）：

```
Provider request failed with HTTP 400:
{"error":{"type":"MissingSessionID","message":"Error from provider (Console):
OpenCode's free tier can only be used in OpenCode"}}
```

导致所有派发到节点的任务在执行阶段必然失败（进度 20% 后中断），且失败原因只在中枢
日志可见，用户无法自愈。

同时，中枢侧已具备集中管理的 LLM 配置（`system_settings` 存储 DeepSeek API Key，
支持运行时热更新），但该配置仅用于中枢自身的任务规划/三件套生成，未下发给执行层。

## 决策

1. **凭据中枢集中下发**：任务派发时，中枢将执行所需的 LLM 凭据随派发消息（WebSocket）
   下发给 Agent 节点；凭据仅内存传输，**不落任务表、不落 Agent 磁盘**。与既定约束
   「LLM API Key 配置必须存储在数据库并支持运行时热更新」一致——任务执行时实时读取
   当前生效配置，Key 热更新后新派发任务即刻生效。
2. **任务级执行模型选择**：新建对话页提供「子 Agent 执行模型」选择器，选项包括：
   - **中枢配置模型**（默认，推荐）：使用系统设置里当前生效的 provider/模型
     （如 deepseek/deepseek-chat）；
   - **OpenCode 官方免费模型**（特殊选项）：`opencode/mimo-v2.5-free`，文案明确提示
     「官方免费通道可能被限制，失败率高」。
   用户选择随创建请求落库（`tasks.executor_model` JSONB），派发时按选择组装下发。
3. **凭据注入方式**：Agent 端在任务执行前通过 tilacode 运行时 API
   `POST /api/integration/deepseek/connect/key`（幂等）注入 API Key；session 创建时
   按 `model: {providerID, id}` 指定模型。免费模型选项不注入凭据、维持现状默认。
4. **兼容性**：存量任务与旧版中枢派发消息无 `executor_model` 时，Agent 回退现状默认
   （免费模型），不破坏旧包行为。

## 备选方案

| 方案 | 结论 |
|------|------|
| A. 中枢集中下发（本决策） | 采纳。节点零配置、Key 集中管理与热更新语义正确、与中枢已有配置复用 |
| B. 节点本地配置（.env 注入 DEEPSEEK_API_KEY + 模型可配） | 否决。每台节点手动配置成本高，Key 分散存储违背集中管理约束；作为 A 的兜底通道保留（环境变量已随子进程透传） |
| C. 全部锁定免费模型并等待官方放开 | 否决。免费通道受限是不可控外部依赖，任务执行成功率无保障 |

## 影响

- **数据库**：`tasks` 新增 `executor_model` JSONB 可空列（Alembic 迁移）。
- **中枢**：创建请求/响应 Schema、派发消息组装（实时读取凭据注入 payload）。
- **Agent**：派发 payload Schema、任务桥接执行前凭据注入与模型指定。
- **前端**：新建对话页执行模型选择器（含免费模型警示文案）。
- **发布**：Agent 行为变更需重新打包发布安装包；旧节点在升级前继续使用免费模型
  （任务仍会失败，属预期过渡状态）。
- **安全**：凭据仅经 WebSocket 内存传输与 tilacode 运行时注入，不写入任何持久化存储；
  任务表、日志、派发消息落盘均不含 Key。

## 关联文档

- [Spec](../spec/029-task-executor-model-spec.md) / [Harness](../spec/029-task-executor-model-harness.md) /
  [Changes](../spec/029-task-executor-model-changes.md)
- ADR-012（tilacode 集成与默认免费模型）、ADR-016（agent_type=code）、ADR-017（派发模式）
- 约束来源：LLM API Key 数据库存储 + 热更新（项目工程约定）

## 实际实施情况

2026-09-10 实施完成，与初版决策一致，补充说明：

- 校验实现：`ExecutorModelSelection` 使用 `Literal["hub","opencode_free"]` + `extra="forbid"`，
  非法 source 在 Pydantic 层即 422，未新增业务错误码（Harness U2「以实现为准」）。
- 免费模型默认值归一化落库为 `mimo-v2.5-free`（U3）。
- 派发凭据注入点在调度器与手动指派两条链路共用 `_build_executor_model_entry`，
  调度循环内以 `llm_cache` 复用配置避免逐任务重复查询；中枢 LLM 配置缺失时
  降级为不带凭据（Agent 回退免费通道），并记录 `executor_model_hub_config_missing` 告警。
- Agent 侧凭据经 `ensure_integration_key`（POST /api/integration/{id}/connect/key）幂等注入，
  session 创建按 `{providerID, id}` 指定模型；Key 不落任何持久化存储。
- 测试：hub 新增 test_task_executor_model.py（U1-U7/I1/I2，含日志脱敏红线断言）；
  agent 桥接新增 I3-I5 与降级用例；前端 lint/tsc 通过。
- 遗留：Agent 行为变更需重新打包发布安装包（旧节点升级前继续使用免费模型）。

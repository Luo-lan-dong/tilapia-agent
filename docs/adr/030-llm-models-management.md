# ADR-030: 多模型管理与自定义 OpenAI 兼容接入

## 状态

Accepted

## 日期

2026-09-11

## 问题背景

中枢 LLM 配置当前仅支持单条 DeepSeek 记录（`llm_providers` 表一行 + GET/PATCH
`/settings/llm`），前端为一个三字段表单。用户诉求：

1. UI 改版：模型以列表管理（图一成品参考：模型 / 服务商 / 操作列，「添加模型」按钮）。
2. 支持更多模型厂商：DeepSeek、火山引擎、MiniMax、Bigmodel、阿里云、Xiaomi MIMO、
   硅基流动、Z.ai、OpenRouter、Kimi、BytePlus、AWS、腾讯云、模力方舟、PPIO 等——
   厂商众多且各自的 SDK/签名差异大，**逐一预置不可行**。

关键事实：现有 `DeepSeekClient` 基于 httpx + Bearer + 标准 `/chat/completions`
路径，base_url 可配——天然兼容所有 OpenAI 协议端点，上述厂商均提供 OpenAI 兼容接口。

## 决策

与用户确认（2026-09-11）：**走自定义路线**（用户自填 OpenAI 兼容端点接入），不做厂商预置；不做「内置模型」分组（中枢无内置可用模型）；**本次一并扩展**新建对话页按任务指定任一已添加模型作为子 Agent 执行模型。

1. **数据模型（零迁移）**：复用 `llm_providers` 表，语义从「单条 deepseek 记录」
   扩展为「自定义模型列表」：
   - `provider` 字段 → 用户可读的「服务商名称」（任意字符串，如 硅基流动/OpenRouter）；
   - `model`/`base_url`/`api_key` 语义不变；
   - `is_active` → 「使用中」标记，应用层保证同一时刻仅一行 true；
   - 查询侧 `get_active_provider` 去掉 `provider == 'deepseek'` 限制（返回结构不变，消费方零改动）。
2. **模型管理 API**（新增，挂 `/settings/llm/models`）：
   - `GET /settings/llm/models`：列表（api_key 脱敏）；
   - `POST /settings/llm/models`：添加（首个自动设为使用中）；
   - `PUT /settings/llm/models/{id}/activate`：切换使用中（原使用中行自动失活）；
   - `DELETE /settings/llm/models/{id}`：删除（~~使用中的行拒绝删除，需先切换~~
    2026-09-12 修订：使用中可删，删除后自动激活列表中 `created_at` 最早的其他
    模型，无剩余则回到未配置状态；见「实际实施情况」修订记录）；
   - 既有 `GET/PATCH /settings/llm` 保留（返回/编辑当前使用中模型，TaskNew 页模型名展示依赖）。
3. **执行模型按任务指定（ADR-029 增强）**：`ExecutorModelSelection` 增加
   `provider_id`（uuid，指向 `llm_providers.id`）；`source` 语义扩展为
   `hub`（当前使用中）| `custom`（指定行）| `opencode_free`。派发组装时 custom
   按行实时读取凭据注入；指定行已被删除则降级为不带凭据（Agent 回退免费通道）
   并记告警。落库 `tasks.executor_model` 形如 `{source: custom, provider_id, model}`。
4. **兼容性说明**：三件套生成依赖 `response_format: json_object` 与
   `reasoning_effort` 参数，部分厂商端点不支持会 400（报错可见）；规范文档提示
   优先选择支持 JSON mode 的模型。已知限制不阻塞多模型接入。

## 备选方案

| 方案 | 结论 |
|------|------|
| A. 自定义 OpenAI 兼容接入（本决策） | 采纳。客户端零改动、用户自助接入任意厂商、UI 简洁 |
| B. 预置厂商列表（图二 15+ 厂商各配模板） | 否决。SDK/签名/参数差异大，维护成本高，与桌面一体化部署冲突 |
| C. 新建独立 `llm_models` 表 | 否决。`llm_providers` 字段完全够用，新建表徒增迁移与双读兼容 |

## 影响

- **数据库**：无结构迁移（仅查询语义与字段注释扩展）。
- **中枢**：LLMProviderService 增删改查/激活切换方法；settings 路由新增 4 个
  models 端点；`ExecutorModelSelection`/派发组装支持 custom 来源。
- **前端**：Settings 页「模型管理」改版（列表 + 添加弹窗 + 启用/删除）；
  TaskNew 执行模型选择器列出已添加模型（分组下拉）。
- **Agent**：零改动（payload 仍走 provider/api_key/model 注入通路）。
- **安全**：api_key 明文存储约定不变（与现状一致），列表接口脱敏返回。

## 关联文档

- [Spec](../spec/030-llm-models-management-spec.md) / [Harness](../spec/030-llm-models-management-harness.md) /
  [Changes](../spec/030-llm-models-management-changes.md)
- ADR-029（执行模型选择与凭据下发，本决策扩展其 custom 来源）
- 现状依据：`llm_providers` 表 / `LLMProviderService` / `GET,PATCH /settings/llm`

## 实际实施情况

2026-09-11 实施完成，与初版决策一致，补充说明：

- 数据库确认零迁移：复用 `llm_providers` 表现有列，「使用中唯一」由应用层
  `activate_model` 互斥切换保证（update 全表失活 + 目标置位）。
- Agent 端确认零改动：custom 来源派发 payload 与 hub 来源结构相同
  （provider/model/api_key/base_url），注入通路复用 ADR-029 的
  `ensure_integration_key` + session 模型指定。
- 指定行被删除时的降级已实现并告警（`executor_model_custom_row_missing`）。
- 已知限制：三件套生成使用 `response_format: json_object` 与
  `reasoning_effort`，部分 OpenAI 兼容端点不支持会 400（错误对用户可见），
  规范提示选择支持 JSON mode 的模型。
- 测试：hub llm 相关 27 passed；前端 lint/tsc/vitest 31 passed；
  API 实测列表脱敏/添加/激活互斥/使用中 409/删除全链路通过。

### 修订记录（2026-09-12）

用户实测反馈两问题，修订如下（不改变整体架构）：

1. **使用中模型无法删除**（原「409 拒绝删除」策略体验差）：`delete_model`
   改为删除后自动激活 `created_at` 最早的其他模型（无剩余则回到未配置态），
   主模型始终可用；前端使用中行恢复删除按钮并二次确认提示自动切换去向。
   原 `LLM_MODEL_IN_USE(203002)` 错误码保留定义但不再由该链路抛出。
2. **对话中无法切换模型**：新建对话页工具栏新增「当前模型」下拉（加载
   `llm_providers` 列表、使用中标记、单模型时禁用），点击即调
   `PUT /llm/models/{id}/activate` 热切换，立即生效。

### 修订记录（2026-09-13）

3. **Agent 端未处理 source=custom 导致回退免费模型**：实测发现 Agent 桥接层
   `_resolve_session_model` 仅识别 `source=hub`，`source=custom` 被当作缺省
   回退到 `opencode/mimo-v2.5-free`，触发官方免费通道 400 失败。已修正
   Agent 逻辑使 `hub`/`custom` 均执行凭据注入与模型指定，`opencode_free`/缺省
   才回退免费模型（与中枢派发语义一致）。
4. **移除「官方免费（可能失败）」选项**：OpenCode 免费通道已被官方限制、
   几乎不可用，且与自定义模型目标冲突。已从前端选择器、前端类型、中枢
   schema 与归一化、相关测试中移除 `opencode_free`；Agent 仍保留缺省/无凭据
   回退内置免费通道的能力，仅作为旧数据兼容与降级保护。
5. **凭据注入不得携带任务目录（connect/key 每次必 500 事故）**：实测发现
   并发目录隔离修复后，`ensure_integration_key` 请求头携带任务目录导致
   tilacode 对每个新任务目录懒初始化 location 时 integration 注册（依赖
   models.dev 数据，缓存 TTL 5 分钟）未完成，`deepseek` 查不到即 500
   （"Key method not found"），任务 100% 失败、重试才可能成功。tilacode
   凭据实为全局存储（`Credential.node` 为 makeGlobalNode，跨目录共享），
   注入目录与任务目录无关。决策：注入恢复走客户端默认目录（location
   常驻、integration 已注册），并保留 500 指数退避重试（最多 4 次）兜底
   tilacode 重启窗口。

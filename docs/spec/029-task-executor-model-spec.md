# 任务执行模型选择与凭据下发 Spec

关联决策：[ADR-029](../adr/029-task-executor-model-dispatch.md)

## 功能描述

新建对话时可选定「子 Agent 执行模型」；任务创建后模型选择随任务落库，派发时中枢将
模型配置与凭据（仅中枢来源）经 WebSocket 下发给节点，Agent 在 tilacode 执行前完成
凭据注入与模型指定，使任务在自有模型上执行，彻底摆脱被官方限制的免费通道默认依赖。

## 用户故事

- US-1：作为用户，我在新建对话页可以看到「执行模型」选择器，默认选中枢当前配置的
  模型（显示模型名），无需理解细节即可获得可用执行能力。
- US-2：作为用户，我可以切换到「OpenCode 官方免费」模型（选项带「可能失败」警示），
  在自有凭据不可用时做尝试。
- US-3：作为用户，我在系统设置里热更新 LLM API Key 后，新派发的任务立即使用新 Key，
  无需重启任何服务。
- US-4：作为用户，任务因执行模型失败时，我可以在任务详情看到失败原因（阶段执行失败
  及 provider 报错），并可直接重新派发。

## 核心流程

```
新建对话页                中枢 Hub                          Agent 节点
─────────                ─────────                         ──────────
选择执行模型 ──创建任务──▶ executor_model 落库
（hub / opencode_free）   （不存 Key）
                                │
                         调度器/手动派发
                                │
                         读 system_settings
                         实时取 Key（仅 hub 来源）
                                │
                         WS dispatch payload ──────────────▶ task_bridge 收任务
                         {executor_model: {          ▶ 执行前 POST /api/integration/
                           source, model,                deepseek/connect/key（幂等）
                           api_key?, base_url?}    ▶ session model 按选择指定
                                                ▶ tilacode 用自有模型执行
```

降级链路：

- payload 无 `executor_model`（旧中枢/存量任务）→ Agent 回退默认免费模型（现状行为）。
- hub 来源但中枢 LLM 配置缺失（无 Key）→ 创建任务仍允许（规划阶段会另行报错）；派发时
  payload 不带 api_key，Agent 侧注入跳过，tilacode 回退免费通道（与现状一致）。
- 免费模型执行失败 → 任务 failed（原因随失败事件展示），用户可切换模型重新派发。

## 接口与数据变更

| 位置 | 变更 |
|------|------|
| `tasks` 表 | +`executor_model` JSONB NULL，形如 `{"source":"hub"\|"opencode_free","model":"deepseek-chat"\|"mimo-v2.5-free"}`；NULL 视为 hub 默认 |
| `POST /api/v1/tasks` | 请求/响应 +`executor_model`（可选，缺省 = hub 默认） |
| WS dispatch payload | +`executor_model`；hub 来源附带 `api_key`/`base_url`（实时读取，仅内存） |
| Agent `.env` | 无新增（凭据走派发链路） |

## 验收标准

1. 新建对话页展示执行模型选择器，默认「中枢配置模型」并显示当前模型名；免费模型选项
   含失败警示文案（US-1/US-2）。
2. 选择「中枢配置模型」创建任务后，节点执行使用中枢配置的 provider/模型，任务可正常
   推进（以节点上报 progress>20% 且无 provider 400 为准）。
3. 选择「官方免费」创建任务时，Agent 不注入自有凭据，session 使用 opencode 免费模型；
   失败时任务 failed 且原因可见（US-4）。
4. 热更新 LLM Key 后新派发任务使用新 Key（US-3，代码路径审查 + 单测覆盖）。
5. 旧格式派发消息（无 executor_model）在 Agent 上行为与升级前一致（回归）。
6. `tasks` 表与日志中不出现 API Key 明文（安全红线）。

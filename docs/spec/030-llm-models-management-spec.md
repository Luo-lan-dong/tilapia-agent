# 多模型管理与自定义接入 Spec

关联决策：[ADR-030](../adr/030-llm-models-management.md)

## 功能描述

系统设置页的「大模型配置」改造为「模型管理」列表：用户通过自定义 OpenAI 兼容
端点接入任意厂商模型（服务商名称 + 模型 ID + Base URL + API Key），列表化管理、
一键切换使用中模型（热生效）；新建对话页「子 Agent 执行模型」可选择任一已添加
模型作为执行模型，派发时中枢按选择注入对应凭据。

## 用户故事

- US-1：作为用户，我在系统设置看到已添加模型的列表（模型 / 服务商 / 操作），
  使用中的模型有明确标记。
- US-2：作为用户，我点击「添加模型」填写 OpenAI 兼容端点信息即可接入新厂商
  （DeepSeek/硅基流动/OpenRouter/阿里云等均支持该协议）。
- US-3：作为用户，我点击「启用」即可把某个模型设为使用中，规划/三件套/派发
  凭据立即跟随，无需重启。
- US-4：作为用户，我可以删除不再使用的模型（使用中的需先切换其他模型）。
- US-5：作为用户，我在新建对话页可为本次任务指定任一已添加模型作为子 Agent
  执行模型（默认仍为中枢当前使用中模型）。

## 核心流程

```
系统设置                          新建对话页                    Agent 节点
────────                          ──────────                    ──────────
+ 添加模型（名称/模型ID/URL/Key）
  └→ llm_providers 插入一行
     （首行自动 is_active）
选择「执行模型」──创建任务──▶ executor_model 落库
列表 启用/删除                 {source: hub|custom|opencode_free,
(使用中唯一)                    provider_id?, model?}
                                     │
                              派发组装 _build_executor_model_entry
                              hub → 查 is_active 行
                              custom → 查 provider_id 行
                                     │ (api_key/base_url/provider/model 仅内存)
                              WS dispatch ────────────────▶ 注入 connect/key
                                                            session 指定模型
```

降级链路：

- 指定的 custom 模型行已被删除 → 派发不带凭据 + 告警（Agent 回退免费通道）；
- hub 来源但当前无使用中模型（列表为空）→ 同上降级；
- 端点不支持 json_object / reasoning_effort → 调用 400，错误对用户可见（已知限制，
  提示选择支持 JSON mode 的模型）。

## 接口与数据变更

| 位置 | 变更 |
|------|------|
| `llm_providers` 表 | 无结构迁移；`provider` 语义 = 服务商名称（任意字符串） |
| `GET /settings/llm/models` | 新增：列表（id/provider/model/base_url/api_key 脱敏/is_active） |
| `POST /settings/llm/models` | 新增：添加，body {provider, model, base_url, api_key}；首行自动激活 |
| `PUT /settings/llm/models/{id}/activate` | 新增：切换使用中 |
| `DELETE /settings/llm/models/{id}` | 新增：删除（使用中拒绝，409） |
| `GET/PATCH /settings/llm` | 保留：返回/编辑当前使用中模型 |
| `POST /api/v1/tasks` | executor_model 支持 `{source: custom, provider_id}` |
| WS dispatch payload | executor_model 组装时 custom 按 provider_id 行注入凭据 |
| SSE/WS 协议 | 无新增事件 |

## 验收标准

1. 设置页展示模型列表，「使用中」有标记；空态引导添加（US-1）。
2. 添加自定义模型成功后出现在列表，首个模型自动设为使用中（US-2）。
3. 切换「使用中」后：TaskNew 页顶部模型名即时更新；新创建任务的三件套生成
   使用新模型（代码路径审查 + 单测）（US-3）。
4. 使用中的模型禁止删除（409），非使用中可删（US-4）。
5. 新建对话页执行模型选择器列出全部已添加模型（分组展示），按任务选择后
   派发 payload 注入对应行凭据（US-5，单测覆盖）。
6. api_key 列表接口脱敏、日志不落明文（安全红线）。

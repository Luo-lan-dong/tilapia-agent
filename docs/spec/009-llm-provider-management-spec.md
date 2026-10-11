# LLM Provider 配置管理 Spec

## 1. 功能描述

为 OPC 中央调度中枢提供 LLM Provider 配置管理能力。MVP 阶段仅支持 DeepSeek，允许管理员通过前端页面配置 API Key、Base URL、Model，并支持运行时热更新。配置持久化到 PostgreSQL，取代原有环境变量配置方式。

## 2. 用户故事

- 作为系统管理员，我希望在前端页面查看当前 DeepSeek 配置，以便确认系统是否已正确接入大模型。
- 作为系统管理员，我希望在前端页面修改 DeepSeek API Key、Base URL、Model，并立即生效，而无需重启后端服务。
- 作为开发者，我希望 LLM 配置从数据库读取，以便在测试环境中灵活切换模型和 Key。

## 3. 核心流程

### 3.1 配置读取流程

```text
+-------------+     +-------------------------+     +-------------------+
| 任务规划 Agent | --> | LLM Provider Service    | --> | PostgreSQL        |
| planning.py |     | (读取活跃 provider 配置)  |     | llm_providers 表  |
+-------------+     +-------------------------+     +-------------------+
                            |
                            v
                     +------------------+
                     | DeepSeekClient   |
                     | (接收配置参数)    |
                     +------------------+
```

### 3.2 配置更新流程

```text
+----------+     +----------------+     +-------------------------+
| 前端设置页 | --> | PUT /settings/ | --> | 更新 llm_providers 表    |
| LLMConfig |     | llm            |     | 刷新内存缓存              |
+----------+     +----------------+     +-------------------------+
                                                |
                                                v
                                         +-------------+
                                         | 立即生效     |
                                         +-------------+
```

## 4. 数据库设计

### 4.1 `llm_providers` 表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| provider | VARCHAR(32) | provider 类型，MVP 仅 `deepseek` |
| api_key | VARCHAR(512) | API Key，明文存储 |
| base_url | VARCHAR(256) | 接口基础 URL |
| model | VARCHAR(128) | 模型名称 |
| is_active | BOOLEAN | 是否启用，同一时刻仅一个活跃 |
| created_at | TIMESTAMPTZ | 创建时间 |
| updated_at | TIMESTAMPTZ | 更新时间 |

### 4.2 初始化数据

系统首次启动或数据初始化时，若 `llm_providers` 表为空，插入一条默认 DeepSeek 记录：

- `provider`: `deepseek`
- `api_key`: 读取 `DEEPSEEK_API_KEY` 环境变量，若未设置则为空字符串
- `base_url`: 读取 `DEEPSEEK_BASE_URL` 环境变量，若未设置则为空字符串
- `model`: 读取 `DEEPSEEK_MODEL` 环境变量，若未设置则为空字符串
- `is_active`: `true`

未配置 API Key 不影响服务启动，用户可在系统设置页面后续填写。

## 5. API 设计

遵循 [api-standards.md](../standards/api-standards.md)。

### 5.1 获取 LLM 配置

```http
GET /api/v1/settings/llm
```

响应：

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "provider": "deepseek",
    "api_key": "sk-xxxxxxxx",
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-chat",
    "is_active": true
  },
  "request_id": "req_xxxxxxxx"
}
```

### 5.2 更新 LLM 配置

```http
PUT /api/v1/settings/llm
Content-Type: application/json

{
  "api_key": "sk-new-key",
  "base_url": "https://api.deepseek.com",
  "model": "deepseek-chat"
}
```

响应：

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "provider": "deepseek",
    "api_key": "sk-new-key",
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-chat",
    "is_active": true
  },
  "request_id": "req_xxxxxxxx"
}
```

## 6. 降级链路

1. **数据库无记录**：返回业务错误，提示用户配置 API Key。
2. **API Key 为空**：返回业务错误，提示用户配置 API Key。
3. **更新后调用失败**：任务规划接口返回错误，前端提示用户检查配置。

## 7. 验收标准

- [ ] 数据库中存在 `llm_providers` 表，且包含一条默认 DeepSeek 记录。
- [ ] `GET /api/v1/settings/llm` 返回当前 DeepSeek 配置（包含 API Key 明文）。
- [ ] `PUT /api/v1/settings/llm` 更新配置后，新任务规划请求立即使用新配置，无需重启服务。
- [ ] 前端侧边栏出现「系统设置」→「大模型配置」菜单，页面可查看和编辑配置。
- [ ] 旧的 `DeepSeekClient` 不再直接读取环境变量，而是从 `LLM Provider Service` 获取配置。
- [ ] 单元测试和 API 测试覆盖配置读写；AI 调用测试使用 mock，不发起真实请求。

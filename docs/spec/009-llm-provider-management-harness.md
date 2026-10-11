# LLM Provider 配置管理 Harness

## 1. 测试目标

验证 LLM Provider 配置管理模块的数据库持久化、API 接口、运行时热更新、前端对接等核心功能，确保不引入回归问题，且 AI 调用测试不消耗真实 token。

## 2. 测试金字塔

```text
        /\
       /  \   E2E：前端配置页 → 后端 API → 数据库 → 任务规划使用新配置
      /____\
     /      \  集成：LLM Provider Service + 数据库 + DeepSeekClient
    /________\
   /          \  单元：Service 层缓存逻辑、Schema 校验、Client 参数组装
  /____________\
```

## 3. 单元测试清单

| 测试项 | 说明 |
|--------|------|
| `LLMProviderService.get_active_provider()` 返回活跃记录 | 验证数据库查询与空值处理 |
| `LLMProviderService.update_provider()` 更新并刷新缓存 | 验证写入后内存缓存同步 |
| `LLMProviderService` 缓存失效逻辑 | 验证更新后下一次读取走数据库 |
| `DeepSeekClient` 使用传入配置初始化 | 验证 Client 不再依赖环境变量 |
| `DeepSeekClient.chat_completion` mock 返回 | 验证返回内容解析 |
| Schema `LLMProviderUpdate` 校验 | 验证空 api_key/base_url/model 返回 422 |

## 4. 集成测试清单

| 测试项 | 说明 |
|--------|------|
| `GET /api/v1/settings/llm` 返回当前配置 | 验证端到端读取链路 |
| `PUT /api/v1/settings/llm` 更新配置并落库 | 验证端到端写入链路 |
| 更新配置后 `DeepSeekClient` 使用新配置 | 验证热更新生效 |
| 数据库无记录时返回业务错误 | 验证降级链路 |
| 更新时缺少必填字段返回 422 | 验证参数校验 |

## 5. E2E 测试场景

### 场景 1：管理员修改 API Key 后任务规划使用新 Key

1. 管理员打开「系统设置」→「大模型配置」页面。
2. 页面显示当前 DeepSeek API Key、Base URL、Model。
3. 管理员修改 API Key 并保存。
4. 后端返回保存成功。
5. 创建新任务，任务规划 Agent 使用新的 API Key 调用 DeepSeek。
6. （使用 mock 验证请求头 Authorization 为新 Key，不发起真实请求）

## 6. CI 门禁

- `pytest` 全部通过。
- `ruff check .` 通过。
- `python scripts/check_file_size.py` 通过。
- 新增测试文件中，AI 调用相关测试必须使用 `unittest.mock` 或 `respx` mock，禁止空跑真实 token。

## 7. 测试数据与隔离

- 每个测试使用独立的数据库事务，测试结束后回滚，不污染默认配置记录。
- 需要初始化默认记录时，通过 fixture 在事务内插入，测试结束后回滚。
- 环境变量 fallback 测试通过 `monkeypatch` 临时修改环境变量，测试结束后恢复。

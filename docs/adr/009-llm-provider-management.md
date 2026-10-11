# ADR-009：LLM Provider 配置管理

## 状态

Accepted

## 日期

2026-08-05

## 问题背景

当前 OPC 中央调度中枢已接入 DeepSeek 模型用于任务规划（`app/agents/planning.py`），但配置方式存在以下问题：

1. API Key、Base URL、Model 全部通过环境变量（`DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL`）注入，修改必须重启服务，无法热更新。
2. 前端管理控制台没有大模型配置入口，运营/管理员无法通过 UI 查看或修改配置。
3. 当前仅支持 DeepSeek，且客户端与 DeepSeek 强耦合，后续扩展其他 provider 成本高。

随着 MVP 推进，需要让管理员能够在前端页面配置 DeepSeek 参数，并立即生效。

## 决策

1. **新增数据库表 `llm_providers`**，用于持久化存储 LLM provider 配置，支持运行时热更新。
2. **MVP 阶段仅支持 DeepSeek 一个 provider**，但表结构和代码抽象预留多 provider 扩展能力。
3. **前端新增「系统设置」→「大模型配置」页面**，提供表单编辑和保存功能。
4. **API Key 不加密存储**，MVP 阶段简化实现，后续根据安全审计结果决定是否加密。
5. **删除旧的环境变量配置逻辑**，`DeepSeekClient` 改为从数据库读取配置；原 `DEEPSEEK_*` 环境变量仅在系统首次初始化时用于生成默认记录，后续运行不再依赖环境变量。
6. **后端先开发、先测试，前端后对接**，符合项目 backend-first 约定。

## 备选方案

### 方案 A：继续走环境变量 + 前端只读展示（已否决）

- **优点**：实现简单，不需要新增表和接口。
- **缺点**：修改配置必须重启服务，无法热更新；前端只能展示，不能编辑。
- **否决原因**：不满足用户「运行时热更新」和「前端配置入口」的核心诉求。

### 方案 B：数据库存储 + CRUD 接口 + 前端配置页（已采纳）

- **优点**：支持热更新、可扩展多 provider、前端可编辑、符合配置管理通用模式。
- **缺点**：需要新增数据库表、API 接口和前端页面，开发量比方案 A 大。
- **采纳原因**：满足 MVP 阶段全部诉求，且为后续扩展预留空间。

### 方案 C：使用外部配置中心（如 Consul/Etcd）

- **优点**：分布式场景下配置同步能力强。
- **缺点**：引入额外依赖，MVP 阶段过度设计。
- **否决原因**：当前为单实例 MVP，PostgreSQL 已能满足配置持久化需求。

## 影响分析

1. **架构影响**：新增 `app/services/llm_provider_service.py` 负责配置读取与缓存；`DeepSeekClient` 改为接收配置参数而非直接读取环境变量。
2. **数据影响**：新增 `llm_providers` 表；初始化脚本需插入一条默认 DeepSeek 记录（使用原环境变量值或占位值）。
3. **接口影响**：新增 `GET/PUT /api/v1/settings/llm` 接口，遵循 `api-standards.md` 统一响应格式。
4. **前端影响**：新增 `Settings` 页面和 `LLMConfig` 子页面，侧边栏新增「系统设置」菜单项。
5. **测试影响**：新增单元测试、API 测试；AI 调用测试需使用 mock，避免真实请求消耗 token。
6. **安全影响**：API Key 明文存储在数据库中，MVP 阶段 Acceptable，后续需评估是否加密。

## 关联文档

- [002-central-dispatch-hub-spec.md](../spec/002-central-dispatch-hub-spec.md)
- [009-llm-provider-management-spec.md](../spec/009-llm-provider-management-spec.md)
- [009-llm-provider-management-harness.md](../spec/009-llm-provider-management-harness.md)
- [api-standards.md](../standards/api-standards.md)
- [database-standards.md](../standards/database-standards.md)
- [environment-config-standards.md](../standards/environment-config-standards.md)

# ADR-002：后端技术栈选型

## 状态

Accepted

## 日期

2026-08-03

## 问题背景

中央调度中枢是 OPC 项目的核心，负责：

- 自然语言需求解析。
- 任务拆解与编排。
- Agent 节点注册、心跳、负载管理。
- 任务调度、进度聚合、异常处理。
- 与 Agent 节点实时通信。

后端需要高并发异步处理能力、快速迭代能力、与 AI/LLM 生态深度集成。

## 决策

采用 **FastAPI + Python 3.11+** 作为后端技术栈。

## 备选方案

| 方案 | 优势 | 劣势 | 结论 |
|------|------|------|------|
| FastAPI + Python 3.11+ | 异步原生支持，AI/LLM 生态极强，开发效率高，团队熟悉 | Python 并发上限不如 Go/Java | ✅ 选中 |
| NestJS（Node.js + TypeScript） | 前后端统一 TS，实时通信强 | AI/ML 生态弱，Agent 节点可能仍需 Python | ❌ 否决 |
| Go + Gin/Fiber | 高并发、部署轻量、性能好 | 团队经验少，AI 生态弱 | ❌ 否决 |
| Java Spring Cloud | 微服务生态完善 | 太重，部署复杂，不适合 MVP 快速验证 | ❌ 否决 |

## 决策原因

1. **调度中枢适合 Python**：任务调度、流程编排、LLM 调用等逻辑在 Python 中表达更自然。
2. **AI 生态刚需**：需求解析、任务拆解、Agent 能力调用大量依赖 LLM，Python 是首选。
3. **异步性能足够**：FastAPI 基于 Starlette + Uvicorn，asyncio 足以支撑 MVP 阶段的并发需求。
4. **团队熟悉**：项目成员已有 FastAPI 经验，可降低学习成本和开发风险。

## 影响

- 后端工程目录命名为 `opc-central-dispatch-hub/`。
- ORM 使用 SQLAlchemy 2.x，迁移使用 Alembic。
- 配置管理使用 Pydantic Settings。
- WebSocket 使用 FastAPI 原生 `WebSocket` 支持。
- 部署使用 Uvicorn + Gunicorn，容器化使用 Docker。

## 关联文档

- [项目设计文档](../OPC超级个体%20·%20AI员工智能体协作平台｜项目设计文档（正式版）.md)
- [coding-standards.md](../standards/coding-standards.md)
- [api-standards.md](../standards/api-standards.md)
- [database-standards.md](../standards/database-standards.md)

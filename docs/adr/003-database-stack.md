# ADR-003：数据库与缓存选型

## 状态

Accepted

## 日期

2026-08-03

## 问题背景

OPC 项目需要持久化存储：

- 任务、子任务、任务日志、交付物元数据。
- Agent 节点注册信息、状态、心跳记录。
- 系统告警、操作记录。

同时需要：

- 任务队列、节点心跳缓存、进度缓存、分布式锁。
- 高并发读写场景下的状态快速更新。

## 决策

- **主数据库**：PostgreSQL 15+
- **缓存 / 队列 / 实时状态**：Redis 7+
- **ORM / 迁移**：SQLAlchemy 2.x + Alembic

## 备选方案

### 主数据库

| 方案 | 优势 | 劣势 | 结论 |
|------|------|------|------|
| PostgreSQL | 关系模型成熟，JSONB 支持灵活，团队熟悉，开源免费 | 纯文档场景不如 MongoDB 灵活 | ✅ 选中 |
| MySQL 8 | 国内生态广泛 | JSON 支持和扩展性弱于 PostgreSQL | ❌ 否决 |
| MongoDB | 文档模型灵活，适合动态结构 | 事务和复杂查询支持较弱，关系表达不自然 | ❌ 否决 |

### 缓存 / 队列

| 方案 | 优势 | 劣势 | 结论 |
|------|------|------|------|
| Redis | 团队熟悉，支持 string/list/set/zset/pub-sub，适合做队列、缓存、心跳 | 消息持久化能力不如 RabbitMQ/Kafka | ✅ 选中（MVP） |
| RabbitMQ | 消息队列功能完善 | 增加运维复杂度 | ❌ 否决 |
| Kafka | 适合高吞吐事件流 | 太重，不适合 MVP | ❌ 否决 |

## 决策原因

1. **PostgreSQL 满足核心需求**：任务、节点、日志都是关系型数据，PostgreSQL 稳定可靠，JSONB 可承载动态配置。
2. **Redis 一专多能**：任务队列、心跳缓存、进度缓存、分布式锁都可以用 Redis 实现，减少组件数量。
3. **团队熟悉**：项目成员已有 PostgreSQL + Redis 使用经验，降低风险。
4. **MVP 阶段保持简单**：避免过早引入 RabbitMQ/Kafka，等业务量增长后再评估是否需要专用消息队列。

## 影响

- 所有表结构变更必须通过 Alembic 迁移。
- Redis key 统一加 `opc:` 前缀。
- 任务队列使用 Redis List 或 Sorted Set 实现。
- 节点心跳使用 Redis + TTL 实现。

## 关联文档

- [项目设计文档](../OPC超级个体%20·%20AI员工智能体协作平台｜项目设计文档（正式版）.md)
- [database-standards.md](../standards/database-standards.md)
- [api-standards.md](../standards/api-standards.md)

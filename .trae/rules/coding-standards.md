***

## alwaysApply: true

# 开发规范先加载规则

## 核心原则

**修改任何代码前，必须先加载并遵守** **`docs/standards/`** **中对应的开发规范**，确保：

- 代码风格、命名、目录结构统一。

- API、数据库、Agent 节点实现符合项目约定。

- Git 提交信息可追溯、可自动化处理。

- 不同模块的代码在边界处保持一致。

***

## 规范文档清单

所有开发规范统一存放于 `docs/standards/`，修改代码前按需加载：

| 规范文档         | 路径                                               | 何时必须加载                                                     |
| ------------ | ------------------------------------------------ | ---------------------------------------------------------- |
| 代码与命名规范      | `docs/standards/coding-standards.md`             | 每次写/改代码前                                                   |
| Git 提交规范     | `docs/standards/git-commit-standards.md`         | 每次执行 git commit 前                                          |
| API 接口规范     | `docs/standards/api-standards.md`                | 新增/修改接口前                                                   |
| 数据库规范        | `docs/standards/database-standards.md`           | 新增/修改表结构、字段、索引前                                            |
| Agent 节点开发规范 | `docs/standards/agent-node-standards.md`         | 新增/修改 Agent 节点代码前                                          |
| 日志规范         | `docs/standards/logging-standards.md`            | 每次写/改后端 Python 代码前（尤其是 API 端点、Service 方法）                  |
| 环境配置规范       | `docs/standards/environment-config-standards.md` | 新增/修改配置项、环境变量前                                             |
| 前端组件规范       | `docs/standards/frontend-component-standards.md` | 新增/修改 Ant Design 组件、前端组件文件前                                |
| 后端组件规范       | `docs/standards/backend-component-standards.md`  | 新增/修改 FastAPI 路由、Pydantic Schema、SQLAlchemy 模型、Alembic 迁移前 |

***

## 必读流程

修改任何代码前，按以下顺序操作：

### 步骤 1：识别本次修改涉及的规范

根据修改范围，确定需要加载的规范：

- 涉及后端 Python 代码 → 加载 `coding-standards.md` + `backend-component-standards.md` + `logging-standards.md`。

- 涉及前端 React/TypeScript 代码 → 加载 `coding-standards.md` + `frontend-component-standards.md`。

- 涉及接口新增或变更 → 额外加载 `api-standards.md`。

- 涉及数据库模型、表结构、迁移 → 额外加载 `database-standards.md`。

- 涉及 Agent 节点注册、心跳、任务执行、交付物 → 额外加载 `agent-node-standards.md`。

- 涉及配置项、环境变量、`.env` 文件、Pydantic Settings → 额外加载 `environment-config-standards.md`。

### 步骤 2：阅读相关规范

重点关注：

- **coding-standards.md**：命名规则、目录结构、类型注解、错误处理、日志规范。

- **git-commit-standards.md**：提交类型、scope、分支模型、PR 规范。

- **api-standards.md**：URL 设计、响应格式、错误码、WebSocket 事件命名。

- **database-standards.md**：表/字段命名、索引命名、迁移规范、Redis key 规范。

- **agent-node-standards.md**：节点目录结构、通信事件、任务执行流程、交付物规范。

- **logging-standards.md**：日志级别、结构化格式、接口日志、业务事件、敏感信息脱敏。

- **environment-config-standards.md**：环境划分、`.env` 管理、Pydantic Settings、生产安全要求。

- **frontend-component-standards.md**：Ant Design 组件踩坑规则、组件文件组织、样式管理。

- **backend-component-standards.md**：FastAPI 路由模式、Pydantic Model/Schema 分离、SQLAlchemy 2.0 异步查询、Alembic 迁移、Import 排序、异常层级。

### 步骤 3：按规范编写代码

- 命名、格式、结构必须与规范一致。

- 接口路径、状态码、错误码必须复用规范中的约定。

- 数据库变更必须写 Alembic 迁移脚本。

- Agent 节点通信事件必须复用 `api-standards.md` 中定义的事件命名和 payload 格式。

- 日志必须使用 `logging-standards.md` 定义的结构化 JSON 格式，关键业务事件必须记录。

- 配置必须走 Pydantic Settings，敏感信息走环境变量，禁止硬编码。

### 步骤 4：提交前检查

- 提交信息是否符合 `git-commit-standards.md`。

- 是否引入了与规范冲突的命名或结构。

- 是否在 PR 描述中引用了相关 Spec/ADR。

***

## 关联 `.trae/rules/decision-documents.md`

本规则与 `decision-documents.md` 并行执行：

1. **先加载决策文档**：确认本次变更是否违反已有 ADR/Spec/Harness。
2. **再加载开发规范**：确认代码实现是否符合命名、接口、数据库等约定。

顺序不可颠倒：先决策，后规范，最后编码。

***

## 违规处理

未按规范编写代码，导致命名混乱、接口不一致、数据库结构不符合约定的，按以下流程处理：

1. 识别违规点。
2. 回退或修正违规代码。
3. 重新阅读对应规范。
4. 按规范重写并补齐测试。
5. 在 `docs/INDEX.md` 的「文档更新记录」中记录规范修正（如规范本身不完善）。

***

## 规范本身的维护

当发现 `docs/standards/` 中某项规范需要调整时：

1. 先更新规范文档。
2. 在 `docs/INDEX.md`「文档更新记录」中登记。
3. 再按新规范修改代码。

禁止在代码中临时绕过规范而不更新文档。

***

## 变更记录

| 日期         | 变更内容                                        | 变更人  |
| ---------- | ------------------------------------------- | ---- |
| 2026-08-03 | 初始创建：要求 AI 每次写代码前先加载 `docs/standards/` 对应规范 | AI助手 |


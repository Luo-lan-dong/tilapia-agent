# OPC 项目代码与命名规范

## 1. 总则

- 代码是写给人看的，机器只是偶尔执行。
- 保持简单、明确、一致，避免过度设计。
- 每个模块职责单一，函数/类只做一件事。
- 优先使用显式命名，避免 magic number 和缩写。

## 2. 目录结构规范

```
opc-central-dispatch-hub/          # 中央调度中枢后端
├── app/
│   ├── api/                       # API 路由层
│   ├── core/                      # 配置、安全、事件总线
│   ├── models/                    # 数据库模型/Pydantic 模型
│   ├── schemas/                   # 请求/响应 DTO
│   ├── services/                  # 业务逻辑层
│   ├── tasks/                     # 任务调度、队列处理
│   └── main.py                    # 应用入口
├── tests/
├── Dockerfile
└── pyproject.toml

opc-web-console/                   # 前端管理控制台
├── src/
│   ├── api/                       # 后端接口封装
│   ├── components/                # 公共组件
│   ├── hooks/                     # 自定义 Hooks
│   ├── pages/                     # 页面级组件
│   ├── stores/                    # 状态管理（按需创建：仅在出现真正跨页面
│   │                              #   全局状态需求时引入，避免空目录与过度设计）
│   ├── types/                     # TypeScript 类型定义
│   └── utils/                     # 工具函数
├── tests/
└── package.json

opc-agent-node-template/           # Agent 节点模板
├── app/
│   ├── client/                    # 与中枢通信客户端
│   ├── executor/                  # 任务执行器
│   ├── models/                    # 数据模型
│   └── main.py
├── tests/
└── Dockerfile
```

## 3. 命名规范

### 3.1 Python（后端 / Agent 节点）

| 类型 | 规范 | 示例 |
|------|------|------|
| 变量 | snake_case | `task_id`, `agent_node` |
| 函数 | snake_case | `dispatch_task()`, `parse_request()` |
| 类 | PascalCase | `TaskDispatcher`, `AgentNodeClient` |
| 常量 | UPPER_SNAKE_CASE | `DEFAULT_TIMEOUT_SECONDS`, `MAX_RETRY_COUNT` |
| 私有成员 | 单下划线前缀 | `_internal_state`, `_send_heartbeat()` |
| 模块/包 | 小写，可用下划线 | `task_scheduler.py`, `agent_client` |
| 文件 | 小写 + 连字符/下划线 | `task-dispatcher.py` 或 `task_dispatcher.py`（统一即可） |

### 3.2 TypeScript / React（前端）

| 类型 | 规范 | 示例 |
|------|------|------|
| 变量/函数 | camelCase | `taskId`, `fetchTaskList()` |
| 组件/类型/接口 | PascalCase | `TaskCard.tsx`, `TaskStatus` |
| 常量 | UPPER_SNAKE_CASE | `API_BASE_URL`, `DEFAULT_PAGE_SIZE` |
| 自定义 Hook | use 前缀 + PascalCase | `useTaskList`, `useAgentStatus` |
| 类型别名 | PascalCase | `type TaskPayload = {...}` |
| 接口 | PascalCase，可加 I 前缀（团队统一即可） | `interface ITaskPayload` 或 `interface TaskPayload` |
| 文件 | 组件 PascalCase，工具 camelCase | `TaskCard.tsx`, `request.ts` |

### 3.3 CSS / 样式

- 使用 Tailwind CSS 或 CSS Modules，禁止手写全局样式覆盖。
- 类名优先使用语义化命名，如 `task-status-badge`。

## 4. 代码风格

### 4.1 Python

- 使用 `ruff` 或 `black` 格式化。
- 函数长度不超过 50 行，超过必须拆分。
- 类型注解必须写，返回值也要标注。
- 异步函数优先使用 `async/await`。
- FastAPI/Pydantic/SQLAlchemy 的具体使用规则详见 `backend-component-standards.md`。

```python
# 推荐
async def dispatch_task(task_id: str, agent_id: str) -> TaskDispatchResult:
    task = await task_service.get_task(task_id)
    if task.status != TaskStatus.PENDING:
        raise TaskStateError(f"Task {task_id} is not pending")
    return await agent_client.send_task(agent_id, task)

# 不推荐
def dispatch(task, agent):
    # ... 过长且无类型注解
```

### 4.2 TypeScript

- 开启 `strict` 模式。
- 优先使用 `interface` 定义对象结构，`type` 用于联合类型。
- 禁止 `any`，必要时使用 `unknown` + 类型守卫。
- 组件 props 必须显式定义类型。
- Ant Design 组件的具体使用规则详见 `frontend-component-standards.md`。

```typescript
// 推荐
interface TaskCardProps {
  taskId: string;
  status: TaskStatus;
  onRetry: (taskId: string) => void;
}

export function TaskCard({ taskId, status, onRetry }: TaskCardProps) {
  return <div>...</div>;
}
```

### 4.3 文件长度规范

- 单个业务文件代码行数建议不超过 **300 行**。
- 当文件超过 **250 行**时，应主动审视是否承担了多个职责，必要时按职责拆分为多个文件。
- 文件行数限制是控制复杂度的手段，核心目标是保持**单一职责**。

#### 不同类型文件建议上限

| 文件类型 | 建议上限 | 说明 |
|---|---|---|
| Python 后端业务文件 | 300 行 | service、模型、工具类等 |
| Python API 路由文件 | 250 行 | 路由只负责入口转发，逻辑下沉到 service |
| React/TS 页面组件 | 350 行 | 页面组件允许略多，因包含布局和状态 |
| React/TS 通用组件 | 200 行 | 公共组件必须保持单一职责 |
| Hook / 工具函数文件 | 200 行 | 一个文件只放一组相关逻辑 |
| 测试文件 | 400 行 | 测试文件可以稍长，但应按场景拆文件 |
| `__init__.py` / 常量表 | 可豁免 | 纯导出、配置映射、枚举定义等 |

#### 豁免场景

以下情况可突破上限，但应在文件顶部注释说明原因：

- 工具生成的代码（如 OpenAPI client、迁移脚本）。
- 大型常量字典或配置映射。
- 数据初始化脚本。
- Alembic 迁移文件。

#### 示例

```python
# 推荐：按职责拆分为两个文件
# task_service.py
async def create_task(payload: TaskCreateRequest) -> Task:
    ...

# task_scheduler.py
async def dispatch_pending_tasks() -> None:
    ...

# 不推荐：一个文件里同时包含创建、调度、查询、统计逻辑
# task_all_in_one.py（超过 300 行）
```

## 5. 注释规范

- 优先用清晰命名代替注释。
- 复杂业务逻辑必须加注释，说明「为什么」而非「做什么」。
- 公共 API、函数、类必须写 docstring / JSDoc。
- 禁止保留已废弃的代码注释块。

```python
# 推荐
async def migrate_task_on_node_failure(task_id: str) -> str:
    """
    当节点心跳中断时，将任务迁移到同类型空闲节点。

    注意：仅迁移状态为 RUNNING 或 PENDING 的任务，已完成任务不处理。
    """
```

## 6. 错误处理

- 统一使用自定义异常，禁止裸抛 `Exception`。
- 异常信息必须包含上下文（task_id、agent_id 等）。
- 边界处（用户输入、外部 API、文件 IO）必须校验。

```python
class TaskDispatchError(Exception):
    def __init__(self, task_id: str, reason: str):
        super().__init__(f"Task {task_id} dispatch failed: {reason}")
        self.task_id = task_id
```

## 7. 日志规范

- 使用结构化日志（Python 用 `structlog` 或标准 `logging`）。
- 日志级别规范：DEBUG（调试）、INFO（关键流程）、WARNING（可恢复异常）、ERROR（需人工介入）。
- 关键节点必须打日志：任务分配、开始执行、完成、失败、迁移。

```python
logger.info(
    "task_dispatched",
    task_id=task_id,
    agent_id=agent_id,
    agent_type=agent_type,
)
```

## 8. 配置文件

- 敏感信息（密码、密钥）必须走环境变量，禁止硬编码。
- 使用 Pydantic Settings 管理配置。
- 不同环境配置通过 `.env` 文件区分。

## 9. 测试规范

- 单元测试文件名：`test_*.py`（后端）或 `*.test.ts`（前端）。
- 测试覆盖核心业务流程，而非仅覆盖 getter/setter。
- 测试数据与测试代码分离，使用 fixture / factory。

## 10. 变更记录

| 日期 | 变更内容 | 变更人 |
|------|---------|--------|
| 2026-08-03 | 初始创建 | AI助手 |
| 2026-08-03 | 新增 4.3 文件长度规范，明确业务文件 300 行上限及豁免场景 | AI助手 |
| 2026-08-06 | 4.2 节新增交叉引用，指向独立的 frontend-component-standards.md | AI助手 |
| 2026-08-06 | 4.1 节新增交叉引用，指向独立的 backend-component-standards.md | AI助手 |

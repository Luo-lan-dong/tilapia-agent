# OPC 后端组件使用规范

## 1. 适用范围

本规范适用于 `opc-central-dispatch-hub/` 和 `opc-agent-programming/` 项目。

涉及**新增/修改后端 Python 组件**前，必须加载本规范。

基本命名、文件长度、注释、日志、配置规范见 `coding-standards.md`，本规范不重复。

---

## 2. FastAPI 使用规范

### 2.1 APIRouter 组织

每个业务模块一个 router 文件，路由函数只做参数转发，逻辑下沉到 service 层。

```python
# ✅ router 只转发
@router.post("/tasks", response_model=TaskResponse)
async def create_task(payload: TaskCreateRequest, service: TaskService = Depends()):
    return await service.create(payload)

# ❌ router 里写业务逻辑
@router.post("/tasks")
async def create_task(payload: dict):
    task = Task(...)
    db.add(task)
    await db.commit()
    return task
```

### 2.2 Dependency Injection

- 用 `Depends()` 注入服务、数据库会话、配置，禁止在路由函数里直接 import 实例。
- 共享依赖（如数据库会话）定义在 `api/deps.py`，模块私有依赖定义在模块内。

```python
# api/deps.py
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        yield session

# api/v1/endpoints/tasks.py
@router.post("/tasks")
async def create_task(
    payload: TaskCreateRequest,
    db: AsyncSession = Depends(get_db),
    service: TaskService = Depends(),
):
    ...
```

### 2.3 Response Model

- 所有路由必须声明 `response_model`，不要裸返回数据库模型。
- 响应用 `response_model_exclude_unset=True` 避免返回 None 字段。

```python
@router.get("/tasks/{task_id}", response_model=TaskResponse, response_model_exclude_unset=True)
async def get_task(task_id: str, service: TaskService = Depends()):
    return await service.get(task_id)
```

### 2.4 WebSocket 事件

- WebSocket 消息格式统一用 JSON，包含 `event` 字段区分事件类型。
- 事件命名用 `snake_case`，格式：`{模块}.{动作}`，如 `hub.task_dispatch`、`agent.heartbeat`。
- 所有 WebSocket 消息必须有对应的 Schema 定义。

### 2.5 错误响应

- 使用 FastAPI 的 `HTTPException`，禁止在路由里裸 `raise Exception`。
- 错误响应格式统一，使用项目自定义的错误码体系（见 `api-standards.md`）。

```python
from fastapi import HTTPException

@router.get("/tasks/{task_id}")
async def get_task(task_id: str, service: TaskService = Depends()):
    task = await service.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    return task
```

---

## 3. Pydantic 使用规范

### 3.1 Model vs Schema 分离

| 类型 | 存放位置 | 命名 | 用途 |
|------|---------|------|------|
| 数据库模型 | `app/models/` | `Task`、`AgentNode` | SQLAlchemy ORM 模型 |
| 请求 Schema | `app/schemas/` | `TaskCreateRequest`、`TaskUpdateRequest` | 接收客户端输入 |
| 响应 Schema | `app/schemas/` | `TaskResponse`、`TaskListResponse` | 返回给客户端 |
| 内部 Schema | `app/schemas/` | `TaskInternal` | 服务间传递，包含敏感字段 |

禁止在路由层直接使用数据库模型作为请求/响应体。

### 3.2 Field 校验器

- 必填字段不设默认值，可选字段用 `Field(default=...)`。
- 字符串校验用 `Field(min_length=..., max_length=..., pattern=...)`。
- 复杂校验用 `@field_validator`，校验失败抛 `ValueError`。

```python
class TaskCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    agent_type: str = Field(pattern=r"^(programming|video|copywriting)$")
    priority: int = Field(default=1, ge=1, le=5)
```

### 3.3 模型配置

- 数据库模型的 `Config` 用 `from_attributes=True`（Pydantic v2）。
- Schema 的 `model_config` 禁止使用 `orm_mode`（已废弃）。

```python
# 数据库模型 Schema
class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    status: TaskStatus
```

---

## 4. SQLAlchemy 2.0 使用规范

### 4.1 异步模式

项目使用 `sqlalchemy[asyncio]`，所有数据库操作必须用 async。

```python
from sqlalchemy.ext.asyncio import AsyncSession

async def get_task(db: AsyncSession, task_id: str) -> Task | None:
    result = await db.execute(select(Task).where(Task.id == task_id))
    return result.scalar_one_or_none()
```

### 4.2 查询模式

- 用 `select()` 语法，禁止用已废弃的 `session.query()`。
- 批量操作用 `executemany()`，不要循环 `add()` + `commit()`。

```python
# ✅ select 语法
stmt = select(Task).where(Task.status == TaskStatus.PENDING).order_by(Task.created_at)
result = await db.execute(stmt)
tasks = result.scalars().all()

# ❌ 废弃的 query 语法
tasks = await db.query(Task).filter(Task.status == TaskStatus.PENDING).all()
```

### 4.3 关系定义

- 一对多用 `relationship()` + `back_populates`。
- 外键必须显式定义，禁止依赖隐式外键命名。

```python
class AgentNode(Base):
    __tablename__ = "agent_nodes"

    id: Mapped[str] = mapped_column(primary_key=True)
    tasks: Mapped[list["Task"]] = relationship("Task", back_populates="agent")

class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(primary_key=True)
    agent_id: Mapped[str | None] = mapped_column(ForeignKey("agent_nodes.id"))
    agent: Mapped["AgentNode | None"] = relationship("AgentNode", back_populates="tasks")
```

---

## 5. Alembic 迁移规范

- 每次表结构变更必须写迁移脚本，禁止手动改库。
- 迁移脚本命名：`{timestamp}_{描述}.py`，描述用下划线分隔。
- 迁移脚本必须包含 `upgrade()` 和 `downgrade()`，downgrade 不能省略。

```python
def upgrade() -> None:
    op.add_column("tasks", sa.Column("priority", sa.Integer(), server_default="1"))

def downgrade() -> None:
    op.drop_column("tasks", "priority")
```

---

## 6. 异步规范

### 6.1 async vs sync 选择

- 涉及 I/O（数据库、HTTP、文件、WebSocket）必须用 async。
- 纯计算逻辑用 sync，不要为了「统一」而把 CPU 密集操作标为 async。
- 调用第三方同步库（如 `requests`）时，用 `asyncio.to_thread()` 包装，禁止直接调用阻塞事件循环。

```python
import asyncio

async def fetch_external_api(url: str) -> dict:
    # 如果 httpx 没有 async 支持，用 to_thread 包装
    return await asyncio.to_thread(requests.get, url)
```

### 6.2 异步上下文管理器

数据库会话、HTTP 客户端等资源必须用 `async with` 管理，确保资源释放。

```python
async with async_session() as session:
    result = await session.execute(select(Task))
    ...
# session 自动关闭
```

---

## 7. Import 规范

导入顺序按组排列，组间空一行：

```python
# 1. 标准库
import asyncio
from datetime import datetime

# 2. 第三方库
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

# 3. 本地模块
from app.core.config import settings
from app.models.task import Task
from app.schemas.task import TaskCreateRequest, TaskResponse
from app.services.task_service import TaskService
```

- 禁止使用相对导入（`from .models import Task`），统一用绝对导入。
- 类型检查导入用 `TYPE_CHECKING`，避免循环依赖。

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.agent_node import AgentNode
```

---

## 8. 异常层级设计

```
AppError (基类)
├── TaskError
│   ├── TaskNotFoundError
│   ├── TaskDispatchError
│   └── TaskTimeoutError
├── AgentError
│   ├── AgentNotFoundError
│   ├── AgentOfflineError
│   └── AgentRegistrationError
└── ConfigError
    ├── MissingConfigError
    └── InvalidConfigError
```

- 所有自定义异常继承 `AppError`。
- 异常命名以 `Error` 结尾，包含领域前缀。
- 异常必须携带上下文（`task_id`、`agent_id` 等），不要只传字符串。

```python
class AppError(Exception):
    """所有业务异常的基类"""
    pass

class TaskNotFoundError(AppError):
    def __init__(self, task_id: str):
        super().__init__(f"Task {task_id} not found")
        self.task_id = task_id
```

---

## 9. 变更记录

| 日期 | 变更内容 | 变更人 |
|------|---------|--------|
| 2026-08-06 | 初始创建：FastAPI、Pydantic、SQLAlchemy、Alembic、异步、Import、异常层级规范 | AI助手 |

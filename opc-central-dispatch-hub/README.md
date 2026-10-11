# OPC 中央调度中枢

OPC（Open Project Cluster）中央调度中枢 FastAPI 后端服务，负责任务调度、Agent 节点管理以及与 LLM 服务的交互。

## 本地开发（源码运行）

### 前置条件

- Python 3.11+
- PostgreSQL 16（本地安装或远程实例）
- Redis **不需要**——`REDIS_URL` 仅需占位值满足配置校验（代码零调用）

### 1. 准备数据库

在 PostgreSQL 中创建用户与数据库（已有所需实例可跳过）：

```sql
CREATE USER opc WITH PASSWORD 'opc';
CREATE DATABASE opc OWNER opc;
```

### 2. 配置环境变量

```bash
cp .env.example .env.local
```

编辑 `.env.local`，至少确认两项：

```ini
DATABASE_URL=postgresql+asyncpg://opc:opc@localhost:5432/opc
REDIS_URL=redis://localhost:6379/0        # 占位即可，不会被实际连接
```

其余配置项均有默认值，详见 `.env.example` 内注释。

### 3. 安装依赖

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows；Linux/macOS 为 source .venv/bin/activate
pip install -e ".[dev]"
```

### 4. 初始化表结构

```bash
alembic upgrade head
```

自动读取 `.env.local` 的 `DATABASE_URL`，把全部迁移跑到最新（建出所有表）。
注意：本命令只建表，**不创建数据库**——上一步的 `CREATE DATABASE` 必须先完成。

### 5. 启动服务

```bash
# 仅本地访问
.venv\Scripts\python -m uvicorn main:app --reload

# 允许局域网/子系统访问
.venv\Scripts\python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

### 6. 验证

- Swagger UI：`http://localhost:8000/docs`
- 健康检查：`http://localhost:8000/api/v1/health/`

### LLM 模型配置

- **推荐**：运行后在 Web 界面「系统设置 → 模型管理」配置（凭据存数据库）；
- 或在 `.env.local` 填写 `DEEPSEEK_API_KEY` 等——首次启动且模型表为空时自动写入默认
  Provider 种子，之后仍以数据库为准。

### 前端（可选）

后端单独可用（Swagger UI 即界面）。需要完整 Web 控制台时：

```bash
cd ../opc-web-console
npm install
npm run dev        # 开发模式 http://localhost:5173，代理 /api 到 8000
```

## 下一步

- 节点（Agent）安装与部署：见 [docs/ops/DEPLOYMENT.md](../docs/ops/DEPLOYMENT.md)
- 桌面版（内嵌 PostgreSQL，免装 PG）：见 [docs/ops/hub-desktop-build-guide.md](../docs/ops/hub-desktop-build-guide.md)

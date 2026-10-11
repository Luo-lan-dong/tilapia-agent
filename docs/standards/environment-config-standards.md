# OPC 项目环境配置规范

## 1. 设计目标

- 区分开发、测试、生产环境，避免配置混乱。
- 所有配置集中管理，禁止硬编码。
- 敏感信息（密码、密钥）必须走环境变量或密钥管理服务。
- 同一套代码在不同环境通过配置切换行为。

## 2. 环境划分

| 环境 | 用途 | 调试信息 | 日志级别 |
|------|------|---------|---------|
| `local` | 开发者本地调试 | 开启 | DEBUG |
| `dev` | 开发联调环境 | 开启 | DEBUG |
| `staging` | 预发布/测试环境 | 部分开启 | INFO |
| `prod` | 生产环境 | 关闭 | INFO |

## 3. 配置管理方式

采用 **环境变量 + `.env` 文件 + Pydantic Settings** 三层管理：

1. **环境变量**：最高优先级，用于生产部署和 CI/CD。
2. **`.env` 文件**：本地开发使用，不提交到 Git。
3. **Pydantic Settings**：代码中统一读取和校验配置。

### 3.1 配置文件命名

| 文件名 | 用途 | 是否提交 Git |
|--------|------|------------|
| `.env.example` | 配置模板，供开发者参考 | ✅ 是 |
| `.env.local` | 本地开发配置 | ❌ 否 |
| `.env.development` | 开发环境配置 | ❌ 否 |
| `.env.staging` | 预发布环境配置 | ❌ 否 |
| `.env.production` | 生产环境配置 | ❌ 否 |

### 3.2 `.env.example` 示例

```env
# 应用基础配置
APP_ENV=local
APP_NAME=opc-central-dispatch-hub
DEBUG=true
LOG_LEVEL=DEBUG

# 后端服务
HOST=0.0.0.0
PORT=8000
WORKERS=1

# 数据库
DATABASE_URL=postgresql+asyncpg://opc:opc@localhost:5432/opc
DATABASE_POOL_SIZE=10

# Redis
REDIS_URL=redis://localhost:6379/0

# MinIO / S3
STORAGE_ENDPOINT=minio.opc.local:9000
STORAGE_ACCESS_KEY=your-access-key
STORAGE_SECRET_KEY=your-secret-key
STORAGE_BUCKET=opc-bucket
STORAGE_USE_SSL=true

# 安全
SECRET_KEY=change-me-in-production
AGENT_REGISTRATION_TOKEN=change-me-in-production
ACCESS_TOKEN_EXPIRE_MINUTES=60

# CORS
CORS_ORIGINS=http://localhost:5173,http://localhost:3000

# 外部服务
OPENAI_API_KEY=sk-xxx
```

## 4. 各环境关键差异

### 4.1 local / dev

- `DEBUG=true`
- `LOG_LEVEL=DEBUG`
- 数据库使用本地或 Docker 部署的 PostgreSQL/Redis。
- 详细错误信息返回给调用方。
- CORS 允许本地前端地址。

### 4.2 staging

- `DEBUG=false`
- `LOG_LEVEL=INFO`
- 使用接近生产的独立数据库和 Redis。
- 错误信息脱敏，不暴露内部堆栈。
- 开启基础监控和告警。

### 4.3 prod

- `DEBUG=false`
- `LOG_LEVEL=INFO`
- 数据库、Redis、MinIO 使用高可用部署。
- 敏感配置走 KMS / Vault / Kubernetes Secrets。
- CORS 严格限制为生产域名。
- 开启全量日志采集、监控告警、链路追踪。

## 5. Pydantic Settings 规范

后端统一使用 Pydantic Settings 管理配置：

```python
from pydantic_settings import BaseSettings
from pydantic import Field

class Settings(BaseSettings):
    app_env: str = Field(default="local", alias="APP_ENV")
    debug: bool = Field(default=False, alias="DEBUG")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    database_url: str = Field(alias="DATABASE_URL")
    redis_url: str = Field(alias="REDIS_URL")
    secret_key: str = Field(alias="SECRET_KEY")
    cors_origins: list[str] = Field(default=[], alias="CORS_ORIGINS")
    # 任务卡死检测阈值（秒）：最小 60 防止误配为 0
    stale_task_timeout_seconds: int = Field(
        default=1800, ge=60, alias="STALE_TASK_TIMEOUT_SECONDS"
    )

    class Config:
        env_file = ".env.local"
        env_file_encoding = "utf-8"
        extra = "ignore"
```

### 5.1 使用方式

```python
from functools import lru_cache

@lru_cache
def get_settings() -> Settings:
    return Settings()

settings = get_settings()
```

### 5.2 任务调度类配置

任务调度相关阈值（`stale_task_timeout_seconds` 等）属于"运行期行为开关"：

- **必须**通过 Pydantic Settings 读取，禁止在业务代码中硬编码常量。
- **必须**设置合理的最小值约束（如 `ge=60`），防止误配为 `0` 或负数导致立即失败。
- 修改阈值后**无需重启**配套的扫描组件：调度器每次扫描都会重新读取最新值。
- 调整后应在 `docs/INDEX.md`「文档更新记录」中登记，并在关联 ADR（如 ADR-015）中补注影响。

## 6. 前端环境配置

前端使用 `.env` 文件 + Vite 环境变量：

| 文件名 | 用途 |
|--------|------|
| `.env` | 默认配置 |
| `.env.local` | 本地覆盖 |
| `.env.development` | 开发模式 |
| `.env.production` | 生产模式 |

```env
VITE_API_BASE_URL=http://localhost:8000/api/v1
VITE_WS_BASE_URL=ws://localhost:8000/ws
VITE_APP_NAME=OPC
```

## 7. Agent 节点环境配置

Agent 节点配置与中枢类似，重点配置：

```env
APP_ENV=local
AGENT_CLIENT_ID=agent-programming-001
AGENT_TYPE=programming
HUB_API_URL=http://localhost:8000
HUB_WS_URL=ws://localhost:8000/ws/agent
AGENT_REGISTRATION_TOKEN=xxx
WORKSPACE_ROOT=/workspace
HEARTBEAT_INTERVAL_SECONDS=5
```

## 8. 禁止事项

- ❌ 禁止在代码中硬编码密码、Token、密钥。
- ❌ 禁止将 `.env.local`、`.env.production` 等真实配置文件提交到 Git。
- ❌ 禁止在生产环境开启 `DEBUG=true`。
- ❌ 禁止不同环境共用同一套数据库或 Redis。
- ❌ 禁止在日志中输出完整敏感配置。

## 9. Docker Compose 多环境配置

```
docker-compose.yml              # 基础服务定义
docker-compose.override.yml     # 本地开发覆盖
docker-compose.staging.yml      # 预发布环境
docker-compose.prod.yml         # 生产环境
```

启动命令：

```bash
# 本地开发
docker-compose up

# 预发布
docker-compose -f docker-compose.yml -f docker-compose.staging.yml up

# 生产
docker-compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

## 10. 配置变更流程

1. 在 `.env.example` 中补充新配置项。
2. 在各环境的 `.env` 文件中填入真实值。
3. 更新 Pydantic Settings 模型。
4. 在 `docs/INDEX.md`「文档更新记录」中登记。

## 11. 变更记录

| 日期 | 变更内容 | 变更人 |
|------|---------|--------|
| 2026-08-26 | 补充任务调度类配置规范：`.env.example` 与 Pydantic Settings 示例增加 `STALE_TASK_TIMEOUT_SECONDS`；新增 §5.2 明确调度阈值必须经 Pydantic Settings、带最小值约束、运行时热更新、调整须登记；同步 ADR-015 任务卡死检测 | AI助手 |
| 2026-08-03 | 初始创建 | AI助手 |

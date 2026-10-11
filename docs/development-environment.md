# OPC 本地开发环境配置

本文件记录 OPC 项目在本地/开发环境使用的数据库、缓存、文件存储连接信息，方便开发调试。

> 注意：本仓库为私有仓库，仅用于开发环境。生产环境配置请走环境变量或密钥管理服务。

## 开发环境连接信息

### PostgreSQL

```text
POSTGRES_HOST=192.168.101.101
POSTGRES_PORT=5432
POSTGRES_DB=opc
POSTGRES_USER=opc
POSTGRES_PASSWORD=
```

### Redis

```text
REDIS_HOST=192.168.101.101
REDIS_PORT=6379
REDIS_PASSWORD=
REDIS_DB=0
```

### MinIO

```text
MINIO_ENDPOINT=192.168.101.101:19000
MINIO_ACCESS_KEY=minio
MINIO_SECRET_KEY=
MINIO_BUCKET=nas
MINIO_USE_SSL=false
```

## `.env.local` 完整模板

将以下内容保存到 `opc-central-dispatch-hub/.env.local`：

```env
APP_ENV=local
APP_NAME=opc-central-dispatch-hub
DEBUG=true
LOG_LEVEL=DEBUG

HOST=0.0.0.0
PORT=8000
WORKERS=1

DATABASE_URL=postgresql+asyncpg://opc:xBHXSwscrLPHF3G6@192.168.101.101:5432/opc
DATABASE_POOL_SIZE=10

REDIS_URL=redis://:redis_dong@fl@123@192.168.101.101:6379/0

STORAGE_ENDPOINT=192.168.101.101:19000
STORAGE_ACCESS_KEY=minio
STORAGE_SECRET_KEY=
STORAGE_BUCKET=nas
STORAGE_USE_SSL=false

SECRET_KEY=dev-secret-key-change-in-production
AGENT_REGISTRATION_TOKEN=dev-agent-token
ACCESS_TOKEN_EXPIRE_MINUTES=60

CORS_ORIGINS=http://localhost:5173,http://localhost:3000
```

## 连接测试

运行项目根目录下的 `scripts/test_connections.py` 脚本验证连接：

```bash
python scripts/test_connections.py
```

## 变更记录

| 日期 | 变更内容 | 变更人 |
|------|---------|--------|
| 2026-08-03 | 初始创建，填入开发环境连接信息 | Admin |

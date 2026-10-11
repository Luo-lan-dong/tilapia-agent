# OPC 项目日志规范

## 1. 设计目标

- 支持快速定位问题（任务链路、节点状态、接口调用）。
- 支持结构化查询和日志聚合（ELK / Loki / 云日志服务）。
- 开发与生产环境差异化输出，避免生产泄露敏感信息。
- 所有关键操作可追溯，尤其是任务调度、节点通信、文件操作。

## 2. 日志级别

| 级别 | 使用场景 |
|------|---------|
| `DEBUG` | 开发调试信息，如详细变量、循环内部状态 |
| `INFO` | 关键业务流程节点，如任务创建、分配、完成、节点上下线 |
| `WARNING` | 可恢复异常或预期外情况，如重试、超时、节点短暂失联 |
| `ERROR` | 需要人工介入的错误，如任务执行失败、数据库连接异常 |
| `CRITICAL` | 系统级故障，如中枢无法启动、存储服务完全不可用 |

## 3. 结构化日志格式

所有日志统一输出 JSON，禁止纯文本自由格式：

```json
{
  "timestamp": "2026-08-03T12:00:00.123+08:00",
  "level": "INFO",
  "logger": "services.task_scheduler",
  "message": "task_dispatched",
  "request_id": "req_xxxxxxxx",
  "task_id": "task_xxxxxxxx",
  "agent_id": "agent-programming-001",
  "agent_type": "programming",
  "duration_ms": 45,
  "event": "task_dispatched",
  "metadata": {}
}
```

### 必填字段

| 字段 | 说明 |
|------|------|
| `timestamp` | ISO 8601 格式，带时区 |
| `level` | 日志级别 |
| `logger` | 日志器名称，建议使用模块路径 |
| `message` | 简短事件名，用 snake_case |
| `request_id` | 请求链路 ID，无则为空 |

### 业务字段（按需填充）

| 字段 | 说明 |
|------|------|
| `task_id` | 关联任务 ID |
| `agent_id` / `client_id` | 关联 Agent 节点 ID |
| `agent_type` | 节点类型 |
| `user_id` | 操作用户 ID |
| `duration_ms` | 操作耗时 |
| `event` | 事件名，与 message 一致 |
| `metadata` | 扩展信息对象 |

## 4. 接口日志规范

### 4.1 请求进入日志

每个 HTTP/WebSocket 请求进入时必须记录：

```json
{
  "timestamp": "2026-08-03T12:00:00.123+08:00",
  "level": "INFO",
  "logger": "api.middleware.request_logger",
  "message": "request_started",
  "request_id": "req_xxxxxxxx",
  "method": "POST",
  "path": "/api/v1/tasks",
  "client_ip": "192.168.1.100",
  "user_agent": "Mozilla/5.0...",
  "event": "request_started"
}
```

### 4.2 请求响应日志

每个请求结束时必须记录：

```json
{
  "timestamp": "2026-08-03T12:00:00.168+08:00",
  "level": "INFO",
  "logger": "api.middleware.request_logger",
  "message": "request_completed",
  "request_id": "req_xxxxxxxx",
  "method": "POST",
  "path": "/api/v1/tasks",
  "status_code": 201,
  "duration_ms": 45,
  "event": "request_completed"
}
```

### 4.3 慢请求告警

单次请求耗时超过阈值（默认 1 秒）时，日志级别提升为 `WARNING`：

```json
{
  "level": "WARNING",
  "message": "slow_request_detected",
  "duration_ms": 3200,
  "threshold_ms": 1000
}
```

## 5. 业务关键事件日志

### 5.1 任务生命周期

| 事件 | 级别 | 必填字段 |
|------|------|---------|
| `task_created` | INFO | task_id, user_id, task_type |
| `task_dispatched` | INFO | task_id, agent_id, agent_type |
| `task_started` | INFO | task_id, agent_id |
| `task_progress_updated` | INFO | task_id, progress_percent, stage |
| `task_completed` | INFO | task_id, agent_id, deliverable_count |
| `task_failed` | ERROR | task_id, agent_id, error_code, reason |
| `task_migrated` | WARNING | task_id, from_agent_id, to_agent_id, reason |
| `task_timeout` | WARNING | task_id, agent_id, timeout_seconds |

### 5.2 节点生命周期

| 事件 | 级别 | 必填字段 |
|------|------|---------|
| `agent_registered` | INFO | client_id, agent_type, capabilities |
| `agent_heartbeat_received` | DEBUG | client_id, status |
| `agent_offline_detected` | WARNING | client_id, last_heartbeat_at |
| `agent_reconnected` | INFO | client_id |

### 5.3 文件操作

| 事件 | 级别 | 必填字段 |
|------|------|---------|
| `file_upload_url_generated` | INFO | task_id, file_key, expires_at |
| `file_upload_completed` | INFO | task_id, file_key, size_bytes |
| `file_download_url_generated` | INFO | task_id, file_key |
| `deliverable_aggregated` | INFO | task_id, deliverable_count |

## 6. 敏感信息脱敏

以下信息禁止以明文形式写入日志：

- 用户密码、Token、API Key。
- 身份证号、手机号、银行卡号等个人隐私。
- 合同/报价单中的金额（除非业务需要且已脱敏）。
- 第三方服务的完整密钥。

脱敏方式：

```python
# 推荐
logger.info("api_key_validated", api_key=mask_secret(api_key))

# 不推荐
logger.info("api_key_validated", api_key="sk-xxxxxxxxxxxx")
```

## 7. 异常日志规范

- 捕获异常时必须记录完整堆栈（开发环境）或摘要（生产环境）。
- 不要吞掉异常，必须包含 `exc_info`。
- 业务异常和系统异常分开处理。

```python
# 推荐
logger.error(
    "task_execution_failed",
    task_id=task_id,
    agent_id=agent_id,
    error_code="301001",
    exc_info=True,
)

# 不推荐
logger.error(f"任务执行失败: {e}")
```

## 8. 环境差异化配置

| 环境 | 日志级别 | 输出目标 | 格式 | 堆栈信息 |
|------|---------|---------|------|---------|
| local | DEBUG | 控制台 | 可读文本（可选 JSON） | 完整 |
| dev | DEBUG | 控制台 + 文件 | JSON | 完整 |
| staging | INFO | 文件 + 日志采集 | JSON | 摘要 |
| prod | INFO | 文件 + 日志采集 | JSON | 摘要 |

## 9. 日志切割与保留

- 按天切割日志文件：`app-2026-08-03.log`。
- 单个文件大小不超过 100MB。
- 本地/开发环境保留 7 天。
- 生产环境保留 30 天，过期后归档或删除。
- 日志文件路径统一：`/var/log/opc/central-hub/`（生产）或 `./logs/`（开发）。

## 10. 技术实现建议

- Python 后端：使用 `structlog` + 标准 `logging`。
- 前端：错误日志上报到 Sentry 或后端 `/api/v1/client-logs`。
- Agent 节点：使用与后端一致的 `structlog`，通过 WebSocket 实时上报 ERROR 级别日志。

## 11. 变更记录

| 日期 | 变更内容 | 变更人 |
|------|---------|--------|
| 2026-08-03 | 初始创建 | AI助手 |

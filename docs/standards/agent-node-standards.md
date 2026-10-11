# OPC 项目 Agent 节点开发规范

## 1. 设计原则

- **一节点一工种**：每个 Agent 节点只负责一种专业能力。
- **无状态设计**：节点不保存任务状态，状态由中央调度中枢统一管理。
- **幂等执行**：同一任务多次下发应保证结果一致或安全失败。
- **自愈能力**：节点异常退出后，重新启动应自动重新注册并恢复心跳。
- **禁止节点间直接通信**：所有通信必须经过中央调度中枢。

## 2. 节点部署方式

按 ADR-006 决策，Agent 节点采用**中枢托管模板 + 安装脚本 + 自动注册**的方式接入。

### 2.1 安装脚本规范

- 每种 Agent 类型至少维护两类脚本：`install-agent-{type}-linux.sh` 和 `install-agent-{type}-windows.ps1`。
- 脚本必须幂等：重复执行不会破坏已有环境。
- 脚本职责边界：
  - **必须做**：下载 OPC Agent 模板、创建工作目录、生成 `.env`、注册为系统服务（可选）。
  - **不必须做，但可内置**：安装 Python、Node.js、FFmpeg、GPU 驱动等第三方依赖。
- 脚本内置依赖安装时，失败必须显式报错并退出，不能静默跳过。
- 脚本必须输出清晰的安装日志，便于排查。

### 2.2 模板管理规范

- Agent 模板统一存放在中枢 `static/agent-templates/{agent_type}/{version}/` 目录下。
- 模板版本号采用语义化版本：`{major}.{minor}.{patch}`，如 `0.1.0`。
- 更新模板时必须同步更新安装脚本中的版本号和 checksum。
- 中枢需提供模板列表查询接口，便于节点检查更新。

### 2.3 自动注册规范

- 安装脚本生成的 `.env` 中必须包含：
  - `HUB_API_URL`：中枢 API 地址。
  - `HUB_WS_URL`：中枢 WebSocket 地址。
  - `AGENT_REGISTRATION_TOKEN`：注册 Token。
- Agent 启动后 5 秒内必须完成注册请求。
- 注册 Token 由中枢生成，建议设置有效期（如 24 小时）。

## 3. 节点类型

| 节点类型 | agent_type | 主要职责 |
|----------|-----------|---------|
| 编程 Agent | `code` | 项目搭建、代码开发、BUG 修复、接口调试、部署 |
| 视频 Agent | `video` | 脚本生成、素材制作、视频剪辑、配音字幕、成片渲染 |
| 文案 Agent | `copywriting` | 短视频脚本、软文、公众号文案、产品介绍 |
| 设计 Agent | `design` | LOGO、海报、UI 界面、宣传物料、配图生成 |
| 运营 Agent | `operations` | 内容复盘、数据统计、渠道素材批量生成 |
| 商务财税 Agent | `business_finance` | 报价单、合同、收支台账、利润统计 |

## 3. 节点目录结构

```
opc-agent-{type}/                  # 例如 opc-agent-programming
├── app/
│   ├── __init__.py
│   ├── main.py                    # 入口
│   ├── config.py                  # 配置（Pydantic Settings）
│   ├── client/
│   │   ├── __init__.py
│   │   └── hub_client.py          # 与中枢通信客户端
│   ├── executor/
│   │   ├── __init__.py
│   │   ├── base_executor.py       # 执行器基类
│   │   └── task_executor.py       # 具体任务执行器
│   ├── models/
│   │   ├── __init__.py
│   │   └── schemas.py             # Pydantic 模型
│   └── utils/
│       ├── __init__.py
│       └── logger.py              # 结构化日志
├── tests/
├── Dockerfile
├── pyproject.toml
└── .env.example
```

## 4. 通信协议

### 4.1 节点注册

节点启动后，立即向中枢发送注册请求：

```http
POST /api/v1/agent-nodes/register
Content-Type: application/json

{
  "client_id": "agent-programming-001",
  "agent_type": "programming",
  "capabilities": ["react", "fastapi", "docker"],
  "hardware_info": {
    "cpu_cores": 8,
    "memory_gb": 32,
    "gpu": null
  },
  "version": "0.1.0"
}
```

### 4.2 心跳上报

- 通过 WebSocket 每 5~10 秒发送一次心跳。
- 心跳内容简洁，避免大 payload。

```json
{
  "event": "agent.heartbeat",
  "payload": {
    "client_id": "agent-programming-001",
    "status": "idle",
    "current_task_id": null
  },
  "timestamp": "2026-08-03T12:00:00Z"
}
```

### 4.3 任务接收

中枢通过 WebSocket 下发任务：

```json
{
  "event": "hub.task_dispatch",
  "payload": {
    "task_id": "task_xxxxxxxx",
    "task_type": "react_frontend_development",
    "input": {
      "description": "...",
      "resource_paths": ["s3://opc-bucket/requirements/task_xxx.md"]
    },
    "timeout_seconds": 3600
  }
}
```

### 4.4 进度上报

任务执行过程中，节点需实时上报进度：

```json
{
  "event": "agent.task_progress",
  "payload": {
    "task_id": "task_xxxxxxxx",
    "progress": 35,
    "stage": "coding",
    "message": "正在生成页面组件",
    "tool": "edit",
    "detail": {
      "files": ["src/pages/Home.tsx"],
      "tokens": {"input": 12000, "output": 800},
      "cost": 0.0
    },
    "todos": [
      {"content": "搭建页面骨架", "status": "completed", "priority": "high"}
    ]
  },
  "timestamp": "2026-08-03T12:10:00Z"
}
```

- `progress`：0~100 整数，必填。
- `stage`：**ADR-055 必带**。执行阶段封闭枚举，供中枢与前端展示「当前动作」：
  `preparing`（准备中）/ `planning`（规划中）/ `coding`（编写中）/ `editing`（修改文件）/
  `running_command`（执行命令）/ `searching`（检索代码）/ `testing`（验证中）/
  `compacting`（压缩上下文）/ `summarizing`（生成说明）/ `idle`（空闲）。
- `message`：人类可读的当前动作描述，**≤120 字符**，不得包含绝对路径以外的敏感内容。
- `tool`：当前工具名，可空。
- `detail`：可观测附加信息（`files` / `tokens` / `cost` / `compaction`），可空。
- `todos`：会话待办列表，可空；存在时中枢以该列表作为展示依据。
- `stage`/`message`/`tool`/`detail` 为 **ADR-055 新增键，向后兼容**：旧中枢按
  `extra=ignore` 忽略；新版中枢落库并在任务摘要面板展示。
- **静默期心跳**：单个执行阶段长时间无事件时（长思考、长命令、上下文压缩），
  节点必须按 `AGENT_PROGRESS_HEARTBEAT_SECONDS`（默认 20 秒）补发进度事件
  （`progress` 不变，`message` 标注「仍在执行：<最近动作>」），避免前端看起来卡死。

### 4.5 结果回传

任务完成后，节点上报结果：

```json
{
  "event": "agent.task_result",
  "payload": {
    "task_id": "task_xxxxxxxx",
    "status": "completed",
    "deliverables": [
      {
        "type": "code_archive",
        "name": "project-source.zip",
        "storage_path": "s3://opc-bucket/deliverables/task_xxx/project-source.zip",
        "size_bytes": 1024000
      }
    ],
    "summary": "企业官网开发完成，包含首页、关于我们、联系我们三个页面。"
  }
}
```

## 5. 任务执行规范

### 5.1 执行流程

1. 接收任务 → 2. 校验任务参数 → 3. 下载资源 → 4. 沙箱执行 → 5. 上传交付物 → 6. 上报结果

### 5.2 沙箱执行

- 所有任务必须在独立工作目录中执行，禁止污染节点环境。
- 工作目录命名：`/workspace/tasks/{task_id}/`。
- 任务执行脚本使用子进程隔离，设置超时和输出上限。

### 5.2.1 准入自评口径（ADR-059）

节点在接单前对**自身**做一次自评，**必须与中枢预筛使用同一口径**，否则会出现
「中枢放行、节点拒单」的双门槛错配（表现为任务回 `pending` 且用户看不到原因）：

```
可自主接单 ⇔
    metrics.memory.available_gb > 1.0     # 绝对可用内存，非百分比
    AND metrics.load_score.score < 80     # 综合负载（含 CPU/内存/IO/网络加权，已平滑）

任一不满足 → 拒单，reason = "resource_busy"
metrics 缺失 → 放行（不因缺字段误伤）；load_score 缺失但 CPU 可读 → 用 CPU 百分比兜底
```

- **禁止使用内存百分比作为主判据**：百分比的分母随机器规格变化——4G 机器 82.7%
  仅剩约 0.7G 却被判「合规」，小内存机器会被系统性低估风险；
- **必须防抖动**：单次瞬时采样不得直接拒单（至少连续两次采样确认），
  否则一次尖峰就会拒单，而中枢不会立即重派（用户侧表现为「排队中 0%」卡住）；
- **拒单日志必须带真实读数**：`available_gb` / `load_score` / 两个阈值一并记录，
  便于现场一眼定性（历史上因日志只有 task_id，排查只能靠事后查库推断）；
- 拒单后中枢会回写 `last_reject_reason` 并对用户可见，并记录 5 分钟回避；
- **口径降级方向要记清**：节点侧 `load_score` 缺失时回退 **CPU 百分比**
  （`AGENT_BUSY_CPU_THRESHOLD_PERCENT`）；中枢侧 metrics 缺失时回退**既有百分比预筛**
  （`DISPATCH_CPU/MEM_THRESHOLD_PERCENT`）。两者是不同兜底路径，新节点应始终上报
  `memory.available_gb` 与 `load_score.score`（`collect_metrics` 已默认包含），
  不要依赖任何一条兜底。

### 5.3 超时处理

- 节点必须按任务 `timeout_seconds` 设置执行超时。
- 超时后主动终止任务进程，并上报 `timeout` 状态。
- **ADR-055 补充（超时语义）**：编程类任务的真实耗时远超固定阈值，因此节点采用**双层语义**：
  1. **无进度看门狗（主，默认启用）**：`TASK_NO_PROGRESS_TIMEOUT_SECONDS`（默认 1800 秒，
     最小值 60）。任何执行活动（SSE 事件、工具调用、进度上报、上下文压缩）都重置计时；
     只有在**完全无活动**超过阈值时，才中断会话并以
     `failed(reason="task_no_progress_timeout:<秒>")` 上报。
  2. **任务总时长上限（可选，默认关闭）**：`TASK_EXECUTION_TIMEOUT_SECONDS`（默认 0 = 不限），
     或中枢派发 payload 的 `timeout_seconds`。仅在运维明确需要硬上限时开启。
  3. 中断动作必须先调 tilacode `POST /api/session/{id}/interrupt`，再上报终态，
     保证执行侧进程不残留。

### 5.4 失败重试

- 节点本地不自动重试，失败直接上报中枢。
- 中枢统一负责重试、迁移、人工确认策略。

## 6. 日志规范

- 日志必须结构化，包含 `task_id`、`client_id`、`event` 等字段。
- 日志分级：DEBUG / INFO / WARNING / ERROR。
- 任务关键节点必须记录：接收、开始、进度、完成、失败。
- 日志本地保留，同时实时上报中枢（ERROR 级别优先）。

```python
logger.info(
    "task_execution_started",
    task_id=task_id,
    client_id=client_id,
    task_type=task_type,
)
```

## 7. 交付物规范

- 所有交付物必须上传到统一共享存储（MinIO/S3）。
- 交付物路径命名：`deliverables/{task_id}/{filename}`。
- 交付物元数据必须包含：类型、名称、路径、大小、创建时间。
- 代码类交付物必须压缩为 zip/tar.gz。
- 视频类交付物必须附带元数据文件（分辨率、时长、编码格式）。

## 8. 节点配置

```env
# 中枢地址（安装脚本根据中枢 IP 自动注入）
HUB_API_URL=http://192.168.101.101:8000
HUB_WS_URL=ws://192.168.101.101:8000/ws/agent

# 节点身份
AGENT_CLIENT_ID=agent-programming-001
AGENT_TYPE=programming
AGENT_REGISTRATION_TOKEN=xxx

# 心跳与任务
HEARTBEAT_INTERVAL_SECONDS=5
TASK_EXECUTION_TIMEOUT_SECONDS=3600
WORKSPACE_ROOT=/workspace

# 存储
STORAGE_ENDPOINT=192.168.101.101:19000
STORAGE_BUCKET=nas
```

## 9. 安全规范

- 节点注册必须携带 Token，中枢校验通过后才允许接入。
- 节点与中枢通信必须走 TLS。
- 任务执行环境最小权限原则，禁止访问节点宿主机敏感资源。
- 任务代码执行前进行基础安全扫描（如禁用危险系统调用）。

## 10. 变更记录

| 日期 | 变更内容 | 变更人 |
|------|---------|--------|
| 2026-08-03 | 初始创建 | AI助手 |
| 2026-08-03 | 补充 Agent 节点部署、安装脚本、自动注册规范（ADR-006） | AI助手 |
| 2026-09-27 | ADR-055 登记：① §4.4 进度上报 payload 对齐实现（`progress` 取代 `progress_percent`，示例改为真实字段），新增 `stage` 封闭枚举 / `message` / `tool` / `detail` 与静默期心跳要求；② §5.3 超时处理改为**双层语义**（无进度看门狗为主 + 可选总时长上限），修复原「固定按任务超时」与编程任务真实耗时严重不符的问题 | AI助手 |
| 2026-09-28 | ADR-059 登记：新增 §5.2.1「准入自评口径」——节点自评必须与中枢同口径（可用内存 >1G **且** `load_score` <80，任一不满足即拒单），**禁止用内存百分比作主判据**，必须防抖动、拒单日志必须带真实读数 | AI助手 |

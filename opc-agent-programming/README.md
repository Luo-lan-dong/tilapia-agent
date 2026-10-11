# OPC Code Agent

中央调度中枢的编程执行 Agent 节点（`agent_type=code`，当前版本 0.5.0，见 ADR-024）。

启动后自动向中枢注册、建立 WebSocket 连接并周期性上报心跳（含 CPU/内存/负载指标）；
收到 `hub.task_dispatch` 后在本地拉起 tilacode server（opencode 二进制），由任务桥接层
创建会话、下发 prompt，并将 tilacode 的 SSE 事件实时翻译上报为任务进度；任务完成后
打包上传交付物（工作目录文件树、产物分块，见 ADR-028）。断线不退进程，应用内自动重连（ADR-020）。

本地 Windows 调试 tilacode 执行层时，可单独运行：

```powershell
.\dist\tilacode-windows-x64\bin\opencode.exe serve --port 4096
```

## 目录结构

```
opc-agent-programming/
├── app/
│   ├── main.py                    # 入口：注册 → WebSocket → tilacode → 任务桥接
│   ├── config.py                  # Pydantic Settings 配置（含默认节点名生成，ADR-016）
│   ├── client/
│   │   └── hub_client.py          # 与中枢通信客户端（注册/心跳/任务事件上报/重连）
│   ├── executor/
│   │   ├── task_bridge.py         # 任务桥接核心：派发 → 会话执行 → 结果回调
│   │   ├── tilacode_manager.py    # tilacode server 进程管理（健康检查/自动重启）
│   │   ├── tilacode_client.py     # tilacode HTTP/SSE 客户端
│   │   ├── sse_consumer.py        # SSE 事件消费
│   │   ├── event_translator.py    # tilacode 事件 → 中枢任务事件翻译
│   │   ├── workspace.py           # 任务工作目录管理
│   │   ├── file_scanner.py        # 执行中文件树扫描上报（ADR-028）
│   │   └── session_store.py       # 会话登记簿（data/sessions.json，ADR-012）
│   ├── models/schemas.py          # Pydantic 数据模型
│   └── utils/                     # 日志、进程监控
├── scripts/
│   ├── install-agent-code-linux.sh    # 生产一键安装/升级（幂等）
│   ├── build-code-agent.sh            # Linux x64 单一产物构建（Nuitka，ADR-024）
│   └── package_template.py            # 模板打包辅助
├── systemd/
│   ├── opc-agent-code.service         # 当前生产服务单元
│   └── opc-agent-programming.service  # 旧版服务单元（0.4.x 源码部署，升级时自动停用）
├── tests/                         # pytest 单元测试
├── tilacode/                      # tilacode 上游源码仓库（vendored，构建二进制用）
└── pyproject.toml
```

## 前置要求

- 开发模式：Python >= 3.10
- 生产模式：单一 tar.gz 产物（Nuitka 编译的代理层 + tilacode 二进制 + systemd 单元），**无需安装 Python**（ADR-024）
- 可访问 OPC central dispatch hub

## 快速开始（开发模式）

### 1. 复制环境变量

```bash
cp .env.example .env
# 编辑 .env，填入 HUB_API_URL 与 AGENT_REGISTRATION_TOKEN
```

### 2. 创建虚拟环境并安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 3. 运行

```bash
python -m app.main
```

启动流程（见 `app/main.py`）：

1. 加载配置，向中枢注册并建立 WebSocket（中枢离线时按重试间隔重试，不退出进程）。
2. 并行拉起 tilacode server 并等待健康，装配任务桥接层。
3. 桥接就绪后上报 `idle`，中枢才开始派发任务。

按 `Ctrl+C` 优雅关闭（后台任务 → 桥接 → 客户端 → tilacode）。

> 本地开发需提供 tilacode 二进制（默认路径 `/opt/opc-agent-programming/bin/tilacode`，
> 可通过 `TILACODE_BINARY_PATH` 指向本地构建产物，如 `dist/tilacode-windows-x64/bin/opencode.exe`）。

## 一键安装（生产环境，Linux x64）

在目标 Linux 机器上执行（需要 root）：

```bash
curl -fsSL http://<HUB_HOST>:8000/static/install-scripts/code/install-agent-code-linux.sh \
  | sudo bash -s -- --hub-url http://<HUB_HOST>:8000 --token <REGISTRATION_TOKEN> [--agent-name <节点显示名>]
```

脚本会：

1. 从中枢资源总清单（ADR-022）获取整体包 URL 并做 sha256 校验（幂等：校验一致则跳过下载）。
2. 解压单一产物到 `/opt/opc-agent-code`（代理二进制 + tilacode 二进制 + systemd 单元）。
3. 生成 `/opt/opc-agent-code/.env`。
4. 注册并启动 `opc-agent-code.service`。

特性：

- **幂等执行**：重复运行会升级整体包、刷新 `.env` 并重启服务。
- **旧版迁移**：自动迁移 0.4.x 源码部署的节点身份（`AGENT_CLIENT_ID`/`AGENT_NAME`），停用旧 `opc-agent-programming` 服务。
- **节点命名**：`--agent-name` 可选；留空时 Agent 启动按 OS 平台+版本自动命名（如 `Ubuntu 22.04`，ADR-016）。

## 配置项说明

| 环境变量 | 必填 | 默认值 | 说明 |
|---------|------|--------|------|
| `APP_ENV` | 否 | `local` | 运行环境（local 日志为可读文本，其他为 JSON） |
| `LOG_LEVEL` | 否 | `INFO` | 日志级别 |
| `HUB_API_URL` | 否 | `http://localhost:8000` | 中枢 REST API 地址 |
| `HUB_WS_URL` | 否 | 由 `HUB_API_URL` 推导 | 中枢 WebSocket 地址 |
| `AGENT_REGISTRATION_TOKEN` | 是 | - | 注册 Token（安装脚本注入；WebSocket 认证用注册接口返回的 Token） |
| `AGENT_CLIENT_ID` | 否 | 自动生成（`hostname-随机串`） | 节点客户端 ID |
| `AGENT_NAME` | 否 | OS 平台+版本（如 `Ubuntu 22.04`，ADR-016） | 节点显示名称 |
| `AGENT_TYPE` | 否 | `code` | Agent 类型 |
| `HEARTBEAT_INTERVAL_SECONDS` | 否 | `5` | 心跳上报间隔 |
| `RECONNECT_INTERVAL_SECONDS` | 否 | `5` | 断线重连重试间隔（ADR-020） |
| `TASK_EXECUTION_TIMEOUT_SECONDS` | 否 | `300` | 任务执行超时 |
| `WORKSPACE_ROOT` | 否 | `/workspace` | 工作目录根路径 |
| `STORAGE_ENDPOINT` / `STORAGE_BUCKET` | 否 | - | 共享存储（交付物上传，可选） |
| `TILACODE_BINARY_PATH` | 否 | `/opt/opc-agent-programming/bin/tilacode` | tilacode 二进制路径 |
| `TILACODE_HOST` / `TILACODE_PORT` | 否 | `127.0.0.1` / `4096` | tilacode server 监听地址 |
| `TILACODE_WORKSPACE_ROOT` | 否 | `/opt/opc-agent-code/workspace` | tilacode 任务工作目录根 |
| `TILACODE_HEALTH_CHECK_INTERVAL` | 否 | `30` | tilacode 健康检查间隔（秒） |
| `TILACODE_AUTO_RESTART` | 否 | `true` | tilacode 异常退出自动重启 |
| `TILACODE_STEP_IDLE_TIMEOUT_SECONDS` | 否 | `30` | 完成判定静默期（`step.ended` 后无新事件即完成） |
| `AGENT_MAX_CONCURRENT_TASKS` | 否 | `2` | 本地最大并发任务数（ADR-032） |
| `AGENT_BUSY_CPU_THRESHOLD_PERCENT` | 否 | `90` | 本地自评 CPU 拒绝阈值（ADR-032） |
| `AGENT_BUSY_MEM_THRESHOLD_PERCENT` | 否 | `90` | 本地自评内存拒绝阈值（ADR-032） |

## 构建发布

在 Linux x64 构建服务器上执行（详见 `scripts/build-code-agent.sh` 头部注释）：

```bash
# 前置：tilacode 二进制（Windows 上 Bun 交叉编译后上传）
cd tilacode/packages/opencode && bun run build --skip-embed-web-ui --target linux-x64-baseline

# 构建整体包（版本号缺省读 pyproject.toml）
bash scripts/build-code-agent.sh
# 产出: artifacts/opc-agent-code-{VERSION}-linux-x64.tar.gz
```

产物通过中枢「资源管理」页发布后，各节点重复执行安装脚本即可升级。

## 卸载

```bash
sudo systemctl stop opc-agent-code
sudo systemctl disable opc-agent-code
sudo rm -f /etc/systemd/system/opc-agent-code.service
sudo systemctl daemon-reload
sudo rm -rf /opt/opc-agent-code
```

旧版（0.4.x 源码部署）：

```bash
sudo systemctl stop opc-agent-programming && sudo systemctl disable opc-agent-programming
sudo rm -f /etc/systemd/system/opc-agent-programming.service
sudo systemctl daemon-reload && sudo rm -rf /opt/opc-agent-programming
```

## 开发与测试

```bash
# 单元测试
python -m pytest tests/

# 代码检查
ruff check app scripts

# 格式化
ruff format app
```

## 注意事项

- 本 Agent 为客户端程序，不暴露 HTTP 服务端口（tilacode server 仅绑定 `127.0.0.1`），不存在与传统 Web 服务的端口冲突。
- 中枢服务端口切换（8000-8010）时，旧版本 Agent 不支持端点轮询，需重新安装或手动更新 `HUB_API_URL`。
- `systemctl` 命令仅在完整 Linux systemd 环境中可用；WSL1 或非 systemd 容器内无法使用服务自启，可用 `python -m app.main` 做本地验证。
- 节点与中枢通信建议启用 TLS（`https`/`wss`）。

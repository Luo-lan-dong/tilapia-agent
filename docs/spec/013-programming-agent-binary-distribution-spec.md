# 编程 Agent 预编译二进制分发 Spec

## 1. 功能描述

为编程 Agent 节点设计基于预编译二进制的分发协议，替代 ADR-012 原计划的「Bun 运行时 + 源码打包」方案。开发者手动构建 tilacode 二进制，复制到中枢 static 目录；子节点通过一行 `curl | bash` 命令从中枢拉取 Agent 实例（含 tilacode 二进制 + Python 外壳 + 配置模板 + systemd 服务），安装后自动注册并保持心跳。

### MVP 范围

- ✅ tilacode 预编译二进制构建（命令行版本，`--skip-embed-web-ui`）
- ✅ 二进制手动复制到中枢 `static/binaries/` 并生成 manifest.json
- ✅ 安装脚本扩展：平台检测、二进制下载、sha256 校验、端口探测
- ✅ Agent 模板升级到 0.2.0（含 tilacode 集成配置）
- ✅ 仅支持 linux-x64（glibc + AVX2）
- ❌ CI 自动构建（后续迭代）
- ❌ arm64 / musl / Windows 支持（后续迭代）
- ❌ HTTPS 分发（后续迭代）
- ❌ GPG 签名校验（后续迭代）

## 2. 用户故事

- 作为开发者，我可以在构建机执行一行 `bun run build` 命令产出 tilacode 二进制。
- 作为开发者，我可以将二进制复制到中枢 static 目录并生成 manifest.json，完成版本发布。
- 作为运维，我可以在新 Linux 机器执行一行 `curl | bash` 命令，安装完整的编程 Agent（含 tilacode）。
- 作为运维，安装脚本自动检测平台架构，非 linux-x64 时明确报错。
- 作为运维，安装脚本自动探测可用端口，同机可部署多个 Agent 实例。
- 作为中枢，我能通过现有 StaticFiles 提供 二进制、模板、脚本 的统一下载。
- 作为开发者，tilacode 升级时只需重新构建二进制并更新 manifest.json，节点重新执行安装脚本即可升级。

## 3. 核心流程

### 3.1 构建与发布流程（开发者侧）

```
开发者在本机/构建机（Linux x64）
    ↓
cd opc-agent-programming/tilacode/packages/opencode
    ↓
bun run build --single --skip-embed-web-ui
    ↓
产出 dist/tilacode-linux-x64/bin/opencode（~130MB）
    ↓
mkdir -p <hub>/static/binaries/tilacode/{version}/
    ↓
cp dist/tilacode-linux-x64/bin/opencode → <hub>/static/binaries/tilacode/{version}/tilacode-linux-x64
    ↓
sha256sum tilacode-linux-x64 → 记录哈希值
    ↓
手动创建 manifest.json（填入版本、sha256、size、构建时间）
    ↓
重新打包 Agent 模板 tarball（0.2.0）→ <hub>/static/agent-templates/programming/0.2.0/
    ↓
提交到版本控制，发布完成
```

### 3.2 子节点安装流程（一行命令）

```
用户在 Linux x64 机器执行：
  curl -fsSL http://<hub>/static/install-scripts/install-agent-programming-linux.sh \
    | sudo bash -s -- --hub-url http://<hub>:8000 --token <token>
    ↓
脚本下载并启动
    ↓
require_root（必须 root）
    ↓
parse_args（--hub-url, --token）
    ↓
detect_platform（uname -s == Linux && uname -m == x86_64？）
    ├── 否 → 报错退出："仅支持 linux-x64 (glibc + AVX2)"
    └── 是 → 继续
    ↓
install_system_deps（复用现有：检测发行版、安装 Python >= 3.10、curl、tar）
    ↓
download_agent_template（下载 opc-agent-programming-0.2.0.tar.gz）
    ↓
download_tilacode_binary（下载 tilacode-linux-x64）
    ↓
verify_sha256（对比 manifest.json 中的 sha256）
    ├── 失败 → 报错退出："二进制校验失败"
    └── 成功 → 继续
    ↓
chmod +x tilacode-linux-x64
    ↓
extract_template（解压到 /opt/opc-agent-programming/）
    ↓
move_binary（tilacode-linux-x64 → /opt/opc-agent-programming/bin/tilacode）
    ↓
setup_virtualenv（创建 .venv，pip install -e .）
    ↓
detect_available_port（4096 起递增探测）
    ↓
write_env_file（写入 HUB_API_URL、TOKEN、TILACODE_PORT、TILACODE_BINARY_PATH）
    ↓
install_systemd_service（注册并启动）
    ↓
Agent 进程启动：
  ├── POST /api/v1/agent-nodes/register（注册中枢）
  ├── 拉起 tilacode server（./bin/tilacode serve --port {TILACODE_PORT}）
  ├── 健康检查 tilacode server（GET http://127.0.0.1:{port}/health）
  ├── 建立 WebSocket 连接
  └── 心跳循环（每 5 秒）
    ↓
中枢标记节点为 idle，安装完成
```

### 3.3 tilacode 升级流程

```
开发者构建新版本二进制
    ↓
复制到 <hub>/static/binaries/tilacode/{new_version}/
    ↓
生成新 manifest.json
    ↓
更新安装脚本中的 TILACODE_VERSION 变量
    ↓
在已部署节点重新执行安装命令（幂等升级）
    ↓
脚本检测到二进制 sha256 变化 → 重新下载替换 → 重启服务
```

### 3.4 端口探测流程

```
port=4096
    ↓
循环：
  ├── 检查 127.0.0.1:{port} 是否可绑定（ss -tln 或 /dev/tcp 探测）
  ├── 可用 → 返回 port
  └── 被占用 → port++，继续（上限 4200，超过则报错）
    ↓
写入 .env: TILACODE_PORT={port}
```

## 4. 降级链路

| 异常场景 | 降级策略 |
|---------|---------|
| 二进制下载失败（网络） | 提示检查网络与中枢地址，重试 3 次后退出 |
| sha256 校验失败 | 报错退出，提示中枢二进制可能损坏，联系管理员 |
| 平台不支持（非 linux-x64） | 报错退出，提示"仅支持 linux-x64 (glibc + AVX2)" |
| 端口 4096-4200 全被占用 | 报错退出，提示手动指定 TILACODE_PORT 环境变量 |
| tilacode server 启动失败 | Agent 标记为 error，不上报 idle，中枢不分配任务 |
| tilacode server 健康检查超时 | 等待 10 秒后重试，3 次失败则标记 error |
| manifest.json 下载/解析失败 | 报错退出，提示中枢 manifest 配置错误 |
| Agent 模板 tarball 下载失败 | 提示检查网络，重试 3 次后退出 |
| Python venv 创建失败 | 复用现有降级：尝试 ensurepip 修复，仍失败则报错 |
| 重复执行安装脚本（幂等） | 检测已存在目录，升级模板与二进制，刷新 .env，重启服务 |

## 5. 验收标准

- [ ] 开发者在 Linux x64 构建机执行 `bun run build --single --skip-embed-web-ui`，产出 `dist/tilacode-linux-x64/bin/opencode`（~130MB）
- [ ] 二进制可独立执行 `./opencode --version`，无需 Bun/Node
- [ ] 二进制可独立执行 `./opencode serve --port 4096`，启动 HTTP server
- [ ] 开发者按迁移路径将二进制复制到中枢 `static/binaries/tilacode/{version}/tilacode-linux-x64`
- [ ] manifest.json 包含正确的 sha256、size、version、platform、arch 字段
- [ ] 中枢 `GET /static/binaries/tilacode/{version}/manifest.json` 返回正确 JSON
- [ ] 中枢 `GET /static/binaries/tilacode/{version}/tilacode-linux-x64` 可下载二进制
- [ ] Agent 模板 tarball 升级到 0.2.0，不含 tilacode 源码，含 Python 外壳 + 配置 + systemd
- [ ] 在 linux-x64 机器执行一行 `curl | bash` 命令，安装成功并自动注册中枢
- [ ] 安装脚本检测到非 linux-x64 平台时，明确报错退出
- [ ] 安装脚本下载二进制后执行 sha256 校验，校验失败时报错退出
- [ ] 安装脚本自动探测可用端口（4096 起），写入 .env
- [ ] Agent 启动后自动拉起 tilacode server，健康检查通过后上报 idle
- [ ] 同机第二次执行安装命令（幂等升级）能正确替换二进制并重启服务
- [ ] tilacode 升级后，节点重新执行安装命令能拉取新版本并生效

## 6. 接口清单

### 6.1 中枢静态资源（复用现有 StaticFiles，无需新增路由）

| 资源 | URL | 说明 |
|------|-----|------|
| 安装脚本 | `GET /static/install-scripts/install-agent-programming-linux.sh` | 现有，扩展内容 |
| Agent 模板 | `GET /static/agent-templates/programming/0.2.0/opc-agent-programming-0.2.0.tar.gz` | 升级版本 |
| tilacode 二进制 | `GET /static/binaries/tilacode/{version}/tilacode-linux-x64` | 新增 |
| tilacode manifest | `GET /static/binaries/tilacode/{version}/manifest.json` | 新增 |

### 6.2 中枢 API（复用现有，无需新增）

| 接口 | 用途 |
|------|------|
| `POST /api/v1/agent-nodes/register` | Agent 注册（现有） |
| `GET /api/v1/agent-nodes/install-scripts?agent_type=programming&os=linux` | 获取安装脚本元信息（现有，version 升级到 0.2.0） |
| WebSocket `/api/v1/agent-nodes/ws` | 心跳与任务下发（现有） |

### 6.3 manifest.json 结构

```json
{
  "tilacode_version": "1.18.13",
  "agent_template_version": "0.2.0",
  "platform": "linux",
  "arch": "x64",
  "abi": "glibc",
  "avx2": true,
  "filename": "tilacode-linux-x64",
  "sha256": "a1b2c3d4e5f67890...(64位十六进制)",
  "size": 136524288,
  "built_at": "2026-08-10T10:00:00Z",
  "build_flags": ["--single", "--skip-embed-web-ui"],
  "download_url": "/static/binaries/tilacode/1.18.13/tilacode-linux-x64"
}
```

## 7. 项目结构

### 7.1 中枢新增/修改

```
opc-central-dispatch-hub/static/
├── install-scripts/
│   └── install-agent-programming-linux.sh          # 修改：新增二进制下载、校验、端口探测
├── agent-templates/
│   └── programming/
│       ├── 0.1.0/                                  # 旧版本（保留）
│       │   └── opc-agent-programming-0.1.0.tar.gz
│       └── 0.2.0/                                  # 新增：含 tilacode 集成配置
│           └── opc-agent-programming-0.2.0.tar.gz
└── binaries/                                       # 新增目录
    └── tilacode/
        └── 1.18.13/                                # tilacode 版本
            ├── tilacode-linux-x64                  # 二进制（~130MB，git lfs 或忽略）
            └── manifest.json
```

### 7.2 Agent 模板 0.2.0 结构（tarball 内容）

```
opc-agent-programming/                              # tarball 解压后的根目录
├── app/
│   ├── __init__.py
│   ├── main.py                                     # 修改：启动时拉起 tilacode server
│   ├── config.py                                   # 修改：新增 TILACODE_* 配置项
│   ├── client/
│   │   ├── __init__.py
│   │   └── hub_client.py                           # 不变
│   └── executor/                                   # 新增：任务桥接层（ADR-012）
│       ├── __init__.py
│       ├── tilacode_manager.py                     # tilacode 进程管理
│       ├── tilacode_client.py                      # tilacode HTTP API 客户端
│       └── task_bridge.py                          # WebSocket ↔ HTTP 桥接
├── systemd/
│   └── opc-agent-programming.service               # 不变
├── pyproject.toml                                  # 修改：新增 httpx 依赖
├── .env.example                                    # 修改：新增 TILACODE_* 配置项
└── README.md
```

注意：tarball **不含** `tilacode/` 源码目录、`.venv/`、`bin/`（二进制单独下载）。

### 7.3 安装后的运行时目录

```
/opt/opc-agent-programming/
├── app/                                            # 从 tarball 解压
├── bin/
│   └── tilacode                                    # 从中枢下载（重命名自 tilacode-linux-x64）
├── .venv/                                          # 安装时创建
├── workspace/                                      # 任务工作目录根（运行时创建）
├── systemd/
│   └── opc-agent-programming.service
├── .env                                            # 安装时生成
└── pyproject.toml
```

## 8. 配置项

### 8.1 Agent .env 新增配置

```bash
# 现有配置（ADR-008）
HUB_API_URL=http://<hub>:8000
AGENT_REGISTRATION_TOKEN=<token>
AGENT_NAME=<hostname>-programming-<random>
HEARTBEAT_INTERVAL_SECONDS=5

# 新增：tilacode 配置（ADR-013）
TILACODE_BINARY_PATH=/opt/opc-agent-programming/bin/tilacode
TILACODE_PORT=4096                                  # 安装脚本探测后填入
TILACODE_HOST=127.0.0.1
TILACODE_WORKSPACE_ROOT=/opt/opc-agent-programming/workspace
TILACODE_HEALTH_CHECK_INTERVAL=30
TILACODE_AUTO_RESTART=true
```

### 8.2 安装脚本新增变量

```bash
# 现有
INSTALL_DIR="/opt/opc-agent-programming"
SERVICE_NAME="opc-agent-programming"
VERSION="0.2.0"                                     # 升级：0.1.0 → 0.2.0
MIN_PYTHON_VERSION="3.10"

# 新增
TILACODE_VERSION="1.18.13"
TILACODE_PLATFORM="linux-x64"
TILACODE_PORT_START=4096
TILACODE_PORT_END=4200
```

## 9. 关联文档

- [ADR-013 编程 Agent 预编译二进制分发协议](../adr/013-programming-agent-binary-distribution.md)
- [ADR-012 基于 tilacode 集成编程执行层](../adr/012-tilacode-execution-layer-integration.md)
- [ADR-008 Linux 编程 Agent 节点 MVP 与节点管理](../adr/008-agent-node-linux-programming-mvp.md)
- [008-node-management-and-programming-agent-linux-spec.md](./008-node-management-and-programming-agent-linux-spec.md)
- [012-tilacode-execution-layer-integration-spec.md](./012-tilacode-execution-layer-integration-spec.md)
- [004-agent-node-protocol-spec.md](./004-agent-node-protocol-spec.md)

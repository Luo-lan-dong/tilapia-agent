# ADR-013：编程 Agent 预编译二进制分发协议

## 状态

Superseded（被 [ADR-022](./022-agent-resource-cloud-distribution.md) 修订分发源、[ADR-024](./024-agent-code-unified-artifact.md) 修订产物协议；当前有效见 ADR-024/025，本 ADR 保留历史协议供追溯）

## 日期

2026-08-10

## 问题背景

[ADR-012](./012-tilacode-execution-layer-integration.md) 决策将 tilacode 以 HTTP Server 模式集成为编程执行层，其「对安装部署的影响」章节原计划：

> - ADR-008 的安装脚本需扩展：除 Python 外还需安装 **Bun 运行时**
> - tilacode 项目代码需打包到 Agent 模板中

经实际验证，该方案存在以下问题：

1. **Bun 运行时安装负担**：每个 Agent 节点需安装 Bun（~100MB）+ tilacode 源码（~500MB node_modules），安装耗时长、失败点多。
2. **源码打包体积过大**：tilacode 源码 + 依赖打包后超过 500MB，远大于预编译二进制。
3. **版本一致性难保证**：各节点本地 `bun install` 可能因网络或 lockfile 漂移导致依赖版本不一致。

经实测，tilacode 支持 **Bun compile 预编译为独立二进制**（[build.ts](../../opc-agent-programming/tilacode/packages/opencode/script/build.ts)），产出约 130MB 的单一可执行文件，**无需 Bun/Node 运行时**，直接 `./tilacode serve` 即可启动。

因此需要设计一套基于预编译二进制的分发协议，覆盖：

- 二进制如何构建、命名、版本管理
- 二进制如何从中枢分发到子节点
- 安装脚本如何扩展以支持二进制下载与校验
- 安装后如何初始化（目录结构、端口分配、配置注入）

## 决策

### 1. 采用预编译二进制分发，替代 ADR-012 的 Bun + 源码方案

Agent 节点不再安装 Bun 运行时和 tilacode 源码，改为下载预编译的独立二进制。

### 2. MVP 阶段只支持 linux-x64 (glibc + AVX2)

- 目标二进制：`tilacode-linux-x64`
- 适用：主流 x64 Linux 服务器（Ubuntu/Debian/CentOS/RHEL 等 glibc 发行版，CPU 支持 AVX2）
- 不支持：arm64、musl (Alpine)、无 AVX2 的老 CPU
- 安装脚本检测到不支持的平台/架构时，明确报错并提示

### 3. 采用命令行版本构建（`--skip-embed-web-ui`）

tilacode 有两种构建形态：

| 构建方式 | 体积 | 包含 | 适用 |
|---------|------|------|------|
| 默认 | 较大 | TUI + 嵌入 Web UI + server | 交互式、需 Web 界面 |
| `--skip-embed-web-ui` | ~130MB | TUI + server（无 Web UI） | headless 服务端部署 |

Agent 节点通过 `tilacode serve` 启动 headless server，不需要 Web UI，采用 `--skip-embed-web-ui` 构建。

### 4. 手动编译 + 复制到中枢 static 分发

MVP 阶段不引入 CI 自动构建，由开发者在构建机上手动执行构建命令，将产物复制到中枢静态目录。

### 5. 中枢通过 StaticFiles 提供二进制下载

复用现有 `/static` 挂载（[main.py](../../opc-central-dispatch-hub/app/main.py) 第 258 行），无需新增路由。

## 构建命令

### 构建环境要求

- Linux x64 机器（glibc，CPU 支持 AVX2）
- 已安装 [Bun](https://bun.sh/) 运行时（仅构建机需要，Agent 节点不需要）
- tilacode 源码已 `bun install`

### 构建命令

```bash
# 进入 tilacode 的 opencode 包目录
cd opc-agent-programming/tilacode/packages/opencode

# 构建当前平台的命令行版本（不嵌入 Web UI）
bun run build --single --skip-embed-web-ui
```

参数说明：

- `--single`：只构建当前平台（linux-x64），不构建全部 12 个目标
- `--skip-embed-web-ui`：跳过 Web UI 嵌入，产出命令行版本
- 离线构建：build.ts 自动读取 `test/tool/fixtures/models-api.json` 作为 models.dev 快照，无需联网

### 全平台交叉编译（可选，后续扩展用）

```bash
cd opc-agent-programming/tilacode/packages/opencode
bun run build --skip-embed-web-ui
# 产出 dist/ 下全部 12 个目标平台的二进制
```

## 产物存放位置与命名方式

### 构建产物（构建机）

```
opc-agent-programming/tilacode/packages/opencode/
└── dist/
    └── tilacode-linux-x64/
        ├── bin/
        │   └── opencode              # 预编译二进制（~130MB，文件名固定为 opencode）
        └── package.json              # 平台元数据
```

**命名规则**：

- 构建产物目录名：`tilacode-{os}-{arch}[-baseline][-musl]`
- 二进制文件名：固定为 `opencode`（build.ts 第 186 行 `outfile: dist/${name}/bin/opencode`，渐进式重命名，暂不改）
- tilacode 版本号：来自 `packages/opencode/package.json` 的 `version` 字段（当前 1.18.13）

### 分发产物（中枢 static 目录）

```
opc-central-dispatch-hub/static/
├── install-scripts/
│   └── install-agent-programming-linux.sh          # 现有，扩展
├── agent-templates/
│   └── programming/
│       └── 0.2.0/                                   # Agent 模板版本（升级，含 tilacode 集成配置）
│           └── opc-agent-programming-0.2.0.tar.gz  # Python 外壳+配置+systemd，不含二进制
└── binaries/
    └── tilacode/
        └── 1.18.13/                                 # tilacode 版本号
            ├── tilacode-linux-x64                   # 二进制（从构建产物复制并重命名）
            └── manifest.json                        # 版本与校验元数据
```

**命名规则**：

| 对象 | 命名格式 | 示例 |
|------|---------|------|
| 二进制目录 | `binaries/tilacode/{tilacode_version}/` | `binaries/tilacode/1.18.13/` |
| 二进制文件 | `tilacode-{os}-{arch}` | `tilacode-linux-x64` |
| Agent 模板包 | `opc-agent-programming-{agent_version}.tar.gz` | `opc-agent-programming-0.2.0.tar.gz` |
| manifest 文件 | `manifest.json`（固定名） | `manifest.json` |

### manifest.json 格式

```json
{
  "tilacode_version": "1.18.13",
  "agent_template_version": "0.2.0",
  "platform": "linux",
  "arch": "x64",
  "abi": "glibc",
  "avx2": true,
  "filename": "tilacode-linux-x64",
  "sha256": "a1b2c3d4e5f6...(64位十六进制)",
  "size": 136524288,
  "built_at": "2026-08-10T10:00:00Z",
  "build_flags": ["--single", "--skip-embed-web-ui"],
  "download_url": "/static/binaries/tilacode/1.18.13/tilacode-linux-x64"
}
```

## 迁移路径

### 从构建产物迁移到中枢 static 目录

```bash
# 1. 在构建机执行构建
cd opc-agent-programming/tilacode/packages/opencode
bun run build --single --skip-embed-web-ui

# 2. 在中枢创建目标目录
mkdir -p ../../opc-central-dispatch-hub/static/binaries/tilacode/1.18.13

# 3. 复制二进制并重命名（opencode → tilacode-linux-x64）
cp dist/tilacode-linux-x64/bin/opencode \
   ../../opc-central-dispatch-hub/static/binaries/tilacode/1.18.13/tilacode-linux-x64

# 4. 生成 sha256 校验值
cd ../../opc-central-dispatch-hub/static/binaries/tilacode/1.18.13
sha256sum tilacode-linux-x64 | awk '{print $1}'  # 记录输出值，填入 manifest.json

# 5. 手动创建 manifest.json（填入上一步的 sha256）
# 6. 提交到版本控制
```

### 更新 Agent 模板 tarball

Agent 模板从 0.1.0 升级到 0.2.0（含 tilacode 集成所需的 Python 外壳代码、配置、systemd 服务）：

```bash
cd opc-agent-programming
# 排除 tilacode/ 源码目录（二进制单独分发）、.venv、__pycache__
tar --exclude='tilacode' --exclude='.venv' --exclude='__pycache__' --exclude='.git' \
    -czf ../opc-central-dispatch-hub/static/agent-templates/programming/0.2.0/opc-agent-programming-0.2.0.tar.gz \
    .
```

### 子节点拉取流程（安装脚本执行）

```
1. 下载安装脚本：curl -fsSL http://<hub>/static/install-scripts/install-agent-programming-linux.sh
2. 执行脚本：bash install-agent-programming-linux.sh --hub-url <hub> --token <token>
3. 脚本内部：
   a. 检测平台架构（uname -s == Linux, uname -m == x86_64），不支持则报错退出
   b. 下载 Agent 模板 tarball：/static/agent-templates/programming/0.2.0/opc-agent-programming-0.2.0.tar.gz
   c. 下载 tilacode 二进制：/static/binaries/tilacode/1.18.13/tilacode-linux-x64
   d. sha256 校验二进制
   e. chmod +x tilacode-linux-x64
   f. 解压模板到 /opt/opc-agent-programming/
   g. 创建 venv，pip install -e .
   h. 探测可用端口（4096 起递增），写入 .env
   i. 注册 systemd 服务并启动
4. Agent 启动后：注册中枢 → 拉起 tilacode server → 健康检查 → WebSocket 心跳
```

## 安装目录结构（Linux）

```
/opt/opc-agent-programming/
├── app/                              # Python 外壳（从 tarball 解压）
│   ├── __init__.py
│   ├── main.py                       # 启动入口（拉起 tilacode + 注册中枢）
│   ├── config.py                     # 配置（含 TILACODE_* 配置项）
│   ├── client/
│   │   └── hub_client.py             # WebSocket 心跳/注册
│   └── executor/                     # 任务桥接层（ADR-012）
│       ├── tilacode_manager.py       # tilacode 进程管理
│       ├── tilacode_client.py        # tilacode HTTP API 客户端
│       └── task_bridge.py            # WebSocket ↔ HTTP 桥接
├── bin/
│   └── tilacode                      # 预编译二进制（从中枢下载，chmod +x）
├── .venv/                            # Python 虚拟环境
├── workspace/                        # 任务工作目录根
├── systemd/
│   └── opc-agent-programming.service
├── .env                              # 运行时配置
└── pyproject.toml
```

## 端口分配策略

- tilacode server 仅监听 `127.0.0.1:{port}`，不对外暴露，中枢无需感知端口
- 安装脚本探测 `4096` 是否占用，被占用则递增到 `4097`、`4098`...，直到找到可用端口
- 探测到的端口写入 `.env` 的 `TILACODE_PORT`
- 理由：无需中枢新增端口池接口；同机多 Agent 实例可共存

## 备选方案

| 方案 | 优势 | 劣势 | 结论 |
|------|------|------|------|
| **A（本决策）：预编译二进制 + 中枢 static 分发 + 手动编译** | Agent 节点零依赖（无需 Bun）；安装快；版本统一 | 需手动构建并复制；中枢带宽压力（130MB/节点） | ✅ 选中 |
| B：Bun + 源码打包（ADR-012 原方案） | 中枢不存大文件 | 每节点装 Bun + 源码（500MB+）；安装慢；版本易漂移 | ❌ 否决 |
| C：预编译二进制 + CI 自动构建发布 | 全自动；版本可追溯 | MVP 引入 CI 复杂度；tilacode 私有 fork 未接入 CI | ⚠️ 延后 |
| D：预编译二进制 + 外部 CDN/对象存储 | 中枢无带宽压力 | 依赖外部可用性；多一层基础设施 | ⚠️ 延后 |
| E：预编译二进制 + 模板 tarball 内嵌 | 一次下载完成 | 每平台一份冗余包；模板包膨胀到 130MB+ | ❌ 否决 |
| F：全平台支持（12 个目标） | 覆盖广 | MVP 工作量大；arm64/musl 需求未验证 | ⚠️ 延后 |

## 决策原因

1. **Agent 节点零运行时依赖**：预编译二进制无需 Bun/Node，大幅降低 Agent 节点安装复杂度和失败率
2. **安装速度快**：下载 130MB 二进制 + Python 外壳（几 MB），远快于 Bun + 源码方案
3. **版本统一可控**：中枢托管二进制，所有节点拉取同一版本，避免 `bun install` 漂移
4. **MVP 范围最小**：只支持 linux-x64，覆盖主流服务器；手动编译无需搭建 CI
5. **复用现有基础设施**：中枢已有 StaticFiles 挂载和安装脚本框架，无需新增分发组件
6. **保留演进空间**：后续可平滑过渡到 CI 自动构建（方案 C）或外部 CDN（方案 D）

## 影响

### 对 ADR-012 的影响

本决策**修订** ADR-012「对安装部署的影响」章节中关于 Bun 运行时和源码打包的决策：

- ❌ 原决策：安装脚本需新增 Bun 运行时安装步骤
- ✅ 新决策：安装脚本下载预编译二进制，无需安装 Bun
- ❌ 原决策：tilacode 项目代码需打包到 Agent 模板中
- ✅ 新决策：tilacode 以预编译二进制单独分发，Agent 模板只含 Python 外壳

ADR-012 的其余决策（HTTP Server 集成模式、任务桥接层、工作目录策略、LLM 配置策略）不受影响。

### 对 ADR-008 的影响

不违反 ADR-008。安装脚本的「检测发行版 → 安装 Python → 下载模板 → venv → 写 .env → systemd → 注册 → 心跳」主流程不变，仅新增 tilacode 二进制下载与初始化步骤。

### 对系统架构的影响

- 中枢新增 `static/binaries/` 静态目录用于托管二进制
- Agent 节点从「单进程 Python」变为「双进程：Python 外壳 + tilacode server」（与 ADR-012 一致）
- 安装脚本新增二进制下载、sha256 校验、端口探测步骤

### 对开发的影响

- 需维护 tilacode 版本与 Agent 模板版本的对应关系（manifest.json）
- tilacode 升级时需重新构建二进制并更新 manifest.json
- 安装脚本需扩展平台检测逻辑（仅 linux-x64 通过）

### 对运维的影响

- tilacode 升级流程：构建机执行 `bun run build` → 复制到中枢 static → 更新 manifest.json → 节点重新执行安装脚本升级
- 中枢需监控 `static/binaries/` 磁盘占用（每个版本约 130MB）
- Agent 节点安装时长增加约 30 秒（下载 130MB 二进制，内网环境下）

### 对安全的影响

- 二进制通过 HTTP 分发，MVP 阶段可接受，生产环境应切 HTTPS（后续迭代）
- sha256 校验防止二进制被篡改或下载损坏
- tilacode server 仅监听 127.0.0.1，不对外暴露

## 后续迭代记录

以下需求已记录，将在后续迭代中实现，不在本次 MVP 范围内：

1. **CI 自动构建**：tilacode 接入 GitHub Actions，tag 触发自动构建并发布到中枢
2. **多平台支持**：扩展 arm64 (glibc)、musl (Alpine)、无 AVX2 baseline 等变体
3. **HTTPS 分发**：中枢启用 HTTPS，保障二进制传输安全
4. **增量更新**：二进制差分升级，减少带宽消耗
5. **签名校验**：除 sha256 外，增加 GPG 签名验证

## 关联文档

- [ADR-012 基于 tilacode 集成编程执行层](./012-tilacode-execution-layer-integration.md)（本决策修订其部署方案）
- [ADR-008 Linux 编程 Agent 节点 MVP 与节点管理](./008-agent-node-linux-programming-mvp.md)
- [ADR-006 Agent 节点部署与接入方案](./006-agent-node-deployment.md)
- [013-programming-agent-binary-distribution-spec.md](../spec/013-programming-agent-binary-distribution-spec.md)
- [013-programming-agent-binary-distribution-harness.md](../spec/013-programming-agent-binary-distribution-harness.md)
- [008-node-management-and-programming-agent-linux-spec.md](../spec/008-node-management-and-programming-agent-linux-spec.md)

## 变更记录

| 日期         | 变更内容                                                       | 变更人 |
| ------------ | -------------------------------------------------------------- | ------ |
| 2026-08-10   | 初始创建：预编译二进制分发协议                                   | AI助手 |
| 2026-09-07   | **被 [ADR-024](./024-agent-code-unified-artifact.md) 修订**：tilacode 二进制不再独立分发（`binaries/tilacode/{ver}/` 单二进制 manifest.json 协议废止），与 Nuitka 编译的代理层合并为单一产物 `agent-code/{ver}/opc-agent-code-{ver}-linux-x64.tar.gz`，sha256 由云端总清单（ADR-022）承载；安装脚本的二进制下载/校验步骤由整体包下载取代；旧目录保留历史版本供已部署节点。tilacode 的 Bun 构建（`--skip-embed-web-ui --target linux-x64-baseline`）与命名迁移流程不变 | AI助手 |

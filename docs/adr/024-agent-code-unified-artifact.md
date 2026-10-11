# ADR-024：Code Agent 统一产物与 Nuitka 源码保护

## 状态

Accepted（主体决策有效；key 规范已被 [ADR-025](./025-agent-resource-type-binding.md) 修订为 v2）

## 日期

2026-09-07

## 问题背景

[ADR-013](./013-programming-agent-binary-distribution.md) 确立了「tilacode 预编译二进制 + Python 代理层模板」的分发体系，[ADR-022](./022-agent-resource-cloud-distribution.md) 将其云端化（COS 托管 + 中枢同步）。落地后暴露三个结构性问题：

1. **同一个产品被拆成三个产物**：Python 代理层（app/）与 tilacode 二进制本是一个整体（code agent 的桥接层与执行层），却分开构建、分开迁移、分开下载、分开版本配对（代理 0.4.4 × tilacode 1.18.13 手工对齐），发布一次要操作两个产物 + 各自的 manifest。
2. **代理层源码明文分发**：模板 tarball 内含 28 个 .py 明文（18 个 app/ + 10 个 linux-python/ 遗留源码），节点侧可随意查看修改。
3. **节点被迫安装 Python**：安装脚本 \~900 行中过半是 Python 检测兜底链（宝塔/pyenv 扫描、EPEL 重试、Miniconda 兜底——全部来自历史踩坑），且 `pip install` 要求节点能访问 PyPI 外网。

## 决策

### 1. 代理层用 Nuitka 真编译（源码保护）

将 `app/`（Python 代理层）用 Nuitka 编译为 Linux x64 单文件二进制 `opc-agent`：

| 方案                     | 保护强度          | 结论                     |
| ---------------------- | ------------- | ---------------------- |
| **Nuitka 真编译（C 级机器码）** | 强             | ✅ 选中                   |
| PyInstaller 单文件        | 中（pyc 可提取反编译） | ❌ 否决                   |
| Cython .so + 保留 venv   | 中             | ❌ 否决（复杂度高，仍需节点 Python） |
| pyarmor 混淆             | 中             | ❌ 否决（大文件需商业授权）         |
| 仅 .pyc                 | 弱             | ❌ 否决（形同虚设）             |

### 2. 整合为单一产物 tarball

一个 tar.gz 承载完整 code agent（代理 + 执行层 + 服务定义），云端清单条目 **3 → 2**：

```
agent-code/{version}/opc-agent-code-{version}-linux-x64.tar.gz
├── bin/
│   ├── opc-agent          # Nuitka 编译的代理层（替代 app/ 源码 + venv）
│   └── tilacode           # Bun 编译的执行层二进制（~130MB，沿用 ADR-013 构建方式）
├── systemd/
│   └── opc-agent-code.service
├── env.example
├── build-info.json        # 构建元数据（tilacode_version / nuitka 版本 / built_at）
└── README.md
```

**安装脚本保持独立产物**（`install-script-linux` 条目）：历史上安装脚本热修频繁（BOM 修复、宝塔扫描、EPEL 兜底全是脚本级修复），独立后修脚本只重发 30KB，不必重传 150MB 大包。否决「makeself 自解压一体安装器」（清单条目减到 1，但每次脚本修复都要重传全量包）。

### 3. 节点零 Python 依赖

- systemd 直跑二进制：`ExecStart=/opt/opc-agent-code/bin/opc-agent`

- 安装目录：`/opt/opc-agent-code/`

- 安装脚本删除整条 Python 检测链与 venv/pip 步骤，仅保留：平台检测 → 依赖检测（curl/tar/systemd）→ 下载整体包（sha256 校验）→ 解压 → 端口探测写 .env → systemd 安装启动

### 4. 统一版本号

- 整体包版本从 **0.5.0** 起步（0.4.4 → 0.5.0，语义为产物形态 breaking change）

- tilacode 升级 = 整体包版本 bump + 重新打包（`build-info.json` 记录内部 tilacode\_version 供展示），消除手工版本配对

- 云端总清单（ADR-022）条目 `version` 字段填整体包版本

### 5. 构建环境：现有 Linux 服务器手动构建

Nuitka 无法在 Windows 交叉编译，统一在 **192.168.101.104 Linux 服务器**上手动构建（用户执行，流程由一键脚本 `scripts/build-code-agent.sh` 固化）：

```
1. tilacode 二进制：Windows Bun 交叉编译（既有流程，ADR-013）→ scp 上传服务器
2. 服务器：Nuitka 编译代理层 → dist/opc-agent
3. 服务器：一键脚本组包 tarball + 计算 sha256
4. 上传 COS：RuoYi 管理后台「资源发布」页（key=agent-code-linux-x64，走 ADR-023 链路）
```

### 6. 升级迁移（已部署节点）

新脚本安装到 `/opt/opc-agent-code/`，若检测到旧目录 `/opt/opc-agent-programming/`：

1. **迁移节点身份**：读取旧 `.env` 的 `AGENT_CLIENT_ID` / `AGENT_NAME` 写入新 `.env`（client\_id 是中枢节点记录的复用键，必须保留，否则产生重复节点）
2. 停止并禁用旧 systemd 服务 `opc-agent-programming.service`
3. 旧目录保留不删（回滚余地），日志提示可手动清理

## 备选方案

| 方案                                    | 优势                                     | 劣势                        | 结论   |
| ------------------------------------- | -------------------------------------- | ------------------------- | ---- |
| **A（本决策）：Nuitka + 单一 tarball + 独立脚本** | 源码强保护；一次构建一次下载；节点零 Python；版本统一；脚本可独立热修 | 构建依赖 Linux 服务器；包体 \~150MB | ✅ 选中 |
| B：PyInstaller + 单一 tarball            | 构建链更简单                                 | pyc 可被提取反编译，保护不足          | ❌ 否决 |
| C：makeself 一体安装器（脚本内嵌）                | 清单仅 1 条目                               | 脚本修复需重传 150MB             | ❌ 否决 |
| D：仅 Nuitka 编译，产物维持分离                  | 改动最小                                   | 不解决分开构建/下载/版本配对问题（用户明确诉求） | ❌ 否决 |
| E：Docker 镜像分发                         | 环境完全一致                                 | 节点需装 Docker，与轻量部署冲突       | ❌ 否决 |

## 决策原因

1. **产品完整性**：代理层与 tilacode 本为一体，统一产物消除三处割裂（构建/分发/版本配对）
2. **源码保护**：Nuitka 编译为机器码，是 Python 生态可用的最强保护（真值钱的 tilacode 已是编译二进制）
3. **部署极简**：节点零 Python、零 PyPI 外网依赖、零 venv——历史踩坑的整个 Python 检测链从根上消失，安装脚本预计从 \~900 行降至 \~250 行
4. **发布收敛**：一次构建产出一个包，经 ADR-023 发布页一键上传 COS，中枢按 ADR-022 同步，全链路已打通
5. **兼容不破坏**：已部署节点不受影响（旧产物目录保留）；升级走幂等安装命令 + 身份迁移

## 影响

### 对 ADR-013 的影响（修订）

- 单二进制 `manifest.json` 协议（`binaries/tilacode/{ver}/manifest.json`）**废止**：tilacode 二进制并入整体包，sha256 由云端总清单（ADR-022）统一承载

- 安装脚本「下载二进制 + 校验」步骤删除，改为下载整体包

- 旧目录 `static/agent-templates/`、`static/binaries/` 保留历史版本供已装节点，不再新增

### 对 ADR-008 / ADR-006 的影响（修订）

- 安装模型从「检测 Python → venv → pip install 模板」变为「解压二进制直跑」；原「安装脚本需安装 Python ≥3.10」的约束仅对开发模式有效

- ADR-008 的 Python 兜底链（宝塔扫描/EPEL/Miniconda）整体作废

### 对 ADR-022 / ADR-023 的影响

- 云端清单 key 约定更新：`agent-template-programming` + `tilacode-linux-x64` → `agent-code-linux-x64`（`install-script-linux` 不变）

- 中枢 `ResourceSyncService` **零改动**（manifest 驱动、key 无关）

- RuoYi 管理后台「资源发布」页 key 白名单需同步更新（后端 `resolvePath` + 前端 `keyOptions`）

### 对运维的影响

- 构建：登录 192.168.101.104 跑一键脚本（首次需装 Python3.11 + gcc + patchelf + Nuitka）

- 发布：整体包经发布页上传，中枢 ≤6h 自动发现或手动「立即检查」

- 升级：节点重跑安装命令（自动迁移节点身份）

## 关联文档

- [ADR-013 编程 Agent 预编译二进制分发协议](./013-programming-agent-binary-distribution.md)（本决策修订其产物协议）

- [ADR-022 Agent 资源云端分发与热更新](./022-agent-resource-cloud-distribution.md)

- [ADR-023 云端资源仓库管理](./023-cloud-repository-management.md)

- [024-agent-code-unified-artifact-spec.md](../spec/024-agent-code-unified-artifact-spec.md)

- [024-agent-code-unified-artifact-harness.md](../spec/024-agent-code-unified-artifact-harness.md)

- [024-agent-code-unified-artifact-changes.md](../spec/024-agent-code-unified-artifact-changes.md)

## 变更记录

| 日期         | 变更内容                                                        | 变更人  |
| ---------- | ----------------------------------------------------------- | ---- |
| 2026-09-07 | 初始创建：Nuitka 编译代理层 + 单一 tarball 产物 + 节点零 Python + 统一版本 0.5.0 | AI助手 |
| 2026-09-08 | 修订「历史条目处理策略」：原「旧 key 保留历史条目至旧节点淘汰」改为「发布端提供下架能力，立即清理」。落地：RuoYi 发布页新增删除条目功能（清单移除+COS 对象删除），中枢状态视图以云端快照为准（下架条目不再展示），全量应用更新时本地清单镜像云端；已实际下架 agent-template-programming 条目与 COS 对象（revision 2026-09-08T01:22:10Z）。key 命名规范明确为 `agent-{type}-{platform}`（未来如 agent-video-linux-x64），类型形态迁移 = 发布新 key + 下架旧 key | AI助手 |


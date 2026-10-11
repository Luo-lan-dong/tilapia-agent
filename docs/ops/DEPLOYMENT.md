# 部署总览（DEPLOYMENT）

> 本项目由多个子项目组成：**中枢**（`opc-central-dispatch-hub`，含 Web 控制台）、
> **编程 Agent 节点**（`opc-agent-programming`）、**办公 Agent 节点**（`opc-agent-office`）。
> 本文档是部署路线的**唯一枢纽**：先选路线，再按链接进入对应文档。

## 路线选择

| 你是谁 | 路线 | 需要构建吗 |
|---|---|---|
| 想尽快用起来 | **路线 A · 安装包部署** | 否（下载/复用现成产物） |
| 开发者 | **路线 B · 源码运行与构建**（B1 源码运行 → B2 构建分发物） | 运行不需要；产出分发物时需要 |

---

## 路线 A · 安装包部署（推荐起步）

> 前提：已有一份中枢安装包 `TilapiaHub-Setup-x64.exe` 与一份节点包
> `opc-agent-code-<版本>-linux-x64.tar.gz`（来源：项目 Releases、或自行按路线 B2 构建）。

### A1. 安装中枢（Windows）

1. 双击 `TilapiaHub-Setup-x64.exe`，按向导安装（用户级安装，无需管理员权限）；
2. 启动「TilapiaHub」——**内嵌 PostgreSQL 自动初始化**（建库/建表/种子数据全自动），无需安装 PG；
3. 打开控制台（桌面窗口或 `http://127.0.0.1:8000`）。

### A2. 配置 LLM 模型

控制台 →「系统设置 → 模型管理」→ 添加 Provider（如 DeepSeek，填 API Key）。
凭据存数据库；所有编排/派发的模型调用都从这里读取。

### A3. 安装 Agent 节点（Linux）

在目标 Linux 机器上执行（`<hub>` 换成中枢地址，`<token>` 换成控制台「系统设置」里的节点注册 Token）：

```bash
curl -fsSL http://<hub>:8000/static/install-scripts/code/install-agent-code-linux.sh \
  | sudo bash -s -- --hub-url http://<hub>:8000 --token <token>
```

脚本自动完成：下载整体包 → sha256 校验 → 解压 `/opt/opc-agent-code/` → systemd 启动，
**节点不需要安装 Python**。办公节点对应 `install-agent-office-linux.sh`（脚本同目录）。

### A4. 验证

- 控制台「节点管理」出现新节点且心跳正常；
- 「新建任务」派发一个测试任务，观察执行进度与产物。

### 节点包怎么进入中枢的分发目录？

中枢从自身 `data/resources/agent-code/<版本>/` 向节点提供安装包。把 tarball 放入：

```
<data 目录>/resources/agent-code/<版本>/opc-agent-code-<版本>-linux-x64.tar.gz
```

（桌面版数据目录见安装目录下 `data/`；若部署了自建云端资源仓库，中枢会自动同步。）

---

## 路线 B · 开发者（源码运行与构建）

### B1. 源码运行

| 组件 | 步骤 | 文档 |
|---|---|---|
| 中枢后端 | 装 PG → 建库 → `.env.local` → `alembic upgrade head` → `uvicorn main:app` | [opc-central-dispatch-hub/README.md](../../opc-central-dispatch-hub/README.md) |
| Web 控制台 | `npm install && npm run dev`（5173，代理 /api → 8000） | 同上 README「前端」节 |
| 办公 Agent 节点 | `.env` → `python -m app.main` → 派发测试任务 | [opc-agent-office/docs/local-dev-guide.md](../../opc-agent-office/docs/local-dev-guide.md) |
| 编程 Agent 节点 | 本地调试建议直接走整体包安装（路线 A3）；构建分发物见 B2 | — |

数据库要点速记：`alembic upgrade head` 只建表不建库；`REDIS_URL` 填占位值即可（代码零调用）；
LLM 凭据存数据库（`llm_providers` 表），`.env.local` 的 `DEEPSEEK_*` 仅空库首启种子。

---

### B2. 构建分发物

| 产物 | 文档 | 关键依赖 |
|---|---|---|
| 中枢桌面安装包（Windows exe + Inno Setup） | [hub-desktop-build-guide.md](./hub-desktop-build-guide.md) | Windows + Node.js + PG 便携版（脚本自动下载） |
| 编程节点整体包（Linux tar.gz） | [code-agent-build-guide.md](./code-agent-build-guide.md) | Linux 构建机（Python 3.11 + Nuitka）+ tilacode 预编译二进制 |
| 办公节点包 | `opc-agent-office/scripts/package_office_artifact.sh`（开发教程 §10 有部署概要） | Linux |
| tilacode 二进制（仅改动了 tilacode 源码时） | 上游 `opc-agent-programming/tilacode/BUILD.md`（构建）与 [code-agent-build-guide §3](./code-agent-build-guide.md#3-上传命令windows-powershell)（上传） | Bun |

构建产物的分发关系：

```
tilacode 二进制 ──► 节点整体包 tarball ──► 中枢 data/resources/ ──► 节点安装脚本
中枢源码 ──► TilapiaHub-Setup-x64.exe ──► 用户机器
```

#### 编程节点（code）构建路径

> 目前仅支持 **Linux 节点**。前提：已按 B1 把中枢前后端跑起来；
> 环境准备（Linux 构建机 / Bun / Node.js / Python）见根 README「环境要求 · 开发者」。

按你的处境对号入座：

- **首次构建**：按 [code-agent-build-guide.md](./code-agent-build-guide.md) 走全流程——
  §2 是需要上传到 Linux 构建机的**文件清单**，§3–§5 是上传 / 环境准备 / 构建命令；
- **日常迭代**（首次构建成功后）：只看 [§8 迭代更新流程](./code-agent-build-guide.md#8-迭代更新流程日常改代码后)——
  改了什么传什么，版本号 patch+1；
- **改了 `opc-agent-programming/tilacode` 执行层源码**：先按上游
  [tilacode/BUILD.md](../../opc-agent-programming/tilacode/BUILD.md) 在 Windows 交叉编译出
  tilacode 二进制 → 上传 Linux 构建机覆盖 `artifacts/tilacode` → 再走节点构建；
- 构建出的 tarball 进入中枢分发目录的方式，见路线 A「节点包怎么进入中枢的分发目录」。

---

## 常见问题速查

| 问题 | 答案 |
|---|---|
| 需要装 Redis 吗？ | 不需要。`REDIS_URL` 填占位值即可（代码零调用，桌面版 launcher 注释明示） |
| `alembic upgrade head` 会建库吗？ | 只建表。数据库本身要先 `CREATE DATABASE` |
| LLM 的 API Key 配在哪？ | 运行期一律读数据库（系统设置 → 模型管理）；`.env.local` 的 `DEEPSEEK_*` 只在空库首启时作种子 |
| 节点要装 Python 吗？ | 不用。整体包含 Nuitka 编译的二进制，systemd 直接跑 |
| 桌面版数据在哪？ | 安装目录 `data/`（内嵌 PG 数据在 `data/pg/`），卸载时一并删除 |

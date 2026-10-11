# Code Agent 统一产物与 Nuitka 源码保护 Spec

关联 ADR：[ADR-024](../adr/024-agent-code-unified-artifact.md)
关联 Harness：[024-agent-code-unified-artifact-harness.md](./024-agent-code-unified-artifact-harness.md)
关联 Changes：[024-agent-code-unified-artifact-changes.md](./024-agent-code-unified-artifact-changes.md)

## 1. 功能描述

1. **Nuitka 编译**：`app/`（Python 代理层）编译为 Linux x64 单文件二进制 `opc-agent`，源码不再进产物
2. **单一产物**：`opc-agent` + `tilacode` + systemd + 配置模板打成一个 tar.gz，云端清单条目 3 → 2（`install-script-linux` + `agent-code-linux-x64`）
3. **节点零 Python**：新安装脚本不检测/安装 Python、不建 venv、不 pip install、不访问 PyPI
4. **统一版本**：整体包版本 0.5.0 起，`build-info.json` 记录内部 tilacode 版本
5. **升级迁移**：已部署节点重跑安装命令，自动迁移节点身份（AGENT_CLIENT_ID / AGENT_NAME）

## 2. 用户故事

| 编号 | 用户故事 | 验收要点 |
|------|---------|---------|
| US-1 | 作为开发者，代理层源码不随产物分发 | tarball 内无 .py 源码（仅二进制/配置/文档）；bin/opc-agent 为 ELF 机器码 |
| US-2 | 作为运维，一次构建产出一个包 | 一键脚本产出单一 tar.gz，含 sha256；云端清单一个条目 |
| US-3 | 作为运维，任意 Linux 服务器不装 Python 即可接入 | 全新无 Python Ubuntu/CentOS 节点执行安装命令成功，systemd 拉起、注册、心跳正常 |
| US-4 | 作为运维，升级不丢失节点身份 | 旧节点（0.4.x）重跑安装命令后，中枢节点列表仍是原记录（client_id 不变），旧服务停用 |
| US-5 | 作为开发者，在资源发布页上传新形态产物 | RuoYi 发布页 key 下拉出现 `agent-code-linux-x64`，发布后清单条目正确 |
| US-6 | 作为用户，中枢仍按 ADR-022 自动同步 | 中枢「立即检查」发现新版本 → 应用更新 → `/static/agent-code/...` 服务新包 |

## 3. 产物结构

### 3.1 tarball 内容

```
opc-agent-code-0.5.0-linux-x64.tar.gz
├── bin/
│   ├── opc-agent                  # Nuitka onefile 二进制（~20-40MB）
│   └── tilacode                   # Bun 编译二进制（~130MB，来自 dist/.../bin/opencode 重命名）
├── systemd/
│   └── opc-agent-code.service     # ExecStart=/opt/opc-agent-code/bin/opc-agent
├── env.example
├── build-info.json
└── README.md
```

`build-info.json`：

```json
{
  "artifact_version": "0.5.0",
  "tilacode_version": "1.18.13",
  "nuitka_version": "2.x",
  "python_version": "3.11.x",
  "built_at": "2026-09-07T12:00:00Z",
  "platform": "linux-x64"
}
```

### 3.2 云端清单（ADR-022 总清单）条目变更

| key | 状态 | path |
|-----|------|------|
| `install-script-linux` | 不变 | `install-scripts/install-agent-programming-linux.sh` |
| `agent-code-linux-x64` | **新增** | `agent-code/0.5.0/opc-agent-code-0.5.0-linux-x64.tar.gz` |
| `agent-template-programming` | 废止（保留历史条目至旧节点淘汰） | `agent-templates/...` |
| `tilacode-linux-x64` | 废止（同上） | `binaries/...` |

## 4. 构建流程（Linux 服务器手动执行）

> 详细操作手册（需上传服务器的文件清单、PowerShell 上传命令、FAQ）见 [code-agent-build-guide.md](../ops/code-agent-build-guide.md)。

### 4.1 服务器首次环境准备（192.168.101.104）

```bash
# Python 3.11 + 编译工具链（Nuitka 依赖 gcc/patchelf）
sudo apt-get install -y python3.11 python3.11-venv gcc patchelf   # Debian/Ubuntu
# 或 yum/dnf 等价命令

# 同步模板源码到服务器（git clone 或 rsync），示例目录 ~/tilapia-agent
```

### 4.2 tilacode 二进制（沿用 ADR-013 流程）

Windows 构建机交叉编译后 scp 上传服务器（或服务器上构建）：

```bash
# Windows: cd tilacode/packages/opencode && bun run build --skip-embed-web-ui --target linux-x64-baseline
# scp dist/tilacode-linux-x64-baseline/bin/opencode user@192.168.101.104:~/tilapia-agent/opc-agent-programming/artifacts/tilacode
```

### 4.3 一键组包（服务器上，脚本固化）

```bash
cd ~/tilapia-agent/opc-agent-programming
bash scripts/build-code-agent.sh 0.5.0
# 产出 artifacts/opc-agent-code-0.5.0-linux-x64.tar.gz + 控制台打印 sha256/size
```

`scripts/build-code-agent.sh` 内部步骤：

```
1. .venv 不存在则创建（python3.11）+ pip install -e ".[dev]" + nuitka
2. Nuitka 编译：
   python -m nuitka --onefile --standalone \
     --output-filename=opc-agent --output-dir=artifacts/nuitka \
     --nofollow-import-to=tests --python-flag=no_site \
     app/main.py
3. 组包：bin/opc-agent + bin/tilacode + systemd + env.example + build-info.json + README
4. 计算 sha256 / size 并打印（发布页上传后自动登记清单）
```

### 4.4 发布

RuoYi 管理后台 → 云端资源 → 资源发布 → key=`agent-code-linux-x64`、版本=`0.5.0`、上传 tar.gz → 发布成功（manifest 自动更新 revision）。中枢侧无需任何改动。

## 5. 安装与升级流程（新安装脚本）

### 5.1 全新安装

```
curl -fsSL {HUB}/static/install-scripts/install-agent-programming-linux.sh | sudo bash -s -- --hub-url {HUB} --token {TOKEN}
  ├─ 平台检测（linux x64）与依赖检测（curl/tar/systemd）——不检测 Python
  ├─ 下载 agent-code 整体包 → sha256 校验
  ├─ 解压到 /opt/opc-agent-code/
  ├─ 生成 .env（AGENT_CLIENT_ID / AGENT_NAME / HUB_URL / TOKEN / TILACODE_PORT 探测）
  ├─ systemd 安装启动（opc-agent-code.service）
  └─ Agent 注册中枢 → 拉起 tilacode → 健康检查 → 心跳
```

### 5.2 旧节点升级（0.4.x → 0.5.0）

```
同上安装命令（幂等）：
  ├─ 检测旧目录 /opt/opc-agent-programming/.env 存在
  │    ├─ 迁移 AGENT_CLIENT_ID / AGENT_NAME 到新 .env（身份保留）
  │    └ systemctl stop opc-agent-programming && systemctl disable（旧服务停用）
  ├─ 检测新目录已有 .env → 保留（幂等重装）
  └─ 旧目录保留不删（提示可手动清理）
```

### 5.3 安装目录

```
/opt/opc-agent-code/
├── bin/opc-agent、bin/tilacode
├── systemd/opc-agent-code.service
├── .env
└── build-info.json
```

## 6. RuoYi 发布页适配（代码改动）

- 后端 `CloudResourceServiceImpl.resolvePath`：新增 `agent-code-linux-x64` → `agent-code/{ver}/opc-agent-code-{ver}-linux-x64.tar.gz`；旧 key（agent-template-programming / tilacode-linux-x64）从白名单移除
- 前端 `repository/index.vue` 的 `keyOptions` 同步更新

## 7. 降级链路

| 场景 | 行为 |
|------|------|
| Nuitka 二进制在节点无法执行（glibc 过旧等） | 安装脚本启动前 `bin/opc-agent --version` 冒烟失败 → 明确报错提示发行版要求 |
| 整体包下载失败 | 安装中止，明确报错（可重试，幂等） |
| 升级后异常需回滚 | 旧目录未删：重跑旧版安装命令（旧产物保留在中枢 static 历史目录） |
| 旧节点继续用旧产物 | 中枢 static 保留 agent-templates/ 与 binaries/ 历史版本，不受影响 |

## 8. 验收标准

1. tarball 内零 .py 源码；`file bin/opc-agent` 显示 ELF 机器码；`bin/opc-agent --version` 正常输出
2. 无 Python 全新节点（Ubuntu 22.04 / CentOS 8 各一台）一行命令安装成功，中枢节点列表出现且心跳/监控正常，能执行一次真实编程任务（E2E）
3. 旧 0.4.4 节点重跑安装命令：中枢节点记录不变（client_id 复用），旧 systemd 服务已停用，新服务运行
4. RuoYi 发布页可发布 `agent-code-linux-x64` 0.5.0；中枢「立即检查」→ 应用更新 → `/static/agent-code/0.5.0/...` 可匿名下载且 sha256 与清单一致
5. Nuitka 二进制冷启动耗时与内存占用记录在案（与源码版对比无显著回退，如冷启动 < 5s）

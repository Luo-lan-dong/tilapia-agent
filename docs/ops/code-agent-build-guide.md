# Code Agent 整体包构建与发布指南

> 基于 [ADR-024](../adr/024-agent-code-unified-artifact.md) 决策：代理层 Nuitka 编译 + tilacode 二进制 + systemd 打成单一 tarball，节点零 Python 依赖。
> 本文档是**操作手册**（文件清单、命令、流程），设计与验收标准见 [024-agent-code-unified-artifact-spec.md](../spec/024-agent-code-unified-artifact-spec.md)。

## 1. 职责分工

| 机器                           | 职责                                         | 产物                                     |
| ---------------------------- | ------------------------------------------ | -------------------------------------- |
| Windows 开发机（本机）              | 编辑源码；tilacode 二进制既有构建（Bun，可复用已构建的版本） | `tilacode-linux-x64`                   |
| Linux 构建服务器（自备，`$SERVER`） | Nuitka 编译代理层；组包 tarball                    | `opc-agent-code-{版本}-linux-x64.tar.gz` |
| 云端资源仓库（可选，自建） | 上传发布（无云端仓库时手动放置到中枢 `data/resources/`，见 [DEPLOYMENT.md](./DEPLOYMENT.md) 路线 A） | manifest.json 条目更新                     |
| 中枢（TilapiaHub）               | 同步云端资源到 `data/resources/`，经 `/static` 分发   | 节点安装源                                  |

> 文中 `<build-server>` / `$SERVER` 泛指任意一台可 SSH 的 Linux 构建机（VPS 或内网服务器均可）。

## 2. 需要上传到 Linux 服务器的文件清单

服务器上的工作目录约定为 `~/tilapia-agent/opc-agent-programming/`，与仓库路径结构一致。

| 文件 / 目录                                   | 必须 | 用途                                                      |
| ----------------------------------------- | -- | ------------------------------------------------------- |
| `app/`（完整目录，含所有 .py）                      | ✅  | Nuitka 编译对象（代理层源码，含 client/executor/models/utils 子包）    |
| `pyproject.toml`                          | ✅  | 依赖声明（构建 venv 执行 `pip install -e .` 解析 httpx/pydantic 等） |
| `scripts/build-code-agent.sh`             | ✅  | 一键构建脚本                                                  |
| `systemd/opc-agent-code.service`          | ✅  | 组包内容：systemd 服务定义                                       |
| `.env.example`                            | ✅  | 组包内容：配置模板                                               |
| `README.md`                               | ✅  | 组包内容：说明文档                                               |
| `artifacts/tilacode`                      | ✅  | tilacode 预编译二进制（Windows 构建产物，见 §3.2）                    |
| `tilacode/packages/opencode/package.json` | 可选 | 供构建脚本读取 tilacode 版本号写入 build-info.json；缺失时显示 `unknown`  |

**不需要上传**：`tests/`、`linux-python/`（遗留源码）、`.venv/`、`.git/`、`.pytest_cache/`、`.ruff_cache/`、`tilacode/` 其余全部源码、`opc-central-dispatch-hub/`。

## 3. 上传命令（Windows PowerShell）

以下命令在仓库根目录执行；`$SERVER` / `$ROOT` 按实际环境修改。

```powershell
$SERVER = "user@<build-server>"    # Linux 构建机（SSH 用户@地址）
$ROOT = "<仓库根目录>"               # 本仓库检出目录

# ① 服务器上创建目录骨架（artifacts 用于存放 tilacode 二进制）
ssh $SERVER "mkdir -p ~/tilapia-agent/opc-agent-programming/artifacts ~/tilapia-agent/opc-agent-programming/tilacode/packages/opencode"

# ② 上传构建所需源码与配置（3 个目录 + 3 个文件）
scp -r "$ROOT\opc-agent-programming\app" "$ROOT\opc-agent-programming\scripts" "$ROOT\opc-agent-programming\systemd" "${SERVER}:~/tilapia-agent/opc-agent-programming/"
scp "$ROOT\opc-agent-programming\pyproject.toml" "$ROOT\opc-agent-programming\README.md" "$ROOT\opc-agent-programming\.env.example" "${SERVER}:~/tilapia-agent/opc-agent-programming/"

# ③ 上传 tilacode 二进制
#    路径 A（推荐）：复用中枢已构建的 1.18.13，无需重新构建
scp "$ROOT\opc-central-dispatch-hub\static\binaries\tilacode\1.18.13\tilacode-linux-x64" "${SERVER}:~/tilapia-agent/opc-agent-programming/artifacts/tilacode"
#    路径 B：若 tilacode 源码有改动需重新构建，见 §6 升级 tilacode

# ④（可选）上传 tilacode 版本描述文件（build-info.json 显示准确版本号）
scp "$ROOT\opc-agent-programming\tilacode\packages\opencode\package.json" "${SERVER}:~/tilapia-agent/opc-agent-programming/tilacode/packages/opencode/"
```

## 4. 服务器首次环境准备（仅一次）

```bash
# Debian / Ubuntu
sudo apt-get update
sudo apt-get install -y python3.11 python3.11-venv gcc patchelf ccache

# Fedora / RHEL / CentOS
sudo dnf install -y python3.11 gcc patchelf ccache
```

说明：

- `python3.11`/`gcc`/`patchelf` 是 Nuitka 编译工具链（**仅构建机需要，Agent 节点不需要**）

- `ccache` 可选但强烈推荐：Nuitka 自动利用它做增量编译，二次构建从 3\~8 分钟降到约 1 分钟

- 服务器需能访问 PyPI（内网可配镜像，见 §8 FAQ）

## 5. 构建命令（服务器上）

```bash
ssh $SERVER
cd ~/tilapia-agent/opc-agent-programming

bash scripts/build-code-agent.sh          # 版本号缺省读取 pyproject.toml（当前 0.5.0）
# 或显式指定：bash scripts/build-code-agent.sh 0.5.1
```

脚本内部自动完成：创建/复用 `.venv` → 安装依赖与 Nuitka → 编译（onefile）→ `--version` 冒烟 → 组包（bin/systemd/env.example/build-info.json/README）→ 打 tarball → 打印 sha256/size。

构建产物：

```
artifacts/
├── opc-agent-code-0.5.0-linux-x64.tar.gz    ← 发布对象（下载到本机或直接发布）
└── nuitka/opc-agent                            ← Nuitka 二进制（组包源）
```


## 7. 节点安装 / 升级

```bash
# 全新安装与旧节点升级是同一条命令（幂等）
curl -fsSL http://<hub>:8000/static/install-scripts/install-agent-code-linux.sh \
  | sudo bash -s -- --hub-url http://<hub>:8000 --token <token>
```

- 全新节点：下载整体包 → sha256 校验（对总清单）→ 解压 `/opt/opc-agent-code/` → systemd 启动。**全程不检测/不安装 Python**
- 验证：`systemctl status opc-agent-code`、中枢节点列表心跳正常

## 8. 迭代更新流程（日常改代码后）

| 改动内容                           | 需重新上传                                                  | 需 bump 版本                |
| ------------------------------ | ------------------------------------------------------ | ------------------------ |
| 仅 `app/` 代理层代码                 | `app/` 目录（一条 scp）                                      | ✅ pyproject.toml version |
| systemd / env.example / README | 对应文件                                                   | ✅ 同上                     |
| tilacode 源码                    | Windows 重新构建二进制 → `scp` 覆盖 `artifacts/tilacode` → 重跑构建 | ✅ 同上                     |

```powershell
# 例：仅改了代理层代码（版本号已 bump 到 0.5.1）
scp -r "$ROOT\opc-agent-programming\app" "${SERVER}:~/tilapia-agent/opc-agent-programming/"
scp "$ROOT\opc-agent-programming\pyproject.toml" "${SERVER}:~/tilapia-agent/opc-agent-programming/"
# 服务器重跑 bash scripts/build-code-agent.sh → 发布页上传 0.5.1 → 节点重跑安装命令
```

tilacode 重新构建（仅 tilacode 改动时）：

```powershell
# Windows：进入 tilacode 的 opencode 包目录构建
cd $ROOT\opc-agent-programming\tilacode\packages\opencode
bun run build --skip-embed-web-ui --target linux-x64-baseline
# 产物 dist/tilacode-linux-x64-baseline/bin/opencode → scp 上传覆盖 artifacts/tilacode
scp dist\tilacode-linux-x64-baseline\bin\opencode "${SERVER}:~/tilapia-agent/opc-agent-programming/artifacts/tilacode"
```

## 9. 常见问题

### Q: 首次构建很慢？

Nuitka 首次全量编译约 3\~8 分钟属正常。安装 `ccache` 后二次构建约 1 分钟。

### Q: 服务器无法访问 PyPI？

配置国内镜像后重跑：

```bash
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
```

### Q: 节点上报「opc-agent 二进制无法执行」/ ldd 缺库？

多为 **glibc 版本兼容问题**：Nuitka 产物动态链接构建机的 glibc，要求节点 glibc ≥ 构建机。排查：

```bash
# 节点上查看 glibc 版本
ldd --version
# 检查缺失库
ldd /opt/opc-agent-code/bin/opc-agent
```

若存在老发行版节点（如 CentOS 8，glibc 2.28）而构建机较新（Ubuntu 22.04，glibc 2.35），需在与节点相同或更老的基线上构建（Docker 起一个对应发行版容器执行 build-code-agent.sh）。

### Q: Nuitka 编译报 pydantic 相关错误？

pydantic v2 的 pydantic-core 是 Rust 编译扩展，个别 Nuitka 版本需要显式提示。把完整报错发给 AI 处理，常见修法是给构建命令追加 `--include-package=pydantic`。

### Q: 构建产物里 sha256 去哪了？

脚本构建完成时在控制台打印 sha256/size。**发布页上传后会自动计算并写入云端总清单**，无需手工登记（ADR-023 链路）。

## 变更记录

| 日期         | 变更内容                             | 变更人  |
| ---------- | -------------------------------- | ---- |
| 2026-09-07 | 初始创建：文件清单、上传命令、构建/发布/节点升级全流程、FAQ | AI助手 |


## 10. 浏览器能力（ADR-061 M7，可选但推荐）

> 子 Agent 的前端实测（`frontend_probe.mjs`）需要三样东西**随包分发**：
> ① 便携 node 运行时（目标节点默认没有 node）② playwright 包 ③ chromium 二进制。
> 三者都由 §10.2 在构建机备好、由 §10.3 打进 tarball、由 §10.4 在节点安装系统库。
> 不做本节也可发布，只是前端任务会报「无法验证（退出码 3）」，不会假成功。

### 10.1 构建机一次性依赖

```bash
sudo apt-get install -y curl tar
```

### 10.2 构建机：准备浏览器工具（一次性，约数百 MB 下载）

```bash
cd ~/tilapia-agent/opc-agent-programming
bash scripts/prepare-browser-tools.sh
# 国内加速（可选）：NODE_MIRROR=https://npmmirror.com/mirrors/node bash scripts/prepare-browser-tools.sh
```

脚本会：下载便携 node → `tools/node/`；`npm install --omit=dev` → `tools/node_modules/`；
`npx playwright install chromium` 且 `PLAYWRIGHT_BROWSERS_PATH=0`（浏览器装在包内，随包搬迁）。
结束会打印 tools 总大小。

### 10.3 构建（含浏览器入包）

```bash
bash scripts/build-code-agent.sh 0.5.1        # 默认**要求**浏览器就绪，缺失直接报错
# 本次不要浏览器：WITHOUT_BROWSER=1 bash scripts/build-code-agent.sh 0.5.1
```
> 需要先把 Windows 本机构建的 tilacode 二进制覆盖到 `artifacts/tilacode`（§3 步骤 ③）。
> 产物 tar.gz 会比原来大（tools 含 chromium，通常百 MB 级）。

### 10.4 节点安装（安装脚本自动处理）

`install-agent-code-linux.sh` 解压后若发现 `tools/node`，会尝试 `apt-get install` chromium 的
系统库（libnss3/libgbm/libasound2 等）；**失败只告警不阻断**（节点照常运行，仅前端实测能力缺失）。

systemd 已注入（`systemd/opc-agent-code.service`）：

```ini
Environment=TILAPIA_TOOLS_DIR=/opt/opc-agent-code/tools
Environment=PLAYWRIGHT_BROWSERS_PATH=0
Environment=PATH=/opt/opc-agent-code/tools/node/bin:...
```

### 10.5 节点侧验证

```bash
# 1) 打包内容
ls /opt/opc-agent-code/tools/node/bin/node /opt/opc-agent-code/tools/frontend_probe.mjs
# 2) 直接跑一次探针（需要先有可访问的页面）
node /opt/opc-agent-code/tools/frontend_probe.mjs --url http://127.0.0.1:8000/ ; echo "exit=$?"
#    期望 exit=0（无控制台错误/失败请求）；exit=3 表示依赖缺失，按 10.1~10.4 排查
```

---

## 11. 本次改动（ADR-061）的验收

逐项验收命令（权限两条路径 / 验收闸与阻断决策 / 预算闸 / 洞察告警 / 中枢待办 / 经验复用 / M7 降级 / 回归）见
[ADR-061 验收操作单（Runbook）](./adr061-acceptance-runbook.md)——照「复制命令 → 对照通过标准」执行，结果记录表回填 Harness。

---

## 12. 产物预检门禁（ADR-061 C2，2026-10-01）

### 12.1 为什么需要它

`packages/opencode/script/build.ts` 的冒烟测试只在**目标平台 == 宿主平台**时才执行：

```ts
if (item.os === process.platform && item.arch === process.arch && !item.abi) { ... }
```

本项目的构建拓扑是「Windows 交叉编译 Linux 产物 → scp 到 Linux 打包」，因此 **Linux 产物从未被真正运行验证过**。2026-10-01 的故障正是这样出的包：产物能加载配置（`debug config` 正常）但**模型目录（catalog）装配为空**，节点上任务全部卡到 `task_startup_stalled` 后只报一句 `ModelUnavailableError`（根因与修复见 Runbook §15）。

### 12.2 门禁做什么

`scripts/build-code-agent.sh` 在 **Nuitka 编译之前**用刚拿到的产物 + 探针工作区跑：

```bash
bash scripts/build-code-agent.sh <版本>                    # 门禁默认开启
bash scripts/build-code-agent.sh <版本> --skip-precheck    # 应急跳过（打印 WARN）
```

| 断言 | 失败信息关键词 |
|---|---|
| 产物可执行、配置可加载（含 `permission_mode`） | `产物无法执行或配置加载失败` |
| 配置注入的探针 provider 被接受 | `产物不接受探针 provider 配置` |
| `debug v2` 的模型目录收录探针 provider | `模型目录未收录探针 provider` |
| `artifacts/opencode` 不比 `artifacts/tilacode` 更新 | `脚本只读取后者` |

探针使用独立 `XDG_*` 目录（不污染构建机 `$HOME`），缓存是冷的——更接近节点首次运行。

### 12.3 构建命令（不要加 `--skip-install`）

```bash
cd opc-agent-programming/tilacode/packages/opencode
bun run script/build.ts --target linux-x64     # ⚠️ --skip-install 会跳过三个原生包的
                                              #    --os="*" --cpu="*" 安装，交叉编译不可用
scp dist/tilacode-linux-x64/bin/opencode <server>:~/opc-agent-programming/artifacts/tilacode
```

上传后的文件名必须是 `artifacts/tilacode`（不是 `opencode`）——门禁第 4 条断言专拦这种错配。

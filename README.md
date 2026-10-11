# tilapia-agent

**tilapia-agent** 是一套「中枢调度 + Agent 节点」的 AI 任务自动化系统：在 Windows 桌面中枢里用自然语言下达任务，由 AI 编排器拆解并派发给局域网或云上的 Linux Agent 节点执行，全过程可在 Web 控制台实时观察进度、日志与产物。

**核心组成**

- **中枢 TilapiaHub**（Windows 桌面版）：Web 控制台 + AI 编排器 + 任务/节点/模型管理，内嵌 PostgreSQL 开箱即用；
- **编程 Agent 节点**（Linux）：Nuitka 编译、零 Python 依赖，执行引擎 `tilacode` 基于 [opencode](https://github.com/anomalyco/opencode) 二次开发（MIT）；
- **办公 Agent 节点**（Linux）：文档 / 表格 / 演示文稿生成工具链。（当前版本不可用）

## 本项目全程由 Agent 开发

本产品的**全部代码均由 AI Agent 自主开发**（人类仅提出需求与做最终验收）。仓库内保留了完整的 AI 开发过程资料：

- **AI 开发规则**：[`.trae/rules/`](./.trae/rules/)（决策文档先行、开发规范先加载、端到端验证循环）；
- **架构决策记录**：[docs/adr/](./docs/adr/) 60+ 篇 ADR，每项重大变更的背景、备选方案与决策原因；
- **开发规范**：[docs/standards/](./docs/standards/) 前后端/数据库/API/日志等 9 份工程规范；
- **决策文档体系**：Spec / Harness（测试清单）/ Changes（变更明细）随每次决策沉淀。

适合用于**学习全 Agent 开发的完整过程**，了解 AI 如何工程化地构建一个多模块项目。

本项目仍有诸多不足，将持续优化迭代。如果你在 **AI 开发规则与规范**或**产品功能**方面有更好的想法，欢迎向作者提建议：邮箱：480170979@qq.com / 微信：Tilapia-agent 联系（见文末「商业授权」），或直接[提交 Issue](../../issues)。

> **本目录用途**：存放 tilapia-agent 的**稳定版源码**。它采用 **PolyForm Noncommercial License 1.0.0**，与主仓库（托管的 Gitee 全量源码）**分开托管**，在本目录内单独关联并推送到 GitHub。
>
> **在本目录内 `git init` 后推送 GitHub；主仓库 `.gitignore` 已忽略本目录，Gitee 不会包含它。**

---

## ⚠️ 重要声明（请先读这一节）

1. **产品不成熟，不得用于生产环境**。本项目处于开发阶段，功能与接口仍在变化，按"现状（AS IS）"提供，不附带任何明示或默示担保。
2. **非商业许可**。本目录下的**项目作者原创部分**采用 PolyForm Noncommercial 1.0.0，**禁止任何商业用途**（详见下方清单）。商业使用需**另行取得商业授权**。
3. **第三方部分是分层的**。本目录包含或依赖第三方及其派生代码，**它们仍适用各自的上游许可**（尤其见下方「基于 opencode 的二次开发」）。本目录的非商业限制**只覆盖项目作者原创的部分**，不能也不打算覆盖第三方部分。

---

## 环境要求（按角色，装你需要的）

### 普通用户（安装包部署）

- **Windows 10/11** 一台（跑中枢桌面版，自带内嵌数据库，无需装任何数据库）
- **Linux** 一台（跑 Agent 节点，需 systemd；轻量 VPS 即可）
- 无需 Python / Node.js / 数据库——安装包自带一切

### 开发者（源码运行与构建）

| 依赖 | 版本 | 用途 |
|---|---|---|
| Python | **≥ 3.11** | 中枢后端与节点代理层 |
| PostgreSQL | **16** | 中枢数据库（桌面版内嵌免装） |
| Node.js | **≥ 18** | Web 控制台构建与开发 |
| Bun | 最新 | 编译 tilacode 二进制（支持 Windows 交叉编译 Linux 产物）——`npm install -g bun` |
| Linux 构建机 | — | 编译节点代理层（Nuitka **不支持交叉编译**，必须在 Linux 出 Linux 二进制）；VPS / WSL2 / Docker 均可，机内装 Python 3.11 + gcc + patchelf（Nuitka 本体由构建脚本自动安装） |

> Redis 不需要（配置项仅占位，代码零调用）。

## 🚀快速开始（三步用起来）

本项目由 **中枢**（Windows 桌面版，含 Web 控制台与内嵌数据库）与 **Agent 节点**（Linux，编程/办公两类）组成。

1. **安装中枢**：双击 `TilapiaHub-Setup-x64.exe`，按向导安装并启动——内嵌 PostgreSQL 全自动初始化，无需安装任何数据库；
2. **配置模型**：控制台「系统设置 → 模型管理」添加 LLM Provider（如 DeepSeek，填 API Key）；
3. **安装节点**：在目标 Linux 机器上执行（地址与 Token 在控制台获取）：

   ```bash
   curl -fsSL http://<hub>:8000/static/install-scripts/code/install-agent-code-linux.sh \
     | sudo bash -s -- --hub-url http://<hub>:8000 --token <token>
   ```

完成后即可在控制台「新建任务」派发任务并观察执行进度。**完整说明（含源码运行与构建）见
[部署总览 docs/ops/DEPLOYMENT.md](./docs/ops/DEPLOYMENT.md)。**

## 📖 文档地图

| 你想做什么 | 去哪 |
|---|---|
| 用安装包跑起来（推荐） | [docs/ops/DEPLOYMENT.md · 路线 A](./docs/ops/DEPLOYMENT.md) |
| 从源码运行中枢 / 前端 / 节点 | [docs/ops/DEPLOYMENT.md · 路线 B](./docs/ops/DEPLOYMENT.md)、[opc-central-dispatch-hub/README.md](./opc-central-dispatch-hub/README.md) |
| 构建中枢桌面安装包 | [docs/ops/hub-desktop-build-guide.md](./docs/ops/hub-desktop-build-guide.md) |
| 构建节点整体包（Linux tarball） | [docs/ops/code-agent-build-guide.md](./docs/ops/code-agent-build-guide.md) |
| 办公节点开发与部署 | [opc-agent-office/docs/local-dev-guide.md](./opc-agent-office/docs/local-dev-guide.md) |
| 架构决策与设计文档 | [docs/INDEX.md](./docs/INDEX.md) |

## 允许（免费、无需授权）

- 下载源码；
- 在本地**修改**源码；
- 在本地**运行**（个人学习、研究、试验、评估）；
- **fork** 本仓库，并**原样免费分发**（不得收费、不得商用）；
- 用于非营利组织、教学、学术研究等**非商业目的**。

## 禁止（需另行取得商业授权）

- 任何**商业用途**：在公司或组织业务中使用、用于生产环境、对外提供服务或产品；
- 将本软件（或其修改版）**转售、收费分发**，或作为 SaaS / 托管服务对外提供；
- 移除或修改 `LICENSE`、版权声明与出处说明；
- 以本项目名义暗示作者为你的修改版本背书；
- 使用项目名称与 Logo 作为你自己的商标（本许可**不授予任何商标权**）。

## 必须保留的信息

- 完整保留 [`LICENSE`](./LICENSE) 全文；
- 保留版权与出处声明；**修改版本须注明"基于 tilapia-agent 修改"**；
- 分发时（无论源码还是二进制）一并提供本许可与第三方声明（[`THIRD-PARTY-NOTICES.md`](./THIRD-PARTY-NOTICES.md)）。

---

## 基于 opencode 的二次开发（必须保留的上游许可）

本项目的**编程节点执行引擎 `tilacode`** 是 **[opencode](https://github.com/anomalyco/opencode)** 的**二次开发分支（fork + 修改）**，不是原创实现。因此：

- 上游许可：**MIT License, Copyright (c) 2025 opencode**（原文见 [`third-party/opencode/LICENSE`](./third-party/opencode/LICENSE)）；
- 上游声明：[`third-party/opencode/NOTICE`](./third-party/opencode/NOTICE)（原样保留）；
- **上述 MIT 许可授予的权利不可撤销**：任何人仍可依 MIT 使用、修改、分发（含商用）**`tilacode` 中源自 opencode 的部分**；本目录的非商业限制**不适用于该部分**。
- 我们的改动同样以 MIT 分发该部分，并保留 opencode 的版权与 NOTICE 声明。详见 [`THIRD-PARTY-NOTICES.md`](./THIRD-PARTY-NOTICES.md)。

---

## 商业授权

如需将本项目用于**商业或生产环境**（含公司/组织内部业务使用、对外提供服务或产品化），请与作者取得联系：

- 联系方式：邮箱 `480170979@qq.com` ｜ 微信 `Tilapia-agent`

## 贡献

提交贡献前请阅读 [`CONTRIBUTING.md`](./CONTRIBUTING.md)：贡献即表示同意你的贡献可按本项目许可分发（作者保留更换许可的权利）。

## 许可文件索引

| 文件 | 说明 |
|---|---|
| [`LICENSE`](./LICENSE) | PolyForm Noncommercial License 1.0.0（项目作者原创部分） |
| [`THIRD-PARTY-NOTICES.md`](./THIRD-PARTY-NOTICES.md) | 第三方组件与其许可（分层说明） |
| [`third-party/opencode/LICENSE`](./third-party/opencode/LICENSE)、[`third-party/opencode/NOTICE`](./third-party/opencode/NOTICE) | 上游 opencode 的 MIT 许可与声明（原样保留） |
| [`CONTRIBUTING.md`](./CONTRIBUTING.md) | 贡献条款 |

---

Copyright (c) 2026 **Tilapia-agent** ｜ 保留所有权利（依 PolyForm Noncommercial 1.0.0 授权非商业使用）

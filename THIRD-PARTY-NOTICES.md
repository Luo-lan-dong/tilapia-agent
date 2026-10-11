# 第三方组件与许可声明（THIRD-PARTY NOTICES）

本文件说明本目录中**第三方组件、派生代码与其上游许可**。

> **分层许可原则**：本目录中的**项目作者原创部分**适用 [`LICENSE`](./LICENSE)（PolyForm Noncommercial License 1.0.0，**禁止商业用途**）。
> 但**第三方及派生部分仍适用其各自的上游许可**，本目录的非商业限制**只覆盖项目作者原创部分**，不能、也不打算覆盖第三方部分。
>
> **MIT / BSD / Apache-2.0 等宽松许可授予的权利不可撤销。** 任何已依这些许可获得授权的人，仍可继续按其条款使用、修改、分发（**含商业用途**）相应的第三方或派生部分；本目录的非商业限制**不能覆盖这些第三方部分**。

本文件所有结论均来自**实际读取仓库内文件**或**本地已安装包的元数据**。凡未能实际读到许可依据的条目，一律标注 **「待核实」**，不作推测。

---

## 1. `tilacode` —— 基于 opencode 二次开发（**最重要的第三方归属**）

**本项目（tilapia-agent）的编程节点执行引擎 `tilacode` 是基于 [opencode](https://github.com/anomalyco/opencode) 二次开发（fork + 修改）而来，不是原创实现。**

| 项目 | 内容 |
|---|---|
| 本地位置 | `opc-agent-programming/tilacode/` |
| 上游项目 | opencode |
| 上游仓库 | <https://github.com/anomalyco/opencode> |
| 上游许可 | **MIT License, Copyright (c) 2025 opencode** |
| 上游 LICENSE | 原样保留于 [`third-party/opencode/LICENSE`](./third-party/opencode/LICENSE) |
| 上游 NOTICE | 原样保留于 [`third-party/opencode/NOTICE`](./third-party/opencode/NOTICE) |

### 1.1 实际读取到的证据

- `opc-agent-programming/tilacode/LICENSE`（**1065 字节，21 行**）实际内容：
  - 第 1 行：`MIT License`
  - 第 3 行：`Copyright (c) 2025 opencode`
  - 正文为标准 MIT 许可全文（含 "Permission is hereby granted, free of charge…" 与 "THE SOFTWARE IS PROVIDED \"AS IS\"…" 段落）。
- `opc-agent-programming/tilacode/package.json`：
  - 第 124 行：`"license": "MIT"`
  - 第 120–123 行：`"repository": { "type": "git", "url": "https://github.com/anomalyco/opencode" }`
- `opc-agent-programming/tilacode/NOTICE`（**918 字节，27 行**）实际内容（摘要，原文见该文件）：
  - 「本产品基于 opencode 项目修改而来。」
  - 原始项目信息：项目名称 `opencode`；原始仓库 `https://github.com/anomalyco/opencode`；版权所有者 `Copyright (c) 2025 opencode`；开源协议 `MIT License`。
  - 修改说明：项目/目录重命名 `opencode → tilacode`；命令行工具重命名 `opencode → tilacode`；配置目录重命名 `.opencode/ → .tilacode/`；环境变量前缀重命名 `OPENCODE_ → TILACODE_`。
  - MIT 协议合规声明：版权声明与许可声明应包含在本软件的所有副本或实质性部分中。

上述 `LICENSE` 与 `NOTICE` 两份文件已**逐字复制**到本目录，未改动任何一个字（复制后 SHA-256 与上游文件一致）。

### 1.2 权利说明（MIT 不可撤销）

- **上游 opencode 部分继续适用 MIT**：任何人仍可依 MIT 使用、修改、分发（**含商业用途**）`tilacode` 中**源自 opencode 的部分**。
- **本目录的非商业限制不覆盖该部分**：PolyForm Noncommercial 1.0.0 只约束项目作者原创部分，不改变、不缩减 MIT 已授予的权利。
- **我们对 opencode 部分的改动同样以 MIT 分发**该部分，并保留 opencode 的版权声明与 NOTICE。
- MIT 要求：分发时须保留上述版权声明与许可声明。

### 1.3 `tilacode` 自身的第三方依赖

依赖清单实际读取自 `opc-agent-programming/tilacode/package.json`（`workspaces.catalog` 与 `dependencies`）。各包 `license` 字段实际读取自本地 bun 缓存 `opc-agent-programming/tilacode/node_modules/.bun/`：

| 包 | package.json 目录声明版本 | license（实际读取） |
|---|---|---|
| `effect` | 4.0.0-beta.83 | MIT |
| `hono` | 4.10.7 | MIT |
| `zod` | 4.1.8 | MIT |
| `ai` | 6.0.168 | Apache-2.0 |
| `solid-js` | 1.9.10 | MIT |
| `drizzle-orm` | 1.0.0-rc.2 | Apache-2.0 |
| `marked` | 18.0.7 | MIT |
| `shiki` | 4.2.0 | MIT |
| `tailwindcss` | 4.1.11 | MIT |
| `vite` | 7.1.4 | MIT |
| `remeda` | 2.26.0 | MIT |
| `luxon` | 3.6.1 | MIT |
| `diff` | 8.0.2 | BSD-3-Clause |
| `dompurify` | 3.3.1 | (MPL-2.0 OR Apache-2.0) |
| `@opentui/core` | 0.4.5 | MIT |
| `electron` | （`trustedDependencies`） | MIT |
| `typescript` | 5.8.2 | Apache-2.0 |
| `oxlint` | 1.60.0 | MIT |
| `semver` | 7.7.4 | ISC |

> **说明与保留**：本地 bun 缓存内含大量版本（约 2600 余条目，含传递依赖），部分包存在多个缓存版本（例如缓存中同时存在 `marked` 17.0.1 与目录声明的 18.0.7）。上表 license 字段按**包名**读取，具体解析版本的对应关系**建议以 `bun.lock` / 实际安装为准**。
> `tilacode` 的完整依赖树规模很大（`package.json` 中另有 `patchedDependencies` 对 15 个包打了补丁），本表只列主要项；**未逐项核对的传递依赖，其许可待核实**。

---

## 2. `opc-web-console`（前端控制台）

依赖声明实际读取自 `opc-web-console/package.json`；`license` 字段实际读取自**本地已安装**的 `opc-web-console/node_modules/<包>/package.json`。

### 2.1 运行时依赖（`dependencies`）

| 包 | 声明版本 | 实际安装版本 | license（实际读取） |
|---|---|---|---|
| `antd` | ^5.29.3 | 5.29.3 | MIT |
| `react` | ^18.3.1 | 18.3.1 | MIT |
| `react-dom` | ^18.3.1 | 18.3.1 | MIT |
| `react-router-dom` | ^7.18.2 | 7.18.2 | MIT |
| `axios` | ^1.19.0 | 1.19.0 | MIT |
| `@xterm/xterm` | ^6.0.0 | 6.0.0 | MIT |
| `@xterm/addon-fit` | ^0.11.0 | 0.11.0 | MIT |
| `bun` | ^1.3.14 | 1.3.14 | MIT |

### 2.2 开发/构建依赖（`devDependencies`，主要项）

| 包 | 实际安装版本 | license（实际读取） |
|---|---|---|
| `vite` | 8.2.0 | MIT |
| `vitest` | 4.1.10 | MIT |
| `typescript` | 6.0.3 | Apache-2.0 |
| `eslint` | 10.8.0 | MIT |
| `msw` | 2.15.0 | MIT |
| `jsdom` | 24.1.3 | MIT |
| `@playwright/test` | 1.62.1 | Apache-2.0 |
| `@vitejs/plugin-react` | 6.0.5 | MIT |
| `@testing-library/react` | 16.3.2 | MIT |
| `@testing-library/jest-dom` | 6.9.1 | MIT |
| `@types/node` | 24.13.3 | MIT |

以下仅在 `package.json` 中声明、本次**未读取到已安装元数据**，许可**待核实**：`@eslint/js`、`@testing-library/user-event`、`@types/react`、`@types/react-dom`、`eslint-plugin-react-hooks`、`eslint-plugin-react-refresh`、`globals`、`typescript-eslint`。

---

## 3. `opc-agent-office`（Python 办公节点）

依赖声明实际读取自 `opc-agent-office/pyproject.toml`（第 18–38 行）。

> **证据来源说明（重要）**：本次在 `opc-agent-office/` 与 `opc-agent-programming/` 下**未找到**这些包的已安装元数据（`*.dist-info`）。下表中已确认的许可读取自本地另一个节点的虚拟环境 `opc-central-dispatch-hub/.venv/Lib/site-packages/` 内**同名包的 `METADATA`**（与该节点声明的依赖集有交集）。该 venv 属于**另一节点**、版本可能与本节点解析结果不同，**仅作为该包许可字段的证据**。

### 3.1 运行依赖

| 包 | pyproject 声明 | license（实际读取，注明来源字段） |
|---|---|---|
| `httpx` | >=0.27.0 | BSD-3-Clause（`METADATA` 的 `License: BSD-3-Clause`，版本 0.28.1） |
| `websockets` | >=12.0 | BSD-3-Clause（`License-Expression: BSD-3-Clause`，版本 17.0.1） |
| `pydantic-settings` | >=2.0 | MIT（`License-Expression: MIT`，版本 2.14.2） |
| `python-dotenv` | >=1.0.0 | BSD-3-Clause（`License: BSD-3-Clause`，版本 1.2.2） |
| `structlog` | >=24.0 | MIT OR Apache-2.0（`License-Expression: MIT OR Apache-2.0`，版本 26.1.0） |
| `psutil` | >=6.0 | **待核实**（未在任何本地 venv 中读到已安装元数据） |
| `python-docx` | >=1.1.0 | **待核实**（同上） |
| `python-pptx` | >=0.6.23 | **待核实**（同上） |
| `openpyxl` | >=3.1.0 | **待核实**（同上） |
| `matplotlib` | >=3.8 | **待核实**（同上） |
| `scrapling[fetchers]` | >=0.4,<0.5 | **待核实**（同上；其 `fetchers` extra 会引入额外依赖，同样待核实） |

### 3.2 开发依赖（`optional-dependencies.dev`）

| 包 | license（实际读取） |
|---|---|
| `ruff` | MIT（`License-Expression: MIT`，版本 0.16.1） |
| `pytest` | MIT（`License-Expression: MIT`，版本 9.1.1） |
| `pytest-asyncio` | Apache-2.0（`License-Expression: Apache-2.0`，版本 1.4.0） |
| `respx` | BSD-3-Clause（`License: BSD-3-Clause`，版本 0.23.1） |

### 3.3 备注

`opc-agent-office/pyproject.toml` 第 7 行为 `license = "MIT"`，即**该节点自身的声明许可是 MIT**（作者原创部分）。若该节点被纳入本非商业发布目录，需注意其自身 MIT 声明与本目录 PolyForm NC 之间的关系。

---

## 4. `opc-agent-programming` 的 Python 依赖

依赖声明实际读取自 `opc-agent-programming/linux-python/requirements.txt`（全文 3 行）：

```
fastapi
uvicorn[standard]
psutil
```

| 包 | license（实际读取） |
|---|---|
| `fastapi` | MIT（`License-Expression: MIT`，版本 0.141.1） |
| `uvicorn` | BSD-3-Clause（`License-Expression: BSD-3-Clause`，版本 0.52.1） |
| `psutil` | **待核实**（未读到已安装元数据） |

`uvicorn[standard]` 会额外引入（以下为实际读取到的许可，版本为本地 venv 中的版本）：

| 包 | license（实际读取） |
|---|---|
| `httptools` | MIT（`License-Expression: MIT`，版本 0.8.0） |
| `watchfiles` | MIT（`License: MIT`，版本 1.2.0） |
| `websockets` | BSD-3-Clause（`License-Expression: BSD-3-Clause`，版本 17.0.1） |
| `python-dotenv` | BSD-3-Clause（`License: BSD-3-Clause`，版本 1.2.2） |
| `pyyaml` | MIT（`License: MIT`，版本 6.0.3） |
| `uvloop` | **待核实**（本地 venv 中未读到） |

另注：`httptools` 的 wheel 内自带 vendored 许可文件 `licenses/vendor/llhttp/LICENSE` 与 `licenses/vendor/http-parser/LICENSE-MIT`，即其**内嵌了第三方（llhttp / http-parser）代码**，分发时这些许可也需一并保留。

---

## 5. 参考资料：`借鉴/deepseek-harness-master/`

- 位置：`借鉴/deepseek-harness-master/`
- 性质：**参考资料（reference material）**，**未包含在本发布目录内**。
- 许可：**MIT License, Copyright (c) 2026 DeepSeek**
  （实际读取：该目录下 `LICENSE` 文件前 4 行为 `MIT License` / 空行 / `Copyright (c) 2026 DeepSeek` / 空行。）
- **如未来将其纳入本目录分发，必须一并保留其 MIT 许可声明。**

---

## 6. 未核实项汇总（「待核实」清单）

以下条目的许可**本次未能通过实际读取确认**，不作任何推测；正式对外分发前建议逐一核实：

1. Python：`psutil`、`python-docx`、`python-pptx`、`openpyxl`、`matplotlib`、`scrapling`（及其 `fetchers` extra 依赖）、`uvloop`。
2. 前端（仅声明未读元数据）：`@eslint/js`、`@testing-library/user-event`、`@types/react`、`@types/react-dom`、`eslint-plugin-react-hooks`、`eslint-plugin-react-refresh`、`globals`、`typescript-eslint`。
3. `tilacode` 的完整传递依赖树（本文件仅列出主要项；`patchedDependencies` 中被打补丁的 15 个包及其上游许可未逐项核对）。
4. 主仓库中其它未在本文件覆盖的目录与资源（例如图标、图片、字体、文档素材）的版权与许可状态。

> 本文件不构成法律意见。涉及分发、商用或合规判断时，请咨询专业法律人士。

---

## 7. 复核方式（可自行验证）

本文件的关键结论均可复现：

- 上游 opencode 许可与声明：对比 `opc-agent-programming/tilacode/LICENSE` 与 `tilapia-agent-PolyForm-NC/third-party/opencode/LICENSE`（应为逐字一致，SHA-256 相同）；`NOTICE` 同理。
- 前端依赖许可：查看 `opc-web-console/node_modules/<包>/package.json` 的 `license` 字段。
- Python 依赖许可：查看 `opc-central-dispatch-hub/.venv/Lib/site-packages/<包>-*.dist-info/METADATA` 中的 `License`、`License-Expression` 或 `Classifier: License ::` 字段，以及该目录下 `licenses/` 内的许可原文。

# 办公节点验收操作单（Runbook）：ADR-065 交付闭环与能力增强（M1–M7）

> 目的：把 ADR-065 全部模块的**可执行验收步骤**固化成「复制命令 → 对照通过标准」的操作单，
> 新会话按本文即可独立完成办公节点的端到端验收；**只有需要用户介入（构建机/节点访问/决策）时才提问**。
> 关联：[ADR-065](../adr/065-office-delivery-loop.md)｜[Spec](../spec/065-office-delivery-loop-spec.md)｜[Harness](../spec/065-office-delivery-loop-harness.md)｜[Changes](../spec/065-office-delivery-loop-changes.md)
> 循环规则与拓扑：`.trae/rules/e2e-dev-loop.md` 与 [e2e-dev-loop-runbook.md](./e2e-dev-loop-runbook.md)（**唯一权威源，命令不在此重复**）。

## 0. 前置（必须先做，否则验收无效）

| # | 前置项 | 为什么 | 怎么做 |
|---|-------|-------|-------|
| 0.1 | **安装脚本重新发布到中枢** `static/install-scripts/office/` | 安装脚本按 ADR-025 与 agent 类型绑定（`key=install-script-office`、路径 `install-scripts/office/`，见 `opc-agent-office/scripts/install-agent-office-linux.sh` 头部注释）。M1/M6 改过该脚本（依赖探针、stealth 开关提示），但 `opc-agent-office/scripts/` **不在整体包内**——不重新发布，节点永远装到旧脚本 | 中枢资源管理页/接口发布 `opc-agent-office/scripts/install-agent-office-linux.sh` 到 `static/install-scripts/office/`（对齐 code 节点既有发布路径 `static/install-scripts/code/`）；发布后 `curl -fsSL http://<hub>:8000/static/install-scripts/office/install-agent-office-linux.sh \| head -5` 复核是新版 |
| 0.2 | 整体包已构建并发布 | 节点装的是整体包（含 `app/`、`pyproject.toml`、`systemd/`、`.env.example`） | `opc-agent-office` 目录下 `bash scripts/package_office_artifact.sh` → 产物 `dist/agent-office-linux-x64-<version>.tar.gz` → 上传中枢资源管理页发布 |
| 0.3 | 办公节点在线且类型正确 | 派发只会落到 `agent_type=office` 的在线节点 | `GET /api/v1/agent-nodes` 查 `status` 与 `agent_type`；节点侧 `journalctl -u opc-agent-office` 应见注册与 WS 连接成功 |
| 0.4 | 中枢在跑、前端可开 | 观测入口 | 本机 `python -m uvicorn app.main:app --host 0.0.0.0 --port 8000`；健康检查**尾斜杠** `curl http://localhost:8000/api/v1/health/` |

> **与编程节点的关键差异（先读，避免照抄 code 节点流程）**
>
> | 维度 | 编程节点（code） | 办公节点（office） |
> |---|---|---|
> | 运行形态 | Nuitka **编译二进制** + 内置 tilacode | **Python 源码 + venv**（无 tilacode、无 4096 端口） |
> | 发布链路 | 源码 scp 到构建机 → `build-code-agent.sh`（patch+1）→ `a.sh` 上传 → 节点 `deploy-opc-agent.sh` | `package_office_artifact.sh` 打包 → 中枢资源管理页发布 → 节点跑安装脚本（安装脚本会建 venv 并 `pip install`，**幂等 stamp 门控**） |
> | 目标节点 | `192.168.101.13` / `.104`（systemd `opc-agent-code`） | 办公节点（systemd `opc-agent-office`，通知 API 端口 **18081**，与 code 的 18080 错开） |
> | 执行引擎 | tilacode 会话（SSE 事件流） | `tool_loop` LLM function calling（无终端、无 shell 工具） |
> | 交付物 | 代码工程/文件 | docx / pptx / xlsx / md（**原素材不可变**，编辑类一律另存） |
> | 验收闸 | `verify.json` L0–L4（节点内置执行器） | `verify.py` **L0 结构 / L1 内容 / L2 语义**（判据只来自中枢下发的 spec/checklist） |
> | 轨迹类别 | status/reasoning/text/tool/todo/usage | status/reasoning/text/tool **+ usage**（无 todo；用量见 M7） |

## 1. 本地自测（改完代码先跑，验收前必跑）

```powershell
# 节点静态检查 + 全量单测（禁止与同库其他进程并发跑 pytest）
cd D:\bianchen\tilapia-agent\opc-agent-office
python -m ruff check app tests
python -m pytest tests -q            # 基线见 Changes §2；ADR-065 M7 后为 162 passed

# 中枢侧（办公相关用例；禁止在开发库上并发跑）
cd ..\opc-central-dispatch-hub
python -m ruff check app tests
python -m pytest tests -q -k office  # 基线 6 passed

# 文档一致性（Windows 需 PYTHONIOENCODING）
cd ..
$env:PYTHONIOENCODING="utf-8"; python scripts/check_docs.py   # 必须零错误
```

> **测试会改写仓库文件**：`opc-agent-office/tests` 里有用例会写 `opc-agent-office/data/task_workdirs.json`
> （`OfficeBridge` 的工作目录登记簿）。跑完测试请 `git status` 复核该文件，**不要把测试产生的任务记录提交上去**。

## 2. 端到端（构建 → 部署 → 派发真实任务）

流程骨架与命令**全部沿用** [e2e-dev-loop-runbook.md](./e2e-dev-loop-runbook.md)（§3 各阶段命令、§3.5 端口检查、
§4 踩坑清单、§6 API 驱动端到端与症状定位表）。办公节点的差异只有下面四点，其余照抄：

1. **构建**：办公节点**不需要** Nuitka 与 patch+1 版本规则；用 `bash scripts/package_office_artifact.sh`
   出 `dist/agent-office-linux-x64-<version>.tar.gz`（版本号取自 `pyproject.toml`，脚本自动读取）；
   随后在中枢资源管理页发布，并把 §0.1 的安装脚本发布到 `static/install-scripts/office/`。
2. **安装/更新**：在办公节点执行安装脚本（脚本自带幂等：升级整体包、刷新 `.env`、重启服务）：
   `curl -fsSL http://<hub>:8000/static/install-scripts/office/install-agent-office-linux.sh | sudo bash -s -- --hub-url http://<hub>:8000 --token <token>`
   ——**改过依赖清单时**，安装脚本的 venv stamp 门控不会自动重装；M1 起脚本内置 `import` 探针，
   缺包会**显式失败/告警**（`scrapling_not_installed` 等），按探针提示重跑安装或手工 `pip install`。
3. **服务名与端口**：`systemctl status opc-agent-office`；通知 API 是 **18081**（端口检查 `netstat -ano | findstr ":18081"`）。
4. **派发**：必须派到**办公类型节点**（`agent_type=office` 的在线节点）。派发请求构造/轮询/读轨迹的
   Python 骨架见 Runbook §6.2（把目标换成办公节点 ID），或直接在前端「新建任务」页选用办公节点。

**验证三查**（同 Runbook §3.4，服务名换 office）：节点日志两连击（注册 + `websocket_connected`）→
DB 心跳刷新 → 中枢任务详情/轨迹。办公节点日志过滤关键词：
`office_task_completed`、`office_task_verify_result`、`office_tool_executed`、`office_budget_soft_limit`、
`office_budget_hard_stop`、`office_task_verify_blocked`、`agent_trace_report_failed`。

## 3. 逐模块验收（派发真实任务 + 观察点 + 期望）

> 全部判据都可**程序化**获取：任务详情 `GET /api/v1/tasks/{id}`、轨迹 `GET /api/v1/tasks/{id}/agent-trace?after_seq=0&limit=1000`、
> 洞察 `GET /api/v1/tasks/{id}/insight`、产物 `GET /api/v1/tasks/{id}/artifacts`、节点日志 `journalctl -u opc-agent-office`。

| 模块 | 派发什么任务 | 观察点 | 期望（判据） |
|---|---|---|---|
| **M1 素材读取** | 附件 1 份 docx + 1 份 xlsx，要求「按素材要点汇总成一份 docx」 | 轨迹 `tool` 行的 `tool` 字段；产物正文 | ① 出现 `read_docx`/`read_xlsx` 工具行且 `ok=true`；② 汇总结果包含素材中**≥3 个独有事实**（专有名词/数字）且准确；③ 日志无素材解析失败（`素材未能读取`） |
| **M2 表格与编辑另存** | 给既有 docx 与 xlsx，要求「补充第 3 节 / 追加一行汇总」 | 产物清单 + 原文件哈希 | ① 出现 `edit_docx`/`edit_xlsx`；② 产出**新文件**且可被 python-docx/openpyxl 打开，新增内容存在；③ **原文件 SHA256 与任务前完全一致**；④ 产物名与原件不同 |
| **M3 产出增强** | 「带目录/页码/页眉页脚的 docx + 含真实数据图表的 pptx」 | 产物元素 | ① docx 打开可见目录、页码、页眉页脚（或解 XML 见 `fldChar`/header-footer 部件）；② pptx 图表**非占位**且数据点与给定数据一致；③ 图表由 `render_chart` 产出 PNG 并嵌入；④ 演讲者备注存在；⑤ **Word 域需打开后 Ctrl+A→F9 更新**（返回值已提示，属正常） |
| **M4 交付质量闸** | 给一份**故意含缺项**的 checklist（如「必须包含结论章节」但要求产出摘要） | 任务状态 + `verify_report` + 轨迹 | ① 节点识别缺项并回灌（轨迹/进度出现 `testing` 阶段与未通过条目）；② 回灌**只改不达标项**（已达标段落内容不变）；③ `repair_rounds` ≤5；④ 第 5 轮仍不过 → 任务进 `verify_blocked`，前端**既有决策入口**可见「卡在哪一条」；⑤ 24h 兜底可置 failed |
| **M5 轨迹镜像** | 任意真实任务，运行中盯中枢时间线 | `agent-trace` 行 | ① 实时可见 `status`/`reasoning`/`text`/`tool`；② `session_id` 全程稳定（`office_{task_id[:8]}`）；③ seq 从 1 递增无空洞；④ 折叠块默认折叠、展开可回看历史 |
| **M6 检索闭环** | 「查资料并给带引用的结论」 | 产物引用 + 节点日志 | ① 每条引用能对应到正文**具体段落**（`map_citations` 映射成立）；② 探针通过时日志**不再出现** `scrapling_not_installed`；③ 检索解析走 Scrapling（代码层面可查）；④ 抓取遵守 robots.txt 且有速率限制 |
| **M7 成本护栏** | 构造会超预算的任务：把节点 `.env` 的 `OFFICE_LLM_TOKEN_BUDGET` 调低（下界 10000）或派发超长任务 | 轨迹 `status`/`usage` 行 + 节点日志 + 任务结局 | ① 达软阈值（默认预算 80%）出现 `office_budget_soft_limit` warning + 一条 `status` 行（`stage=budget_soft_limit`、含 `budget_percent`），**任务继续**；② 达 100% 出现 `office_budget_hard_stop` warning + `status` 行（`stage=budget_hard_stop`），**停止调用 LLM**；③ 任务**不报 completed**，走 `on_failed` 且 reason 含 `budget_hard_exceeded` 与当前用量；④ 任务结束有 1 条 `kind=usage` 轨迹行，含 `prompt_tokens`/`completion_tokens`/`rounds`/`budget_percent` |

### 3.1 横向约束抽查（V1–V5）

| # | 抽查项 | 判据 |
|---|-------|------|
| V1 | 判据来源 | 验收条目逐条可回溯到中枢派发的 `specification`（无节点自造判据）——对比 `verify_report` 的条目与派发 spec |
| V2 | 主观项不硬判 | 「排版美观/语气」类条目在结果中标 `needs_human`，不计入硬闸失败 |
| V3 | 无假成功 | 预算硬停、素材全失败、`llm_unavailable` 等场景**绝不呈现为 completed**（看任务状态与 reason） |
| V4 | 降级可观测 | D1–D7 任一降级触发时，日志/轨迹存在对应 reason（`search_unavailable`/`material_parse_failed`/`chart_render_failed`/`budget_*`/`scrapling_not_installed`…），**不存在静默降级** |
| V5 | 原文件不可变 | 所有 `edit_*` 任务后原文件校验和不变 |

## 4. 已知边界清单（验收时不要误判为缺陷）

| # | 边界 | 说明与影响 |
|---|------|-----------|
| B1 | **`session_id` 只能合成** `office_{task_id[:8]}` | 中枢派发 payload **不含任何会话标识字段**，office 节点没有 tilacode 会话；中枢注释明确 payload 的 `session_id` 不写入 `tasks.session_id`。该值由 `office_session_id()` 统一产出、**任务内稳定不变**——验收看「同任务内是否跳变」，不要与 code 节点的真实会话 ID 比对 |
| B2 | **无 `idle` 阶段** | 办公节点起止轨迹行复用既有 stage 枚举（起始 `preparing`、结束 `summarizing`），**未扩展 stage 枚举**。时间线上看不到 `idle` 属正常 |
| B3 | **轨迹行不进 WS 离线缓冲** | `_BUFFERED_EVENTS` 只服务 WS 事件（含 `agent.task_verify_blocked`）；轨迹走 HTTP 幂等旁路（`(task_id, seq)`），掉线期间的行**不回队补发**——中枢按 seq 幂等，未来重发安全，但当前不做离线补报 |
| B4 | **stealth 默认关** | `OFFICE_ENABLE_STEALTH=false` 为默认；SPA/强反爬站点需渲染时置 `true`（要求已安装 Patchright 浏览器）。默认关时 `web_fetch` 走 httpx/Scrapling 常规通道 |
| B5 | **URL HEAD 探测默认关** | L1 的「引用链接可访问」判据需 checklist 显式要求（`check_url_status`）+ 显式开启探测；默认不探测，故「引用可达」默认不判 |
| B6 | **Word 域需 Ctrl+A→F9** | docx 目录/页码/页眉页脚是**域（field）**，Word 打开时需 Ctrl+A→F9 更新才显示页码；节点返回值已提示，**不是产出失败** |
| B7 | **测试会改写 `data/task_workdirs.json`** | 跑 `pytest` 会写 `opc-agent-office/data/task_workdirs.json`（任务→工作目录登记簿）。该文件是**运行时数据**，跑完测试复核 `git status`，不要提交测试残留 |
| B8 | 预算阈值只来自节点配置 | office 派发 payload 无 `budget` 字段，软/硬阈值均取自节点 `.env`（`OFFICE_LLM_TOKEN_BUDGET` + `OFFICE_LLM_BUDGET_SOFT_RATIO`）；验收前确认目标节点的 `.env` 值 |
| B9 | 100% 硬停的终态语义 | Spec D5 描述为「进入阻断态」，本次实现按用户指示走**失败上报**（`on_failed` + reason `budget_hard_exceeded`，并停发 `agent.task_budget_stop` 事件），**比阻断更强**：任务不会停在 `verify_blocked` 等人工决策，而是直接 failed。若要「阻断待决策」语义需另立变更 |
| B10 | 安装脚本需重新发布 | 见 §0.1：`scripts/` 不在整体包内，脚本改动必须单独发布到中枢 `static/install-scripts/office/`，否则节点用旧脚本（依赖探针/stealth 提示都到不了） |

## 5. 验收结果记录表（执行后回填）

| 模块 | 验收动作（本次实际派发的任务 ID） | 结果 | 证据（轨迹 seq / 日志行 / 哈希） |
|---|---|---|---|
| M1 素材读取 | | ☐ 通过 ☐ 不通过 | |
| M2 表格与编辑另存 | | ☐ 通过 ☐ 不通过 | |
| M3 产出增强 | | ☐ 通过 ☐ 不通过 | |
| M4 交付质量闸 | | ☐ 通过 ☐ 不通过 | |
| M5 轨迹镜像 | | ☐ 通过 ☐ 不通过 | |
| M6 检索闭环 | | ☐ 通过 ☐ 不通过 | |
| M7 成本护栏 | | ☐ 通过 ☐ 不通过 | |
| V1–V5 横向 | | ☐ 通过 ☐ 不通过 | |

## 6. 收尾复位

1. 若验收中为测试调低过 `OFFICE_LLM_TOKEN_BUDGET` / 打开过 `OFFICE_ENABLE_STEALTH`，**改回 `.env` 默认值**并重启服务；
2. 清理验收用的临时任务与产物（按需，注意 B7：不要提交测试残留）；
3. 把实测结论回填 [Harness §2.1 用例结果回填](../spec/065-office-delivery-loop-harness.md) 与本表，
   并在 [Changes §3 模块进度记录](../spec/065-office-delivery-loop-changes.md) 登记；
4. 未通过项按 [e2e-dev-loop-runbook.md §6.3](./e2e-dev-loop-runbook.md) 症状决策树分层定位（中枢 REST → 节点日志 → 交付物）。

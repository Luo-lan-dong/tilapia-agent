# ADR-062：办公 Agent 节点（opc-agent-office）

## 状态

Accepted

## 日期

2026-09-28

## 问题背景

当前系统中枢只接入了一种 Agent 节点类型（`code` 编程代理，基于 tilacode 执行引擎）。用户需要一类「办公子代理」：能写文档（docx）、做 PPT（pptx）、查资料（联网搜索 + 网页抓取 + 提炼汇总）。编程代理的执行引擎 tilacode 面向代码工程，不适合办公任务形态；中枢技能框架（ADR-033）虽然可以加技能，但那是中枢本机执行，不符合「一节点一工种」（agent-node-standards §1）的横向扩展架构。

用户已确认的决策输入（2026-09-28 讨论）：

1. 执行形态：**独立 office 节点**（非中枢技能包）；
2. 执行引擎：**自研轻量工具循环**（非 tilacode）；
3. 第三方接入协议正式化：**暂缓**（本次只做 office 类型，协议沿用现状）；
4. 联网搜索：**Scrapling 做抓取解析** + 免 Key 检索入口起步；
5. 第一期能力：文档 + PPT + 查资料三项全做；
6. 部署平台：**Linux 先行**（与现有编程节点一致）；
7. LLM 凭据：**中枢下发**（ADR-029 同通道，节点端零配置）。

## 决策（Decision）

### 1. 新节点类型 `office`

- 新建 `opc-agent-office` 项目，骨架复用编程节点成熟部分：`hub_client.py`（注册/心跳/WS 重连/端点轮换）、`models/`、`utils/` 基本照搬；`executor/` 替换为办公执行器。
- `agent_type = 'office'`；注册、心跳、任务下发、进度上报、结果回传全部沿用既有协议（agent-node-standards §4，ADR-055 进度语义、ADR-059 准入自评口径照常生效）。
- 云资源 key 按既有命名规范：`agent-office-linux-x64`（ADR-022 规范），安装脚本 `install-agent-office-linux.sh`。

### 2. 执行引擎：自研工具循环

节点端实现 LLM 函数调用循环：系统提示词（办公任务专家）→ 任务输入 → LLM 产生工具调用 → 执行工具 → 结果回填 → 循环直到产出最终交付物（轮次上限 + token 预算保护）。

- **凭据来源**：消费 `hub.task_dispatch` payload 中的 `executor_model` 条目（ADR-029，中枢随 WS 内存下发 `{source, model, provider?, api_key?, base_url?}`），以 OpenAI 兼容协议直连 `base_url`；凭据缺失时任务失败并上报明确原因（不落库不落日志）。
- **进度映射**：复用既有 stage 封闭枚举，不扩展——`planning`（规划大纲）/ `searching`（查资料）/ `coding`（编写文档或 PPT）/ `summarizing`（汇总提炼）/ `idle`。

### 3. 工具集（第一期四件套）

| 工具 | 实现 | 产物 |
| --- | --- | --- |
| write_docx | python-docx，标题/段落/表格/图片 | `.docx` |
| write_pptx | python-pptx，版式/文本/图表占位 | `.pptx` |
| web_search | 检索入口可插拔：第一期 DuckDuckGo HTML 结果页（免 Key，Scrapling Fetcher 抓取解析）；接口预留商用 API（博查/Tavily）与自建 SearXNG 的替换点 | URL+标题+摘要列表 |
| web_fetch | Scrapling：`Fetcher`（curl_cffi）为主，被反爬拦截时升级 `StealthyFetcher`；正文转 Markdown；遵守 robots.txt | 页面正文 Markdown |

- Markdown 为免费附带产物（查资料中间稿、文档源稿均可落 `.md`）。
- **Scrapling 选型理由**（用户提问确认）：Scrapling 是抓取框架而非搜索引擎，覆盖「URL → 正文」段；「关键词 → URL」段由检索入口承担，两者组合成完整查资料链路，且搜索来源可替换。

### 4. 中枢侧配套改动（最小集）

| 位置 | 改动 |
| --- | --- |
| `app/agents/planning.py` | `VALID_AGENT_TYPES` 加 `office`；规划提示词补充 office 能力描述与判据（写文档/做PPT/查资料类任务 → office） |
| `app/services/agent_node_management_service.py` | `SUPPORTED_INSTALL_SCRIPTS` 加 `("office", "linux")`；模板目录 `static/agent-templates/office/` |
| 节点管理/仪表盘展示 | agent_type=`office` 的中文名（「办公代理」）与图标文案 |
| 调度器 | **零改动**（按 `task.agent_type` 匹配节点，机制现成） |

### 5. 产物与交付

沿用 ADR-028 工作目录绑定 + ADR-059 执行中增量产物同步：产物写任务 `output_dir`，`agent.task_result` 回传交付物清单（docx/pptx/md 文件名+路径），前端交付物聚合页自动可见。

## 备选方案（Considered Options）

| 方案 | 结论 | 原因 |
| --- | --- | --- |
| 中枢技能包（文档/PPT/搜索做成中枢技能） | ❌ 否决 | 占用中枢资源、无法多机横向扩展、「子代理」形态不存在；与「一节点一工种」冲突 |
| 复用 tilacode 引擎改提示词 | ❌ 否决 | tilacode 是代码编辑底座，办公任务别扭；绑定其配置体系（provider 毒键等历史坑）；工具集不对口 |
| 商用搜索 API（博查/Tavily）起步 | ⏸️ 延后 | 需 Key 与按次计费；第一期免 Key 起步，检索入口已抽象可随时替换 |
| 自建 SearXNG | ⏸️ 延后 | 桌面一体化部署（ADR-021）多一个常驻服务偏重；作为检索入口的后续可选项 |

## 影响

### 对系统架构的影响

- 节点类型从 1 种扩展为 2 种，验证「多类型 Agent」扩展路径（后续 design/copywriting 复用本次铺的骨架与发布流程）。
- 协议本身零变更：本次不动事件 schema，不做 protocol_version（用户决策：暂缓）。

### 对开发/运维的影响

- 新增节点工程 `opc-agent-office/`（含安装脚本、模板发布、systemd）；编程节点不受影响。
- Scrapling 依赖在安装脚本中安装（`pip install "scrapling[fetchers]"` + `scrapling install` 浏览器组件仅 StealthyFetcher 需要，第一期可选装）。
- LLM 调用消耗走任务级 executor_model（用户设置页的模型与 Key），无新增配置面。

### 对安全的影响

- 联网抓取遵守 robots.txt + 限速；任务凭据仅内存使用不落盘；产物落工作目录，与编程节点同等隔离。

## 关联文档

- [ADR-029 任务执行模型选择与凭据中枢下发](./029-task-executor-model-dispatch.md)
- [ADR-022 Agent 资源云分发](./022-agent-resource-cloud-distribution.md)（key 命名规范）
- [ADR-028 任务工作目录绑定与产物同步](./028-task-workspace-binding-and-artifact-sync.md)
- [ADR-032/033 并发调度与智能调度](./032-concurrent-task-scheduling.md)、[./033-ai-skills-and-smart-dispatch.md]
- [ADR-055 编程执行层缺陷全量修复与进度可视化](./055-execution-layer-hardening.md)、[ADR-059 调度准入与产物韧性](./059-scheduling-resilience-and-artifact-sync.md)
- [agent-node-standards.md](../standards/agent-node-standards.md)

## 变更记录

| 日期 | 变更内容 | 变更人 |
|------|---------|--------|
| 2026-09-28 | 初始创建：独立 office 节点 + 自研工具循环（docx/pptx/DDG 检索/Scrapling 抓取）+ 凭据中枢下发 + Linux 先行 | AI助手 |

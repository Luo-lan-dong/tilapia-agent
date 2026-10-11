# ADR-048：开发规则文档库与可选规则包下发

- **状态**：Accepted
- **日期**：2026-09-25
- **关联**：ADR-033（技能框架）、ADR-028/ADR-034（一次性 token 下载先例）、ADR-040（Orchestrator 工具循环）、ADR-037（三件套版本化）

## 1. 问题背景

1. 子 Agent 节点执行任务时没有任何"开发规范"约束通道：中枢下发的只有三件套 specification，规范遵守仅靠 prompt 一句提示，无强制层。
2. 中枢与节点的职责边界模糊：节点在执行中自行做技术选型，中枢选型时又不知道节点环境。
3. 用户对工程化程度的需求是**分层的**：简单任务（改个页面、修个小 bug）不需要完整工程化；复杂任务需要节点遵守开发规范、留痕决策文档（即 `.trae/rules/coding-standards.md` + `decision-documents.md` 两份规则的节点版）。
4. 用户需要在编排对话中自主决定工程化程度（含利弊说明：规范化/文档齐全 vs 决策文档耗 token），选择结果随任务下发到节点执行。

## 2. 决策（Decision）

### 2.1 中枢规则文档库（规则正文固化在本地中枢）

- 新增 `standard_docs` 表：`{id, name, category, description, apply_when, content(TEXT), version, is_builtin, created_at, updated_at}`。**DB 存储即"下发前固化在本地中枢"**，节点离线改派时直接读库，不重新生成。
- `apply_when` 字段记录该文档"何时必须加载"（如"写/改后端代码前"），用于节点端生成按需加载索引。
- 新增 CRUD API（`/docs`）+ 前端侧边栏"文档"管理页（新建/编辑 Markdown/上传 .md/删除/预览）。
- Alembic 迁移预置两份**节点裁剪版**模板（`is_builtin=true`）：
  - `coding-standards`（节点版）：仿 `.trae/rules/coding-standards.md` 的加载器结构，路径改为节点端 `.tilacode/rules/`，含按需加载流程；
  - `decision-documents`（子 Agent 裁剪版）：明确分级——中枢三件套已定方案的按三件套执行；重大偏离必须停下上报、留痕简版决策文档；小修小补不写决策文档（防 token 爆炸）。

### 2.2 任务绑定规则包（可选下发，版本锁定）

- `tasks` 表新增 `rule_docs JSONB NULL`。**NULL = 不下发规则包，节点自由发挥**——工程化程度由用户决定。
- **内容快照语义**：创建任务时把选中文档的 `{doc_id, name, version, apply_when, content}` 全文快照进 `tasks.rule_docs`（与 specification JSONB 存三件套全文同模式）。快照后文档迭代/删除均不影响已建任务，改派/重跑严格一致；派发时直接读任务行构造 payload，无需回查文档库。
- 创建任务 API 新增可选参数 `rule_doc_ids`，创建时锁定内容快照。
- 派发时 scheduler 解析任务快照 → 计算 sha256 → payload 下发 `{id, name, file_name, version, apply_when, url(下载 token), sha256, size}`。规范全文**不内联 WS payload**，走下载端点（复用 ADR-028/034 先例）。**token 在 TTL 内可重复下载**（支持 Agent 侧失败重试），过期后失效。

### 2.3 节点端固定规则目录 + AGENTS.md 加载器

- Agent 端收到 `rule_docs` 后下载（sha256 校验，复用 attachments 下载模式），在**任务工作目录**写入：
  - `.tilacode/rules/<file_name>`：各规范文档正文；
  - `AGENTS.md`：由 Agent **Python 侧**生成的固定加载器索引（仿 `.trae/rules/coding-standards.md`）——列出每份文档的 `file_name + apply_when`，要求"动工哪部分就读哪份"，**按需加载而非全量读取**。
- tilacode（opencode fork）原生支持项目级 `AGENTS.md` 自动注入会话上下文，无需改 tilacode 源码（实施第一步先验证注入行为）。
- `.tilacode/` 已在产物回传排除清单（ADR-028）中，规则文件不污染交付物；`AGENTS.md` 位于工作目录根部，随产物归档可追溯。
- **降级链路**：规则文档下载失败 → 重试 1 次 → 仍失败仅记 warning + task_progress 提示，任务照常执行（规范是质量约束不是执行依赖）。
- 未下发 `rule_docs` 时：不写任何规则文件，节点行为与现状完全一致。

### 2.4 AI 查询技能

- 注册 `list_standard_docs` 技能（复用 ADR-033 框架），Orchestrator 编排对话中可查询文档库清单（name/description/apply_when/version），为 P1 编排集成做基础。

### 2.5 阶段划分

| 阶段 | 内容 |
| --- | --- |
| **P0（本次）** | 文档库表+CRUD+管理页+预置模板、任务绑定与版本锁定、payload 通道+下载端点、节点端落盘+AGENTS.md 注入、list_standard_docs 技能 |
| **P1（后续）** | 编排流程集成：询问工程化需求（含利弊说明）、技术栈询问与推荐对话、按技术栈选模板/生成开发规范、create_task 技能传参、"后端先行"原则入三件套生成提示词 |
| **P2（后续）** | 环境要求清单文档随包下发 + 节点开工自检 + 缺失阻塞上报 |

## 3. 备选方案（Considered Options)

| 方案 | 结论 | 原因 |
| --- | --- | --- |
| 规范全文内联 WS payload | ❌ 否决 | 消息过大不可靠；一次性 token 下载已有两处先例 |
| 规范正文固化在节点安装包 | ❌ 否决 | 用户失去按任务选择工程化程度的控制权；节点端无法按任务定制 |
| 每次派发由 LLM 现写规范 | ❌ 否决 | token 贵、质量不稳定；P1 采用"模板库 + 按项目微调" |
| 规范放节点全局目录（跨任务共享） | ❌ 否决 | 规范是任务绑定且版本锁定的，全局放置会跨任务污染 |
| AGENTS.md 交给执行中的 AI/tilacode 维护 | ❌ 否决 | 会被任务过程改坏；必须由 Agent Python 侧在派发时写入 |
| 规范下载失败即任务失败 | ❌ 否决 | 规范是约束不是依赖；warning + 提示即可，不阻塞交付 |

## 4. 影响分析

- **中枢**：新表 + 迁移（含 seed）、`/docs` CRUD 端点、task 创建参数、scheduler payload 构造、一次性下载端点、新技能、前端新页面（`opc-web-console`）。
- **Agent**：`schemas.py` 新增 `TaskRuleDocInfo`、`hub_client` 下载、`workspace.py` 落盘与 AGENTS.md 生成、`task_bridge.py` 链路接入。
- **兼容性**：`rule_docs` 缺省 NULL，旧 payload/旧节点行为不变；旧节点收到 `rule_docs` 字段（Pydantic 默认忽略未知字段）不受影响。
- **token 成本**：节点端按需加载（索引制），不因下发规则而全量读规范。

## 5. 验收要点

1. 不选规则包创建任务 → 派发 payload 无 `rule_docs`，节点工作目录无规则文件，行为与现状一致。
2. 选规则包创建任务 → 内容快照锁定；文档后续迭代/删除不影响该任务；改派重跑仍用快照内容。
3. Agent 收到规则包 → `.tilacode/rules/` 文档落盘 sha256 一致、`AGENTS.md` 索引含全部文档的 apply_when。
4. 下载失败 → 重试 1 次（TTL 内 token 可重复下载）→ 仍失败 warning + 任务继续，不失败。
5. tilacode 会话中 `AGENTS.md` 与规则文档可被模型读取（人工/脚本验证注入行为）。

## 6. 实际实施情况（2026-09-25）

与初版决策的两处偏差（均已回写 Spec）：

1. **tasks.rule_docs 由"版本引用"改为"内容快照"**：初版存 `[{doc_id, version}]`，实施时发现派发需回查文档库且文档删除后历史任务无法取内容；改为全文快照后版本锁定天然成立、派发零回查，与 specification JSONB 存全文的既有模式一致。
2. **下载 token 由"即用即焚"改为"TTL 内可重复下载"**：初版仿 handoff 即用即焚，但 Agent 下载失败重试 1 次会拿到已焚 token 恒 401；改为 600s TTL 内幂等（规则文档非敏感 bundle，重复下载无风险）。

其余按决策实施。V-1 验证通过（tilacode `instruction-context.ts` 源码确认项目级 AGENTS.md 自动注入）。实施偏差中另发现：测试清理 fixture 会误删预置模板 seed，已修复为仅清理 `is_builtin=false`。

### 6.1 P1 编排集成实施（2026-09-25）

1. **编排提示词**（orchestrator_prompts 第 7 条）：全新需求编排流程 = 需求讨论 → 技术栈确认（含推荐方案与理由）→ 工程化询问（说明利弊：规范化/文档齐全 vs 决策文档耗 token）→ 工程化时查库绑定/生成规范 → 未确认不绑定。
2. **create_task 技能**扩展 `rule_doc_names` 参数：按文档名精确解析绑定（缺失跳过并在结果中提示），未传时不绑定（节点自由发挥回归语义）。
3. **generate_standard_doc 技能**：按已确认技术栈调用 LLM 生成规范文档入库（含 apply_when），供绑定；同名冲突/缺参/LLM 失败均降级为结果提示不抛错。
4. **三件套生成"后端先行"**（spec_builder_prompts 三处）：spec 先定义接口契约；tasks 强制后端任务前置于前端对接且以"接口全调通"为闸；checklist 以"所有后端接口联调通过"为前端对接验收前置检查点。
5. 验证：技能测试 4 项 + create_task 回归 7 项（含规则包绑定 2 项新用例）+ 提示词守卫 4 项全过；/skills 确认 list_standard_docs 与 generate_standard_doc 注册启用。

### 6.2 P2 环境要求清单实施（2026-09-25，设计经用户修正）

**初版方向否决**：原计划 Python 侧接单时硬检查（执行 check_cmd + 版本比较），不满足即拒单改派。用户修正：**检查与安装都由子节点的执行 AI 完成**——AI 有 shell 能力，缺失项可自行执行安装；Python 硬检查+拒单反而把"装个环境"变成"任务失败"。

**修正后设计**：
1. **清单结构化内联下发**：`tasks.environment_requirements JSONB`（`[{name, check_cmd, min_version?, install_hint?}]`），payload 内联（小、无下载通道）；来源 = 编排对话中技术栈确认后 AI 整理（create_task 技能 `environment` 参数 / REST 参数）。
2. **首次判断以工作目录为准**：`.tilacode/environment.md` 不存在 = 项目首次开发（或改派到新节点的新工作目录）→ prompt 注入"开工第一步：逐条检查 → 缺失自行安装（可参考 install_hint）→ 结果写入 environment.md"；文件存在 = 同项目迭代/同节点重跑 → 仅提示"环境已就绪，禁止重复探测"。
3. **改派新节点天然重查**：新节点 = 新工作目录 = environment.md 不存在 = 重新检查安装，无需额外状态。
4. Agent 端零新增依赖，纯 prompt 工程；某项环境确实无法安装时由 AI 在进度上报中说明（不静默跳过）。

验证：Agent 4 项（解析/首次指令/已就绪跳过/无清单回归）+ 中枢 3 项（落库/NULL 回归/payload 内联）+ 技能透传 1 项全过；迁移 20260925_task_env_req 应用；创建任务带环境清单端到端冒烟通过。

### 6.3 目录结构与元规则体系修订（2026-09-25，P2 后用户纠偏）

P0-P2 实施后用户指出四处偏差，本轮全部修正：

1. **目录结构对齐中枢两层体系**（用户画定）：
   - `.tilacode/rules/` 只放**元规则（驱动者）**：`coding-standards.md`（规则①，指向 docs/standards/）、`decision-documents.md`（规则②，指向 docs/adr、docs/spec、docs/INDEX.md）、`environment-check.md`（环境检查，新增，此前遗漏）；
   - **规范正文**落 `docs/standards/`（按技术栈命名，中枢编写或文档库下发，生成入库复用 P1 技能）；
   - `docs/` = 规则驱动的文档体系（INDEX.md 唯一权威登记源 + standards/规范层 + adr/决策层 + spec/决策层）；
   - **任务三件套**从 `docs/specs/` 移到工作目录根部 `specs/`（ADR-037 路径修订）。
2. **两份预置文档重写**：逐节以中枢 `.trae/rules` 两份为模板，仅替换路径指向与项目特定内容（删除 `G:\boleyun\经验.md` 等中枢专属项）；元规则①的规范清单表为动态部分——派发时把本次实际下发的规范清单渲染进 `{{STANDARDS_TABLE}}` 占位符。迁移 20260925_meta_rules_v2 upsert 覆盖旧 seed。
3. **元规则自动附加 + target 字段**：元规则两份每次工程化任务自动随 payload 下发（不需 LLM/用户显式选择），payload 条目新增 `target`（standard=docs/standards/，meta-rule=.tilacode/rules/）；环境清单非空时自动渲染附加 environment-check.md。防御：元规则文档误选为规范正文绑定时快照静默过滤（meta_rule_as_standard_rejected 告警）。
4. **"每次开发前必加载"双层硬机制**：①`tilacode.jsonc` 的 `instructions` 数组（tilacode v1 config 原生支持，"Additional instruction files"）——每次会话自动注入元规则全文，Python 侧 materialize 时 upsert 同步（续接会话补齐）；②AGENTS.md（tilacode 自动注入的兜底层）指向元规则+承载本次规范清单。
5. 验证：Agent 134 项全量通过（含 target 分目录/instructions 同步/AGENTS.md 新结构/specs 路径）、中枢规则文档 16 项通过（含清单渲染/环境文档渲染/元规则过滤）；端到端冒烟：创建工程化任务 → payload = 规范正文(standard)×1 + 元规则①②(meta-rule)×2 + 环境检查文档(meta-rule)×1，占位符渲染与 sha256 下载闭环全部通过。

# ADR-061: 子 Agent 全自主开发能力加固（M1–M7 模块化推进）

## 状态

Accepted（**待用户逐模块确认后实施**；本阶段只固化文档，不写代码）

## 日期

2026-09-28

## 问题背景

节点子 Agent 的产品定位是「全自主」：自己写代码、自己测试、自己对接前端，用户尽量不参与或少参与。
2026-09-28 做了 **4 份只读代码审计**（tilacode V2 执行层 / 节点 Python 侧 / 中枢 / 前端），
结论一句话：

> **不是"缺机制"，而是 V2 会话核心迁移时，把 V1 的「干活纪律」和「防呆」留在了旧路径上。**
> 节点真实链路（`packages/core/src/session/**`）跑的是一个只有一句话人格的 agent，
> 既没人要求它自检，也没有任何东西能在它空转时拦住它。

### 审计结论（仅列结论与关键证据，逐条展开见 Spec §1）

| # | 结论 | 关键证据（文件:行） |
|---|------|-------------------|
| A1 | 节点 agent 的系统提示词**只有一句话**，无任何验证/纪律要求 | `packages/core/src/plugin/agent.ts:12-13,127`（`BUILD_SYSTEM`） |
| A2 | V1 提示词**有**验证纪律，V2 完全不加载 | `packages/opencode/src/session/prompt/default.txt:74-75`；V2 侧 `grep '.txt"'` 仅命中 slash command 模板 |
| A3 | V1 编辑后**回灌 LSP 诊断**，V2 没有 | V1 `tool/edit.ts:196-201`、`write.ts:75-79`、`apply_patch.ts:265-289`；V2 `tool/builtins.ts:26-29` 把 LSP 列为待移植 |
| A4 | 任务「完成」是**纯静默判定**，不看任何验收 | `sse_consumer.py:259-274,378-385`；`task_event_handler.py:579-655` 全程不读 `task.specification` |
| A5 | 验收清单**已生成/已下发/已落盘**，但无人读回 | `workspace.py:46-50,718-723`（`specs/<标题>-<id8>/checklist.md`） |
| A6 | **失败信号本身已很好**（合并 stderr + 退出码 + 头尾截断 + 全文落盘路径） | `tool/bash.ts:51-57,118-121`；`tool-output-store.ts:13-14,74-104,138-174` |
| A7 | `serve` 模式**无权限应答器**，`ask` 无限等待；节点的自动应答是**唯一**防线 | `cli/cmd/serve.ts:6-23`；`permission.ts:197-218`（无超时）、`:76-86`（未匹配默认 ask）、`:231-247`（拒绝连带拒绝） |
| A8 | V2 **无 Doom Loop 检测、无会话级重试**（二者均为未勾选 TODO） | `runner/llm.ts:55,86`；传输层重试仅 2 次（`llm/src/route/executor.ts:36-38`） |
| A9 | 中枢对**节点执行**无 token/轮次/花费预算，只有 1800s 时间兜底 | `app/core/config.py:70-74`；`config.py:179-217` 的预算项只作用于中枢自身编排 LLM |
| A10 | 每任务用量**只写日志不落库**；`tool`/`detail`/节点 `session_id` 被丢弃 | `task_event_handler.py:359-429`；`ws_messages.py:100` 解析了 `tool` 但无人读 |
| A11 | 中枢**只能 cancel、不能 steer**；但节点侧 `send_prompt(delivery=steer)` 已支持 | `task_service.py:767`；`tilacode_client.py:282-287` |
| A12 | 任务级**严格 1 任务 → 1 节点**；V2 **无 `task` 工具**（子 agent 通道断开） | `models/task.py:90-92`；`tool/builtins.ts:26-29`；`grep parentID packages/core/src` 仅两个只读点 |
| A13 | 经验记忆系统（ADR-050 第三层）**已实现**，但只接中枢宝塔 MCP | `app/services/experience_service.py`；`app/skills/bt_call_guard.py:213-260` |
| A14 | **项目网络未被封**（更正历史结论）：npm / GitHub 实测可达 | Python httpx 与 bun fetch 实测 `registry.npmjs.org` 200、`github.com` 200；此前 000 系 PowerShell schannel 假象 |
| A15 | 前端**已有 Playwright**，但无截图/视觉断言代码 | `opc-web-console/package.json:31`；`e2e/` 仅 1 个 spec 且无 `page.screenshot()` |

### 用户原始诉求与已定决策（**逐阶段回读用，防止上下文丢失**）

| 编号 | 用户原话要点 | 已定决策 / 边界 |
|------|-------------|----------------|
| Q1 | 「有没有一个让他自己看得见的闭环？如果没有，该怎么设计」 | 需要：验证纪律 + 可执行验收 + 完成闸（→ M1、M4） |
| Q2 | 「分层验证该怎么实现？我没有经验」 | 按「最便宜且能证伪」的顺序逐级跑，红了把失败原文回灌（→ M4） |
| Q3 | 经验记忆「**当前想做在单个节点子 agent 系统里面的**，后续打通到中枢：中枢显示节点 A、节点 B 的经验，定时整理，其他开发机按需拉取」 | 复用 ADR-050 已有表/服务，扩到节点来源 + 环境签名 + 派发前召回 + 定时整理（→ M6） |
| Q4 | 「权限策略是**全权开放**，不然等待权限很麻烦，因为没有人会给他授权」 | 保持无人值守可自主；但 `ask` 语义必须收敛为明确 allow/deny，并补应答兜底（→ M2） |
| Q5 | 「怕模型陷入死循环白烧 token」＋「中枢把目标节点**拉进中枢的会话、默认折叠**，用户可展开查看完整会话」 | 预算闸（先软后硬）+ 可折叠会话镜像（→ M3、M5） |
| Q6 | 「前端对接最主要的是视觉反馈；开发机是纯命令行的；也没有工具让他起截图、操作浏览器」 | 文本优先的浏览器反馈 + 截图作为产物 + 依赖固化（→ M7） |
| Q7 | 「长任务可观测性我也不知道该怎么设计」 | 先停止丢弃字段，再建用量序列/告警/时间线（→ M5） |
| Q8 | 「多 agent 什么意思？他在里面设计的吗？」 | 现无并行扇出，子 agent 通道断开；**本 ADR 不做**（→ 见备选方案） |
| B1 | 「本地有 bun 编译器，那份文档里要编译 Linux 的也可以编译出来」 | 构建流程与网络实测记入 Harness §6；**待执行** |
| B2 | 「允许跑一次源码级 e2e，但**不是现在**；主要任务先把文档固化下来」 | 授权已获得，执行时机由用户指定（见 D4） |
| B3 | 「**一个模块一个模块完成**，不要每个都做一点；文档太长看着容易乱」 | 见 D1/D3：一次一个模块，文档保持短、按模块分节 |
| B4 | 「另一个会话在跑，怕两个会话改同一份文件乱掉」 | 见 D3.4（**该会话已停止，约束解除**） |
| B5 | 「M2 权限语义**要不要由中枢统一下发**？默认只能在工作区内；工作区外没许可一律不准放行」 | **是**：策略由中枢统一下发；工作区内 allow、**工作区外一律 deny**（见 D5.1） |
| B6 | 「M4 我**比较怕假成功**」 | 完成闸**从严**：「无法验证」也不得呈现为正常完成（见 D5.2） |
| B7 | 「M5 中枢**回落一份只读镜像**，要**实时**看到那边在做什么、正在执行什么命令；后续想在中枢和节点**直接对话**，比如 `@节点A`」 | 选定**只读镜像 + 实时**；新增 `@节点` 定向对话（见 D5.3） |
| B8 | 「M4 自修轮数默认改成 **5**；预算可以改成 **80%**」 | 自修 ≤5 轮；软阈值 80%（硬阈值 100%，见 D5.4） |
| B9 | 「完成时创建 todo 表…我想要他在中枢的**任务摘要**那里实时更新；摘要**分两部分**：上层子 agent、下层中枢待完成的任务」 | 新增分段任务摘要（见 D5.5）；经 2026-09-28 澄清：**下段 = 中枢编排器自己的待办**（独立为 M5d），**不是** `tasks.md`、**不是**任务链 |
| B10 | 「文档里有 V1 通道和 V2 通道，**当前用的是哪个**？两者什么区别？」 | 节点链路 = **V2**；对照表见 Spec §1.1（见 D5.6） |

## 决策

### D1 · 模块化推进（本 ADR 的核心工作方式）

- **一次只推进一个模块**，完成 → 验证 → 向用户汇报 → 用户确认后，才进入下一个。
- **不并行铺开**：禁止「每个模块各做一点」。每个模块要么完整交付并通过验收，要么不做。
- 每个模块**开工前重新讨论一次**（用户明确要求）；讨论结论追加到本 ADR 的「实际实施情况」小节。

### D2 · 模块清单、依赖与顺序

按**依赖与杠杆**排序（不是按 Q 编号顺序）。**状态以本表为准**（2026-09-28 刷新）：

| # | 模块 | 对应 | 状态 | 依赖 | 规模 | 一句话目标 |
|---|------|------|------|------|------|-----------|
| 1 | **M1a 干活纪律（提示词）** | Q1 | ✅ **已完成** | 无 | S | 让 agent「知道要自检」：把 V1 的验证纪律移植进 `BUILD_SYSTEM` |
| 2 | **M2a 权限语义（无人值守 + 原因回灌 + 节点去 blanket allow）** | Q4 | ✅ **已完成** | 无 | S | 工作区外/.env 一律 deny；`ask` 不再挂死；拒绝原因可读；存量工作区自愈 |
| 3 | **M3 防打转与会话级重试** | Q5 | ✅ **已完成** | 无 | M | 同参重复 3 次不执行 + 有上限重试（5 次/60s）+ 无进展信号 |
| 4 | **M5a-steer 中枢↔节点定向发言通道** | Q5+Q7 | ✅ **已完成** | 无 | S | `hub.task_steer` → 节点 `send_prompt(steer)`，中枢能对运行中任务发言 |
| 5 | **M4 全量（判据结构化 + 分层执行 + 完成闸 + 主动对话找人 + ④自修 ≤5 轮）** | Q1+Q2 | ✅ **已完成** | #4 ✅ | L | checklist → `verify.json` → 分层跑 → 全绿才完成；红/无法验证则**主动在对话区找人决定** |
| 6 | **M2b 中枢下发权限策略** | Q4 | ✅ **已完成** | M2a ✅ | S | 中枢下发 `permission_policy`；节点只追加白名单 + 兜底拉取 + 审计 |
| 7 | **M5a 另一半：每任务用量落库** | Q7 | ✅ **已完成** | 无 | S | `tool_call_logs(kind="llm_usage")` + 迁移；成本曲线可算 |
| 8 | **M5b 预算闸（80% 软 / 100% 硬）** | Q5 | ✅ **已完成** | #4 ✅ + #7 ✅ | M | 80% 发收尾指令（不停任务）、100% 强制停止；step+token 双维度 |
| 9 | **M5c 只读镜像 + 会话视图 + 时间线** | Q7 | ✅ **已完成** | #4 ✅ | L | 实时只读镜像（可离线回看）+ 任务摘要上段保现状 + 用量曲线 + 告警 |
| 10 | **M5d 中枢编排器待办** | Q7 | ✅ **已完成** | **无**（可独立实施） | M | 中枢自己的 todo：存储 + `todowrite` 式技能 + 摘要**下段**渲染 |
| 11 | **M6 经验记忆下沉与跨机复用** | Q3 | ✅ **已完成** | #7 ✅（字段读取） | M | 复用 ADR-050：节点来源 + 环境签名 + 派发前召回 + 定时整理 |
| 12 | **M7 前端视觉反馈** | Q6 | ✅ **代码完成**（依赖烘焙待构建期执行） | #5 ✅ | M | 文本优先的浏览器反馈 + 截图产物 + 依赖固化 |
| — | ~~M1b 编辑后 LSP 回灌~~ | Q1 | ⏸ **已推迟**（2026-09-28 用户决定） | — | **L**（原估 M） | 勘察结论：V2 **没有** LSP 运行时，需从 V1 **移植整套**（非接线），故推迟 |

> **M1 拆 a/b 的原因**：a 是改十几行提示词的零风险项，b 牵扯整条 LSP 运行时移植，风险量级完全不同，必须先勘察再立项。
>
> **不属于任何模块、但必须做的全局待办**（见下方 §全局待办）：
> ① 发布二进制 + 重打节点模板（否则 M1a/M2a/M3 在生产上零效果）；② 源码级 E2E（ADR-060 P4 / E2E-A5）；
> ③ `tool/skill.ts` 错误映射补改。

### D3 · 文档与协作规则

1. **短文档优先**：本 ADR 是回读入口；每模块的详细需求只在 Spec 的**同名小节**里，实施某模块时只读那一节。
2. **改动前置**：每个模块实施前，先在本 ADR 追加该模块的「实施范围/验收方式」，用户确认后才动代码（遵循 `.trae/rules/decision-documents.md`）。
3. **边做边登记**：Changes 按模块分表，改一个文件登记一行，**禁止完成后一次性补记**。
4. **并发边界**（原为「另有会话在跑」；**2026-09-28 用户确认该会话已停止，约束解除**）：
   - 保留的常规纪律：每次写入前重读目标文件；若发现被并发修改则停止并询问。
   - 原受限区域（中枢 `app/services/**`、`app/core/config.py`、`app/models/task.py`、`opc-web-console/src/**`）**现已解禁**，M5/M6/M7 可按模块顺序推进；各模块仍须开工前经用户确认。
5. **分阶段汇报格式（用户 2026-09-28 指定，每阶段必报）**：每次汇报必须包含四段——
   ① **本阶段完成了哪些内容**（文件/函数级）；② **实现了什么效果**（可验证的结果或"仅行为倾向、需重建二进制后观察"）；③ **还剩哪些没有完成**（未做项/未验证项/待勘察项）；④ **正在开始哪些内容**（下一模块或下一步）。

### D4 · 已获授权但**暂不执行**的动作

| 动作 | 状态 |
|------|------|
| 源码级 E2E（真实消耗 DeepSeek token，验证 P4 摘要热前缀；凭据在 `tilacode-local.db` 的 `credential` 表） | **已授权，待用户指定时机** |
| 交叉编译 `linux-x64-baseline`（`bun run build --skip-embed-web-ui --target linux-x64-baseline`；GitHub 实测可达） | **已授权，待用户指定时机**；失败即停下询问，不自行配代理 |

### D5 · 用户本轮确认的决策（2026-09-28，供 Spec/Harness 引用）

| 编号 | 决策 | 落点 |
|------|------|------|
| **D5.1** | **权限策略由中枢统一下发**；默认**工作区内 allow、工作区外一律 deny**（用户未授权不放行），`.env` 等敏感读取同样拒绝；例外只能由中枢显式开白名单 | Spec §3-M2、§5-M2 |
| **D5.2** | **完成闸从严**（用户"比较怕假成功"）：有红不报 `completed`；**「无法验证」也不得呈现为正常完成**（独立状态 + 摘要显著标记）；不允许"无验证结论却 completed"的路径 | Spec §3-M4③、§5-M4、Harness 守卫用例 |
| **D5.3** | 会话获取路线**选定只读镜像**（可离线回看/可审计），且**实时**可见"正在做什么、正在执行哪条命令"；并支持**中枢↔节点直接对话**（`@节点A` → 该段文字 steer 到该节点活动会话，回复经镜像回流） | Spec §3-M5a/M5c、§5-M5 |
| **D5.4** | 参数默认值：M4④ 自修 **≤5 轮**；M5b 软阈值 **80%**（硬阈值 100% 保留） | Spec §3-M4④/M5b |
| **D5.5** | **任务摘要分两段**：**上段 = 子 Agent 实时状态**（状态 Tag/标题/进度条/当前动作/子 Agent 待办——现有 5 块**全留**）；**下段 = 中枢编排器自己的待办**（全新能力，独立为 **M5d**：中枢侧存储 + 编排器 todo 技能 + 下段渲染）；要求**实时更新** | Spec §3-M5c/M5d、§5-M5、§5-M5d |
| **D5.6** | 澄清**通道口径**：节点执行链路 = **V2**；V1 为保留的旧路径（含 `task` 子 agent、LSP 回灌、Doom Loop、会话级重试等 V2 尚未接回的能力） | Spec §1.1 |

> **已决（2026-09-28 澄清后）**：D5.5 上段保持现状 5 块；下段 = **中枢编排器自己的待办**，独立为 **M5d**
> （已核实中枢侧为零基础：`tasks.todos` 仅存节点上报内容，技能注册表约 20 个技能中无任何 todo 能力）；
> 预算**保留双档**（80% 软 / 100% 硬）。
>
> **修正记录**：本文档早前一版曾把下段写成"中枢规划任务（`tasks.md`）+ 中枢执行待办两个子区"，
> 或"由任务链派生"，**均系推测，已废弃**——用户口径明确为"**中枢编排器自己的待办**"，
> 与右侧「任务清单」页签（`tasks.md` 内容）**不是一回事**。

## 备选方案

| 方案 | 结论 | 原因 |
|------|------|------|
| A. 模块化推进，一次一个（本决策） | **采纳** | 用户明确要求；可追踪、可回退、每步可验收 |
| B. Q1–Q8 每个都先做一点 | 否决 | 用户明确否决：容易跟不上进度、乱 |
| C. 严格按 Q1→Q8 顺序 | 否决 | 依赖倒置（Q5 的 steer 通道是 Q1 ④ 的前置） |
| D. 只改提示词（最省） | 否决 | 没有闸门，agent 仍会提前收工并被判成功（A4） |
| E. 先做任务级并行多 agent | 否决 | V2 子 agent 通道断开（A12）；ADR-055 已否决移植 `task` 工具 |
| F. 用更聪明的模型替代机制建设 | 否决 | A1/A4/A8 是结构问题，换模型不改变「无人校验」 |

## 影响

### 节点侧（`opc-agent-programming`）

- M2/M4/M5/M6 涉及 `app/executor/task_bridge.py`、`workspace.py`、`app/client/hub_client.py`、`app/executor/sse_consumer.py`。
- 节点是 Nuitka 打包产物 → 任何节点侧改动都需要**重新构建并分发 Agent 模板**（`BUILD.md`）。

### 执行层（`tilacode/packages/core`）

- M1/M3/M4 涉及 `plugin/agent.ts`、`session/runner/llm.ts`、`tool/{edit,write,apply-patch,bash}.ts`。
- tilacode 走 ADR-013 预编译二进制分发 → 改动需**重建 `linux-x64-baseline` 并更新 `manifest.json` 的 sha256/size**，否则节点重跑安装脚本不会升级。

### 中枢侧（`opc-central-dispatch-hub`）

- M4 涉及 `spec_builder`/`task_service`；M5 涉及 `task_event_handler.py`、`ws_messages.py`、`tool_call_logs` 迁移；M6 复用 `experience_service`。
- **与另一并发会话的工作区重叠**，需按 D3.4 划界。

### 兼容性与风险

- 旧节点不携带新字段时中枢必须按「无数据」处理（沿用 ADR-055 可选字段约定）。
- M1a 是纯提示词改动，**唯一风险是行为漂移**（更啰嗦/多做无用验证）→ 需用 P0 基线（缓存命中率 95.2%）与任务时长对比复核。
- M3 的重试**必须有上限**：V1 的 `SessionRetry.policy` 是无上限重试，直接照搬会放大 token 浪费，与 Q5 的诉求相反。
- M5 的 `hub.task_steer` 是**新增协议事件**，需同时更新节点与中枢，且旧节点必须忽略未知事件。

## 关联文档

- [061-agent-autonomy-hardening-spec.md](../spec/061-agent-autonomy-hardening-spec.md)（分模块需求 + 验收标准）
- [061-agent-autonomy-hardening-harness.md](../spec/061-agent-autonomy-hardening-harness.md)（测试清单 + CI 门禁 + 构建/网络实测）
- [061-agent-autonomy-hardening-changes.md](../spec/061-agent-autonomy-hardening-changes.md)（按模块的变更登记）
- [ADR-060](./060-tilacode-prefix-cache.md)（同为执行层优化；P4 的 E2E-7 与 D4 共用同一次源码级 E2E 授权）
- [ADR-050](./050-bt-tool-governance-experience-memory.md)（经验记忆系统，M6 的复用对象）
- [ADR-055](./055-execution-layer-hardening.md)（执行层加固、`detail` 出口；否决并行多 agent 的依据）
- [ADR-013](./013-programming-agent-binary-distribution.md) + `opc-agent-programming/tilacode/BUILD.md`（二进制构建与分发）
- 审计证据来源：本次 4 份只读审计（tilacode V2 / 节点 / 中枢 / 前端），结论已内联到 §问题背景 A1–A15

## 实际实施情况（分模块登记）

### 全局待办（不属任何模块，但影响"改动是否真正生效"）

| # | 事项 | 为什么必须做 | 状态 |
|---|------|-------------|------|
| G1 | **发布 tilacode 二进制**（复制到 `hub/static/binaries/tilacode/<版本>/` + 回填 manifest 的 sha256/size + 改安装脚本 `TILACODE_VERSION`） | M1a/M2a/M3 全在 tilacode 内，**不发布则生产零效果**。构建配方见 Harness §6（**必须同时给 `OPENCODE_CHANNEL=local`**，否则节点换库丢历史） | ⏸ 已构建并验证（`dist/`，140.7MB / sha256 `6041b5bb…`），**按用户决定暂不发布** |
| G2 | **重打节点模板 tarball** + 更新其 manifest sha256 | M2a 的**节点侧**改动（`workspace.py`：模板去 blanket allow + 存量自愈）靠它分发，光升级二进制无效 | ⬜ 未做 |
| G3 | **源码级 E2E** | 验 ADR-060 P4（摘要热前缀）+ `E2E-A5`；也可顺带端到端证明 M2a 的"工作区外被拒" | ⏸ 已授权；用户决定暂不跑 |
| G4 | `tilacode/.../tool/skill.ts:97` 错误映射补改 | M2a 内唯一未核实的同类落点 | ⬜ 约定下次顺手补 |

### M1a · 实施范围（2026-09-28 登记，**待用户确认后动代码**）

**目标**：让 V2 的默认 agent 具备「验证自己的结果」的行为倾向。当前 `BUILD_SYSTEM` 只有一句话（`plugin/agent.ts:12-13`）。

**改动面**：仅 `tilacode/packages/core/src/plugin/agent.ts` 的 `BUILD_SYSTEM` 常量（被 `:127` `item.system ??= BUILD_SYSTEM` 用于默认 `build` agent）。**零控制流、零装配改动**——
因此 `runner/llm.ts:208` 的 `system: [agent.info?.system, system.baseline]` 两段式结构不变，**ADR-060 的前缀不变量不受影响**（守卫用例 `UT-M1a-2`）。

**移植来源与取舍**（逐条对齐 V1 `packages/opencode/src/session/prompt/default.txt`）：

| 来源 | 内容 | 处置 |
|------|------|------|
| `default.txt:74` | "Verify the solution if possible with tests. NEVER assume specific test framework or test script. Check the README or search codebase…" | **移植**（核心） |
| `default.txt:75` | "VERY IMPORTANT: When you have completed a task, you MUST run the lint and typecheck commands…" | **移植**，但删掉"问用户要命令"（无人可问）→ 改为"找不到就明说未验证" |
| `default.txt:62` | "NEVER assume that a given library is available… first check that this codebase already uses the given library" | **移植**（防自造依赖） |
| `codex.txt:49` | "Never ask permission questions like 'Should I proceed?' or 'Do you want me to run tests?'" | **移植**（无人值守必需） |
| `default.txt:68` | "DO NOT ADD ANY COMMENTS unless asked" | **刻意不移植**：与本项目 ADR-048 元规则要求中文 docstring 直接冲突 |
| `default.txt:84` | "answer concisely with fewer than 4 lines" | **刻意不移植**：与自主任务需要收尾/handoff 报告冲突 |
| `default.txt:81` | "prefer to use the Task tool" | **不移植**：V2 无 `task` 工具（子 agent 通道断开） |

**备选方案**：

| 方案 | 结论 | 原因 |
|------|------|------|
| A. 只扩充 `BUILD_SYSTEM` 常量（推荐） | **采纳** | 改动最小、零控制流、不碰装配（保前缀不变量）、节点/TUI/CLI 所有入口一处生效 |
| B. 移植 V1 整套 provider 专属提示词并按模型选择 | 否决 | 引入"按模型选提示词"分支；V1 内容需逐条甄别（多条与本项目约定冲突）；收益集中在十几行验证纪律 |
| C. 不改系统提示词，改在节点 `_build_prompt` 注入 | 否决 | 只覆盖中枢派发场景（TUI/CLI 不受益）；提示词属 agent 自身能力，应在 agent 定义处单点 |
| D. M1a 与 M1b（LSP 回灌）合并 | 否决 | M1b 尚未勘察、风险未知；合并破坏"一次一个模块"的可追踪性 |

**验收方式**：单测 `UT-M1a-1~3`（要点存在 / 装配与其它 agent 不受影响）+ 回归
（`packages/core` 的 `bun typecheck` 与 `bun test`，沙箱环境类失败集合须与改动前一致）。
**行为效果需重建 tilacode 二进制后**才能观察：与 ADR-060 基线（缓存命中率 **95.2%**）对比不劣化，且任务步数/时长无异常膨胀。

**回滚**：还原该常量即可（单文件单常量）。

**待用户确认的关键点**：上面拟定的 `BUILD_SYSTEM` **英文措辞**；以及"刻意不移植"的两条（注释规则、4 行限制）是否符合预期。
→ **2026-09-28 用户确认："可以开始改代码"**，措辞与两条不移植决定均通过。

### M1a · 实施结果（2026-09-28 **已完成**）

- **改动**：`packages/core/src/plugin/agent.ts` 的 `BUILD_SYSTEM` 由 **1 句扩为 3 段**——第一句人格定义**原样保留**，
  新增「Work from evidence, not assumption」（读周边代码/沿用既有库与约定、不臆测库可用、从 README/包清单确定检查命令、改完自查）
  与「Verify before you report done」（必须跑 lint/typecheck/测试、失败读原文改因后重跑、不问"要不要跑"、无检查可跑要明说未验证）
  两节，末尾加收尾报告要求（改了什么/跑了什么验证/还有什么未验证）+ 未明确要求不得提交。
- **未改**：任何控制流、任何装配代码。`runner/llm.ts` 的两段式 `system: [agent.info?.system, system.baseline]` 不变
  → **ADR-060 的前缀缓存不变量不受影响**。
- **新增测试**：`packages/core/test/agent.test.ts` +3 项，并抽出 `builtInAgents` 复用内置 agent 装配（原 1 处内联装配改为复用）：
  - `UT-M1a-1`：默认 agent 的 system 含 6 条纪律关键短语；
  - `UT-M1a-2`：纪律**不串**到 `explore`/`compaction`，`plan` 仍无 system；
  - `UT-M1a-3`：既有内置提示词特征串仍在（本次只应影响 `BUILD_SYSTEM`）。
- **验证结果**：`test/agent.test.ts` **10 项全过**（7 既有 + 3 新增）；`bun typecheck`（tsgo --noEmit）**退出码 0**；
  `packages/core` 全量 `bun test` **986 通过 / 7 跳过 / 100 失败**。失败集合与改动前一致（99 项沙箱环境类）
  **外加 1 项偶发**：`Npm.add > reifies when package cache directory exists without the package installed`
  在全量并行下 5s 超时，**单独运行 998ms 通过**；同文件 `Npm.install` 的失败原因为
  `EPERM: operation not permitted, open 'C:\Users\kwyt1\.local\share\tilacode\log\tilacode.log'`（沙箱禁止写工作区外路径）。
  **结论：本次改动无回归**。
- **效果**：属**行为倾向**层面（提示词），**需重建 tilacode 二进制**才能在真实任务中观察；
  观察口径 = ADR-060 基线（缓存命中率 95.2%）不劣化 + 任务步数/时长无异常膨胀。
- **回滚**：还原该常量即可。

> 每完成一个模块在此追加：实施范围 → 与决策的偏差 → 验证结果。

---

### M2 · 实施范围（2026-09-28 勘察后登记，**待用户确认后动代码**）

#### 勘察结论（代码级，逐条有出处；**其中两条推翻了此前假设**）

| # | 结论 | 证据 |
|---|------|------|
| **R1** | **节点写的 `"permission": {"*": "allow"}` 并不是"宽松但无害"**：它经 V1→V2 迁移成 `{action:"*", resource:"*", effect:"allow"}`（`v1/config/migrate.ts:74-91`），由配置插件**追加到每个 agent 权限数组末尾**（`config/plugin/agent.ts:70-78`），而求值是 **`findLast` 最后匹配者胜**（`permission.ts:76-86`）→ **节点配置把 tilacode 内置的全部护栏覆盖成 allow**：`.env` 读取的 `ask`、工作区外的 `ask`、`question`/`plan_*` 的 `deny`、以及 tool-output/tmp/plans 白名单**全部失效** | `migrate.ts:82-89`、`config/plugin/agent.ts:77`、`permission.ts:76-86` |
| **R2** | 判定顺序：先 `denied()`，**只看 agent 自身规则**（saved 规则无法推翻 deny，`permission.ts:147-149`）；再 `all = [...rules, ...savedRules]` 合并求值——**任一资源 deny → deny；任一 ask → ask；否则 allow**（`:155-162`） | `permission.ts:147-162` |
| **R3** | **拒绝原因到不了模型**：各工具用 `Effect.mapError(() => new ToolFailure({...}))` 兜底（如 `bash.ts:196` 的 `Unable to execute command: X`），会把 `BlockedError` **塌缩成通用失败文案** → 模型不知道"这是策略拦截"，会反复重试同一条路 | `bash.ts:196`、`tool/tool.ts:91-112`（`settle` 无 permission 分支） |
| **R4** | V1 `permission` 支持**按资源**嵌套（`{external_directory: {"*": "deny", "/x/*": "allow"}}`），迁移保留资源维度 → 表达白名单**是可能的** | `migrate.ts:82-89` |
| **R5** | **但"工作区外一律 deny"不能写在节点配置里**：节点不知道 tilacode 内部白名单路径（`<data>/tool-output/*`、`<tmp>/*`、plans 目录），而配置规则追加在最后 → 一句 `external_directory: "*" → deny` 会把内部白名单**一起 deny 掉**（可能打断产物落盘/计划模式） | `plugin/agent.ts:105-111` 与 R1 的追加语义 |
| **R6** | `serve` 模式**无任何权限应答器**，`ask` 在 `Deferred` 上无限等待（`permission.ts:197-218`），节点事件泵是**唯一**防线 | 见 A7 |

#### 决策：拆成 M2a / M2b（用户已选 M2；按"一次一小步"再拆）

- **M2a 最小安全闭环**（建议先做：改动小、可立即验证、直击两个真问题）
  1. **tilacode**：新增**无人值守开关**（环境变量），开启后 `assert` 遇到 `ask` 一律按 **`deny`** 处理——**不改内置 ruleset**，故 tool-output/tmp/plans 白名单与"工作区内免断言"语义**原样保留**；且 `ask` 不再进入无限等待 → **顺带消灭 R6 的挂起隐患**。
  2. **tilacode**：**拒绝原因回灌**——`BlockedError` 映射为带策略信息的 `ToolFailure`（如 `Blocked by permission policy: external_directory …；该路径不在允许范围内，不要重试`），替换现有通用文案（修 R3）；集中一处映射，各工具兜底复用。
  3. **节点**：`TILACODE_CONFIG_TEMPLATE` **移除 `"permission": {"*": "allow"}`**（修 R1），并设置无人值守开关。
- **M2b 中枢下发策略**（第二步）
  1. 中枢派发 payload 携带 `permission_policy`（如 `{mode:"workspace-only", extra_allow:[...]}`）；
  2. 节点据此生成**只含白名单追加**的配置（绝不再写 blanket allow），并留审计日志；
  3. 节点**兜底拉取 pending 权限**并应答（作为 M2a 之后的保险）。

#### 备选方案

| 方案 | 结论 | 原因 |
|------|------|------|
| A. M2a（无人值守 ask→deny + 原因回灌 + 节点移除 blanket allow）→ M2b（中枢下发） | **采纳** | 最小改动解决"默认放行"与"挂起"；不触碰内置白名单；交互式使用不受影响 |
| B. 只在节点配置里写 deny 规则 | 否决 | 会把 tilacode 内部白名单一起 deny（R5）；且无法区分交互式/无人值守，交互用户也被硬拒 |
| C. 把内置默认的 `ask` 全改成 `deny` | 否决 | 全局行为变更，破坏交互式 TUI 的询问体验，影响面超出节点场景 |
| D. 只加节点兜底应答，不改语义 | 否决 | 兜底只治"挂起"，治不了 R1 的"工作区外仍被放行" |

#### 验收方式

- **单测（tilacode）**：无人值守开启时 `ask` → `BlockedError`（**不产生 pending、不阻塞**）；关闭时行为与现状一致；`BlockedError` 产生的 `ToolFailure` 文案**含策略信息**（非通用文案）。
- **单测（节点）**：生成的工作区配置**不含** blanket `"*" → "allow"`；无人值守开关被置位；M2b 后含中枢下发的白名单且**只加白名单**。
- **集成/E2E**：真实任务中让 agent 读工作区外文件 → **被拒 + 消息含原因 + 任务继续**（不挂起、不整轮失败）；读 `.env` 同样被拒。
- **回归**：`packages/core` 的 `bun typecheck` + `bun test`（沙箱环境类失败集合不变）；节点 `pytest` + `ruff`。
- **部署前提**：tilacode 需重建二进制；节点需重新打包模板（均已授权、待用户指定时机）。

#### 待用户确认

1. **是否按 M2a → M2b 两步走** → **已确认：拆 M2a → M2b**。
2. **无人值守开关形态** → **已确认：配置键**（`permission_mode: "interactive" | "unattended"`，默认 `interactive`）。
   实现约束（勘察新增）：配置键必须加在 **V1 schema** 上并显式迁移到 V2，否则 V1 解析会当未知键丢弃
   （`config.ts:143` 两个解码均为 `onExcessProperty: "ignore"`，即**未知键被忽略而非报错**——顺带更正了节点注释里
   "加 V2 键会让整个文件作废"的过时说法）。
3. **工作区内是否也收紧** → **已确认：只收"工作区外 + `.env`"**，工作区内保持放行。

### M2a · 实施结果（2026-09-28 **已完成**）

- **tilacode（7 文件）**：V1/V2 schema 与迁移新增 `permission_mode`；`permission.ts` 新增 `refusesUnanswered()`
  并在 `assert`/`ask` 中把无人值守下的 `ask` 当 `deny`（**不改内置 ruleset** → tool-output/tmp/plans 白名单
  与"工作区内免断言"原样保留，既有测试 "allows managed output reads without granting external directory access" 仍通过）；
  `tool/tool.ts` 新增 `permissionFailure()`，11 个工具的错误映射改走它 → **拒绝原因带命中规则 + "不要重试"**。
- **节点（1 文件）**：模板移除 `"permission": {"*": "allow"}`、改设 `permission_mode="unattended"`；
  `_heal_workspace_config` 增加**存量工作区迁移**（仅在 `permission` 恰好等于旧模板默认值时迁移，不误动用户自定义规则）。
- **验证**：`bun typecheck` 退出码 0；`packages/core` **990 通过 / 7 跳过 / 99 失败**（99 项全为既有沙箱环境类，
  **非环境类失败为空**）；节点 `pytest` **182 通过** + `ruff` 全过。共新增 3 项用例、更新 10 处断言
  （6 处工具拒绝文案断言由"通用文案"改为断言新的可读文案，属有意行为变更）。
- **效果**：① 工作区外与 `.env` 在无人值守下**一律被拒**（此前被 blanket allow 放行）；
  ② `ask` 不再无限等待 → **挂起隐患消除**；③ 拒绝原因可读、可指导模型换做法。
- **遗留**：`tool/skill.ts:97` 的错误映射未改造（未核实其是否已带细节）。
- **部署前提**：tilacode 需重建二进制；节点需重新打包模板（已授权，待用户指定时机）。

### M2b · 中枢下发权限策略（**未开始**）

- 待办：中枢派发 payload 携带 `permission_policy` → 节点据此**只追加白名单**（绝不再写 blanket allow）
  + 兜底拉取 pending 应答 + 审计日志。依赖 M2a 的语义基础（已完成）。

---

### M3 · 实施范围（2026-09-28 勘察后登记，**待用户确认后动代码**）

**目标**：把 V2 丢失的两个防呆能力补回来——**但重试必须加上限**（V1 是无上限，与"防白烧 token"背道而驰）。

#### 勘察结论（代码级）

| # | 结论 | 证据 |
|---|------|------|
| **S1** | V2 **没有** Doom Loop 检测、**没有**会话级重试；二者都是未勾选 TODO | `core/src/session/runner/llm.ts:55,86`；`grep doom_loop packages/core/src` 零命中 |
| **S2** | V1 的 Doom Loop 判定范围是**同一条 assistant 消息内最近 3 个工具调用**（同工具 + `JSON.stringify(input)` 完全相同 + 非 pending），命中后走 `permission.ask("doom_loop")`（默认 **ask → 等人**） | `opencode/src/session/processor.ts:29,356-380` |
| **S3** | **照搬 S2 到 V2 会永远触发不到**：V2 每个 provider turn（step）通常只发一个工具调用，而判定窗口若限定在"同一条 assistant 消息内"，窗口永远凑不满 3 —— 必须**跨 step** 统计 | S2 与 `runner/llm.ts:396-406` 的单调用步进结构 |
| **S4** | V1 重试：`delay()` 尊重 `retry-after-ms` / `retry-after`（秒或 HTTP 日期），否则指数退避 `2000 × 2^(attempt-1)`，封顶 30s；策略**无上限次数**（仅对不可重试错误 `Cause.done`） | `opencode/src/session/retry.ts:26-64,176-199` |
| **S5** | **传输层已有 2 次重试**（429/503/504/529 与 5xx，抖动退避 500ms→10s）→ 会话级重试若也无限叠加，会形成指数级放大 | `llm/src/route/executor.ts:36-38,345-364` |
| **S6** | V2 的 provider 错误当前只做 `failAssistant`（把助手标失败），无任何恢复路径；唯一恢复是上下文溢出压缩 | `runner/llm.ts:236-241,282-294` |

#### 决策（拟）

1. **Doom Loop（V2）**：新增**跨 step 的滚动窗口**（默认 N=3），签名 = `工具名 + 规范化 input 的 JSON`；连续 N 次完全相同 → **该次调用不执行**，注入一条提示（"检测到连续重复的相同调用，未执行——换一种做法，或说明为何必须重复"），让模型基于现有结果继续（对齐中枢 ADR-043/058 的处理风格）。状态放在 runner 的会话级实例状态里。
2. **会话级重试（V2，带上限）**：新增 `core/src/session/retry.ts`，移植 S4 的 `delay()` 语义与可重试判定；**新增上限**：最大尝试次数（默认 **5**）+ 总时长上限（默认 **60s**）；用尽 → 明确失败并把原因写入助手失败消息。`ContextOverflow` **不重试**（继续走压缩）。
3. **无进展信号（仅可观测）**：连续 K 轮无新工具/无文件产出时记一条结构化信号（供 M5c 告警用）；**本模块不做自动停止**（避免与 Doom Loop 职责重叠）。

#### 备选方案

| 方案 | 结论 | 原因 |
|------|------|------|
| A. 移植 V1 语义 + **跨 step** 判定 + 重试加上限 | **采纳** | 保留 V1 已被验证的语义，修掉两处不能照搬的地方 |
| B. 原样照搬 V1 | 否决 | 打转检测在 V2 永不触发（S3）；无上限重试与"防白烧 token"矛盾（S4） |
| C. 只在节点侧（Python）检测 | 否决 | 节点看不到工具入参完整语义（中枢丢弃 `tool`、detail 无 input），判不准；且交互式 TUI 不受益 |
| D. 只做 Doom Loop、不做重试 | 否决 | 一次瞬时 5xx 就让整个任务失败，整体成本更高 |

#### 验收方式

- `UT-M3-1` 跨 step 连续 3 次同工具同参 → 该回合不执行 + 提示注入（**不是**直接失败）；
- `UT-M3-2` 入参递增 → 不触发（防误伤）；
- `UT-M3-3` 可重试错误在**上限内**重试成功；超过上限 → 明确失败并带原因；
- `UT-M3-4` `ContextOverflow` 不重试（走压缩）；
- `UT-M3-5` `delay()` 纯函数：`retry-after` 头优先、指数退避、封顶；
- 回归：`packages/core` `bun typecheck` + `bun test`（沙箱环境类失败集合不变）。

#### 待用户确认

1. **打转命中后的行为**：(a) 跳过该调用 + 提示换做法、模型继续（推荐）；(b) 结束该回合并强制文本作答；(c) 直接让任务失败。
2. **重试上限**：最大尝试 **5** 次 + 总时长 **60s**（推荐），是否合适。
3. **窗口大小**：**3**（与 V1 一致，推荐）。
4. 无进展信号是否纳入本模块（建议：只记信号，不做行为）。
→ **2026-09-28 用户确认**：打转命中 → **跳过该调用 + 注入提示让模型换做法**（`(a)`）；
   重试上限 **5 次 / 总时长 60s**；无进展信号 **纳入但只记信号**；窗口大小 **3**。

### M3 · 实施结果（2026-09-28 **已完成**）

- **新增 2 个源文件**：
  - `core/src/session/retry.ts`：`delay()`（provider 显式延迟优先，否则 `2000×2^(n-1)` 封顶 30s）
    与 `decide()`（先判可重试，再判 **5 次**上限与 **60s** 时间预算）；用 `LLMError.retryable` / `.retryAfterMs`
    读取错误面已有的信息，不重新解析响应头。
  - `core/src/session/doom-loop.ts`：`threshold=3` 的**跨 step** 滚动窗口，签名 = `工具名 + JSON(input)`；
    命中返回 `hint`。
- **改 1 个源文件**（`core/src/session/runner/llm.ts`）四处：imports、层状态、`tool-call` 分支的打转拦截、
  provider 流的有上限重试循环 + `Step.Ended` 的无进展信号。
  两个实现要点：① 打转拦截**必须仍回一条工具结果**，否则下一轮 provider 请求会因未应答的工具调用而失败；
  ② 重试**仅在"本轮尚未发布任何输出"时**进行（`!publisher.hasAssistantStarted()`），避免重复流；每次尝试前重置
  `overflowFailure`，否则上一轮的 overflow 标记会让重试的流被整段跳过。
- **验证**：`bun typecheck` 退出码 0；`packages/core` **1003 通过 / 7 跳过 / 99 失败**
  （99 项全为既有沙箱环境类，**非环境类失败为空**）。新增 13 项用例：`session-retry.test.ts` 7 项、
  `session-doom-loop.test.ts` 4 项、`session-runner.test.ts` 2 项集成。
- **已知测试缺口**：runner 级"重试后成功"用例未写（假客户端 `streamFailure` 对每次 `stream()` 都生效，
  无法脚本化"先失败后成功"），该语义由 `decide/delay` 纯函数用例覆盖；无进展信号为日志级、未写断言。
- **部署前提**：tilacode 需重建二进制才在生产生效。

---

### M1b · 前置勘察结论（2026-09-28 完成，**结论：需移植，非接线**）

| 通道 | LSP 现状 | 证据 |
|------|---------|------|
| V1（`packages/opencode/src/lsp/`） | **完整实现**：`client.ts` / `diagnostic.ts` / `language.ts` / `launch.ts` / `lsp.ts` / `server.ts`；17 个文件引用 | 目录列举 + `lsp.ts:152`（`Effect.logInfo("all LSPs are disabled")`） |
| **V2（`packages/core/src/`）** | **只剩配置 schema**（`config/lsp.ts`、`v1/config/lsp.ts`），**无任何 LSP 运行时** | `core/src` 内仅 10 个文件提及 lsp，且均为配置/测试；**无 `lsp/` 目录** |

- 用户截图中的 `LSPs are disabled` 出自 TUI 侧栏（`packages/tui/src/feature-plugins/sidebar/lsp.tsx:25`）；
  V2 下**没有可上报"已启用"的 LSP 服务**，故恒为 disabled —— 与"运行时缺失"一致（**不是"存在但被禁用"**）。
- **影响**：M1b 的原假设"接线即可"**不成立**；实际等于**从 V1 移植一套 LSP 运行时**
  （进程启动、协议握手、诊断收集、配置解析、生命周期），规模明显大于原估的 M。
- **备选方案**：
  | 方案 | 说明 |
  |------|------|
  | A. 移植 V1 LSP 运行时 | 收益最完整（编辑后秒级增量诊断），但规模最大 |
  | B. 替代：编辑后对**改动文件**跑轻量检查 | 便宜，但每次都要起进程（tsc 等），且与 M1a 提示词纪律、M4 分层验证职责重叠 |
  | C. **推迟 M1b**，优先做 M4（验收闭环） | M4 收益更高、不依赖 LSP；M1b 留作后续独立立项 |
- **当前处理**：按 **C（推迟）** 登记，不阻塞 M4；实施与否待用户决定。
  → **2026-09-28 用户决定：推迟 M1b，改做 M4。**

---

### M4 · 实施范围（2026-09-28 勘察后登记，**待用户确认后动代码**）

**目标**：把 `checklist.md`（已生成、已下发、已落盘，但**无人读回**）变成**机器可判的验收闸**，并根治"假成功"——静默即 completed 的现状（`sse_consumer._finish_completed()` 在 `:378-381` 直接置 `completed`）。

#### 勘察结论（代码级）

| # | 结论 | 证据 |
|---|------|------|
| **V1** | 判据载体是 `TaskSpecification{spec, tasks, checklist}` —— **三个自由文本字段，无结构化判据** | `hub/app/schemas/task_building.py:11-16` |
| **V2** | 三件套已全程打通：中枢生成 → `tasks.specification` JSONB → 派发透传 → 节点写 `specs/<标题>-<id8>/{spec,tasks,checklist}.md` | `workspace.py:43-50,718-723` |
| **V3** | **完成判定是纯静默式**：`step.ended` 后 30s 无新事件 + 轮询 `session/active` 不活跃 → `completed`；`_dispatch_result` 仅按 `result.status == "completed"` 分支，**不读 `specification`** | `sse_consumer.py:378-385`、`task_bridge.py:879` |
| **V4** | **`TaskStatus` 已有 `review`（待人工确认）** | `hub/app/models/task.py:24-36`（另有 `requeue_pending`） |
| **V5** | 节点**没有任何测试/lint/build 执行能力**（唯一 `create_subprocess_exec` 是启动 tilacode 自身） | 审计 A4/A6 |

#### 决策（拟）

1. **判据结构化**：`TaskSpecification` 增 `verify: list[{id, 描述, 命令, 期望, 必过, 层级}] | None`；
   `spec_builder` 在生成 `checklist.md` 的同一轮**同时**产出结构化判据（解析失败则降级为"无判据"，不阻塞建任务）；
   `checklist.md` **保留**给人看。传输沿用既有 `specification` 透传；节点 `_SPEC_DOC_PATHS` 增 `verify.json`。
2. **节点分层执行器**（新模块 `app/executor/verify_runner.py`）：按 L0 类型/语法 → L1 静态 → L2 单测 → L3 构建 → L4 端到端；
   判据来源优先级 **`verify.json` > 项目自带配置（`package.json` scripts / `pyproject.toml` / `Makefile`）> 按改动文件类型推导的最小检查**；
   每层有超时预算（30s/60s/5min/10min/20min）；输出取**尾部**并给出可读失败摘要。
3. **完成闸（用户明确"怕假成功"）**：
   - 必过层全绿 → `completed`；
   - 有红 → 进入 ④（steer 自修 ≤5 轮），用尽仍红 → `failed` 且把**未过判据**写进失败原因；
   - **无法验证 → 置 `review`（复用既有状态，零迁移）**并在任务摘要显著标记，**不得**呈现为正常完成。
4. **可观测**：`detail.verify = {passed, failed, unverified, results[]}` 随 `task_progress` 上报；前端任务摘要显示。

#### 备选方案

| 方案 | 结论 | 原因 |
|------|------|------|
| A. 上述四步；"无法验证"复用 `review` | **采纳** | 零迁移；`review` 语义（待人工确认）正好匹配 |
| B. 新增 `completed_unverified` 状态 | 否决（备用） | 需枚举 + DB 约束 + 前端 + 调度排除，成本高且语义与 `review` 重叠 |
| C. 判据只留 `checklist.md`，让模型"读文档自己判断" | 否决 | 不可复现、不确定、烧 token；也无法做闸门 |
| D. 验收在中枢侧执行 | 否决 | 工作区与产物都在节点；违背"中枢只编排" |
| E. 由节点模板的 `environment-check.md` 机制承接 | 否决 | 那只是提示词级环境检查，无判定与闸门 |

#### 验收方式

沿用 Harness 已定义的 `UT-M4-1~8` + `IT-A3` + `E2E-A1`（含守卫用例 `UT-M4-8`：**不存在"无验证结论却 completed"的路径**）。
回归：节点 `pytest` + `ruff`；中枢 `pytest` + `ruff`；前端 `typecheck`/`lint`/`check:colors`/`vitest`/`build`。

#### 待用户确认

1. **「无法验证」的落法** —— **2026-09-28 用户提出更优方案（待最终确认）**：
   用户指出「不要静默停在某个状态等人，应当**在中枢对话页面直接告诉用户、由用户决定**，走此前设计的 `@节点` 对话链路」。
   澄清事实：`review` **不是**"无法验证"状态，它是既有的「方案待人工确认」（ADR-038）；当前系统**没有**"无法验证"语义，属 M4 新建。
   证据补充：`review` 属 `ACTIVE_STATUSES`（非终态，`models/task.py:43-48`），且超时兜底**只覆盖 `running`**
   （`scheduler.py:648,698,711`）→ **单纯复用 `review` 会一直等下去**（不静默：`dashboard_service.py:244,262` 有告警，
   `plan_review_service.py:49` 有操作入口，但没人点就永不结束）。
   **拟采纳的用户方案（「主动找人 + 就地问」）**：
   ```
   验收跑完 → 有红 / 无法验证
      ├─ 先自修（④，≤5 轮，走 steer 回灌）
      └─ 修不动 → 中枢在该任务对话区主动发消息（未过判据 + 失败摘要 + 可选动作）
                → 用户在对话里回复（走同一 steer 通道；用户也可主动 @ 追问）
      └─ 兜底：N 小时无回应 → 自动 failed 并写明原因（防长期无人时永久悬挂）
   ```
   **顺序依赖**：该方案依赖 **M5a 的 `hub.task_steer` 通道 + M5c 的会话视图**（二者**尚未建**）。
   故建议调整为 **先做 M5a-steer（小）→ 再做 M4 全量**；M5a-steer 同时解锁 M4④ 与后续预算闸的软阈值收尾。
2. **分层命令来源优先级**是否认可（`verify.json` > 项目自带配置 > 按文件类型推导）。
3. **M4④（失败自修）依赖 M5a 的 steer 通道**：先做 M4①②③、④ 待 M5a（推荐）；还是先把 M5a 的 steer 提前
   —— **倾向后者（与第 1 条同因）**。
4. **必过层定义**：建议 L0/L1 必过；L2 若项目存在则必过；L3/L4 由 `verify.json` 显式标 `必过`。

#### 用户确认结果（2026-09-28）

- 第 1 条：**采纳用户方案**——"无法验证/验收红"不静默停状态，而是**由中枢在任务对话区主动告知用户并等其决定**（走 steer 通道）；**并加 24h 超时兜底**（无回应 → 自动 `failed` 并写明原因）。
- 第 3 条：**先做 M5a-steer，再做 M4 全量**（顺序已调整）。
- 第 4 条：**L0/L1 必过；L2 存在则必过；L3/L4 由 `verify.json` 标 `必过`**。

---

### M5a-steer · 实施范围（2026-09-28 登记，**先行实施**）

**目标**：打通"中枢 → 节点活动会话"的**定向发言**通道。它是三件事的共同前置：① M4 的"主动找人 + 就地问"；
② M4④ 的失败自修 ≤5 轮回灌；③ M5b 预算闸的软阈值收尾。**规模刻意做小**：节点侧 `send_prompt(delivery="steer")`
能力**已存在**（`tilacode_client.py:282-287`），缺的只是"事件 + handler + 中枢入口"。

**中枢侧**
1. `app/schemas/ws_messages.py`：新增 `HUB_TASK_STEER = "hub.task_steer"` 与 payload 模型（`{task_id, text, by?, reason?}`）。
2. `app/services/task_service.py`：新增 `steer_task(session, task_id, text)` —— 仅允许对 `assigned`/`running` 任务发言；
   取 `assigned_node_id` 经 `agent_node_ws_service` 推送；失败（节点离线）明确报错而非静默丢弃；写一条审计（`tool_call_logs`）。
3. `app/api/v1/endpoints/tasks.py`：`POST /api/v1/tasks/{task_id}/steer`（体 `{text}`）。
4. 前端 `@节点` 输入框属 M5c；本步先提供后端入口 + 一个最简入口以便验证。

**节点侧**
1. `app/client/hub_client.py`：`_TASK_STEER_EVENT = "hub.task_steer"` + `_handle_task_steer(payload)` → `bridge.steer(...)`；
   **未知事件必须忽略**（旧节点兼容）。
2. `app/executor/task_bridge.py`：`steer(task_id, text)` → 复用既有 `_client.send_prompt(session_id, text, directory=...)`
   （`delivery="steer"` 已是默认）；失败记 warning、不崩、不阻塞任务。
3. **上行确认**：发出后立刻上报一条 `agent.task_progress`（`message="已收到中枢指令：<摘要>"`），让中枢**马上看到指令已送达**
   （完整会话镜像仍归 M5c）。

**验收**
- 单测：中枢 `steer_task` 对非运行态任务拒绝、对未分配节点拒绝；节点 `_handle_task_steer` 正确调用 `send_prompt`；payload 校验。
- `IT-A4`（已有定义）：中枢发 `hub.task_steer` → 节点转 `send_prompt(steer)` → 会话收到新输入。
- `E2E-A6`（部分）：真实链路上对运行中任务发言 → 观察到"已收到中枢指令"上报且节点活动继续。
- 旧节点兼容：未知事件被忽略、无异常。

**明确不做（本步内）**：完整会话镜像与对话 UI（M5c）、用量落库（M5a 的另一半）、预算闸（M5b）、“无法验证”主动通知的全流程（M4）。

### M5a-steer · 实施结果（2026-09-28 **已完成**）

- **中枢（4 文件）**：`HUB_TASK_STEER` 事件常量；`TaskService.steer_task()`（仅 `assigned`/`running`；未分配节点/空文本/
  **节点离线**均明确报错；**不改任务状态**；写审计日志）；`TaskSteerRequest`（1..4000 字符，`extra="forbid"`）；
  `POST /api/v1/tasks/{task_id}/steer`（不允许时 409 `TASK_STATE_NOT_ALLOWED`）。
- **节点（2 文件）**：`_TASK_STEER_EVENT` + 分发分支 + `_handle_task_steer()`（校验 payload、任务不在本节点则忽略、
  `bridge.steer` 异常不外抛）；`TaskBridge.steer()` 复用 `interrupt` 的会话归属约定 `ses_task_{base_task_id or task_id}`，
  调 `send_prompt(delivery="steer")`，并用**已知进度值**回一条「已收到中枢指令：…」作**送达确认**
  （新增 `_last_progress_value` 跟踪与清理；未知进度不发送，避免把中枢进度条打回 0）。
- **验证**：中枢新测试 **6/6**（含"发言后 `status` 与 `progress` 均不变"这一与 cancel 的本质区别断言）；
  节点 `test_hub_client_reconnect.py` **22/22**、节点全量 **187 通过**；本次涉及文件 `ruff` 全过。
- **兼容性**：旧节点遇未知事件走 `hub_client.py:584-585` 的 `unhandled_event` 调试日志 → 安全忽略（已核实无白名单拦截）。
- **已知缺口**：① `TaskBridge.steer` 的桥接级用例（会话 ID 约定 + 送达确认）未写；
  ② `POST /{task_id}/steer` 的 API 级用例未写（服务级已覆盖）。
- **下一步**：按已确认顺序进入 **M4 全量**（判据结构化 + 分层执行 + 完成闸 + "红/无法验证 → 主动对话找用户" + ④自修 ≤5 轮）。
  > **顺序修正（2026-09-28 后续）**：M4 为 L 规模，先插入零依赖的 **M5a 用量落库**（本轮完成）与 **M2b**（S），再进 M4。

### M5a 用量落库 · 实施范围与结果（2026-09-28 **已完成**）

> **登记方式说明**：本模块的实施范围与结果在同一轮内登记。原因：它是 M5a-steer 的**剩余一半**（用户 2026-09-28 决定拆分），
> 行为契约在 ADR-060 P0（日志口径）与 D5.3（用量曲线需求）中已完全确定，改动为"同一次解析结果多写一张表"的原子步骤，
> 不存在需要用户裁决的行为分叉。**如需回退，回退点为下面的 3 处文件改动 + 1 个迁移。**

**目标**：节点经 `agent.task_progress` 上报的 `detail.tokens`/`detail.cost` 此前**只写日志**
（`task_progress_cache_metrics`），无持久化 → 用量曲线、预算闸（M5b）、经验记忆（M6）都缺数据源。

**实施范围**
1. `app/models/tool_call_log.py`：`ToolCallLogKind` 新增 `LLM_USAGE = "llm_usage"`；`chk_tool_call_logs_kind` 白名单同步补 `'llm_usage'`。
2. `alembic/versions/20260928c_llm_usage_kind.py`：只放宽 CHECK（drop→create），down_revision = `20260928b_requeue_pending`（当时唯一 head）。
3. `app/services/task_event_handler.py`：`_log_cache_metrics()` 重构为 `_parse_usage_metrics()`（纯解析，返回归一整数字典）
   + `_record_cache_metrics()`（同源：仍记原有日志事件，**并**经 `tool_call_log_service.record(kind="llm_usage")` 落库）。
4. 不改节点：`detail` 结构（tokens/cost/files）由 `event_translator._step_detail` 早已提供，本模块只补中枢消费侧。
5. **`tasks.progress_tool`（Harness `UT-M5-4` 的另一半）**：节点自 ADR-055 起就在上报 `tool`（`AgentTaskProgressPayload.tool`，≤64），
   中枢处理器**从未消费**、上报即丢弃 → 新增列（迁移 `20260928d_task_progress_tool`）+ `_sanitize_progress_tool()`（非法类型忽略、
   超长截断 64）+ 响应 schema 暴露，使「当前正在执行哪个工具/命令」在中枢可查（M5c 会话视图与告警依赖此维度）。

**关键设计取舍**
- **落库走 `tool_call_log_service.record`（独立会话 + 内部 `except` 兜底）**，而不是塞进进度更新的同一事务：
  指标是旁路，写失败只告警，绝不回滚 `task.progress`（与 ADR-047 采集原则一致）。
- **零样本不落库**：`input == 0 and cache_read == 0`（首轮无缓存）返回 `None`，不产生噪音行。
- **`session_id` 不猜测**：桥接层会话名约定是 `ses_task_{base_task_id or task_id}`，子任务场景下猜测会失真，
  故只写 `task_id` + `args_summary={"agent_id": node_id}`，保持数据诚实。
- **`name="agent_llm_usage"`**：节点 `detail` 里没有模型名（`_step_detail` 只带 tokens/cost/files），
  不编造模型名，用稳定事件名；模型维度待节点侧补字段后再填。

**验收与证据**
- 新增单测 5 条（`tests/services/test_task_event_handler.py`）：`UT-M5a-3` 落库字段逐项断言（含 `hit_rate=0.8`、`cost`、`files_touched`、`tool`）、
  `UT-M5a-4` 无 `detail`/零 token/结构非法三种输入均**不落库**、`UT-M5a-5` CHECK 白名单必须覆盖枚举全部取值（防枚举与约束两处手写漂移）、
  `UT-M5a-6` `progress_tool` 落库、`UT-M5a-7` tool 非法类型忽略 + 超长截断且进度照常落库。
- 实测：该文件 **41 passed**；`test_tool_call_log_service.py` + `test_task_steer.py` + `test_task_cancel.py` **20 passed**；涉及文件 `ruff` 全过。
- **库级实证**（不只模型层）：`alembic_version = 20260928d_task_progress_tool`；`tasks.progress_tool varchar(64)` 与
  「含 `llm_usage` 的 CHECK」经 `information_schema.columns` / `pg_constraint` 查得；`alembic heads` 单一 head = `20260928d_task_progress_tool`。
- **真实落库探针**：对真实库执行 `tool_call_log_service.record(kind="llm_usage", …)` → 实际插入 1 行、字段正确、探针数据已删除
  （证明约束接受新 kind，而非仅 Pydantic/ORM 层通过）。
- **勘察修正**：alembic 命名约定会把字面约束名再包一层 → 实际 DB 名为 `chk_tool_call_logs_chk_tool_call_logs_kind`
  （既有迁移同样如此）；故 `UT-M5a-5` 按 `sqltext` 内容定位约束，不按名字匹配。

**明确不做（本步内）**：预算闸判定逻辑（M5b）、用量曲线与告警前端（M5c）、`llm_usage` 的节点侧新增字段（模型名/轮次）、
历史日志行的回填（无数据源，不回填）。

**下一步**：**M2b 中枢下发权限策略**（S）；其后 M5d（中枢待办）→ M4 全量 → M5b → M5c → M6 → M7。

### M2b · 实施范围（2026-09-28 登记；**Q1~Q4 已由用户裁决，同日实施完成**）

**目标**：把「权限策略」的**唯一真源**从中枢下发到节点，使 D5.1 的「工作区内 allow / 工作区外一律 deny（含 `.env`）、例外只走显式白名单」
在**节点本地没有策略时也成立**，且每次自动判定可审计。M2a 已完成节点侧兜底（模板去掉 blanket allow、`permission_mode="unattended"`、
拒绝原因可读），M2b 补「策略来源」与「判定受控」这两截。

**现状与缺口（本次勘察，均有行号实证）**

| # | 缺口 | 证据 |
|---|------|------|
| 1 | **派发载荷完全不含权限策略** | `scheduler.py:135-161` `_build_dispatch_message()` 只带 task_id/title/description/specification/mode/base_task_id/change_brief/output_dir/environment_requirements |
| 2 | **中枢没有通用权限策略的存储位** | `models/system_settings.py:57-61` 仅有 ADR-053 的 `terminal_permission_mode`（终端命令域），无通用策略列 |
| 3 | **节点策略写死在代码里** | `workspace.py:35-40` 模板硬编码 `"permission_mode": "unattended"`；`permission` 键/node 侧无白名单来源 |
| 4 | **节点对权限询问无条件放行**（绕过 deny 的实测风险点） | `task_bridge.py:1347-1379` `_handle_permission_event()` 对每个 `permission.v2.asked` 调 `permission_reply(...)`，而 `tilacode_client.py:358-368` 的默认实参是 `reply="once"` → **任何到达 ask 的请求都被放行一次**，无策略判断 |
| 5 | 自动判定无审计 | 现仅 `permission_auto_replied` 一条节点日志（`task_bridge.py:1367-1370`），无「依据哪条策略、命中/未命中白名单」 |

**规则语义（本次勘察确认，决定白名单形态）**
- 配置侧 `permission` 是「工具(action) → 效果 或 {资源(resource) → 效果}」两级映射（`v1/config/permission.ts:17-36`），
  迁成规则数组时**按 key 顺序追加**（`v1/config/migrate.ts:75-92`），生效取 `findLast(...)`（`permission.ts:77-87`）→ **后写覆盖先写**；
  agent 级 `permission` 再 `Object.assign` 覆盖全局（`v1/config/agent.ts:77`）。
- `permission_mode="unattended"` 时 `ask` **直接判 deny**（`permission.ts:157-162, 203-209, 211-231`），
  且 `assert` 抛 `BlockedError`（M2a 已把可读原因回灌给模型，`tot` 工具侧 `Tool.permissionFailure`）。
- 结论：**白名单只能表达为「追加 allow 规则」**，绝不能出现 `"*": "allow"`；deny 不需要显式下发（默认即 deny）。

**推荐方案（S 规模；标 ⚠ 的为需用户裁决的分叉，见下方 Q1~Q4）**
1. **中枢存储**：`system_settings` 新增 JSON 列 `permission_policy`（单行全局），形如
   `{"mode": "unattended", "allow": [{"action": "read", "resource": "/usr/include/**"}, {"action": "bash", "resource": "git *"}]}`；
   NULL → 视为最严（`unattended` + 空白名单）。管理台编辑入口归 M5c/前端阶段，本轮提供 API + 默认值。
2. **下发生效**：`_build_dispatch_message()` 增加 `permission_policy` 字段（读取全局策略；节点按 payload 覆盖本地缓存）。
3. **节点落地**（`workspace.py`）：把策略写成工作区 tilacode 配置的 `permission_mode` + `permission`（**只追加 allow 条目**，逐条 `action → {resource → "allow"}`）；
   保留 M2a 的自愈（历史 `{"*": "allow"}` 仍被清除）。
4. **节点判定受控**（`task_bridge._handle_permission_event`）：先按策略判「该 action/resource 是否在允许范围」→ 命中才 `reply="once"`，
   否则 `reply="reject"` + 可读原因（让 agent 拿到明确拒绝而非静默/放行）。⚠ Q3
5. **审计**：中枢对「策略下发」写一条 `tool_call_logs`；节点对**每次**自动判定写结构化日志（含命中/未命中、策略版本/来源）。⚠ Q4
6. **兜底**（节点本地无策略、中枢不可达时）：⚠ Q2

**备选方案（不采纳的理由）**
- *把策略塞进 agent 提示词*：不是强制约束，模型可无视 → 违背 D5.1「一律 deny」。
- *节点侧维护策略副本并各自为政*：与 D5.1「**由中枢统一下发**」冲突，且多节点必然漂移。
- *直接下发 `"*": "allow"` + 靠 tilacode 内置规则兜底*：M2a 已实测这条路会把内置 guardrail 全部覆盖（`findLast` 后写胜出），故禁止。

**验收（待确认后细化到 Harness）**
- `UT-M2b-*`：策略序列化（只产出 allow 条目、不含 `"*"`）、空/NULL 策略降级为最严、payload 缺失时用缓存、判定命中/未命中两分支、
  拒绝时携带原因、审计字段齐全。
- `IT-A1` 扩展：中枢下发含白名单的策略 → 节点工作区配置出现对应 allow 条目且**无 blanket allow** → 白名单内路径可用、白名单外仍被拒。
- 兼容：旧节点忽略未知 payload 字段照常跑（不因缺策略而崩）。

**明确不做（本步内）**：管理台权限策略编辑 UI（归 M5c/前端）、命令级终端的 `manual/ai_auto/full` 联动（ADR-053 领域，另议）、
按 agent/按任务的策略继承树（⚠ Q1 若选 B 则纳入）。

#### 待用户裁决（Q1~Q4）——**已裁决（2026-09-28）**

- **Q1 策略来源与层级**：全局单条（推荐，S）／全局 + 任务级 override（M）／仅任务级。
- **Q2 兜底形态**：本地缓存 + 派发即权威、无策略按最严继续（推荐，S）／新增 `hub.permission_policy` 请求-响应事件对（M）／心跳捎带。
- **Q3 非白名单询问的处理**：回复 `reject` + 可读原因（推荐）／`reject` 无原因／维持 `once`（不推荐，等于绕过 deny）。
- **Q4 审计落点**：中枢记下发 + 节点记每次判定（推荐）／再把每次判定上行回传中枢落库（M+）。

> **裁决结果**：Q1 全局单条 · Q2 本地缓存 + 派发即权威（无策略按最严）· Q3 `reject` + 可读原因 · Q4 中枢记下发 + 节点记判定。
> 即四项均取推荐方案。

### M2b · 实施结果（2026-09-28 **已完成**）

**中枢（1 模型列 + 1 迁移 + 1 新服务 + 1 schema + 1 端点 + 2 派发点）**

| 文件 | 内容 |
|------|------|
| `app/models/system_settings.py` | 新增 `permission_policy`（JSONB，单行全局；NULL = 未配置 = 最严） |
| `alembic/versions/20260928e_permission_policy.py` | 新增该列（down_revision=`20260928d_task_progress_tool`） |
| `app/services/permission_policy_service.py`（新） | `default_policy()` 最严默认、`normalize_policy()` 归一（**丢弃 blanket allow**、非法条目逐条剔除、非法 mode 回落 unattended、条数/长度上限）、`load_policy()`、`save_policy()`、`audit_summary()` |
| `app/schemas/system_settings.py` | `PermissionPolicyAllowEntry` / `PermissionPolicyResponse` / `PermissionPolicyUpdate`（`extra="forbid"`） |
| `app/api/v1/endpoints/settings.py` | `GET` / `PUT /api/v1/settings/permission-policy` |
| `app/services/scheduler.py` | 自动派发：整批只读一次策略 → 写入每条 `payload.permission_policy`；审计行（`kind=dispatch`）追加策略摘要 |
| `app/services/task_service.py` | 手动派发：同样挂载策略 + 审计追加 |

**节点（1 新模块 + 4 处接线）**

| 文件 | 内容 |
|------|------|
| `app/executor/permission_policy.py`（新） | `normalize_policy()`（None=沿用缓存／非法=最严）、`apply_to_config()`（写 mode + **只追加 allow**、清除三种历史 blanket allow）、`effective_policy_from_config()`（回读生效策略）、`wildcard_match()`（**精确镜像** tilacode `Wildcard.match`：`\`→`/`、`*`→`.*`、`?`→`.`、Windows 忽略大小写、`"git *"` 可匹配裸 `git`）、`is_allowed()`、`deny_reason()` |
| `app/models/schemas.py` | `TaskDispatchPayload.permission_policy` |
| `app/executor/workspace.py` | `ensure_tilacode_config(dir, policy=None)` 应用策略并**返回生效策略**（无策略=沿用缓存；文件不存在=模板最严） |
| `app/executor/task_bridge.py` | `_prepare_session` 归一化并下发策略、登记 `_session_policies`；`_session_policy()`（内存优先，其次**回读工作区配置**）；`_handle_permission_event` 改为**策略驱动**：命中白名单→`once`，否则→`reject` + 可读原因；任务收尾清理 |
| `app/executor/tilacode_client.py` | `permission_reply(..., message=None)` 支持携带拒绝原因（服务端 `handlers/permission.ts:74` 已透传） |

**关键语义与安全不变量**
- **修掉了实测可绕过的放行口**：节点此前对**每个**权限询问无条件回 `once`（`tilacode_client.permission_reply` 默认实参），
  等于把「工作区外 deny / `.env` 拒绝」等护栏全部绕过；现在未命中白名单一律 `reject` 并带原因。
- **fail-closed**：`action`/`resources` 缺失或不可评估时也拒绝（宁可多拒不可少拒）；`is_allowed` 不做任何放宽性兜底
  （曾考虑加 `fnmatch` 兜底，因其 `[...]` 语义比 tilacode 宽而被**否决并删除**）。
- **blanket allow 三处防线**：中枢归一化丢弃、节点应用时清除、模板本身不含（M2a 已改）。

**验收与证据**
- 中枢：`test_settings.py` **11 passed**（+4：默认最严、PUT/GET 一致、blanket 被丢弃、额外字段 422）；`test_task_lifecycle.py` **4 passed**
  （含 WS 端到端断言 `payload.permission_policy` 存在，覆盖 IT-A1 的下发半边）；涉及文件 `ruff` 全过。
- 节点：`pytest tests` **200 passed**（+13：`test_permission_policy.py` 7 项、`test_workspace.py` 3 项、`test_task_bridge.py` 3 项改写/新增）、`ruff` 全过。
- 迁移已在库生效（`system_settings.permission_policy` 列）。
- **环境噪声（登记）**：库中存在上轮中断测试残留的 `test-task-*` pending 任务，会让 `test_task_lifecycle` 的 WS 收到**别的任务**的派发
  而误报失败（与本次改动无关）；已清理该模式残留后复跑通过。真实用户 pending 任务**未动**。

**已知缺口（登记）**
1. 白名单的**管理台编辑 UI** 未做（本轮只有 API，归 M5c/前端）。
2. **节点模板 tarball 未重打**（G2）：节点侧改动要在生产生效必须重打并发布模板。
3. 任务级 override 未做（Q1 选了全局单条）。

**下一步**：**M5d 中枢编排器待办**（M 规模，依赖无）。

### M5d · 实施范围（2026-09-28 登记；**Q5 已裁决，同日实施完成**）

**目标（用户 B9 + D5.5 已定）**：任务摘要**下段 = 中枢编排器自己的待办**（全新能力）。上段仍是节点上报的现有 5 块
（状态 Tag / 标题 / 进度条 / 当前动作 / 子 Agent 待办），**一块不动、不串位**；要求**实时更新**。

**现状与缺口（本次勘察，均有行号实证）**

| # | 事实/缺口 | 证据 |
|---|----------|------|
| 1 | 中枢**没有**任何待办存储：`tasks.todos` 只存**节点上报**的 JSONB | `models/task.py:239-243`（注释「Agent 实时上报的待办列表」） |
| 2 | 技能注册表 23 个技能中**没有**任务级 todo 能力；唯一近似能力是**会话面板**式 | `skills/registry.py:41-65`；`deploy_plan.py:76-88` 把清单写进 `specification.tasks`（**会话**维度、非任务维度） |
| 3 | 技能上下文**没有 task_id**，但可经会话反查 | `skills/base.py:25-42`（`SkillContext.session_id`）；`models/task_building_session.py:66-68`（`TaskBuildingSession.task_id`） |
| 4 | 任务摘要渲染是**轮询**而非 WS 推送 → 「实时」= 下一轮询周期内可见 | `opc-web-console/src/components/TaskSummary.tsx:113-178`（6 区块，数据全来自 `task`）；`TaskNewRightPanel.tsx:199` + `hooks/usePolling.ts:119` |
| 5 | 任务详情 schema 由 ORM 直接校验（3 处）→ 加关系字段需同时改 3 处并用 `selectinload` 防异步懒加载 | `services/task_service.py:368,456,556` 的 `TaskResponse.model_validate(task)` |

**推荐方案（M 规模）**
1. **存储：新表 `task_orchestrator_todos`**（与节点 `tasks.todos` **物理分离**）：
   `id / task_id(FK,index) / seq / content(≤500) / status(pending|in_progress|completed) / source / completed_at / created_at / updated_at`，
   `status` 带 CHECK。**选表而非 JSONB 列**的理由：需要逐项「推进/完成」的**行级操作**与稳定 ID（前端勾选、后续审计），
   且不触碰 `TaskResponse.model_validate` 的 3 个校验点（见缺口 5）。
2. **服务**：`app/services/orchestrator_todo_service.py` —— `list/add/start/complete/update/remove`，
   约束：content 去空白后 1..500；单任务上限 50 条（超出报错而不是静默截断）；`seq` 追加在末尾。
3. **技能** `todo_write`（`app/skills/orchestrator_todo.py`，注册进 `SKILL_REGISTRY`）：
   动作 `add | start | complete | update | remove | list`；任务由 `context.session_id → TaskBuildingSession.task_id` 反查，
   **无绑定任务时明确失败**（"尚未绑定任务，请先创建/派发任务"），不静默写入。
4. **只读端点**：`GET /api/v1/tasks/{task_id}/orchestrator-todos`（**不改 `TaskResponse`**，理由见缺口 5）→
   前端在 `TaskNewRightPanel` 的轮询里一并取，渲染 `TaskSummary` **下段**（含空态、完成勾标、进行中 Tag）。
5. **审计与实时**：技能执行本身走既有 `skill_call` 链路；下段靠既有轮询刷新（与节点待办同机制）。
6. **上段保护**：`TaskSummary` 现有 6 区块代码与断言**不改动**，只在其后追加下段（新增 `data-testid`）。

**备选方案（不采纳的理由）**
- *复用 `deploy_plan` 写 `specification.tasks`*：那是**会话面板**维度，任务详情页看不到，且会污染规格三件套。
- *把中枢待办塞进 `tasks.todos`*：与节点上报**同字段**，两边互相覆盖（D5.5 明确要求物理分离）。
- *新建独立 WS 推送通道*：需要新增前端订阅与连接管理，收益仅是省一个轮询；本轮不做（M5c 若引入实时通道可并入）。

**验收（待确认后细化到 Harness）**
- `UT-M5d-1`：技能 `add/start/complete/update/remove/list` 六个动作各自的状态迁移与 `seq` 顺序；
- `UT-M5d-2`：**物理分离** —— 写入中枢待办后 `tasks.todos`（节点上报）不变；
- `UT-M5d-3`：无绑定任务 → 明确失败且**不写库**；
- `UT-M5d-4`：上限/空白/超长 content 的边界；
- `UT-M5d-5`：端点返回按 `seq` 升序、可空；
- `UT-M5d-6`（前端）：上段 5 块仍在（既有断言不动）+ 下段渲染中枢待办 + 中枢无待办时下段明确空态；
- `E2E-A8`：编排器对话中调用 `todo_write` → 任务摘要下段出现该项（真实链路）。

**明确不做（本步内）**：管理台/前端**编辑**中枢待办（见 Q5）、跨任务共享待办、把中枢待办下发给节点执行（那是 steer/dispatch 的事）、
待办变更历史留痕（只存最新态 + 时间戳）。

#### 待用户裁决（Q5）——**已裁决**

- **Q5 下段是否允许用户交互**：**只读展示**（推荐：编排器维护、前端只展示，与「只读镜像」哲学一致，本轮最小）／
  允许用户勾选完成与新增（前端 + 写接口，需定义与编排器的并发编辑语义，M+）／只读 + 允许删除项（折中）。

> **裁决结果**：**只读展示**。前端不提供勾选/新增/删除入口；`source` 字段预留 `user` 以便将来扩展。

### M5d · 实施结果（2026-09-28 **已完成**）

**中枢（1 新表 + 1 迁移 + 1 新服务 + 1 新技能 + 1 只读端点）**

| 文件 | 内容 |
|------|------|
| `app/models/task_orchestrator_todo.py`（新） | 表 `task_orchestrator_todos`（id/task_id/seq/content/status/source/completed_at + `TimestampMixin`），`status` 带 CHECK，`(task_id, seq)` 复合索引 |
| `app/models/__init__.py` | 注册 `TaskOrchestratorTodo` |
| `alembic/versions/20260928f_orchestrator_todos.py`（新） | 建表 + 2 索引（down_revision=`20260928e_permission_policy`） |
| `app/services/orchestrator_todo_service.py`（新） | `list/add/update/remove` + `render_lines`；content 1..500、单任务 ≤50、`seq` 追加、状态白名单、`completed_at` 同步、**跨任务 ID 过滤** |
| `app/skills/orchestrator_todo.py`（新） | 技能 `todo_write`（`add/start/complete/update/remove/list`）；任务由 `session_id → TaskBuildingSession.task_id` 反查，回退 `parent_task_id`；**未绑定任务明确失败且不写库** |
| `app/skills/registry.py` | 注册 `OrchestratorTodoSkill`（技能数 23 → **24**） |
| `app/schemas/task.py` | `OrchestratorTodoItem`（`from_attributes`）/ `OrchestratorTodoListResponse`（含 pending/completed 计数） |
| `app/api/v1/endpoints/tasks.py` | `GET /api/v1/tasks/{task_id}/orchestrator-todos`（任务不存在 404 `201001`） |

**前端（下段渲染，Q5 只读）**

| 文件 | 内容 |
|------|------|
| `src/types/task.ts` | `OrchestratorTodo` / `OrchestratorTodoListResponse` |
| `src/api/tasks.ts` | `fetchOrchestratorTodos(taskId)` |
| `src/hooks/useTaskPlanTracking.ts` | 同轮 `Promise.all` 拉任务详情 + 中枢待办；**下段失败不影响任务轮询**（保留上次结果）；终态停止轮询行为不变 |
| `src/components/TaskSummary.tsx` | 新增 `OrchestratorTodoSection`（标题「中枢待办」+ `已完成/总数` + 复用上段条目样式 + 明确空态）；**上段 6 区块代码未改动**，仅在其后追加 |
| `src/components/TaskNewRightPanel.tsx` / `src/pages/TaskNew.tsx` | 透传 `orchestratorTodos` |
| `src/pages/TaskNew.css` | 下段样式（`--border` 虚线分隔、`--text-secondary` 标签；**全用语义变量**，符合 ADR-052 颜色禁令） |
| `src/__tests__/components/TaskSummary.test.tsx` | +2 项：上段与下段并存且不串位（含 `1/2` 计数）、中枢无待办时下段明确空态 |

**关键设计取舍**
- **独立表而非 JSONB 列**：需要逐项「开始/完成/改/删」的**行级操作**与稳定 ID；同时避开 `TaskResponse.model_validate` 的 3 个校验点
  （加关系字段需同步改 3 处并 `selectinload` 防异步懒加载）。
- **独立只读端点而非并入任务详情**：同上，且端点天然可空（中枢未写待办 → 空列表，非 404）。
- **下段失败不清空**：下段是辅助信息，偶发取数失败不应把已有清单抹掉（与任务详情失败即重试的语义分开）。

**验收与证据**
- 中枢：`tests/skills/test_orchestrator_todo_skill.py` **8 passed**（顺序/状态迁移/**与节点待办物理分离**/未绑定任务不写库/内容与条数上限/非法 action/`parent_task_id` 兜底/状态白名单）；
  `tests/api/v1/test_tasks.py -k orchestrator` **2 passed**（升序 + 计数；空态 + 404）；涉及文件 `ruff` 全过；迁移已在库生效（`alembic_version = 20260928f_orchestrator_todos`）。
- 前端：`TaskSummary.test.tsx` **7 passed**（含 2 项新增下段用例）；颜色门禁对不同文件无新增违规。
- **前端基线（登记，非本次引入）**：`npx tsc -b` 有 **2 处既有错误**（`PlanReviewPanel.tsx` 缺 `requeue_pending` 映射、`useTaskPlanActions.ts` 的 `assigned_node_id: string|null` 不能赋给 `Task`）——
  均属**另一工作流（ADR-059 D5）在途未提交的改动**，`git status` 显示这些文件本就处于修改中；本次改动文件无新增类型错误。

**已知缺口（登记）**
1. 下段**只读**（Q5 裁决）：用户不能勾选/新增/删除；`source` 已预留 `user`。
2. 实时性依赖既有 **5 秒轮询**（与上段同机制）；独立 WS 推送未做（可并入 M5c）。
3. 待办**无历史留痕**（只存最新态 + 时间戳）。

**下一步**：**M4 全量**（L 规模；判据结构化 + 分层执行 + 完成闸 + 主动找人 + ④自修 ≤5 轮）。

### M4 · 实施范围（2026-09-28 登记，**待用户确认 Q6~Q8 后实施**）

**目标（用户已定 + D5.2/D5.4）**：任务**不许假成功**。
① checklist → 结构化判据 `verify.json`；② 节点**分层执行** L0→L4（每层超时 30s/60s/5min/10min/20min）；
③ **完成闸**：必过层全绿才允许 `completed`；④ 红/无法验证 → **中枢主动在任务对话区找用户决定**（+ 24h 无回应兜底 → `failed`）；
⑤ 失败**自修 ≤5 轮**。已定细则：**必过层 = L0/L1 必过**（L2 存在则必过；L3/L4 仅当判据显式标注必过）；
命令优先级 **`verify.json` > 项目自带配置（package.json scripts / pyproject / Makefile）> 按文件类型推导**；**无可用检查必须显式上报「无法验证」**，不得静默判成功。

**现状与缺口（本次勘察）**

| # | 事实/缺口 | 证据 |
|---|----------|------|
| 1 | 完成判定**只看「会话是否空闲」**，与验收无关 | `sse_consumer.py:378-385` `_finish_completed()` → `ConsumeResult(status="completed")`；`_await_until_idle_or_active()`（:259-274） |
| 2 | 上报完成前**没有任何验证执行点** | `task_bridge._dispatch_result()`（:922-975）：`completed` 直接 `on_completed(...)`（:966-973） |
| 3 | **同会话续跑**机制已存在，可直接复用于「自修回灌」 | `task_bridge._generate_change_summary()`（:977-999）→ `self._run_session(session_id, task_id, prompt, ...)` |
| 4 | 三件套以 **JSONB 整体**随派发下发 → 新增判据字段**无需改调度器** | `scheduler.py:150` `"specification": task.specification`；`models/task.py:159` 注释「三件套(spec/tasks/checklist)」 |
| 5 | 节点按 `_SPEC_DOC_PATHS` 逐字段落盘（当前 3 项，全按**文本**写） | `workspace.py:52-57`；`write_specification_docs()`（:683-690）；新增 `verify.json` 需支持 **JSON 序列化** |
| 6 | 中枢**没有**「等你决定」的可停状态；`review` 已被 **plan 审批**占用（`plan_review_service.py:49` 允许对 review/completed 动作） | 复用 `review` 会误入审批流 → 见 Q7 |
| 7 | 中枢已有「陈旧任务清扫」范式可承载 24h 兜底 | `scheduler.py:648,698,711`（现仅覆盖 `running`） |

**推荐方案（分两阶段）**

**M4①②③（判据 + 分层执行 + 完成闸 + 阻断上报）**
1. **判据结构化**：`TaskSpecification` 增加可选 `verify`（hub `schemas/task_building.py` + 节点 `models/schemas.py`），形如
   `{"checks": [{"layer": "L0|L1|L2|L3|L4", "name", "cmd", "cwd"?, "timeout_s"?, "must_pass"?}]}`；
   **向后兼容**：缺失 `verify` 时按「无判据 → 无法验证」处理（不得静默判成功）。
2. **落盘**：`_SPEC_DOC_PATHS` 增 `"verify": "verify.json"`，写入器对 `dict` 走 `json.dumps`，其余仍按文本写。
3. **节点执行器** `app/executor/verify_runner.py`：按 `verify.json` → 项目配置 → 推导 取命令；L0→L4 顺序执行；
   每层默认超时 30s/60s/5min/10min/20min（可被 check 覆盖）；捕获退出码 + 输出尾部（截断）；产出结构化报告。
4. **完成闸**：`_dispatch_result()` 在 `on_completed` 之前执行验证——必过层全绿才上报完成；报告摘要随完成回调带出。
5. **阻断上报**：红/无法验证 → 上报中枢进入「等用户决定」态（Q7 决定形态）；中枢侧 24h 扫描兜底 → `failed`（写明原因）。
6. **可观测**：每层结果（命令/退出码/耗时/输出尾部）落 `tool_call_logs`（`kind=agent_task_event`，避免新增 kind）。

**M4④（失败自修 ≤5 轮）**
7. 完成闸失败 → 节点在**同一会话**用 `_run_session` 回灌修复提示（失败命令 + 输出尾部 + 未过判据），重新执行分层验证；
   连续 5 轮仍不过 → 转「阻断上报」路径。每轮记结构化日志（轮次/未过判据/耗时）。

**备选方案（不采纳的理由）**
- *让 agent 在提示词里"自证通过"*：无外部判据 = 假成功温床，正是用户最担心的情形。
- *完成闸放中枢侧（节点只上报原始日志）*：判据要在工作目录里执行，跨机取日志再跑一遍等于把节点当哑终端，且丢环境。
- *用 `review` 复用「等你决定」*：会与 plan 审批流撞车（见缺口 6）。

**验收（详见 Harness）**：`UT-M4-1~8` + `IT-A3` + `E2E-A1`（本模块）；关键守卫——**必过层红则不得出现 `completed`**、**无判据必须 `无法验证`**、**每层超时按未通过计**。

**明确不做（本步内）**：把验证失败自动改代码（那是自修的副产品，不是本模块目标）；LSP 级诊断（M1b 已推迟）；
判据的图形化编辑器（先只读展示 JSON/markdown）。

#### 待用户裁决（Q6~Q8）——**已裁决**

- **Q6 判据由谁生成**：中枢编排器 LLM 生成 `verify.json`（推荐）／节点 AI 自行推导／中枢纯规则推导。
- **Q7 阻断态形态**：新增任务状态 `verify_blocked`（推荐）／复用 `review` + `review_kind`／不改状态只发消息。
- **Q8 自修执行者**：节点自行修复（推荐）／中枢经 steer 回灌／两者结合。

> **裁决结果**：Q6 **中枢编排器 LLM 生成** · Q7 **新增 `verify_blocked`** · Q8 **两者结合**（节点先自修 ≤5 轮，用尽后中枢介入找用户决定）。

#### M4 · 实施结果（2026-09-28/29 **已完成**，`E2E-A1` 待真实链路）

**中枢（10 文件 + 1 迁移）**
- `services/verify_plan.py`（新）：判据归一化（层白名单、条数/长度上限、**必过层规则固化** L0/L1 必过 · L2 存在即必过 · L3/L4 需显式、每层超时 30s/60s/5min/10min/20min）。
- `schemas/task_building.py` + `agents/spec_builder_prompts.py` + `agents/spec_builder.py`：`TaskSpecification.verify` 字段 + 判据轮提示词 + 第 4 轮生成与 `parse_verify_json()` 容错解析（**失败不丢三件套**）；`skills/task_creation.py` 创建任务时携带归一化判据、`deploy_plan` 保留判据字段。
- 判据随 `Task.specification`（JSONB）整体派发 → **调度器零改动**。
- `models/task.py`：`TaskStatus.VERIFY_BLOCKED` + `verify_report` / `verify_blocked_at` 两列 + `ACTIVE_STATUSES` 纳入；迁移 `20260928g_verify_blocked`（扩展 status CHECK，已在库生效）。
- `schemas/ws_messages.py`：`AGENT_TASK_VERIFY_BLOCKED` + `TaskVerifyBlockedPayload` + `TaskCompletedPayload.verify_report`。
- `services/task_event_handler.py`：`_handle_task_verify_blocked()`（置态 + 落报告/时刻 + **构建会话追加「等你决定」消息** + 审计）+ `_append_decision_message()`。
- `services/scheduler.py`：`fail_expired_verify_blocked_tasks()`（**24h 无回应兜底 failed**）+ 调度循环调用。
- `services/task_service.py` + `schemas/task.py` + `api/v1/endpoints/tasks.py`：`decide_verify_blocked()` 与 `POST /tasks/{id}/verify-decision`（`accept` / `retry`）。

**节点（5 文件）**
- `app/executor/verify_runner.py`（新）：`load_plan`（优先级 `verify.json` > 项目配置 > 推导 > 无）、`from_project_config`、`derive_checks`、`run_plan`、`_run_check`（超时/非 0/异常**一律按未通过**）、`VerifyReport`（含输出尾部与必过失败清单）。
- `workspace.py`：`verify.json` 落盘（dict → JSON）+ 协议字段；`models/schemas.py` 协议字段。
- `task_bridge.py`：**完成闸** `_run_completion_gate()`（必过层全绿才 `completed`；红 → 自修 ≤5 轮后复验；**无法验证 → 不自修直接阻断**）+ `on_verify_blocked` 回调 + `on_completed(verify_report=)`。
- `hub_client.py` / `main.py`：`report_verify_blocked()` 与回调接线。

**验收与证据**
- 中枢：`tests/services/test_verify_plan.py` **6 passed**（必过层默认/显式覆盖/超时上限/非法剔除/条数上限/容错解析）；
  `tests/services/test_task_verify_blocked.py` **6 passed**（阻断处理器置态+对话区消息+审计/非执行态忽略/**24h 兜底**/未超时不动/`accept`/`retry` steer）；`ruff` 全过；迁移已在库生效。
- 节点：`tests/test_verify_runner.py` **12 passed**（判据优先级四级、真实命令通过/失败、**超时按未通过**、非必过失败不阻断、无判据 unverifiable、payload 形状）；
  `tests/test_task_bridge.py` **+4 passed**（闸门四路径：绿→放行不修、红→修后通过、红→5 轮用尽阻断、无判据→直接阻断）；`ruff` 全过。
- **完成闸生效的直接证据**：接线后节点套件出现 7 个「完成路径」用例失败（无判据 → 无法验证 → 阻断）；将该文件闸门替换为「总是放行」夹具后回归全绿，闸门行为改由上述专门用例覆盖。
- **环境说明**：分层执行器的真实子进程用例在本机沙箱下需放宽（`spawn EPERM`，已按规则升级一次执行）；生产节点进程无此限制。

**已知缺口**
1. ~~前端 `verify_blocked` 状态标签与决策按钮~~ **已补齐（2026-09-29）**：`TaskSummary` 新增阻断决策区（橙色标签「待验收决定」+ 未通过必过项清单 + 「继续修复 / 接受现状」按钮）、
   `decideVerifyBlocked()` API、`TaskNew` 决策处理（决策后立即重查）；`TaskSummary.test.tsx` **9 passed**（含 2 项决策 UI 用例）。
2. `IT-A3`（分层验证集成）与 `E2E-A1`（真实链路）未执行。
3. 自修多轮上下文细化（避免重复踩同一坑）留待优化。

**下一步**：**M5b 预算闸**（80% 软 / 100% 硬）。

### M5b · 实施范围（2026-09-29 登记；**Q9~Q11 已裁决，同日实施完成**）

**目标（D5.4 已定）**：任务级**预算闸**——**80% 软**（发收尾指令，让子 Agent 收敛并留好现场）、**100% 硬**（停止任务）。
目的：防「白烧 token / 无限打转」把成本拖爆，同时保留「软着陆」而不是一超限就砍。

**现状与缺口（本次勘察）**

| # | 事实/缺口 | 证据 |
|---|----------|------|
| 1 | 中枢**没有任何预算/限额代码**（现有「预算」字样都与本模块无关） | `core/config.py:173,182,204`（技能超时预算）、`skills/bt_deploy_prompts.py:37`（提示词里的调用次数建议） |
| 2 | **成本维度结构性缺失**：V2 的 `cost` 恒为 0 → 只能用 **step / token** 度量 | ADR-060 P4（`runner/llm.ts` `cost: 0`）；`tool_call_logs(kind=llm_usage)` 已落 `input/output/reasoning/cache_read/cache_write`（M5a） |
| 3 | 复用语件齐备：软 → `steer_task()`、硬 → `cancel_task()`、现场 → `chain_root()`/`build_handoff_payload()`、周期 → 调度循环 | `task_service.py`（steer/cancel）、`task_handoff_service.py:66,182`、`scheduler.py` 循环（已挂陈旧清扫与验收兜底） |
| 4 | 触发必须**幂等**（否则每轮循环都会重复发指令/重复取消） | 需在任务上记录状态位（见推荐方案） |

**推荐方案（M 规模）**
1. **配置**：`system_settings.budget_policy`（JSONB，全局默认）：`{max_steps, max_tokens, soft_ratio: 0.8}`；
   任务级 override：`tasks.budget`（JSONB，创建任务可选指定；NULL → 用全局）。API：`GET/PUT /api/v1/settings/budget-policy`。
2. **度量**：按任务聚合 `tool_call_logs(kind='llm_usage')` → **steps = 行数**、**tokens = Σ(input+output)**（cache_read 不计入总量，单独观测）。
   `max_steps`/`max_tokens` 任一超阈值即触发（见 Q11）。
3. **触发（幂等）**：调度循环每轮扫描执行中任务：
   - 用量 ≥ `soft_ratio` 且未软通知 → `steer_task` 发**收尾指令**（尽快收敛、写 `project_context.md`/本轮修改说明、不要再开新方向），记 `budget_state='soft_notified'`；
   - 用量 ≥ 100% 且未硬停 → `cancel_task`（硬停），记 `budget_state='hard_stopped'`；
   - 两阈值各自**只触发一次**（新增 `tasks.budget_state` 列）。
4. **审计**：软通知写 `tool_call_logs(kind='steering')`（它就是一次 steer）；硬停复用 `cancel_task` 既有审计并追加预算原因。
5. **可观测**：任务响应暴露 `budget_state`/用量百分比（供前端/M5c 用量曲线复用）。

**备选方案（不采纳的理由）**
- *等 `cost` 可用再做强预算*：V2 结构性为 0，等不到；step/token 已能覆盖「打转」与「烧 token」两类问题。
- *一超限就硬停*：砍掉正在收尾的任务，现场丢失更贵（用户明确要软/硬两档）。
- *把预算判断放节点侧*：节点只看得见自己的会话，跨段/跨重派的总量只有中枢掌握（且硬停本来就要中枢发指令）。

**验收（详见 Harness）**：`UT-M5-3`（80% 触发 steer、100% 触发 cancel、阈值可配）+ 幂等（同一阈值不重复触发）+ `E2E-A2`。

**明确不做（本步内）**：用量曲线与预算可视化（M5c）、按模型/按技能的差异化预算、节点侧本地预算兜底。

#### 待用户裁决（Q9~Q11）——**已裁决**

- **Q9 配置层级**：全局默认 + 任务级 override（推荐）／仅全局／仅任务级。
- **Q10 软阈值行为**：只发收尾指令、不停任务（推荐）／发指令并**暂停任务链下一段派发**／只告警不改行为。
- **Q11 度量维度**：**step + token 双维度（任一超限即触发）**（推荐）／仅 step／仅 token。

> **裁决结果**：三项均取推荐方案——**全局默认 + 任务级按字段 override** · **80% 只发收尾指令不停任务** · **step + token 双维度任一超限即触发**。

### M5b · 实施结果（2026-09-29 **已完成**）

| 文件 | 内容 |
|------|------|
| `app/services/budget_policy_service.py`（新） | `DEFAULT_BUDGET_POLICY`（60 轮 / 200 万 tokens / 0.8）、`normalize_policy`（边界夹取，**不会把预算设成 0**）、`resolve_budget`（任务级按字段覆盖）、`load_policy`/`save_policy`、`usage_for_task`（steps=用量行数、tokens=Σ(input+output)、cache_read 单列观测）、`evaluate`（ok/soft/hard 双维度取最大比值）、`wrap_up_instruction`、`budget_exceeded_reason`、`needs_action`（**幂等守卫**） |
| `app/models/system_settings.py` + `app/models/task.py` | `budget_policy`（全局 JSONB）、`tasks.budget`（任务级 override JSONB）、`tasks.budget_state`（NULL/soft_notified/hard_stopped） |
| `alembic/versions/20260928h_budget_policy.py` | 三列（down_revision=`20260928g_verify_blocked`），**已在库生效** |
| `app/services/scheduler.py` | `enforce_task_budgets()` + `_run_budget_sweep()`，挂进调度循环；软→`steer_task(收尾指令)`、硬→`cancel_task(reason=预算耗尽…)`；**每阈值只触发一次**；软通知未送达（节点离线）**不标记**、下轮重试 |
| `app/services/task_service.py` | `cancel_task(..., reason=...)`（默认仍为「用户手动取消」）；两处创建任务路径写入任务级 `budget`（归一化后） |
| `app/schemas/system_settings.py` + `app/api/v1/endpoints/settings.py` | `BudgetPolicyResponse/Update` + `GET|PUT /api/v1/settings/budget-policy` |
| `app/schemas/task.py` | 创建请求支持 `budget`；响应暴露 `budget` / `budget_state`（供前端与 M5c 用量曲线复用） |
| `tests/services/test_budget_gate.py`（新） | 7 项：边界夹取、按字段 override、双维度升降级、**幂等**、用量聚合、**80% 发指令不停任务 → 100% 强制停止**、软通知失败下轮重试 |

**验收与证据**：`tests/services/test_budget_gate.py` **7 passed**；`ruff` 全过；`import app.main` 通过；`alembic heads` 单 head（`20260928h_budget_policy`）且迁移已在库生效。
**勘察修正（重要）**：`tool_call_logs.result_summary` 是 **Text 列（JSON 字符串）**而非 JSONB——首版实现按 dict 解析导致真实库插入/读取失败，
由新测试当场暴露并修正（服务侧兼容 str/dict 两种形态，测试按 `json.dumps` 写入）。

**已知缺口**：① 预算使用率的前端展示与用量曲线归 **M5c**；② 按模型/按技能的差异化预算与节点侧本地兜底未做；③ `E2E-A2`（真实链路预算触发）未执行。

**下一步**：**M5c 只读镜像 + 会话视图 + 时间线 + 用量告警**（L 规模）。

### M5c · 实施范围（2026-09-29 登记；**Q12/Q13 已裁决，分两段实施**）

**目标（D5.3 + D5.5 已定）**：① 中枢侧**只读会话镜像**，可**离线回看**且**实时**可见「正在做什么、正在执行哪条命令」；
② 任务摘要**上段保现状**（现有 6 区块一块不动）；③ **进度时间线 + 用量曲线**；④ **用量/异常告警**；
⑤ `@节点` 直接对话（M5a-steer 后端已具备，补前端入口）。

**已裁决（用户 2026-09-29）**
- **Q12 镜像粒度 = 动作级摘要时间线**：记录阶段/工具名/命令摘要/用量点/校验报告，**不存原始工具输出全文**（体积与隐私都最小；失败输出尾部在验收报告里已有）。
- **Q13 告警 = 按需计算 + 仪表盘聚合**：阈值用已定基线（**缓存命中率 < 95.2%** / **连续 ≥3 次同签名调用** / **≥4 步无文件写入**），任务详情页实时算就出，仪表盘汇总复用现有 alerts 机制，**不引入外部通知渠道**。

**现状与缺口（本次勘察）**

| # | 事实/缺口 | 证据 |
|---|----------|------|
| 1 | 中枢只保留**最新**进度（`progress_stage/message/tool`），**没有动作历史** → 无法离线回看 | `models/task.py` 的 progress 三列是单值；`_handle_task_progress` 覆盖写入 |
| 2 | 用量点已落库（M5a），可直接做**曲线与命中率复核** | `tool_call_logs(kind='llm_usage')`（含 `files_touched`，预算闸已用它聚合） |
| 3 | 既有事件表可当日志时间线底座（派发/发言/验收/完成都在写） | `tool_call_logs`：`dispatch` / `steering` / `agent_task_event` / `llm_usage` |
| 4 | 「正在执行哪条命令」已由 `progress_message`（节点侧把 bash 主参数拼成一句话、≤120 字）提供 → **无需新协议** | `event_translator.py:403-420`（已知限制：无耗时/退出码） |
| 5 | 仪表盘告警是**按需计算**（无告警表），与 Q13 一致 | `dashboard_service.py`（review 告警）+ `system_settings.alerts_read_at` 水位 |

**推荐方案（分两段）**

**M5c-1（后端只读接口 + 动作历史）**
1. **动作历史**：`_handle_task_progress` 在 **工具或阶段发生变化**时追加一条 `tool_call_logs(kind='agent_task_event', name='tool_activity')`（内容：stage/tool/message/progress）——不新增节点协议，天然节流（同一动作不重复记）。
2. **时间线端点** `GET /api/v1/tasks/{id}/timeline`：合并事件表（派发/发言/动作/用量/验收/完成）+ 任务关键节点，按时间升序，带人类可读标题与摘要。
3. **用量曲线端点** `GET /api/v1/tasks/{id}/usage`：`llm_usage` 行 → 逐点 `{at, input, output, cache_read, hit_rate, files_touched, tool}` + 汇总（总 tokens、整体命中率）+ **与 95.2% 基线对比**。
4. **告警（按需计算）**：`task_alert_service.compute_alerts(...)` 纯函数实现三条规则（样本不足时不告警，避免噪音）；随 `/usage` 与 `/timeline` 一并返回；仪表盘聚合加一条「预算/用量异常任务数」。

**M5c-2（前端视图）**
5. 任务摘要面板新增：**用量曲线**（tokens 与命中率）、**时间线列表**、**告警条**、**`@节点` 输入框**（调 M5a-steer）；上段 6 区块与 M5d 下段**均不改动**，只做追加。

**备选方案（不采纳的理由）**
- *完整会话事件流镜像*（Q12 否决）：需节点新协议 + 新表 + 全量消息落地，体积与敏感信息代价大，而「正在做什么」现有字段已能表达。
- *持久化告警记录*（Q13 否决）：多一张表与写入路径；按需计算可复用现有仪表盘机制。
- *靠前端直连节点读会话*：违反「中枢为唯一入口」，且节点离线就断供（D5.3 要求可离线回看）。

**验收（详见 Harness）**：`UT-M5-5`（三条告警规则各自触发与不触发）、`UT-M5-7`（上段保现状）、`UT-M5-8`（当前命令可见）、`UT-M5-9`（`@节点` 定向）、`E2E-A3/A6/A7`。

**明确不做（本步内）**：原始工具输出全文镜像、外部通知渠道、跨任务的全局用量成本看板（归仪表盘后续迭代）。

### M6 · 实施结果（**环境签名 + 派发前召回已完成**，定时整理待做；2026-09-29）

**裁决**：Q14 **节点侧计算环境签名 + 心跳上报** · Q15 **作为元规则文档随派发下发**。

- **环境签名（跨机复用匹配键）**：节点侧 `app/env_signature.py` 把「OS + 架构 + Python 版本 + 平台 + Node/Bun 主版本」
  压成 sha256 前 16 位（进程内缓存一次；支持 `TILAPIA_ENV_SIGNATURE` 强制统一；**探测失败返回 None 不阻断**），
  随心跳上报 → 中枢 `agent_nodes.environment_signature`。
- **经验条目补来源与环境**：`experience_records.source_node_id` + `environment_signature`（+ 复合索引）；
  `record_failure()` 支持写入两者。
- **派发前召回**：`recall_for_dispatch()` **同环境优先**（命中次数降序），无同环境命中才回退「环境未知」的 ADR-050 旧条目，
  **绝不跨环境乱用**；召回结果由 `render_dispatch_doc()` 渲染为 `experiences.md`，
  经 `build_rule_docs_payload(session, task, node)` 第 4 段落 `.tilacode/rules/` 并进 AGENTS.md 索引（**零协议改动**）；
  召回与命中计数都有异常兜底，**不阻断派发**。
- **证据**：中枢 `test_experience_env.py` **6 passed**、节点 `test_env_signature.py` **3 passed**；两侧 `ruff` 全过；迁移已在库生效。
- **定时整理（同日完成）**：新模块 `app/agents/experience_consolidator.py` —— `parse_draft()` 容错解析「根因/解法」两段；
  `ExperienceConsolidator.consolidate()` 按批（默认 10 条）为 `pending` 条目起草，写入 **`status="draft"`（不生效）**；
  调度循环按日守卫触发（`_consolidate_experiences_if_due`，与链路清理同模式）。**草稿不进入派发前召回**（召回只认 resolved），
  用户确认后由既有 `experience_resolve` 置 `resolved` 才生效；LLM 失败/解析失败保持 `pending`（不写半成品）。
- **证据（整理）**：`test_experience_consolidation.py` **4 passed**（解析容错、只写草稿、失败保持 pending、确认后才进召回）。
- **M6 结论**：环境签名 + 派发前召回 + 定时整理（草稿 + 人工确认）三段齐备；剩余 `IT-A5`（真实链路集成）待与 G3 同批执行。

#### M5c · 实施结果（2026-09-29 **已完成**）

**M5c-1 后端**
- **动作历史**：`_handle_task_progress` 在工具/阶段**变化**时写一条 `tool_call_logs(kind='agent_task_event', name='tool_activity')`（无新节点协议、天然节流）；
  `steer_task` 成功后写 `steering/task_steer_sent`，使中枢发言也进入时间线。
- **只读洞察端点**：`GET /api/v1/tasks/{id}/insight` → 用量曲线点 + 汇总（含 **95.2% 基线对比**）+ 告警 + 动作级时间线（≤300 条，人类可读标题）。
- **告警（按需计算，Q13）**：命中率低于基线（≥5 个样本才判）、连续 ≥3 次同签名动作（工具+命令摘要）、连续 ≥4 个统计点无文件写入。
- **仪表盘聚合**：`dashboard_service._collect_alerts` 追加「预算接近上限 / 已按预算强制停止」告警（只按 `budget_state` 单列过滤，**不做跨表聚合**，查询开销可控）。
- **证据**：中枢 `tests/services/test_task_insight.py` **6 passed**（含三条规则的**不误报**路径）；`ruff` 全过；`import app.main` 通过。
- **顺带修复**：`tests/conftest.py` 清理/恢复顺序——新 FK 表 `task_orchestrator_todos` 必须先于 `tasks` 删除（本次由新用例暴露）。

**M5c-2 前端**
- `TaskInsightPanel.tsx`（新）：**告警条**（antd Alert）+ **用量汇总**（轮次/tokens/缓存命中率 + 与 95.2% 基线对比的进度条）+ **最近动作时间线**（antd Timeline，最新在前）+ **`@节点` 发言输入框**（调 M5a-steer，不中断任务）。
- `TaskSummary` 仅在其后**追加** `<TaskInsightPanel>`：**上段 6 区块与下段中枢待办均未改动**（UT-M5-7 守卫继续成立）。
- 取数接入既有轮询（`useTaskPlanTracking` 同轮 `Promise.all`，洞察/待办失败**不清空**已有内容）；`api/tasks.ts` 增 `fetchTaskInsight()` 与 `steerTask()`；`TaskNew` 增 `handleSteerTask`。
- **证据**：`TaskInsightPanel.test.tsx` **6 passed** + `TaskSummary.test.tsx` **9 passed**；颜色门禁无新增违规（唯一告警仍是既有的 `App.tsx:57`）。

**已知缺口**
1. 仪表盘「预算/用量异常」告警**未补专门用例**（逻辑简单，仅单列过滤）；`E2E-A3/A6/A7` 未执行。
2. `IT-A3`（分层验证集成）仍待补。

**下一步**：**M6 经验记忆下沉与跨机复用**（M 规模）。


### M7 · 实施范围与结果（2026-09-29 登记并实施）

**裁决**：Q16 **预装 playwright + chromium**（依赖固化）· Q17 **文本优先、截图可选**。

**已实施（代码层）**
| 文件 | 内容 |
|------|------|
| `opc-agent-programming/tools/frontend_probe.mjs`（新） | 浏览器探针：默认输出**结构化 JSON**（HTTP 状态、标题、结构快照〔标题层级 + 可交互元素可访问名〕、可见文本摘要、控制台错误、页面异常、失败请求与状态码）；`--shots <dir> [--full-page]` 才落截图与文本快照；**退出码即判据**（0 通过 / 1 有发现 / 2 导航失败 / 3 依赖缺失）；`node --check` 通过 |
| `opc-agent-programming/tools/package.json` + `tools/README.md`（新） | 声明 `playwright` 依赖与安装命令；说明退出码语义、`TILAPIA_TOOLS_DIR` 与 `PLAYWRIGHT_BROWSERS_PATH=0`（浏览器装进包内、tar 可搬迁） |
| `opc-agent-programming/app/executor/workspace.py` | 模板 `instructions` 增 `.tilacode/rules/frontend-verification.md`；`_heal_workspace_config` **补全必载指令**（只增不删不重排，历史工作区同样生效）；新增 `ensure_frontend_tooling()`：落 `.tilacode/tools/frontend_probe.mjs`（内容变更才覆盖）+ `.tilacode/rules/frontend-verification.md`（何时必须实测/怎么读输出/什么算通过） |
| `opc-agent-programming/app/executor/task_bridge.py` | `_prepare_session` 调 `ensure_frontend_tooling()` |
| `opc-agent-programming/tests/test_workspace.py` | +1 项（探针与规则落盘、instructions 含新规则、幂等不重写）；原「配置不覆盖」用例语义更新为「保留用户内容 + 补全必载指令」 |

**证据**：节点 `ruff` 全过；`test_workspace.py` **24 passed**；节点全量 **217 passed / 3 failed**（3 项为**已知沙箱 spawn EPERM**，与本模块无关）；`node --check` 探针通过。

**依赖烘焙（构建期，与 G2 一并执行，本轮未跑）**
```bash
cd tools
PLAYWRIGHT_BROWSERS_PATH=0 npm ci --omit=dev
PLAYWRIGHT_BROWSERS_PATH=0 npx playwright install chromium
```
`tools/` 会被 `package_template.py` 打进模板（其 EXCLUDES 只排除 `scripts/`）；节点进程需注入
`TILAPIA_TOOLS_DIR=<安装目录>/tools` 与 `PLAYWRIGHT_BROWSERS_PATH=0`——探针在常规解析失败后按该变量定位 playwright。

**已知缺口**：① 依赖烘焙与 `TILAPIA_TOOLS_DIR` 注入未执行/验证（构建期，按用户「全部写完再一次性编译」）；② 探针真实浏览器链路未在本机跑（无 playwright，且属源码级 E2E 授权范围）；③ `UT-M7-3/4`（截图产物、依赖固化验证）待构建后执行。

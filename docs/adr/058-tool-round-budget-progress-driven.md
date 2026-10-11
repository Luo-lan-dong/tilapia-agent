# ADR-058: 工具轮预算改为进度驱动（修复正常长任务被轮数上限误杀）

## 状态

Accepted

## 日期

2026-09-27

## 问题背景

用户实测：部署任务在模型已侦察完毕、正要输出部署规划时被强制收尾，正文出现
「**已达到最大技能调用轮数**，需要基于已获取信息给出最终回答」。

### 证据（真实会话 `895b1003-ee4a-4935-9d3b-42e73979a4b1`）

| 时间 | 调用 | 性质 |
|------|------|------|
| 09:51:53 | `bt_mcp_setup`（根据接入文件配置服务器） | 用户显式要求，必要 |
| 10:01:12 | `bt_mcp_tools` + `bt_deploy_guide(python)` + `bt_deploy_guide(php)` | 侦察，必要 |
| 10:03:46 | `bt_deploy_guide({})` + `bt_mcp_servers` | 侦察，部分冗余 |
| 10:04:01~ | `run_command` × 3（查找产物包 `artifacts.zip`） | 产物定位，必要 |

该会话累计 **12 次 LLM 轮 / 20 次技能调用**，最终被
`orchestrator_max_tool_rounds`（默认 **8**，`config.py:156-160`）拦下，
`orchestrator.py:1017` 注入 `ORCHESTRATOR_FORCE_FINAL_PROMPT`
（文案「已达到最大技能调用轮数，请基于已获取的信息直接给出最终回答，不要再调用技能」），
随后 `_stream_plain_answer` 无 tools 收尾。

**关键事实：这些调用「各不相同、每个都有意义」** —— 模型并没有打转，
只是「配置服务器 + 查清单 + 查部署指引 + 定位产物」的侦察阶段就吃掉了 6 轮以上，
留给规划与执行的预算不足。

### 设计意图与实际效果的背离

| 防线 | 设计目的 | 现状评估 |
|------|---------|---------|
| `orchestrator_max_tool_rounds`（8） | 防止模型无限调用工具烧 token | **误杀正常长任务**；粒度是「总轮数」，无法区分「有进展的多样化调用」与「原地打转」 |
| Doom Loop 检测（连续 3 轮同名同参，ADR-043） | 防打转 | **精准**：只在调用签名完全一致时触发，正常任务不误伤 |
| 技能超时（300s，ADR-043） | 防单个技能挂死 | 精准 |
| 步数上限（tilacode 语义 48，见 `ORCHESTRATOR_FORCE_FINAL_PROMPT` 引用的规范） | 防失控 | 与本处 8 轮不一致 |

即：**真正针对「死循环烧 token」的精准防线是 Doom Loop 检测，它已经存在且有效；
8 轮上限是一个粗粒度兜底，却成了主要误杀源。**

### 加剧因素（ADR-057 的副作用）

ADR-057 让模型能看到全部 98 个工具（此前 38 个工具名对它不存在），
模型因而**更愿意先侦察再动手**（查清单 → 查 schema → 查指引 → 定位产物）。
侦察是正确行为，但会把轮数预算吃掉更多 —— 使 8 轮的误杀概率显著上升。

## 决策

**把轮数上限从「唯一防线」降级为「安全网」，真正的提前停止交给进度检测。**

### D1 · 轮数上限提高并成为可调安全网（默认 8 → 40）

- `orchestrator_max_tool_rounds` 默认值改为 **40**（`ge=1` 保持）；
- **上限语义不变**：仍在「轮次边界」检查（工具执行完、下一轮开始前），
  **绝不中断正在执行的技能**；达到上限仍是「三重禁用 + 强制文本收尾」
  （tools=[] + tool_choice=none + FORCE_FINAL_PROMPT，ADR-040 语义不变）；
- 理由：ADR-056 后窗口 1M、Prompt Cache 命中率约 98%，单轮成本大幅下降，
  40 轮的真实成本可接受；而 8 轮已经证明会误杀正常任务。

### D2 · 新增「无新信息」进度检测（真正的提前停止依据）

- 统计**连续**轮次中「未引入任何新工具结果」的次数：
  - 判定「该轮无新增」= 本轮所有工具调用的技能名集合，**都曾在本轮对话内成功返回过**
    （含失败返回，失败也是信息）且本轮无成功的新技能；
  - 连续达到阈值（新增配置 `orchestrator_no_progress_rounds`，默认 **4**）即强制收尾；
- 与 Doom Loop 的区别：Doom 检测「**完全相同**的调用」，本检测覆盖
  「**换了参数但反复查同一批工具且无新结论**」这类更隐蔽的打转；
- 收尾文案与 Doom Loop 区分，注明是「连续多轮未获得新信息」而非「达到轮数上限」，
  便于用户与运维判断真实原因。

### D3 · 逼近上限时先警示（可观测）

- 当 `rounds_used` 达到 `ceil(max_rounds × 0.75)` 时，若尚未警示过，
  下发一次 `warning` 事件：「本轮已使用 N/M 轮，任务较复杂」；
- 目的：用户知道「不是卡住，而是在做长任务」，而不是突然被收尾；
- 仅在工具轮边界触发，且每个对话请求最多一次。

### D4 · 诊断信息落到执行链日志

- 每轮记录 `kind=llm_call` 时，已有 `tool_calls` 计数；**不新增字段**；
- 强制收尾时记 `orchestrator_max_rounds_reached` /
  `orchestrator_no_progress_stop` 日志（含 `rounds_used` / `max_rounds` / `session_id`），
  使「为什么被收尾」可从日志与链路表直接判定。

## 备选方案

| 方案 | 结论 |
|------|------|
| **A（本决策）提高上限 + 进度检测 + 警示** | **采纳**。保留安全网语义的同时消除误杀；进度检测复用既有签名基础设施，改动集中在编排循环 |
| B. 仅把 8 改为 40 | 部分采纳（作为 D1）。单独使用仍会在真正打转时烧满 40 轮，故必须配 D2 |
| C. 完全取消轮数上限，只留 Doom Loop | 否决。Doom 只覆盖「完全相同调用」；换参数反复试探（如 `bt_mcp_schema` 逐个查 98 个工具）可无限进行，需要总预算安全网 |
| D. 改为 token 预算 / 时长预算上限 | 暂缓。更贴近成本本质，但需接入 usage 累计与时钟，且「单轮很贵」与「多轮很便宜」的混合场景阈值难定；本轮先用轮数 + 进度，后续按实测再演进 |
| E. 让模型自行决定何时停止 | 否决。实测模型会在侦察后继续尝试执行，缺乏自我预算意识；且失败时用户没有任何兜底 |
| F. 按任务类型给不同上限（部署类给 40、闲聊给 8） | 暂缓。需要任务分类信号，当前 `mode`（fast/thinking）不足以区分；先用统一 40 |

## 影响

### 中枢后端

- `app/core/config.py`：`orchestrator_max_tool_rounds` 默认 8 → **40**；
  新增 `orchestrator_no_progress_rounds`（默认 4，`ge=2`）；
  新增 `orchestrator_rounds_warning_ratio`（默认 0.75，`gt=0, le=1`）；
- `app/agents/orchestrator.py`：
  - 工具轮内维护 `seen_skill_names: set[str]` 与 `no_progress_streak: int`；
  - 每轮末尾判进度：无新技能 → `no_progress_streak += 1`，否则清零；
    达阈值 → 记日志 + 注入专用收尾提示 + `_stream_plain_answer` 收尾；
  - 逼近上限时下发一次 `warning`（D3）；
  - 达到上限时记 `orchestrator_max_rounds_reached` 日志（D4）；
- 新增提示词常量 `ORCHESTRATOR_NO_PROGRESS_PROMPT`（与 Doom/上限文案区分）。

### 数据库 / 前端 / 协议

- 无迁移、无接口变更；**前端零改动**（复用 `warning` 事件与既有 step/chunk 协议）；
- `SkillResult`、SSE 事件名与字段均不变。

### 兼容性

- 达到上限的行为与文案不变（仅阈值变大）；新增的「无新信息」收尾是**新终止路径**，
  只在连续 4 轮零新增时触发，正常任务不会命中；
- `.env.example` 的 `ORCHESTRATOR_MAX_TOOL_ROUNDS=8` 示例值同步改为 40。

## 关联文档

- [ADR-043](./043-execution-guard-v1.md)（Doom Loop 检测与技能超时：本 ADR 说明其与轮数上限的分工）
- [ADR-040](./040-orchestrator-agent-loop-v1.md)（多轮循环与三重禁用收尾）
- [ADR-056](./056-llm-params-calibration.md)（窗口 1M 与缓存命中：提高上限的成本前提）
- [ADR-057](./057-bt-tools-inventory-compaction.md)（工具清单可见性提升，加剧了侦察轮数消耗）
- 排查记录：[2026-09-27-上下文计量失真与压缩误触发](../issues/2026-09-27-上下文计量失真与压缩误触发-排查记录.md)
- [058-tool-round-budget-spec.md](../spec/058-tool-round-budget-spec.md)
- [058-tool-round-budget-harness.md](../spec/058-tool-round-budget-harness.md)
- [058-tool-round-budget-changes.md](../spec/058-tool-round-budget-changes.md)

## 实际实施情况（2026-09-27）

### 决策落实

- **D1**：`orchestrator_max_tool_rounds` 默认 8 → **40**；上限语义（轮次边界检查、
  三重禁用收尾）不变；`.env.example` 同步。
- **D2**：工具轮末尾以「本轮技能集合 − 已返回技能集合」判新增；无新增则累加
  `no_progress_streak`，达 `orchestrator_no_progress_rounds`（默认 4）即记
  `orchestrator_no_progress_stop` + warning + 注入 `ORCHESTRATOR_NO_PROGRESS_PROMPT` + 纯文本收尾。
- **D3**：`rounds_used >= ceil(max_rounds × ratio)`（默认 0.75）时下发一次 warning。
- **D4**：循环退出后记 `orchestrator_max_rounds_reached`（含 `rounds_used`/`max_rounds`/`session_id`）。

### 实施中发现的关键交互（已写入 Spec §4）

**无进展检测与 Doom Loop 的分工必须精确**：无进展检测覆盖的是「**换了参数**但反复查
同一批工具且无新结论」；若模型发出的是**同名同参**调用，第 3 轮会被 Doom Loop（ADR-043）
先命中，无进展分支根本不会执行。首轮两条用例失败即因此（诊断输出显示
`warning=检测到重复的工具调用`）。测试脚本已改为参数逐轮递增以落在目标形态上。

### 验证

- UT-75~UT-85 全部分组通过：含核心守卫 UT-77「多阶段侦察不被误杀」、
  UT-82「三重禁用语义不变」、UT-83/UT-84「Doom/超时防线不回归」、
  UT-78「无进展收尾含 `orchestrator_no_progress_stop` 日志断言」；
  改动文件 ruff check/format 全过。
- 后端已用新代码重启（PID 2068，10:49:18，`/docs` 与 `/api/v1/skills` 均 200）。

### 遗留

- **测试基础设施既有问题**（非本变更引入）：多用例同进程时 `tests/conftest.py` 清库出现
  `fk_dispatch_decisions_tasks_task_id` 外键冲突（`dispatch_decisions` 引用 `tasks` 而清库顺序未覆盖），
  表现为用例级 ERROR，单独运行即通过。建议后续修 conftest 清理顺序。
- **token/时长预算**（备选 D）与**按任务类型分档**（备选 F）未做：待本轮上限 + 进度检测
  上线后按真实数据评估。

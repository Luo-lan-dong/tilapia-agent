# 工具轮预算改为进度驱动 Changes 变更清单

> 关联：[ADR-058](../adr/058-tool-round-budget-progress-driven.md) · [Spec](./058-tool-round-budget-spec.md) · [Harness](./058-tool-round-budget-harness.md)
> 规则：边开发边实时登记（修/增/减 + 函数级明细 + 作用说明），**禁止开发完成后一次性补记**。
> 范围约束：覆盖 D1（上限 8→40 可调安全网）、D2（无新信息进度检测）、D3（逼近上限警示）、
> D4（收尾原因落日志）。**不含** token/时长预算（备选 D，暂缓）、按任务类型分档（备选 F，暂缓）。

## 计划变更点（编码前骨架）

| # | 文件 | 计划变更类型 | 计划函数/类级明细 | 作用说明 |
|---|------|------------|-----------------|---------|
| 1 | `app/core/config.py` | 修 | `orchestrator_max_tool_rounds` 默认 8 → 40 | 上限降级为可调安全网 |
| 2 | `app/core/config.py` | 增 | `orchestrator_no_progress_rounds`（默认 4，`ge=2`）、`orchestrator_rounds_warning_ratio`（默认 0.75，`gt=0, le=1`） | 进度检测阈值与警示比例 |
| 3 | `app/agents/orchestrator_prompts.py` | 增 | `ORCHESTRATOR_NO_PROGRESS_PROMPT` | 与 Doom/上限区分的收尾提示 |
| 4 | `app/agents/orchestrator.py` | 修 | `chat_stream` 工具轮末尾：维护 `seen_skill_names` / `no_progress_streak`，超阈值收尾；逼近上限发一次 warning；上限收尾记日志 | D2/D3/D4 落地 |
| 5 | `.env.example` | 修 | `ORCHESTRATOR_MAX_TOOL_ROUNDS` 示例 8 → 40，增两项新配置注释 | 部署模板 |
| 6 | `tests/agents/test_orchestrator.py` | 增 | UT-75~UT-85 + IT-15~IT-17 | 核心验收与三条既有防线回归 |

## 变更清单（实际实施明细）

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| `app/core/config.py` | 修 | `orchestrator_max_tool_rounds` 默认 8 → **40**，注释改为「可调安全网」并写明误杀证据 | D1 |
| `app/core/config.py` | 增 | `orchestrator_no_progress_rounds`（默认 4，`ge=2`）、`orchestrator_rounds_warning_ratio`（默认 0.75，`gt=0, le=1`） | D2 阈值 / D3 预警比例 |
| `app/agents/orchestrator_prompts.py` | 增 | `ORCHESTRATOR_NO_PROGRESS_PROMPT` | 与「轮数上限」「Doom Loop」三者文案互相区分，便于辨别真实原因 |
| `app/agents/orchestrator.py` | 修 | 导入增 `math`、`ORCHESTRATOR_NO_PROGRESS_PROMPT` | 警示阈值计算与收尾提示 |
| `app/agents/orchestrator.py` | 修 | `chat_stream` 初始化：`no_progress_threshold`、`warning_at = max(1, ceil(max_rounds × ratio))`、`seen_skill_names` / `no_progress_streak` / `rounds_warning_emitted` | D2/D3 状态 |
| `app/agents/orchestrator.py` | 修 | 工具轮末尾：本轮技能集合与 `seen_skill_names` 求差判新增 → 清零或累加；达阈值记 `orchestrator_no_progress_stop` + warning + 注入无进展提示 + `_stream_plain_answer` 收尾并 `return` | D2 进度驱动提前停止 |
| `app/agents/orchestrator.py` | 修 | 工具轮末尾：`rounds_used >= warning_at` 且未警示过 → 下发一次 warning（含 N/M 轮） | D3 可观测 |
| `app/agents/orchestrator.py` | 修 | 循环退出后记 `orchestrator_max_rounds_reached`（含 `rounds_used`/`max_rounds`/`session_id`）；三重禁用收尾语义不变 | D4 收尾原因可判定 |
| `.env.example` | 修 | `ORCHESTRATOR_MAX_TOOL_ROUNDS` 8 → 40；新增两项新配置及注释 | 部署模板 |
| `tests/agents/test_orchestrator.py` | 增 | 辅助 `_ScriptedSkillsClient`（逐轮技能脚本 + `repeat` 循环模式）；UT-75~UT-85（11 项） | 核心验收 + 三条既有防线回归 |

## 登记记录

- 2026-09-27：创建骨架（ADR-058 四件套建立；Spec/Harness 同步完成，未写生产代码）。
- 2026-09-27：立项依据为**真实会话证据**（`895b1003`：12 轮 / 20 技能，被 8 轮上限在第 6 轮后
  强制收尾；调用序列显示每轮技能各不相同、均为有意义的侦察动作，非打转）。
- 2026-09-27：范围界定——**token/时长预算**（备选 D）与**按任务类型分档**（备选 F）暂缓，
  待本轮上限 + 进度检测上线后按实测数据再评估。
- 2026-09-27：实施完成并登记上表。实施要点：
  1. **进度判定放在工具轮末尾**（而非轮首）——只有本轮工具真的执行完才知道有没有新信息；
  2. **失败/超时也算「已返回」**：技能失败本身是信息，若计为无进展会把「反复重试失败技能」
     误判成打转（该场景应由 Doom Loop 处理），故 `seen_skill_names` 不区分成功与失败；
  3. **判定优先于上限**：无进展收尾在工具轮末尾即时生效（早于循环条件检查），
     故真打转时不必等满 40 轮。
- 2026-09-27：**诊断中发现的一处与 Doom Loop 的交互（重要）**：无进展检测要求
  「换了参数但无新信息」，若测试用**同名同参**反复调用，会在第 3 轮被
  Doom Loop（ADR-043）先命中并收尾，无进展分支永远执行不到。首次两条用例失败即此原因
  （诊断输出显示 `warning=检测到重复的工具调用`、第三轮 `step_finished=failed`）。
  已把测试脚本的参数改为**逐轮递增**（`{"n": <轮次>}`）以落在目标形态上；
  该交互已在 Spec §4「与 Doom 的关系」记录。
- 2026-09-27：**测试结果**——UT-75~UT-85 全部分组通过：
  `round_budget_defaults` / `round_budget_config_validation` / `multi_stage_scouting_not_killed_by_round_limit`
  （核心）/ `no_progress_stops_with_distinct_message`（含 `orchestrator_no_progress_stop` 日志断言）/
  `new_skill_resets_no_progress_streak` / `failed_skill_counts_as_returned` / `rounds_warning_emitted_once` /
  `max_rounds_keeps_triple_disable_semantics` / `doom_loop_still_triggers` / `skill_timeout_still_continues` /
  `no_progress_takes_precedence_over_round_limit`；ruff check/format 全过。
- 2026-09-27：**测试夹具的一个既有问题（非本变更引入）**：多用例同进程跑时
  `tests/conftest.py` 清库阶段出现
  `ForeignKeyViolationError: update or delete on table "tasks" violates foreign key constraint
  "fk_dispatch_decisions_tasks_task_id"` —— `tasks` 被 `dispatch_decisions` 引用而清库顺序未覆盖。
  表现为**用例级 ERROR**（单独运行即通过，如 `skill_timeout_still_continues`）。
  本变更未改 conftest；已记录待后续修复（属测试基础设施问题）。
- 2026-09-27：后端已用新代码重启（PID 2068，10:49:18，`/docs` 与 `/api/v1/skills` 均 200）。

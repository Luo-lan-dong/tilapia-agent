# 中枢 AI 技能体系与智能调度 Harness（测试清单）

> 关联：[ADR-033](../adr/033-ai-skills-and-smart-dispatch.md) / [Spec](./033-ai-skills-and-smart-dispatch-spec.md)

## 1. 测试金字塔

- 单元测试：Skill 基类与注册表、AI 决策白名单校验与回退、批量决策解析、
  审计落库、Orchestrator function calling 循环。
- 集成测试：调度器 AI 决策钩子（mock LLM）、SSE step 事件流、技能端点
  契约、设置开关热更新。
- E2E：对话调用技能展示步骤卡片、技能页管理、AI 调度开关三态行为。

## 2. 单元测试清单

### 技能框架（opc-central-dispatch-hub，tests/）

| 编号 | 用例 | 断言 |
|------|------|------|
| UT-1 | Skill 基类契约 | name/description/parameters/returns/execute 齐全；parameters 为合法 JSON Schema |
| UT-2 | 注册表注册 5 个 MVP 技能 | skill_key 无冲突，全部可被检索 |
| UT-3 | hub_skills.enabled=false 的技能 | 注册表工具集过滤后不包含该技能（LLM 不可见） |
| UT-4 | find_idle_nodes 技能 | 返回与调度预筛同口径的候选节点（在线+水位+槽位） |
| UT-5 | get_node_metrics / get_task_status 技能 | 正确包装现有 service 返回结构 |
| UT-6 | 技能 execute 抛异常 | 返回 SkillResult(failed) 而非向上抛出（由 LLM 循环承接） |

### OrchestratorAgent（对话循环）

| 编号 | 用例 | 断言 |
|------|------|------|
| UT-7 | LLM 返回 tool_calls | 技能执行 + 结果回传 + 继续下一轮 |
| UT-8 | 连续多技能调用 | 每次调用均产生 step_started/step_finished 事件对，seq 递增 |
| UT-9 | 达到 ORCHESTRATOR_MAX_TOOL_ROUNDS | 强制终止工具循环，产出最终回答 |
| UT-10 | generate_spec_docs 技能 | specification_delta 事件照常下发，三件套结构不变 |
| UT-11 | 无 LLM 配置 | 返回明确降级错误事件，不抛 500 |

### AI 智能调度（scheduler + 决策）

| 编号 | 用例 | 断言 |
|------|------|------|
| UT-12 | AI_DISPATCH_ENABLED=false | 行为与现状一致：负载升序分配，零 LLM 调用 |
| UT-13 | 开关开启 + 候选唯一 | 直接规则分配，不调用 LLM（mock 断言零调用） |
| UT-14 | 开关开启 + 多候选 | 批量合并一次 LLM 调用（多任务不产生多次调用） |
| UT-15 | LLM 输出白名单外 node_id（幻觉） | 该任务回退负载升序分配，不重试；审计 fallback=true |
| UT-16 | LLM 输出合法 node_id | 采纳 AI 选择；审计 validated=true、final=ai_choice |
| UT-17 | LLM 超时（8s）/抛异常 | 本轮任务全部回退规则分配，调度循环不中断 |
| UT-18 | LLM 输出无法解析的 JSON | 同 UT-15 回退路径 |
| UT-19 | 每次决策 | dispatch_decisions 落库含 candidates 快照/原始输出/duration_ms |
| UT-20 | 上下文瘦身 | LLM 请求仅含候选节点关键字段，不含全量 metrics |

## 3. 集成测试

| 编号 | 场景 | 断言 |
|------|------|------|
| IT-1 | SSE 对话流：用户问节点 → step_started/finished + chunk 最终回答 | 事件顺序与 payload 结构符合 Spec §4.1 |
| IT-2 | 技能端点：GET 列表 / GET 详情 / PATCH 禁用 | 响应格式符合 api-standards §2.3；禁用后工具集生效 |
| IT-3 | 设置开关：PUT ai-dispatch 热更新 | 无需重启，下一轮调度周期生效 |
| IT-4 | 调度全流程：开关开启 + 2 任务 3 节点 → AI 决策 → 派发 | 任务 assigned；决策审计与实际分配一致 |

## 4. E2E 测试场景

| 编号 | 场景 | 步骤 | 预期 |
|------|------|------|------|
| E2E-1 | 对话技能步骤展示 | 「新建对话」输入"看下哪些节点空闲" | 消息内出现可折叠步骤卡片（工具名/参数/结果/耗时），随后 AI 总结回答 |
| E2E-2 | 技能页管理 | 打开 /skills 页 → 禁用 get_node_metrics → 对话再问节点指标 | 列表 5 技能可见；禁用后 AI 不调用该技能并口头说明 |
| E2E-3 | AI 调度三态 | ①默认关闭创建任务 ②设置页开启后再创建 ③关闭 LLM 配置再创建 | ①纯规则分配 ②多候选时审计表有 AI 决策记录 ③自动降级纯规则 |

## 5. CI 门禁

- `pytest`（hub）全量通过；本 Harness 新增用例随 PR 同提交。
- `ruff format --check && ruff check`（hub 目录）。
- 前端 `tsc -b` + `eslint`（技能页 / 步骤卡片组件 / 设置开关）。
- 文件长度检查：`scripts/check_file_size.py`——orchestrator.py、
  scheduler.py 改造后仍须 ≤300 行（Orchestrator 预计需拆分
  prompts / 循环 / SSE 适配），页面 ≤350 行 / 组件 ≤200 行 / hooks ≤200 行。

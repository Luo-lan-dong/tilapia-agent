# 会话内任务链自动挂接与规范文档按需生成 Spec（session-task-chain）

> 关联决策：[ADR-036](../adr/036-session-task-chain-and-spec-command.md)
> Harness：[036-session-task-chain-harness.md](./036-session-task-chain-harness.md)
> Changes：[036-session-task-chain-changes.md](./036-session-task-chain-changes.md)

## 1. 功能描述

1. **自动挂链**：同一构建会话内第二次及以后创建任务时，自动挂接到本会话最近创建的任务（含链尾子任务），形成迭代链——Agent 端复用链根工作目录与 tilacode 会话（ADR-012），handoff 正常触发（ADR-034）。
2. **可读目录命名**：新链根任务自动生成 `{净化中文标题}-{任务ID前8位}` 工作目录；链上子任务继承链根目录。
3. **三件套按需生成**：会话内简单增量修改直接创建任务复用现有文档；全新需求/复杂改造必须先生成；`/spec` 命令强制生成。
4. **命令面板**：输入框输入 `/` 弹出命令提示面板，点击回填。

## 2. 用户故事

- US-1：我在同一对话里先做了登录页，再说"背景改纯蓝"——新任务自动挂到登录页任务链，Agent 在原目录原会话上增量修改，不再从头重写。
- US-2：任务完成后我在 Agent 节点文件管理器里看到 `登录页面开发-a3f2c1d4` 这样的目录，一眼知道对应哪个任务。
- US-3：简单修改（改颜色/文案）AI 不再重新生成三件套，直接建任务派发；我输入 `/spec` 时强制重新生成。

## 3. 核心流程

### 3.1 自动挂链

```
create_task 技能 → TaskService.create_task
  │
  ▼
request.parent_task_id 非空？（?parent= 入口）
  ├─ 是 → 现有链派生逻辑（不变）
  └─ 否 → request.session_id 非空？
       ├─ 否 → 新链根（现状）
       └─ 是 → 查 tasks.session_id=会话 最近一条（created_at desc limit 1）
            ├─ 无 → 新链根 + warning 日志
            └─ 有 → parent = 该任务（链尾）；change_brief 空时填 requirement
```

### 3.2 工作目录解析

```
优先级：显式 output_dir > 继承链根 > 自动生成
  ├─ 显式：request.output_dir 校验后直接用
  ├─ 挂链：session.get(Task, root_task_id).output_dir（根为空 → None）
  └─ 新链根：_sanitize(requirement)[:20] + "-" + task_id.hex[:8]
       （Task id 在 flush 前显式生成，目录名与库中 ID 一致）
```

### 3.3 /spec 强制生成

```
用户消息 strip == "/spec"（精确匹配）
  │
  ▼
chat_stream 前置拦截（不走 LLM 决策轮）
  │
  ▼
合成 tool_call → _run_tool_call(generate_spec_docs)
  │  事件：step_started → chunk/thinking/specification_delta/specification → step_finished
  ▼
SkillResult.summary 按 chunk 分片 + done（会话持久化照常）
```

## 4. 验收标准

- [x] 同会话第二次 create_task：新任务 parent_task_id=链尾任务、root_task_id=链根；change_brief 自动填充。（UT：test_auto_link_second_task_in_session / test_skill_auto_link_via_service）
- [x] 新链根任务 output_dir 为 `中文标题-短ID`（非法字符净化）；子任务继承链根 output_dir；显式 output_dir 优先。（UT：test_root_dir_named_with_title_and_id_prefix / test_child_inherits_root_output_dir / test_explicit_output_dir_priority）
- [x] `/spec` 精确匹配强制生成（技能禁用时也生效），事件序列与持久化与正常技能轮一致；普通消息不受影响。（UT/IT：test_spec_command_forces_spec_skill / test_spec_command_works_when_skill_disabled / test_spec_command_round_persists_specification_and_steps）
- [x] 简单增量修改时提示词引导 AI 跳过 generate_spec_docs（提示词策略，人工验证）。
- [x] 无数据库迁移；Agent 端零改动；`?parent=` 流回归通过。（全量 pytest 328 passed + 既有用例回归）

## 5. 关联文档

- [ADR-036](../adr/036-session-task-chain-and-spec-command.md)
- [ADR-012](../adr/012-tilacode-execution-layer-integration.md) / [ADR-028](../adr/028-task-workspace-binding-and-artifact-sync.md) / [ADR-033](../adr/033-ai-skills-and-smart-dispatch.md)

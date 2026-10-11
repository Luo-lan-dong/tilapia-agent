# 会话内任务链自动挂接与规范文档按需生成 Harness（测试清单）

> 关联：[ADR-036](../adr/036-session-task-chain-and-spec-command.md) / [Spec](./036-session-task-chain-spec.md)

## 1. 测试金字塔

- 单元测试：自动挂链查询、目录命名净化、/spec 拦截与事件序列。
- 集成测试：技能创建链路（create_task 挂链 + change_brief）、构建流 /spec 轮持久化。
- E2E：同会话两轮需求 → 第二任务挂链且 Agent 复用目录与会话（启动实测）。

## 2. 单元测试清单（hub tests/）

| 编号 | 用例 | 断言 |
|------|------|------|
| UT-1 | 同会话第二次创建任务 | parent_task_id=会话最近任务；root_task_id=链根 |
| UT-2 | 显式 parent（?parent=） | 显式值优先，不触发自动挂链查询覆盖 |
| UT-3 | 自动挂链时 change_brief 为空 | 填充 requirement 截断；已有值不覆盖 |
| UT-4 | 新链根目录命名 | `{净化标题}-{id前8}`；非法字符 `/\:*?"<>|` 去除、空白转 `-` |
| UT-5 | 子任务继承链根 output_dir | 与链根一致；链根为空则子任务为 None |
| UT-6 | 显式 output_dir | 优先级最高，不被自动命名覆盖 |
| UT-7 | 无 session_id | 不挂链、目录仍自动命名（新链根） |
| UT-8 | /spec 精确匹配 | LLM 决策轮零调用；事件序列 step_started→specification→step_finished→chunk→done |
| UT-9 | /spec 带空白 | strip 后匹配生效；非 /spec 消息不拦截 |
| UT-10 | /spec 技能被禁用 | 仍强制执行 |
| UT-11 | create_task 技能透传 change_brief | TaskCreateRequest.change_brief=requirement |

## 3. 集成测试

| 编号 | 场景 | 断言 |
|------|------|------|
| IT-1 | 构建流发送 /spec | SSE 事件正常、specification 与 steps 落库 |

## 4. E2E 测试场景

| 编号 | 步骤 | 预期 |
|------|------|------|
| E2E-1 | 同一会话：创建登录页任务派发 → 提修改需求再创建派发 | 第二任务 parent/root 挂链；Agent 复用目录与 `ses_task_<root>` 会话；handoff 触发；文件在原文件上修改 |
| E2E-2 | 输入 `/` → 面板弹出 → 选 /spec | 三件套重新生成并回写面板 |

## 5. CI 门禁

- hub `pytest` 全量 + `ruff format --check && ruff check`；前端 `tsc + eslint`。
- 组件文件行数限制（组件 ≤200 行）。

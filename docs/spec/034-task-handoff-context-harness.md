# 跨 Agent 任务现场迁移与项目上下文记忆 Harness（测试清单）

> 关联：[ADR-034](../adr/034-task-handoff-context.md) / [Spec](./034-task-handoff-context-spec.md)

## 1. 测试金字塔

- 单元测试：上下文 upsert、handoff 触发判定、打包排除规则、token 校验。
- 集成测试：completed 上报入库 → 派发带 handoff → bundle 下载解压全链路（mock Agent）。
- E2E：A 离线改派 B 的现场恢复场景。

## 2. 单元测试清单（中枢 tests/）

| 编号 | 用例 | 断言 |
|------|------|------|
| UT-1 | task_completed 带 project_context | task_chain_contexts upsert（root_task_id 维度，覆盖更新） |
| UT-2 | task_completed 无 project_context（旧 Agent） | 不建上下文记录，任务正常完成 |
| UT-3 | 链上有历史产物 → 派发 payload | handoff 字段存在（bundle_url/context_md/size） |
| UT-4 | 链上无产物（首个任务） | payload 无 handoff 字段 |
| UT-5 | 打包排除规则 | tar.gz 不含 node_modules/.git/.tilacode/__pycache__/.venv |
| UT-6 | 打包聚合 | 覆盖链内全部任务的产物文件 |
| UT-7 | bundle_url token 校验 | 正确 token 200；错误/过期 token 401/410 |
| UT-8 | bundle 超过 HANDOFF_MAX_BUNDLE_BYTES | 拒绝打包，派发退化为无 handoff + 告警日志 |
| UT-9 | GET /tasks/{id}/context | 返回链最新 context_md |
| UT-10 | 过期 bundle 清理 | TTL 过后暂存文件被清理 |

## 3. 集成测试

| 编号 | 场景 | 断言 |
|------|------|------|
| IT-1 | 全链路：completed(带 context) → 派发带 handoff → mock Agent 下载解压 | bundle 可下载、sha256 一致、目录结构与排除规则正确 |
| IT-2 | Agent 下载失败重试 1 次后放弃 | 任务照常执行，中枢收到告警日志 |

## 4. E2E 测试场景

| 编号 | 场景 | 步骤 | 预期 |
|------|------|------|------|
| E2E-1 | A 离线改派 B 现场恢复 | A 完成登录页 → A 下线 → 对话提出新修改 → 改派 B | B 工作目录含 A 的代码/文档与 project_context.md；B 的 tilacode 首轮输出体现已阅读上下文（引用既有文件） |

## 5. CI 门禁

- `pytest`（hub + agent）全量通过；`ruff format --check && ruff check`。
- Agent 端：bundle 下载/解压/幂等单测随 PR 提交；`hub_client.py` 改后 ≤300 行。
- 前端（context 查看若涉及）：tsc + eslint。

# 节点并发任务调度 Harness（测试清单）

> 关联：[ADR-032](../adr/032-concurrent-task-scheduling.md) / [Spec](./032-concurrent-task-scheduling-spec.md)

## 1. 测试金字塔

- 单元测试：中枢调度候选筛选、拒绝改派、回避 TTL；Agent 自评拒绝、多槽执行。
- 集成测试：WS 事件流（dispatch → rejected → redispatch → completed）。
- E2E：单节点并发两任务、全节点不可用排队等待。

## 2. 单元测试清单

### 中枢（opc-central-dispatch-hub，tests/services/）

| 编号 | 用例 | 断言 |
|------|------|------|
| UT-1 | 节点 busy 但资源空闲且槽位未满 | 仍被选为候选（busy 不再排除） |
| UT-2 | 节点 CPU 95%（超阈值） | 被预筛排除 |
| UT-3 | 节点内存 95%（超阈值） | 被预筛排除 |
| UT-4 | 节点在跑任务数 = max_concurrent_tasks | 被预筛排除 |
| UT-5 | 节点无 metrics（NULL/缺字段） | 资源预筛放行，仅槽位约束 |
| UT-6 | 多候选节点 | 按综合负载分 load 升序选第 1 名 |
| UT-7 | task_rejected(reason=slots_full) | 任务回 pending、节点未置 idle、回避记录写入 |
| UT-8 | 改派时回避已拒绝节点 | 同轮不再选中该节点；选其他候选 |
| UT-9 | 回避记录 TTL 过期 | 5 分钟后该节点重新可被选中 |
| UT-10 | 任务改派成功 | 回避记录清除 |
| UT-11 | 旧 Agent 拒绝（payload 无 reason） | 按 slots_full 处理，行为一致 |
| UT-12 | 任务完成/失败释放 | 在跑计数减 1（tasks 表统计口径），节点状态由心跳派生 |

### Agent（opc-agent-programming，tests/）

| 编号 | 用例 | 断言 |
|------|------|------|
| UT-13 | 在跑 0 任务 + 资源空闲 | accepted，执行任务 |
| UT-14 | 在跑数 = AGENT_MAX_CONCURRENT_TASKS | rejected(reason=slots_full) |
| UT-15 | 实时 CPU 超 90%（mock psutil） | rejected(reason=resource_busy) |
| UT-16 | 两任务并发执行 | 各自独立 consumer，进度/todo 分别上报不串扰 |
| UT-17 | 并发执行中 interrupt(task_id) | 仅中断指定任务，另一任务不受影响 |
| UT-18 | todo.updated 事件路由 | 按 sessionID 路由到对应任务的 consumer |
| UT-19 | tilacode 崩溃 | 所有在跑任务均收到 failed 回调 |

## 3. 集成测试

| 编号 | 场景 | 断言 |
|------|------|------|
| IT-1 | WS 全流程：派发 → Agent 拒绝 → 中枢改派下一节点 → accepted → completed | 任务终态 completed，拒绝/改派结构化日志齐全 |
| IT-2 | 单节点槽位满 + 无其他节点 | 任务保持 pending；槽位释放后下一轮自动派发 |

## 4. E2E 测试场景

| 编号 | 场景 | 步骤 | 预期 |
|------|------|------|------|
| E2E-1 | 单节点并发执行 | 在线节点 1 台（上限 2），连续创建 2 个任务并自动派发 | 两任务均进入 running 并各自完成；节点展示 busy 但调度未排队 |
| E2E-2 | 排队等待再分配 | 节点上限打满（2 任务在跑），再创建第 3 个任务 | 第 3 任务 pending；任一任务完成后第 3 任务自动派发 |

## 5. CI 门禁

- `pytest`（hub + agent）全量通过；新增用例必须随 PR 同提交。
- `ruff format --check && ruff check`（两个 Python 项目各自目录）。
- 前端 tsc + eslint（本期前端不强制改动，回归现有检查即可）。
- 文件长度检查：`scripts/check_file_size.py`（hub_client.py / task_bridge.py
  并发化后仍须 ≤300 行，超出先拆分）。

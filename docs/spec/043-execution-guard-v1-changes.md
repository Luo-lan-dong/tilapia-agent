# 执行保护 v1 Changes 变更清单

> 关联：[ADR-043](../adr/043-execution-guard-v1.md) · [Spec](./043-execution-guard-v1-spec.md) · [Harness](./043-execution-guard-v1-harness.md)
> 规则：边开发边实时登记（修/增/减 + 作用说明），禁止开发完成后一次性补记。

## 变更清单

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| `app/core/config.py` | 修 | 增 `skill_execution_timeout_seconds`（默认 300，ge=1）+ `orchestrator_doom_loop_threshold`（默认 3，ge=2） | 技能超时预算与 doom 判定阈值配置化 |
| `app/agents/orchestrator_prompts.py` | 修 | 增 `ORCHESTRATOR_DOOM_LOOP_PROMPT` | doom 命中后的收尾提示（告知模型停止重复、基于已有结果作答） |
| `app/agents/orchestrator.py` | 修 | ①增 `import asyncio/contextlib`；②增 `_tool_calls_signature()`（名称+参数 sort_keys 规范化，非法 JSON 回退原串）；③`chat_stream`：循环外初始化 `recent_signatures`/`doom_threshold`，工具轮 assistant 入列后插入 doom 判定块（连续 threshold 轮签名相同 → 不执行、补「未执行」step 卡片与 tool 消息、发 `_TRAJECTORY_EVENT` + `warning` 事件、注入 doom 提示走 `_stream_plain_answer` 收尾 return）；④`_run_tool_call`：技能消费循环改为共享 deadline 的逐事件 `asyncio.wait_for(gen.__anext__())` 限时，`TimeoutError` 转失败 `SkillResult`（step_finished failed + tool 消息回传）+ `contextlib.suppress` 包裹的 `gen.aclose()` + `session.rollback()` 丢弃未提交事务 | 技能卡死不再悬挂整轮对话；模型打转提前止损不烧满步数上限 |
| `tests/agents/test_orchestrator.py` | 修 | `_fake_settings` 扩展为 `rounds + **overrides` 通用代理（原单参版本删除）；增 UT-1/2 签名规范化、UT-3 技能超时（_HangingSkill + 0.2s 预算）、UT-5 doom 连续 3 轮收尾、UT-6 阈值内换技能不触发、UT-7 交替重复不误伤、UT-8 参数变化不误伤，共 6 个测试函数 | Harness UT 清单覆盖 |
| `docs/INDEX.md` | 修 | 1.1/1.2/1.3 登记执行保护 v1；六、更新记录补 2026-09-23 行 | 决策文档索引 |
| `docs/spec/040-orchestrator-agent-loop-roadmap.md` | 修 | P3 状态更新为「实施中（ADR-043）」；P1 验收标准勾选 | 路线图同步 |

## 登记记录

- 2026-09-23：创建骨架（ADR-043 四件套建立；方案经用户确认：doom 提前强制收尾 / 超时 300s / 超时回传失败消息继续循环）。
- 2026-09-23：登记全部实施变更（上表）。技术要点：技能超时采用「共享 deadline 的逐事件 wait_for」而非 `asyncio.timeout` 包整个消费循环——后者在生成器挂起于 yield 期间超时会误 cancel 外层 SSE task；逐事件限时只在等待技能产出时计时，`wait_for` 超时 cancel 的是内部 `__anext__` 包装 task，不伤及外层。
- 2026-09-23：⚠️ 测试执行受阻——远程 PostgreSQL（192.168.101.101:5432）主机整体验证失败（ping 不通，今早可查），30 项测试报 `WinError 121` 连接错误；代码与 ruff 已完成，pytest 回归待 DB 恢复后补跑。
- 2026-09-23：DB 恢复后补跑通过——tests/agents/test_orchestrator.py 30 passed（24 存量 + 6 新增），上下文/技能相关回归 29 passed（test_context_compaction + test_context_environment + test_history_recall）；后端已重启加载 ADR-043 代码，健康检查 200。路线图 P3 状态更新为已完成。

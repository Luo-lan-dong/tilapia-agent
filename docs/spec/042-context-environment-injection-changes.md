# 环境上下文注入 v1 Changes 变更清单

> 关联：[ADR-042](../adr/042-context-environment-injection.md) · [Spec](./042-context-environment-injection-spec.md) · [Harness](./042-context-environment-injection-harness.md)
> 规则：边开发边实时登记（修/增/减 + 作用说明），禁止开发完成后一次性补记。

## 变更清单

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| `alembic/versions/20260921_env_ctx.py` | 增 | 迁移 20260921_env_ctx | `task_building_sessions.context_environment` JSONB（环境块快照） |
| `app/models/task_building_session.py` | 修 | 增 `context_environment` 列映射 | 快照持久化 |
| `app/services/context_environment_service.py` | 增 | `build_environment_block()` / `query_cluster_summary()` / `build_environment_update_message()` | 环境块生成（时间粒度到小时）、在线节点聚合（离线不计）、增量消息构造 |
| `app/agents/orchestrator.py` | 修 | `_build_messages` 增 environment_block 参数拼入 system 尾部；chat_stream 读取快照 → Baseline/增量决策 → 快照落库（预压之后追加保证位置正确） | Baseline 不可变（system 用快照版本）+ 变化增量（`<environment-update>` 尾部追加，不落库） |
| `tests/services/test_context_environment.py` | 增 | UT-1~5 + 概况聚合/降级共 7 项 | 格式/聚合/降级/小时粒度/增量构造/聚合查询/失败降级 |
| `tests/agents/test_orchestrator.py` | 增 | IT-1~4 共 4 项 | 首轮 baseline/未变不追加/变化出增量+快照更新/无会话回归 |
| `scripts/e2e_env_20260921.py` | 增 | 端到端脚本 | 问日期/集群状态验证环境块生效 |

## 登记记录

- 2026-09-21：创建骨架（ADR-042 四件套建立，随开发实时登记）。
- 2026-09-21：登记全部实施变更（上表）；测试期间发现手动 `alembic upgrade head` 与测试子进程 alembic **并发竞争**导致版本表/schema 不一致误报（规避：迁移命令避免 PowerShell 管道提前终止，避免与测试并发执行）。
- 2026-09-21：端到端验证通过——模型准确回答当前日期时间（2026-09-22 星期二凌晨，环境块真实生效）与集群在线概况（2 个 code 节点 idle，零技能调用）。后端 31 项测试全过。


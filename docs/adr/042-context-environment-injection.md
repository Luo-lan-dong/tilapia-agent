# ADR-042: 环境上下文注入 v1（日期时间 + 集群概况 Baseline/增量注入）

## 状态

Accepted

## 日期

2026-09-21

## 问题背景

路线图 P2（缺陷 4）：中枢 system = 静态角色提示 + 技能清单 + spec 草案，模型对运行
环境零感知——不知道当前日期时间（"明天派发"类时间表述依赖猜测）、不知道集群当前
有哪些节点在线（简单调度问题也要调一次技能）。

与用户对齐概念后确认：环境信息 = 模型对话时应当知道的客观系统事实（日期时间、
集群在线状态）；注入方式采用 tilacode Context Epoch 的轻量复刻——**Baseline 不可变
+ 变化增量**，保证 provider cache（前缀缓存）会话内命中最优。

## 决策

与用户确认（2026-09-21）：

1. **环境块内容**（两项，用户选定）：
   - 日期时间：`YYYY-MM-DD（星期X）HH时`（粒度到小时——分钟级会导致增量泛滥），
     含时区说明；
   - 集群概况：在线节点（starting/idle/busy）按 agent_type 分组统计；
   - 使用中模型标识随块一并注入。
2. **注入策略（Baseline + 增量）**：
   - 首轮：环境块拼入 system baseline 尾部，快照落会话行 `context_environment`
     （JSONB：`{block, updated_at}`）；
   - 后续轮：生成当前实时块与快照**字符串对比**——相同不注入；不同则在
     prompt_messages 尾部追加一条增量 system 消息
     （`<environment-update>` 包裹）并更新快照；
   - system baseline 中的环境块**始终使用快照版本**（而非实时），保证会话内
     system 前缀完全稳定，cache 命中最优；
   - 增量消息不落库：环境是可再生的确定性事实，每轮重算对比，只传最新状态。
3. **会话行新列**：`task_building_sessions.context_environment` JSONB（迁移一枚）。
4. **无会话对话**（session_id 为空）：环境块每轮直接注入 system（无快照语义，
   简化处理）。
5. **降级**：集群概况查询失败时环境块降级为仅日期时间，不中断对话。

明确不做：完整 Context Epoch 持久化体系；AGENTS.md 项目指令发现（中枢无项目目录
语义）；环境块分钟级时间（增量泛滥）。

## 备选方案

| 方案 | 结论 |
|------|------|
| A. Baseline（快照）+ 尾部增量 system 消息（本决策） | 采纳。前缀稳定 cache 最优，增量仅变化时出现 |
| B. 每轮重建 system 含实时环境块 | 否决。环境变化即整段前缀 cache 失效，长会话成本高 |
| C. 环境块进压缩/历史消息持久化 | 否决。环境是可再生事实，持久化徒增消息结构复杂度 |

## 影响

### 数据库

- 迁移一枚：`task_building_sessions.context_environment` JSONB NULL。

### 中枢后端

- `app/services/context_environment_service.py`（新增）：`build_environment_block()`
  （日期时间 + 集群概况查询聚合）、快照对比与增量生成。
- `app/models/task_building_session.py`：新列。
- `app/agents/orchestrator.py`：chat_stream 接入（快照读取 → baseline/增量决策 →
  快照落库）；`_build_messages` 增 environment_block 参数拼入 system 尾部。

### 前端

- 无（system 注入对前端透明；增量消息 role=system 不落库不透传历史）。

### 兼容性

- 存量会话（无 context_environment）首轮即建立快照，行为自然过渡；
- 技能开关变化仍走 system 重建（罕见操作，cache 失效可接受，不纳入增量源）。

## 实际实施情况

- 全部按决策实施（2026-09-21 完成），无决策偏差。
- 实施期间发现：手动迁移与测试子进程 alembic 并发竞争导致版本表/schema 短暂不一致
  （测试误报 DuplicateColumnError），属操作时序问题非代码缺陷，规避后通过。
- 端到端验证：模型准确回答当前日期时间（跨日场景）与集群在线概况（零技能调用）。
- 测试：环境服务 7 项 + orchestrator 集成 4 项全过（累计 31 项相关测试）。

## 关联文档

- [040-orchestrator-agent-loop-roadmap.md](../spec/040-orchestrator-agent-loop-roadmap.md)（P2）
- [ADR-041](./041-context-management-v1.md)（P1，同一上下文管理脉络）
- [ADR-040](./040-orchestrator-agent-loop-v1.md)（轨迹回流）

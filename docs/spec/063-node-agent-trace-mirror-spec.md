# 节点 Agent 会话轨迹镜像 Spec（群聊式折叠块 + `@节点` 对话）

> 本文档配套 [ADR-063](../adr/063-node-agent-trace-mirror.md)，描述用户故事、核心流程、降级链路与验收标准。
> 实施明细见 [063-node-agent-trace-mirror-changes.md](./063-node-agent-trace-mirror-changes.md)；测试清单见 [063-node-agent-trace-mirror-harness.md](./063-node-agent-trace-mirror-harness.md)。
> 实施进度：M1–M5 已全部完成（2026-10-02）；遗留真实环境 E2E 手测项见 Harness §4。

## 1. 功能描述

把节点 agent 的会话轨迹（思考/正文/工具调用/待办/用量/状态）结构化上报中枢并落库，
在会话流里以「群聊式折叠块」实时旁观（只读），支持回看与 `@节点` 对话。六类轨迹行：

| kind | 内容 | 来源 |
|---|---|---|
| `status` | 轮次开始/结束、阶段、当前动作、模型、工作目录、上下文用量 | 节点既有进度/用量上报 |
| `reasoning` | 节点 agent 的思考片段（增量分段） | tilacode 会话 part |
| `text` | 节点 agent 的正文片段（交付说明、结论） | 同上 |
| `tool` | 工具名 + 入参/结果摘要 + 退出码 | 同上 |
| `todo` | 待办列表快照（变化时） | 节点 todo 上报 |
| `usage` | 单轮 token/费用增量 | ADR-061 M5a 用量通道 |

## 2. 用户故事

- 作为用户，我在中枢会话里就能看到节点「在干什么、怎么想的」，不必自己开终端登节点。
- 多节点协作时，每个节点一个折叠块，像群聊一样各自汇报工作进度；默认折叠不干扰会话流。
- 任务结束后我仍能回看完整轨迹（节点离线也能看）。
- 我可以对**执行中**的节点 @ 补话（steer，不中断任务）；对**空闲**的节点 @ 开新一轮任务。

## 3. 核心流程

```
节点 tilacode SSE 事件 ──► EventTranslator（分类 reasoning/text/tool）
                              │  AgentTraceCollector：seq 统一 + 32 行/1s 攒批
                              │  递归脱敏（Bearer/sk-/凭据赋值/敏感键）+ UTF-8 头尾截断
                              ▼
              POST /api/v1/tasks/{id}/agent-trace（批量、(task_id, seq) 幂等）
                              ▼
                 中枢 task_agent_trace 落库（8KB 截断 / 5000 行上限）
                              ▼
      前端折叠块展开时每 ~1s GET .../agent-trace?after_seq=N 增量拉取（收起即停）
                              ▼
            NodeTraceBlock 按 seq 渲染（只读、跟随/上滚暂停、加载更早）

@节点（执行中）──► POST /v1/tasks/{id}/steer（复用 ADR-061 M5a，不中断）
@节点（空闲）───► POST /v1/tasks/{id}/node-chat（同根任务链建后续任务，同工作目录定向派发）
```

## 4. 降级链路

| 故障 | 行为 |
|---|---|
| 轨迹上报失败 | 整批回队重试；持续失败只告警，**绝不影响任务执行与完成判定**（旁路通道红线） |
| 上报队列积压（>1024 行） | 丢最老并补一条 `status` 告知行（背压保护） |
| 单行 payload 超限 | UTF-8 字节级头尾截断（前 6144B + `truncated` 标记 + 后 1536B）；读取侧坏行兜底 `{"raw": ...}` |
| 前端轮询失败 | 退避自愈，恢复后按 `after_seq` 续拉 |
| 节点离线 | 折叠块仍可回看已落库轨迹（D3 整体落库） |
| 任务 running 时误调 node-chat | 中枢 409 并提示走 steer（前端已分流） |
| 敏感信息 | apiKey/token/`.env` 内容/`sk-` 形态串统一替换 `***`，不落原文（红线） |

## 5. 验收标准

1. 派发真实任务后 `select count(*) from task_agent_trace` > 0，至少出现 `status`+`tool`（真实任务含 `reasoning`/`text`）；
2. 重复 POST 同 `(task_id, seq)` 返回 `ignored`，不产生重复行；
3. 折叠块**默认折叠**；块头显示「节点徽标 · 节点名 · 任务名 · 最新动作 · 进度 · 行数」；展开按 seq 渲染、**只读无输入框**；
4. 展开时 ~1s 增量追加；上滚暂停跟随并提示「有新输出」，点击恢复；收起即停止轮询（Network 面板可证）；
5. 刷新/断线后重新展开仍能按 `after_seq` 取回全部历史；长任务顶部「加载更早」可补齐；
6. `@` 执行中节点走 steer（任务状态保持 running）；`@` 空闲节点开启新一轮（parent=原任务、同工作目录定向派发）；
7. 脱敏抽查：构造含 `sk-` 的输出，落库行内不含明文（Runbook §4）；
8. 三端回归全绿：节点 `pytest tests -q` + ruff；中枢相关集合 pytest + ruff；前端 vitest + `tsc -b` 零错误。

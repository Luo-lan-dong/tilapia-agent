# 上下文管理 v1 Harness

> 关联：[ADR-041](../adr/041-context-management-v1.md) · [Spec](./041-context-management-v1-spec.md) · [Changes](./041-context-management-v1-changes.md)
> **修订 2026-09-27（P0）**：新增 §2.5（计量校准与触发重构）与 §3 IT-5/IT-6；改动明细见 [041-context-management-p0-changes.md](./041-context-management-p0-changes.md)。

## 1. 测试金字塔

```
      E2E（1）           长对话压缩闭环 + 占用圆环（浏览器）
       集成（6）          压缩投影对话继续 / overflow 重跑 / usage 事件 / 回查闭环
                          / 校准回写闭环（IT-5） / 触发不空转（IT-6）        ← P0 新增 2
      单元（22+）         估算/触发/摘要组装/投影切分/技能过滤/窗口字段
                          / 密度校准与夹取 / should_compact_now（P0 新增 8）
```

## 2. 单元测试清单

### 2.1 ContextCompactionService（tests/services/test_context_compaction.py）

| 编号 | 用例 | 断言要点 |
|------|------|---------|
| UT-1 | chars/4 估算 | estimate_tokens 精确等于 len/4 |
| UT-2 | 预压触发判定 | used > window−8192−20000 → True；边界内 → False |
| UT-3 | 切分点计算 | 尾部 recent≈8000 token 之内消息保留，其余进 head |
| UT-4 | 序列化格式 | user/assistant/tool 前缀正确；工具结果超 2000 字符截断 |
| UT-5 | 五段摘要模板 | build_prompt 含五段标题与全部规则；锚定旧摘要文本注入 |
| UT-6 | 压缩落库 | context_compaction 列写入 {summary_text, message_count}；messages 列原文不变 |
| UT-7 | 压缩投影 | _build_messages 有压缩点时输出 = [checkpoint(user)] + messages[message_count:] |
| UT-8 | 无压缩点回归 | 行为与 ADR-040 现状一致 |
| UT-9 | 摘要失败跳过 | compact 内部 LLM 异常 → 不抛出、返回 False、对话继续 |

### 2.2 context_usage（tests/agents/test_orchestrator.py 增补）

| 编号 | 用例 | 断言要点 |
|------|------|---------|
| UT-10 | usage 透传事件 | 流结束 yield context_usage，used=usage.prompt_tokens、percent 计算正确 |
| UT-11 | 无 usage 估算回落 | usage 缺失时 used=估算值 |
| UT-12 | overflow 兜底重跑 | 首轮 400 含长度关键词 → compact 后重跑成功、对话不中断 |
| UT-13 | 普通 400 不误触发 | 错误无长度关键词 → 直接 error 收尾（不压缩） |

### 2.3 history_recall 技能（tests/skills/test_history_recall.py）

| 编号 | 用例 | 断言要点 |
|------|------|---------|
| UT-14 | keyword 匹配 | 命中含关键词的消息，content 截 500 字符 |
| UT-15 | role 过滤 + limit | 组合过滤正确；无 keyword 返回最近 N 条 |
| UT-16 | 无会话/无匹配 | 优雅返回提示文本不崩溃 |

### 2.4 窗口元数据（tests/api/v1/test_llm_providers 增补）

| 编号 | 用例 | 断言要点 |
|------|------|---------|
| UT-17 | context_window 读写 | 创建/更新接口接受并保存；默认 65536 |

### 2.5 计量校准与触发重构（P0 修订；tests/services/test_context_compaction.py + tests/agents/test_orchestrator.py）

| 编号 | 用例 | 断言要点 |
|------|------|---------|
| UT-18 | 密度参数化估算 | `estimate_tokens(text, chars_per_token=4)` 精确等于 `len/4`；未传参时等于既有 `CHARS_PER_TOKEN` 行为（回归，替代原 UT-1 的常数硬断言） |
| UT-19 | 校准公式 | `calibrate_chars_per_token(5000, 2000, None)` → `2 + 0.3×(2.5−2) = 2.15`；`previous` 为 None 时以常数 2 起算 |
| UT-20 | 校准夹取 | 观测密度 0.5 → 结果 1.5；观测 20.0 → 结果 6.0；不产生越界值 |
| UT-21 | 校准噪声门槛 | `real_prompt_tokens` 为 0 / 1999 / None → 返回 previous 原值（不校准）；2000 → 参与校准 |
| UT-22 | 密度注入估算 | 同一 messages 在 `chars_per_token=2` 与 `=2.5` 下的估算比为 2.5/2（线性可预期） |
| UT-23 | `should_compact_now` 无新增 | 既有 `message_count` 已覆盖本次 head → False（配合既有防重复判定同源） |
| UT-24 | `should_compact_now` 有新增 | 越线且 head 较 `message_count` 有新增 → True；未越线 → False |
| UT-24b | head 切分口径 | head 必须按**会话原始历史**切分（与摘要端防重复同源）；按投影后请求切分会因 checkpoint 挤占 recent 而使 head 变短，导致判定与摘要端错配 |
| UT-24c | 压力口径 | 压力未越线（投影后请求很小）时即便原始历史很大也不推进——判定读实际请求，不再读原始全量历史 |
| UT-25 | 判定用投影后消息（orchestrator） | 构造「压缩点已覆盖全部历史」的会话：判定输入为 `prompt_messages`，不得因原始 `messages` 未变而重复判定为真 |
| UT-26 | 事件条件化（orchestrator） | `should_compact_now` 为 False 时事件序列**不含** `context_compacting`，但含 `context_compacted(compacted=false)`；为 True 时两者成对 |
| UT-27 | 校准回写（orchestrator） | usage 返回 `prompt_tokens=5000` 时，会话 `context_compaction.est_chars_per_token` 被写为校准值；usage 缺失时不写 |

## 3. 集成测试

| 编号 | 用例 | 方式 |
|------|------|------|
| IT-1 | 压缩后对话继续 | 构造 40+ 条历史 + 小窗口（配 1024）→ 触发压缩 → 第二轮对话成功且 prompt 首条为 checkpoint |
| IT-2 | 原文保留 | 压缩后 messages 列长度与内容不变（快照对比） |
| IT-3 | 回查闭环 | 压缩后问早期细节 → mock 模型调用 history_recall → 结果回流 → 回答引用原文 |
| IT-4 | SSE 事件序列 | meta → … → context_usage → done 顺序正确，percent 数值范围合法 |
| IT-5 | 校准回写闭环（P0） | 真实/mock usage 连续多轮 → `context_compaction.est_chars_per_token` 单调收敛且落在 `[1.5,6.0]`；下一轮判定使用该值 |
| IT-6 | 触发不空转（P0） | 构造「越线 → 压缩成功 → 再跑一轮」：第二轮不再下发 `context_compacting`，且不再调用摘要 LLM（以 mock client 调用计数断言）。**前提**：第二轮请求（checkpoint + recent 尾部）需自身越线才会进入推进判定；压缩后请求通常被压回触发线以下，此时不判定推进属期望行为 |

## 4. E2E

| 编号 | 场景 | 验证 |
|------|------|------|
| E2E-1 | 浏览器长对话：连续多轮对话撑大上下文 | 圆环百分比持续上升；触发压缩后对话继续；追问早期细节模型回查原文 |

## 5. CI 门禁

- pytest tests/services/test_context_compaction.py tests/skills/test_history_recall.py
  tests/agents/test_orchestrator.py tests/api/v1/test_llm_providers 全绿；
- ruff format/check 通过；前端 tsc + eslint 通过。

### 5.1 P0 修订新增门禁

- UT-18~UT-27 全部落地且通过（含 UT-18 对既有常数行为的回归保护）；
- **守卫用例**：构造「越线但 head 无新增」场景，断言 `context_compacting` 事件
  出现次数为 0（防「每轮空转提示」回归）；
- **守卫用例**：断言本次变更未引入迁移文件（`alembic/versions/` 无新增），
  以固化「复用既有 JSONB 列」的设计约束；
- 密度校准的夹取边界（1.5 / 6.0）与噪声门槛（2000）双侧边界用例必须存在。

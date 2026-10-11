# 压缩投影边界与摘要热前缀复用 Harness（P1）

> 关联：[ADR-054](../adr/054-compaction-boundary-and-prefix-reuse.md) · [Spec](./054-compaction-boundary-prefix-spec.md) · [Changes](./054-compaction-boundary-prefix-changes.md)

## 1. 测试金字塔

```
      E2E（1）           长任务压缩后继续（无 400 中断）+ 摘要命中率观测
       集成（3）          切点落在工具轮的完整对话 / 三级降级链 / 存量脏点可开
      单元（12+）         safe_cutoff 边界与方向 / 投影不变量 / 前缀式请求组装
                          / 指令文本 / 落库字段
```

## 2. 单元测试清单

### 2.1 safe_cutoff（tests/services/test_context_compaction.py）

| 编号 | 用例 | 断言要点 |
|------|------|---------|
| UT-28 | 切点在 `tool` 上 → 前移到父级 assistant 之前 | 输入 `[user, assistant(tool_calls), tool, tool, user]`，`cutoff=2` → 返回 1 |
| UT-29 | 切点在 `tool` 上（多 tool 同轮） | `cutoff=3`（第二个 tool）→ 同样返回父级 assistant 的下标 1 |
| UT-30 | 切点不在 `tool` 上 → 原样返回 | `cutoff=0/1/4`（user/assistant/user）→ 原值 |
| UT-31 | 方向不变量 | 对随机/构造序列，`safe_cutoff(m, c) <= c` 恒成立 |
| UT-32 | 边界与脏数据 | `cutoff=0`、`cutoff=len(messages)`、越界值先 clamp；找不到父级时原样返回含 `tool` 的切点（不抛错） |
| UT-33 | 前移后 head 为空 | 整个历史只有一轮工具且切点在其中 → 校正到 0 → `compact_if_needed` 不压缩（`None, 0`） |

### 2.2 投影不变量（tests/services/test_context_compaction.py）

| 编号 | 用例 | 断言要点 |
|------|------|---------|
| UT-34 | 投影首条历史不为 tool | 任意压缩点（含脏 `message_count`）下，`projected[1]["role"] != "tool"` |
| UT-35 | 存量脏点可开 | 直接给 `project_messages` 传 `message_count` 落在 `tool` 上的压缩点 → 结果仍合法（动态校正生效） |
| UT-36 | 无压缩点回归 | 行为与既有 UT-8 一致 |

### 2.3 摘要前缀式调用（tests/services/test_context_compaction.py + tests/clients/test_deepseek.py）

| 编号 | 用例 | 断言要点 |
|------|------|---------|
| UT-37 | 真前缀组装 | 摘要请求 messages[0] 等于传入的真实 system 文本；messages[1:1+len(head)] 与 head 逐字相等；末条为压缩指令 user |
| UT-38 | tools 透传 | `chat_completion_messages(..., tools=[...])` 时 payload 含 `tools`；不传时 payload 不含该键（向后兼容） |
| UT-39 | 指令文本 | `build_summary_instruction` 含五段模板标题、规则要点；有旧摘要时含锚定段 |
| UT-40 | 落库字段 | 压缩成功后 `context_compaction` 含 `cutoff_role`，且 `message_count` 等于安全切点 |

### 2.4 降级链（tests/services/test_context_compaction.py）

| 编号 | 用例 | 断言要点 |
|------|------|---------|
| UT-41 | 第一优先级失败 → 第二优先级 | mock 首次调用抛异常 → 第二次调用不带 tools 且成功 |
| UT-42 | 前两档失败 → 第三优先级 | mock 前两次抛异常 → 第三次为合成 system + 单条 user 形态 |
| UT-43 | 三档全失败 | 三次均异常 → 返回 `(False, 原压缩点)`，不抛出 |
| UT-44 | 空返回视为失败 | 返回 `""` / `"   "` → 进入下一优先级（不落库空摘要） |

## 3. 集成测试

| 编号 | 用例 | 方式 |
|------|------|------|
| IT-7 | 切点落在工具轮的真实对话 | 构造历史使其压缩切点必然落在 `tool` 上 → `chat_stream` 一轮 → 无 `error` 事件、以 `done` 收尾、`context_compacted(compacted=true)`，且第二次 LLM 调用的 messages 不含以 `tool` 开头的序列 |
| IT-8 | 三级降级链端到端 | mock client 控制三级失败点，断言压缩结果与对话终态 |
| IT-9 | 存量脏点会话仍可对话 | 预置 `message_count` 落在 `tool` 上的会话 → 跑一轮 → 无 400、无 `error` |

## 4. E2E

| 编号 | 场景 | 验证 |
|------|------|------|
| E2E-2 | 浏览器：长任务对话连续多轮（含工具调用）撑到自动压缩 | 压缩后对话继续、无「对话处理异常」；日志出现压缩完成事件且 `cutoff_role != "tool"`；`llm_usage_records` 中摘要调用的 cache 命中率 > 0（前缀复用生效） |

## 5. CI 门禁

- `pytest tests/services/test_context_compaction.py tests/agents/test_orchestrator.py
  tests/clients/test_deepseek.py` 全绿；
- **守卫用例**：`safe_cutoff` 方向不变量（UT-31）与「投影首条历史不为 tool」
  （UT-34）必须存在——前者防死循环，后者防 400 回归；
- **守卫用例**：断言本次变更未引入迁移文件（`alembic/versions/` 无新增）；
- ruff format/check 全过；前端零改动（无需前端门禁）。

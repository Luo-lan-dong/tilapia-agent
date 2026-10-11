# Orchestrator Agent Loop v1 Harness

> 关联：[ADR-040](../adr/040-orchestrator-agent-loop-v1.md) · [Spec](./040-orchestrator-agent-loop-v1-spec.md) · [Changes](./040-orchestrator-agent-loop-v1-changes.md)

## 1. 测试金字塔

```
        E2E（1）           真实会话跨轮回引用 + SSE 真流式观测
       集成（6）           Orchestrator 循环行为（mock 流式客户端）
      单元（12+）          流式拼装 / 回流 / 落库 / 兜底
```

## 2. 单元测试清单

### 2.1 DeepSeekClient 流式 tools（tests/clients/test_deepseek_stream_tools.py）

| 编号 | 用例 | 断言要点 |
|------|------|---------|
| UT-1 | 单工具分片拼装 | 首帧 id+name、后续帧 arguments 增量按 index 拼接为完整 tool_call |
| UT-2 | 多工具交错分片 | 两个工具的 delta 按 index 交错到达，各自独立拼装不串位 |
| UT-3 | 正文+思考+工具混合流 | content/reasoning_content/tool_calls 同时增量时三类事件均正确产出 |
| UT-4 | usage 末帧统计 | include_usage 末帧 usage 正常落库（复用 ADR-038 逻辑） |
| UT-5 | 流式 4xx 错误 | 流式错误响应抛 DeepSeekAPIError 且信息截断 |
| UT-6 | tool_choice 透传 | payload 正确携带 tool_choice="none" 且 tools 为空 |

### 2.2 历史回流 _build_messages（tests/agents/test_orchestrator.py 增补）

| 编号 | 用例 | 断言要点 |
|------|------|---------|
| UT-7 | 含工具轨迹的历史回传 | assistant.tool_calls 与 role:"tool" 消息原样进入 prompt_messages |
| UT-8 | 旧格式兼容 | 无 tool_calls 的历史行为与现状一致（steps/thinking 剥离） |
| UT-9 | 脏数据兜底 | tool_calls 缺 tool 响应时自动补空 tool 消息 |
| UT-10 | spec 上下文注入保留 | current_specification 注入逻辑不受重构影响 |

### 2.3 循环行为（tests/agents/test_orchestrator.py 增补，mock 流式客户端）

| 编号 | 用例 | 断言要点 |
|------|------|---------|
| UT-11 | 纯文本轮真流式 | chunk 事件与流式 delta 一致（非 4 字符切块），首 chunk 早于流结束 |
| UT-12 | 工具轮续行 | 有 tool_calls 时执行技能 → tool 消息入 prompt → 第二轮流式调用 |
| UT-13 | thinking 真流式 | reasoning delta 逐段转发 thinking 事件 |
| UT-14 | 达上限三重禁用 | 最后一轮 tools=[]、tool_choice="none"、FORCE_FINAL_PROMPT 注入 |
| UT-15 | DSML 修正轮流式 | 伪工具标记触发不带 tools 的流式重试，仍失败剥离标记 |
| UT-16 | 本轮轨迹产出 | 内部轨迹事件按 [assistant(tool_calls), tool×N] 顺序产出 |

### 2.4 落库协议（tests/api/v1/test_task_building_session.py 增补）

| 编号 | 用例 | 断言要点 |
|------|------|---------|
| UT-17 | 轨迹按序落库 | JSONB 中 assistant(tool_calls) → tool × N → assistant(回复) 顺序正确 |
| UT-18 | specification 不覆盖 | 工具轮对话未触发文档生成时会话三件套保留（回归） |
| UT-19 | 无轨迹轮兼容 | 纯文本轮落库结构与现状一致（回归） |

## 3. 集成测试清单

| 编号 | 用例 | 方式 |
|------|------|------|
| IT-1 | TestClient SSE 全链路：文本轮 | 注册 mock 流式 provider → 断言事件序列 meta→thinking×N→chunk×N→done |
| IT-2 | TestClient SSE 全链路：工具轮 | mock 两轮流式（工具轮+文本轮）→ 断言 step_started/finished 位置与最终回复 |
| IT-3 | 会话恢复回流 | 首轮工具对话落库后，携 JSONB 历史发起第二轮 → 断言 LLM 收到的 messages 含 tool 轨迹 |
| IT-4 | 技能回归 | find_idle_nodes / get_task_status / generate_spec_docs 全部既有断言通过 |
| IT-5 | 4xx 无 tools 降级 | mock 首次流式 400（tool 相关）→ 断言降级非流式重试成功 |
| IT-6 | 流中断降级 | mock 流中途抛异常 → 断言 error + done 事件且不崩溃 |

## 4. E2E 测试场景

| 编号 | 场景 | 验证 |
|------|------|------|
| E2E-1 | 浏览器真实对话：第一问触发查节点技能，第二问"刚才那个节点呢" | 步骤卡片正常；第二问回复引用第一轮工具结果；网络面板 chunk 为流式增量 |

## 5. CI 门禁

- `pytest tests/agents tests/clients tests/api/v1/test_task_building_session.py tests/skills` 全绿；
- `ruff format --check` + `ruff check` 通过；
- 前端无需改动，不设前端门禁（协议不变）。

## 6. 回归范围

- orchestrator 既有 12 项测试（含同轮 generate→create 回归）必须全过；
- deepseek 既有测试全过；
- task_building_session / skill 相关测试全过。

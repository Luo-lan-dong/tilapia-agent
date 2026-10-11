# 三件套生成协议重构 Harness

> 关联 ADR-046 / spec-builder-markdown-three-round-spec。测试框架：pytest + asyncio（后端）。

## 1. 测试金字塔

```
        E2E（scripts/e2e，手动触发）
       ─────────────────────────
        集成（技能层/端点层回归，存量）
       ─────────────────────────
        单元（test_spec_builder.py 重写，本 Harness 核心）
```

## 2. 单元测试清单（tests/agents/test_spec_builder.py）

### 2.1 对话判定轮

| 用例 | 断言 |
|------|------|
| UT-1 ready 路径 | 正文剥离 `<decision>` 标签后经 chunk 下发；判定 ready |
| UT-2 need_info 路径 | chunk 为追问文本；status{is_ready_to_create:false}；无 specification_delta |
| UT-3 标签缺失兜底 | 无标签输出视为 need_info（安全侧） |
| UT-4 thinking 透传 | reasoning 增量 → thinking 事件，且不混入正文 chunk |

### 2.2 文档轮

| 用例 | 断言 |
|------|------|
| UT-5 真流式增量 | 三字段均有 specification_delta，拼接 == 全量 specification |
| UT-6 前序文档上下文 | tasks 轮 prompt 含 spec 全文；checklist 轮含 spec+tasks |
| UT-7 草案增量基础 | 文档轮 prompt 含当前草案 |
| UT-8 完成 status | 成稿后 status{is_ready_to_create:true} 在全量 specification 之前/之后位置正确，done 收尾 |
| UT-9 编写提示 | 进入文档轮时下发 status{message:"正在编写规范文档…"} |

### 2.3 失败语义

| 用例 | 断言 |
|------|------|
| UT-10 判定轮 API 失败 | error + done，无后续轮调用 |
| UT-11 文档轮 API 失败 | error + done，无全量 specification |
| UT-12 客户端调用无 JSON Mode | 各轮调用 response_format 均为 None |

### 2.4 非流式兼容

| 用例 | 断言 |
|------|------|
| UT-13 chat() 返回结构 | ai_reply/specification/is_ready_to_create/model/mode 字段齐全且值正确 |

## 3. 集成回归（存量，不新增）

- `tests/skills/test_spec_generation.py`：技能转发 error/chunk/specification 等事件与上下文回写（上次修复的守卫不回退）。
- `tests/api/v1/test_task_building.py`：`/chat` 非流式与 `/chat/stream` 端点。

## 4. E2E 场景（手动，scripts/e2e）

| 场景 | 步骤 | 预期 |
|------|------|------|
| E2E-1 成稿真流式 | 新会话输入完整需求 → 观察面板 | 过渡语先出现，三份文档逐字实时生成，创建按钮点亮，任务可创建派发 |
| E2E-2 追问路径 | 输入模糊需求（"做个网站"） | AI 追问，面板不变，补充后成稿 |

## 5. CI 门禁

- `pytest tests/agents/test_spec_builder.py tests/skills/test_spec_generation.py tests/api/v1/test_task_building.py` 全过
- 全量 pytest 不因本改动新增失败

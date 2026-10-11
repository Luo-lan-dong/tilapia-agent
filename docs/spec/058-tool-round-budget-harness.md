# 工具轮预算改为进度驱动 Harness

> 关联：[ADR-058](../adr/058-tool-round-budget-progress-driven.md) · [Spec](./058-tool-round-budget-spec.md) · [Changes](./058-tool-round-budget-changes.md)

## 1. 测试金字塔

```
      E2E（1）           真实部署类多阶段任务不再被 8 轮误杀
       集成（3）          多阶段侦察跑完 / 无进展收尾 / 逼近上限警示
      单元（9+）          默认值 / 无进展计数 / 警示阈值 / 上限语义 / 既有防线回归
```

## 2. 单元测试清单（tests/agents/test_orchestrator.py）

| 编号 | 用例 | 断言要点 |
|------|------|---------|
| UT-75 | 默认上限为 40 | `Settings().orchestrator_max_tool_rounds == 40`；`orchestrator_no_progress_rounds == 4`；`orchestrator_rounds_warning_ratio == 0.75` |
| UT-76 | 配置边界校验 | `orchestrator_no_progress_rounds=1` → 校验失败；`=2` 通过。`orchestrator_rounds_warning_ratio=0` / `1.5` → 失败；`=0.5` 通过 |
| UT-77 | **多阶段侦察不被误杀（本修复核心）** | 构造连续 8 轮、每轮调用**不同**技能（模拟配置服务器→查清单→查指引→定位产物…），设 `max_rounds=8`：**不应**出现「最大技能调用轮数」收尾；第 8 轮后自然进入下一轮或正常收尾 |
| UT-78 | 无进展计数触发 | 连续 `no_progress_rounds`（设 2）轮调用**同一批**技能（参数不同）→ 收尾，且收尾前注入的提示含「未获得新信息」而非「轮数上限」 |
| UT-79 | 新技能清零计数 | 无进展 1 轮后，下一轮引入新技能 → 计数清零，不触发收尾 |
| UT-80 | 失败返回也算「已返回」 | 技能返回失败但为首次调用 → 计为新技能（不累积无进展） |
| UT-81 | 逼近上限警示一次 | `max_rounds=4`、比例 0.75 → 第 3 轮下发一次 `warning`；继续到第 4 轮不重复下发 |
| UT-82 | 上限收尾语义不变 | 达到 `max_rounds` → 最后一次请求 `tools` 为空且 `tool_choice="none"`，日志含 `orchestrator_max_rounds_reached` |
| UT-83 | Doom Loop 回归 | 连续 3 轮完全相同调用 → 行为与 ADR-043 一致（收尾文案「检测到重复的工具调用」） |
| UT-84 | 技能超时回归 | 技能超时 → `step_finished(failed)` + 循环继续（ADR-043 不变） |
| UT-85 | 进度判定优先于上限 | 两者同轮满足时，以「无进展」收尾（文案与日志为 no_progress） |

## 3. 集成测试

| 编号 | 用例 | 方式 |
|------|------|------|
| IT-15 | 多阶段任务端到端 | 脚本化 10 轮不同技能 + 最终文本回答 → 全程无 error、无上限收尾、末轮为纯文本 |
| IT-16 | 无进展端到端 | 脚本化「同批技能 × 4 轮」→ 收尾文案含「未获得新信息」，且 `steps` 中每次调用都有 `step_finished` |
| IT-17 | 警示事件透传 | SSE 序列中出现一次 `warning`，文案含「已使用 N/M 轮」；且该事件不破坏后续 `chunk`/`done` 顺序 |

## 4. E2E

| 编号 | 场景 | 验证 |
|------|------|------|
| E2E-5 | 真实部署类任务（宝塔 MCP 已注册） | 从「配置服务器」到「输出部署规划」全程不再被轮数上限打断；若最终仍达上限，日志能明确给出 `rounds_used` 与原因 |

## 5. CI 门禁

- `pytest tests/agents/test_orchestrator.py tests/services/test_context_compaction.py` 全绿；
- **守卫用例**：UT-77 必须存在——它是本 ADR 的核心验收，防「轮数上限又变回误杀源」；
- **守卫用例**：UT-83 / UT-84 必须存在——防为提高上限而削弱 Doom Loop / 超时防线；
- **守卫用例**：UT-82 必须断言 `tools` 为空 + `tool_choice="none"`——防三重禁用收尾被改坏；
- ruff format/check 全过；前端零改动（无需前端门禁）。

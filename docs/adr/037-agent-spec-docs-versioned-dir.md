# ADR-037: 三件套规范文档版本化目录（docs/specs/<标题-短ID>/）

## 状态

Accepted

## 日期

2026-09-17

## 问题背景

任务链迭代时，中枢随派发下发最新三件套（spec/tasks/checklist），Agent 端
`write_specification_docs` 每次都覆盖写入工作目录 `docs/` 固定路径——
上一轮（乃至更早轮次）的需求文档被覆盖丢失，无法追溯"每轮任务当时依据
的是哪版文档"。用户参照 `.trae/specs` 的组织方式（每个 spec 一个独立
目录，内含三件套文件），要求版本化保存。

另有一处既有文案失真：迭代链分支 prompt 恒写"完整需求文档见 docs/
（已更新为最新版）"，但简单增量修改不重新生成文档时（ADR-036 按需生成
策略）docs/ 并未更新，误导 AI。

## 决策

1. **版本化目录**：三件套写入 `docs/specs/<净化中文标题>-<任务ID前8>/`
   （spec.md / tasks.md / checklist.md），与 ADR-036 工作目录可读命名
   规则一致（同一净化函数语义：非法字符集 `/\:*?"<>|`、空白转 `-`、
   压缩连字符、截断 20 字、空串兜底 `task`）。任务 ID 前 8 位直接锚定
   本轮迭代任务，追溯最强。
2. **Agent 端自推导 slug，无协议变更**：标题源为派发 payload 现有的
   `task_name`（兜底 `description`），不加新字段、不动 api-standards。
   两端标题源不同（中枢用 requirement）导致标题段可能不同，以 task_id
   前缀锚定追溯。
3. **快照语义**：每轮带 `specification` 的派发生成一个版本目录；同任务
   改派重跑写同一目录（幂等覆盖，语义即"本轮文档更新"）；简单修改未
   重新生成文档时不产生新目录。存量旧 `docs/spec.md` 不迁移。
4. **prompt 同步修正**：迭代分支按 `doc_paths` 是否为空区分"本轮文档
   已更新至 <路径>"与"本轮未重新生成文档，历史文档见 docs/specs/ 各
   目录"，消除失真；首轮分支模板不变（路径自适应）。

## 备选方案

| 方案 | 结论 |
|------|------|
| 固定路径 + 时间戳后缀文件名 | 否决。三文件各自带时间戳易散乱，目录级归组更符合 `.trae/specs` 习惯 |
| 中枢下发 spec_doc_dir 字段 | 否决。Agent 端可从现有 payload 自行推导，协议变更是不必要的复杂度 |
| 英文 slug（与 .trae/specs 完全同款） | 否决。中文标题需翻译，实现复杂且翻译可能不准（与 ADR-036 同结论） |
| 中枢会话草稿也加版本历史 | 否决（用户确认）。范围仅 Agent 端；草稿保持单值最新 |

## 影响

### Agent 端

- `workspace.py`：新增 `_sanitize_title_for_dir` / `spec_docs_relative_dir`；
  `_SPEC_DOC_PATHS` 值改纯文件名；`write_specification_docs` 增 `doc_dir` 参数。
- `task_bridge.py`：`_prepare_session` 计算 doc_dir 并传入；`_build_prompt`
  三分支文案按 doc_paths 区分。
- 产物回传（file_tree / export.zip / file_fetch）零改动：file_scanner
  不排除 docs/ 子目录，`docs/specs/` 自然随产物回传。

### 中枢 / 前端

- 零改动（会话草稿保持现状；前端文档面板不动）。

### 兼容性

- 存量链续接：旧 `docs/spec.md` 仍在工作目录（不迁移），新 prompt 显式
  指向 `docs/specs/<slug>/`，避免 AI 误读旧文档。
- 旧版本中枢 + 新版本 Agent：payload 无变化，完全兼容；新版本中枢 +
  旧版本 Agent：旧 Agent 写 docs/ 固定路径（现状行为），可接受。

## 关联文档

- [ADR-012](./012-tilacode-execution-layer-integration.md)（"AI 更新三件套（全量）"语义，本决策将其落盘路径版本化）
- [ADR-036](./036-session-task-chain-and-spec-command.md)（可读命名规则来源）
- [agent-spec-docs-versioned-dir-spec](../spec/037-agent-spec-docs-versioned-dir-spec.md) /
  [harness](../spec/037-agent-spec-docs-versioned-dir-harness.md) /
  [changes](../spec/037-agent-spec-docs-versioned-dir-changes.md)

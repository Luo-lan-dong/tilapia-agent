# 三件套规范文档版本化目录 Spec（agent-spec-docs-versioned-dir）

> 关联决策：[ADR-037](../adr/037-agent-spec-docs-versioned-dir.md)
> Harness：[037-agent-spec-docs-versioned-dir-harness.md](./037-agent-spec-docs-versioned-dir-harness.md)
> Changes：[037-agent-spec-docs-versioned-dir-changes.md](./037-agent-spec-docs-versioned-dir-changes.md)

## 1. 功能描述

Agent 端任务工作目录中，三件套规范文档不再覆盖写入 `docs/` 固定路径，
而是按本轮任务写入版本化目录：

```
docs/specs/<净化中文标题>-<任务ID前8>/
├── spec.md
├── tasks.md
└── checklist.md
```

历史版本目录按轮次共存于 `docs/specs/` 下，随产物回传（file_tree /
export.zip）自然可见、可追溯。中枢会话草稿保持单值最新（不在本次范围）。

## 2. 用户故事

- US-1：登录页迭代三轮（浅色 → 蓝渐变 → 纯蓝），`docs/specs/` 下有三
  个版本目录，每轮任务当时依据的文档原样保留。
- US-2：简单增量修改（未重新生成三件套）不产生新目录，prompt 明确告知
  AI "本轮未重新生成文档"。
- US-3：同一任务因节点掉线改派重跑，写入同一版本目录（幂等），不产生
  重复目录。

## 3. 核心流程

```
中枢派发（payload.specification 非空）
  │
  ▼
Agent _prepare_session：
  doc_dir = spec_docs_relative_dir(task_id, task_name or description)
  write_specification_docs(ws_dir, payload.specification, doc_dir)
  │  写入 docs/specs/<slug>/spec.md|tasks.md|checklist.md
  ▼
_build_prompt（按 doc_paths 分支）：
  ├─ 首轮 + doc_paths 非空 → "需求文档（docs/specs/<slug>/…）请先阅读"
  ├─ 迭代 + brief + doc_paths 非空 → "本轮文档已更新至 <路径>，历史版本在 docs/specs/ 其他目录"
  ├─ 迭代 + brief + doc_paths 空 → "本轮未重新生成文档，历史文档见 docs/specs/ 各目录"
  └─ 迭代无 brief → "历史需求文档见 docs/specs/（多版本按目录区分）"
```

净化规则（与 ADR-036 一致）：非法字符 `/\:*?"<>|` 与空白转 `-`、压缩
连续 `-`、去首尾 `-.`、截断 20 字、空串兜底 `task`。

## 4. 验收标准

- [x] 带 specification 的派发写入 `docs/specs/<净化标题>-<task_id前8>/`，不再覆盖 `docs/` 根下旧文档。
- [x] 两轮迭代产生两个版本目录共存；export.zip / file_tree 包含全部版本。
- [x] specification 为 None 的迭代（简单修改）不产生新目录，prompt 文案不再声称"已更新为最新版"。
- [x] 同 task_id 重跑写同一目录（幂等覆盖）。
- [x] 无协议变更；中枢与前端零改动；存量旧 docs/spec.md 不迁移。

## 5. 关联文档

- [ADR-037](../adr/037-agent-spec-docs-versioned-dir.md)
- [ADR-012](../adr/012-tilacode-execution-layer-integration.md) / [ADR-036](../adr/036-session-task-chain-and-spec-command.md)

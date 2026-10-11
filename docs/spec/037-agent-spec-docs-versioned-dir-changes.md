# 三件套规范文档版本化目录 Changes（变更清单）

> 关联：[ADR-037](../adr/037-agent-spec-docs-versioned-dir.md) / [Spec](./037-agent-spec-docs-versioned-dir-spec.md) / [Harness](./037-agent-spec-docs-versioned-dir-harness.md)
>
> 按 `.trae/rules/decision-documents.md` 要求：边开发边实时登记，禁止开发完成后一次性补记。

## 变更清单

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| docs/adr/037-agent-spec-docs-versioned-dir.md | 增 | - | 决策文档 |
| docs/spec/037-agent-spec-docs-versioned-dir-spec.md | 增 | - | 功能 Spec |
| docs/spec/037-agent-spec-docs-versioned-dir-harness.md | 增 | - | 测试清单 |
| docs/spec/037-agent-spec-docs-versioned-dir-changes.md | 增 | - | 本变更清单 |
| docs/INDEX.md | 修 | 1.1/1.2/1.3/六 | 登记本次决策 |
| docs/adr/012-tilacode-execution-layer-integration.md | 修 | 修订记录 | 三件套落盘路径版本化，指向 ADR-037 |
| docs/adr/036-session-task-chain-and-spec-command.md | 修 | 修订记录 | 关联命名规则沿用说明 |
| opc-agent-programming/app/executor/workspace.py | 修 | 新增 `_DIR_TITLE_MAX_CHARS` / `_ILLEGAL_DIR_CHARS` / `_sanitize_title_for_dir` / `spec_docs_relative_dir`；`_SPEC_DOC_PATHS` 值改纯文件名；`write_specification_docs` 增 `doc_dir` 参数写入版本目录 | 三件套版本化目录落盘（ADR-037） |
| opc-agent-programming/app/executor/task_bridge.py | 修 | `_prepare_session` 计算 doc_dir 并传入；`_build_prompt` 迭代分支按 doc_paths 是否为空区分文案（修复"未生成文档却声称已更新"失真） | prompt 指向本轮版本目录 |
| opc-agent-programming/tests/test_workspace.py | 修 | write_specification_docs 用例改传 doc_dir 断言版本目录；新增 test_write_specification_docs_idempotent_same_slug / test_sanitize_title_for_dir_rules / test_spec_docs_relative_dir_format | 版本目录单元回归 |
| opc-agent-programming/tests/test_task_bridge.py | 修 | execute_task 落盘断言改版本目录路径；test_build_prompt_iteration_scenarios 扩为四分支（新增 brief+无文档用例） | 桥接层 prompt 与落盘回归 |

<!-- 开发过程中在此实时追加：文件 → 变更类型（修/增/减） → 函数/类级明细 → 作用说明 -->

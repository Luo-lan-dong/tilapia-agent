# 三件套规范文档版本化目录 Harness（测试清单）

> 关联：[ADR-037](../adr/037-agent-spec-docs-versioned-dir.md) / [Spec](./037-agent-spec-docs-versioned-dir-spec.md)

## 1. 测试金字塔

- 单元测试：净化函数规则、版本目录路径推导、写入幂等。
- 桥接测试：_prepare_session 落盘路径、_build_prompt 三分支文案。
- E2E：同链两轮迭代 + 一次简单修改，验证目录共存与回传 zip。

## 2. 单元测试清单（opc-agent-programming/tests/）

| 编号 | 用例 | 断言 |
|------|------|------|
| UT-1 | 净化函数规则 | 非法字符/空白转 -、连续 - 压缩、截断 20、空串兜底 task |
| UT-2 | spec_docs_relative_dir | `docs/specs/<净化标题>-<task_id前8>`；title None 兜底 task |
| UT-3 | write_specification_docs(doc_dir) | 三文件写入 `docs/specs/<slug>/`；None 字段跳过 |
| UT-4 | 同 slug 二次写入 | 幂等覆盖，不产生新目录 |
| UT-5 | _prepare_session 落盘 | specification 非空时写入版本目录；doc_paths 为版本目录内路径 |
| UT-6 | prompt 首轮分支 | 文案含版本目录路径 |
| UT-7 | prompt 迭代 + brief + 文档 | "本轮需求文档已更新至 <路径>" |
| UT-8 | prompt 迭代 + brief + 无文档 | "本轮未重新生成需求文档"（不再声称已更新） |
| UT-9 | prompt 迭代无 brief | "历史需求文档见 docs/specs/ 目录" |

## 3. E2E 测试场景

| 编号 | 步骤 | 预期 |
|------|------|------|
| E2E-1 | 同一会话连续两轮迭代 + 一轮简单修改 | docs/specs/ 下仅两个版本目录（两轮生成各一）；简单修改轮无新目录；export.zip 含全部版本与 login.html |

## 4. CI 门禁

- opc-agent-programming `pytest` 全量通过；`ruff format --check && ruff check`。

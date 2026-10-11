# 068 编排 AI 产物感知与文件访问 Changes

> 关联 ADR：[ADR-068](../adr/068-orchestrator-artifact-access.md) ｜ Spec：[068-orchestrator-artifact-access-spec.md](./068-orchestrator-artifact-access-spec.md) ｜ Harness：[068-orchestrator-artifact-access-harness.md](./068-orchestrator-artifact-access-harness.md)
>
> 按规则**边开发边实时登记**（文件 → 函数级明细），禁止开发完成后一次性补记。

## 变更清单

| # | 文件 | 类型 | 函数/类级明细 | 作用 |
|---|---|---|---|---|
| 1 | `docs/adr/068-orchestrator-artifact-access.md` | 增 | 全文 | ADR 决策 |
| 2 | `docs/spec/068-orchestrator-artifact-access-spec.md` | 增 | 全文 | Spec |
| 3 | `docs/spec/068-orchestrator-artifact-access-harness.md` | 增 | 全文 | Harness |
| 4 | `docs/spec/068-orchestrator-artifact-access-changes.md` | 增 | 全文 | Changes 本体 |
| 5 | `docs/INDEX.md` | 修 | 1.1/1.2/1.3 表各追加 068 行 | 决策登记 |
| 6 | `docs/changelog/2026-10.md` | 修 | 追加 068 记录 | 更新记录 |
| 7 | `opc-central-dispatch-hub/app/services/artifact_service.py` | 修 | 增 `build_zip_from_cache()`（幂等：已有 export.zip 直接返回不覆盖；排除 `.manifest.json`/`.synced.json`/`.tmp`；空缓存 RuntimeError）；增 `import zipfile` 与 `_ZIP_EXCLUDED_NAMES` 常量 | 本地打包兜底（ADR-068 §2.4） |
| 8 | `opc-central-dispatch-hub/app/skills/artifact_access.py` | 增 | 新文件：`ListArtifactsSkill`（产物状态汇总 + build_zip 兜底 + 不完整性警告）、`ReadFileSkill`（三类白名单来源解析 + UTF-8/NUL 二进制判定 + 2MB 截断）；模块级 `_resolve_task_id`（显式参数>会话 task_id>父任务）、`_resolve_readable_path`、`_find_session_attachment`、`_decode_text` | 产物感知 + 通用文件读取技能（ADR-068 §2.1/2.2） |
| 9 | `opc-central-dispatch-hub/app/skills/registry.py` | 修 | import 两技能 + `SKILL_REGISTRY` 追加 `ListArtifactsSkill`/`ReadFileSkill` | 技能注册（hub_skills 无记录默认启用，无需迁移） |
| 10 | `opc-central-dispatch-hub/app/skills/bt_call_guard.py` | 修 | `maybe_run_local_command()`：附件库未命中后追加 `_resolve_session_artifact_path()` 产物解析，hub_note 改为产物/附件双指引；新增 `_resolve_session_artifact_path()`（session_id→TaskBuildingSession.task_id→get_cached_file_path）；增 import `uuid`/`TaskBuildingSession`/`artifact_service` | Upload 代执行文件源扩展（ADR-068 §2.3，ADR-049/051 修订指向） |
| 11 | `opc-central-dispatch-hub/app/services/orchestrator_wake_service.py` | 修 | `build_wake_message()`：指令尾部追加「产物能力提示」（list_artifacts 核对交付物 / read_file 看产物 / Upload file_name=export.zip 中枢代执行） | 唤醒轮产物指引（ADR-068 §2.5） |
| 12 | `opc-central-dispatch-hub/app/services/artifact_service.py` | 修 | 增 `list_cached_files()`：manifest 优先、缺失时目录扫描兜底（排除元数据文件） | list_artifacts 清单来源兜底（UT-4） |
| 13 | `opc-central-dispatch-hub/tests/skills/test_artifact_access.py` | 增 | 新文件 19 用例：list_artifacts（UT-1~8）/ read_file（UT-9~15 + 产物优先）/ Upload 产物文件源（UT-16/19，mock subprocess）/ 唤醒消息指引（UT-22） | ADR-068 单测（实测 19 passed） |
| 14 | `opc-central-dispatch-hub/tests/skills/test_bt_call_guard.py` | 修 | UT-U3 断言适配：`未找到附件`→`未找到文件` + `list_artifacts` 指引断言 | hub_note 文案变更同步（ADR-068 §2.3） |
| 15 | `docs/adr/049-baota-mcp-deployment.md` | 修 | §2.6 产物上传条目追加「修订（ADR-068）」标注（上传机制两轮演进说明） | 修订指向双向标注（序号治理） |
| 16 | `docs/ops/e2e-dev-loop-runbook.md` | 修 | §4 踩坑清单追加「同一测试库并发跑 pytest 互踩」（FK 假失败根因与串行处置） | 经验沉淀（本批踩坑实录） |

（实施期间持续登记）

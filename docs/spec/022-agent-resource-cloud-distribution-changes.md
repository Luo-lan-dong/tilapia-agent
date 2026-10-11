# Agent 资源云端分发与热更新 Changes 变更清单

关联 ADR：[ADR-022](../adr/022-agent-resource-cloud-distribution.md)
关联 Spec：[022-agent-resource-cloud-distribution-spec.md](./022-agent-resource-cloud-distribution-spec.md)
关联 Harness：[022-agent-resource-cloud-distribution-harness.md](./022-agent-resource-cloud-distribution-harness.md)

> 按 `.trae/rules/decision-documents.md` 要求：编码前仅创建骨架，开发过程中**每修改/新增/删除一个文件立即登记**，不得事后一次性补记。

## 变更清单

| 日期 | 文件路径 | 变更类型（修/增/减） | 函数/类级明细 | 作用说明 |
|------|---------|---------------------|--------------|---------|
| 2026-09-06 | docs/adr/022-agent-resource-cloud-distribution.md | 增 | - | ADR 决策文档 |
| 2026-09-06 | docs/spec/022-agent-resource-cloud-distribution-spec.md | 增 | - | Spec |
| 2026-09-06 | docs/spec/022-agent-resource-cloud-distribution-harness.md | 增 | - | Harness |
| 2026-09-06 | docs/spec/022-agent-resource-cloud-distribution-changes.md | 增 | - | Changes 骨架 |
| 2026-09-06 | docs/INDEX.md | 修 | 1.1/1.2/1.3/六 登记 | 决策文档索引 |
| 2026-09-06 | docs/spec/022-agent-resource-cloud-repository-api.md | 增 | 目录结构/manifest 规范/HTTP 行为/发布流程/验收清单 | 云端仓库接口要求（云端开发者交付依据） |
| 2026-09-06 | opc-central-dispatch-hub/app/models/system_settings.py | 修 | 新增 `agent_resource_manifest_url` 列 | 云端 manifest URL 持久化（热更） |
| 2026-09-06 | opc-central-dispatch-hub/alembic/versions/20260906_resource_manifest_url.py | 增 | upgrade/downgrade | system_settings 加列迁移 |
| 2026-09-06 | opc-central-dispatch-hub/app/services/system_settings_service.py | 修 | 新增 `get_agent_resource_manifest_url` / `set_agent_resource_manifest_url` | manifest URL 读写服务方法 |
| 2026-09-06 | opc-central-dispatch-hub/app/core/config.py | 修 | 新增 `resource_cache_dir` / `resource_sync_interval_seconds` | 缓存目录与定时检查间隔配置 |
| 2026-09-06 | opc-central-dispatch-hub/app/services/resource_sync_service.py | 增 | `ResourceSyncService.check/apply/import_zip/get_status_view`、`SyncResult`、`ManifestError/ShaMismatchError`、本地 manifest/state 读写、`_diff`、`_download_and_verify`（asyncio.Lock 互斥+原子落盘） | 资源同步核心服务（检查只提示、应用用户确认、zip 导入） |
| 2026-09-06 | opc-central-dispatch-hub/app/schemas/resource_settings.py | 增 | `ManifestUrlUpdate` / `ResourceUpdateRequest` / `ResourceItemInfo` / `ResourceStatusResponse` / `ResourceUpdateResponse` | 资源管理 DTO |
| 2026-09-06 | opc-central-dispatch-hub/app/core/resource_static.py | 增 | `register_resource_static`（catch-all 路由）/ `resolve_baseline_static_dir` / `_resolve_under`（防目录穿越） | /static 动态服务（缓存优先→内置基线回退），运行期生效无需重启 |
| 2026-09-06 | opc-central-dispatch-hub/app/main.py | 修 | 移除 StaticFiles mount；接入 register_resource_static 与 resource_check_loop（lifespan） | /static 切换动态服务；资源检查定时循环接入生命周期 |
| 2026-09-06 | opc-central-dispatch-hub/app/api/v1/endpoints/resource_settings.py | 增 | GET 状态 / PUT manifest-url / POST check / POST update / POST import | 资源管理 API（/api/v1/settings/resources） |
| 2026-09-06 | opc-central-dispatch-hub/app/api/deps.py | 修 | 新增 `get_resource_sync_service` | 资源服务依赖（缓存目录来自配置） |
| 2026-09-06 | opc-central-dispatch-hub/app/api/v1/api.py | 修 | 注册 resource_settings 路由（prefix=/settings/resources） | 路由挂载 |
| 2026-09-06 | opc-central-dispatch-hub/app/services/resource_check_scheduler.py | 增 | `run_resource_check_loop` / `start_resource_check_loop`（间隔 0=禁用） | 资源云端检查定时循环 |
| 2026-09-06 | opc-central-dispatch-hub/launcher/desktop.py | 修 | `_inject_env` 新增 RESOURCE_CACHE_DIR / RESOURCE_SYNC_INTERVAL_SECONDS | 桌面模式资源缓存与检查间隔注入 |
| 2026-09-06 | opc-central-dispatch-hub/scripts/build_cloud_manifest.py | 增 | `collect_entries` / `sha256_of` / `main`（识别安装脚本/模板/二进制，自动算 sha256+size） | 云端 manifest.json 生成工具（发布辅助） |
| 2026-09-06 | opc-central-dispatch-hub/pyproject.toml（隐式） | 修 | 新增运行依赖 python-multipart | UploadFile zip 导入所需 |
| 2026-09-08 | opc-central-dispatch-hub/app/services/resource_sync_service.py | 修 | `get_status_view`：key 集合由「本地∪云端并集」改为「云端快照为准（快照为空时退回本地）」；`_apply_locked`：全量更新（keys 为空）时本地清单整体镜像云端清单（`_save_local_manifest(cloud_manifest)`），部分更新仍走 merge | 云端下架条目的消费端配套：发布端删除条目后，中枢不再展示历史遗留条目；全量应用更新时本地清单同步移除已下架条目 |
| 2026-09-08 | opc-central-dispatch-hub/tests/services/test_resource_sync_service.py | 修 | 新增 `test_apply_full_update_mirrors_cloud_removal` / `test_status_view_hides_cloud_removed_entry` | 上述两行为的回归测试（5 passed） |

（开发过程中持续追加，格式同上）

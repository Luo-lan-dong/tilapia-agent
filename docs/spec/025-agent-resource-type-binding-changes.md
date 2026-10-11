# Agent 资源按类型分发 Changes 变更清单

关联 ADR：[ADR-025](../adr/025-agent-resource-type-binding.md)
关联 Spec：[025-agent-resource-type-binding-spec.md](./025-agent-resource-type-binding-spec.md)
关联 Harness：[025-agent-resource-type-binding-harness.md](./025-agent-resource-type-binding-harness.md)

> 按 `.trae/rules/decision-documents.md` 要求：编码前仅创建骨架，开发过程中每修改/新增/删除一个文件立即登记。

## 变更清单

| 日期 | 文件路径 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|---------|--------------|---------|
| 2026-09-08 | docs/adr/025-agent-resource-type-binding.md | 增 | - | ADR 决策文档 |
| 2026-09-08 | docs/spec/025-agent-resource-type-binding-spec.md | 增 | - | Spec |
| 2026-09-08 | docs/spec/025-agent-resource-type-binding-harness.md | 增 | - | Harness |
| 2026-09-08 | docs/spec/025-agent-resource-type-binding-changes.md | 增 | - | Changes 骨架 |
| 2026-09-08 | docs/INDEX.md | 修 | 1.1/1.2/1.3/五/六 登记 | 决策文档索引 |

| 2026-09-08 | Tilapia-web/RuoYi-Vue/ruoyi-system/.../domain/CloudCosConfig.java | 修 | 新增 `hubToken` 字段+getter/setter | 中枢对接 Token 实体 |
| 2026-09-08→09 | Tilapia-web/RuoYi-Vue/ruoyi-system/.../mapper/cloud/CloudCosConfigMapper.xml | 修 | resultMap/select/insert/update 补 hub_token 列；09-09 修复 selectConfigVo 与 resultMap 均缺 hub_token 导致 Token 校验恒 false | Token 持久化（今日修复为最终态） |
| 2026-09-08 | Tilapia-web/RuoYi-Vue/ruoyi-system/.../ICloudCosConfigService.java | 修 | 新增 `regenerateHubToken`/`isValidHubToken`/`buildManifestUrl` | Token 生成校验与地址拼接接口 |
| 2026-09-08 | Tilapia-web/RuoYi-Vue/ruoyi-system/.../impl/CloudCosConfigServiceImpl.java | 修 | Token 生成（双 UUID 拼接 64 位）/常量时间语义校验/`buildManifestUrl`（桶+前缀拼 URL） | Token 与地址实现 |
| 2026-09-08→09 | Tilapia-web/RuoYi-Vue/ruoyi-admin/.../cloud/CloudHubController.java | 增/修 | `GET /portal/open/hub/config`（X-Hub-Token 认证；09-09 从初版 `/cloud/hub/manifest-url` 迁入官网公开路径） | 中枢服务端配置下发接口 |
| 2026-09-08→09 | Tilapia-web/RuoYi-Vue/ruoyi-framework/.../config/SecurityConfig.java | 修 | 放行 `/portal/open/**`（09-09 撤销初版 `/cloud/hub/**` 放行；接口自身 Token 认证） | 匿名放行（仅门户公开路径） |
| 2026-09-08 | Tilapia-web/RuoYi-Vue/ruoyi-admin/.../cloud/CloudCosConfigController.java | 修 | 新增 `POST /config/hub-token`（重新生成） | Token 管理端点 |
| 2026-09-08 | Tilapia-web/RuoYi-Vue/ruoyi-system/.../impl/CloudResourceServiceImpl.java | 修 | `resolvePath` key v2：`install-script-{type}` → `install-scripts/{type}/install-agent-{type}-linux.sh`（TYPE_PATTERN 校验）；整体包白名单不变 | 脚本按类型生成 path |
| 2026-09-08 | Tilapia-web/RuoYi-Vue/sql/cloud_init.sql + scripts/add_hub_token.py | 修/增 | hub_token 列 DDL（同步 init 与增量脚本，增量已执行） | 数据库变更 |
| 2026-09-08 | Tilapia-web/RuoYi-Vue3/src/api/cloud/cos.js | 修 | 新增 `regenerateHubToken` | 前端 API |
| 2026-09-08 | Tilapia-web/RuoYi-Vue3/src/views/cloud/cos/index.vue | 修 | 新增「中枢对接」卡片（Token 展示/生成/重置提示）；说明文案更新 install-scripts/{类型}/ | Token 管理界面 |
| 2026-09-08 | Tilapia-web/RuoYi-Vue3/src/views/cloud/repository/index.vue | 修 | `keyOptions` 脚本项改 `install-script-code`；`needVersion` 按前缀判断 | 发布页类型绑定 |
| 2026-09-09 | opc-central-dispatch-hub/app/core/config.py + .env.example | 修 | 新增 `agent_resource_hub_config_url`/`agent_resource_hub_token`（Pydantic Settings，发布前手动改） | 服务端地址/Token 写死配置（修订决策 3，撤销 DB 存储） |
| 2026-09-09 | opc-central-dispatch-hub/app/models/system_settings.py | 修（撤销变更） | 初版曾加 hub_api_url/hub_token 列，09-09 撤销；仅保留 manifest_url（注释改为「运行期缓存」） | 模型回归（用户零配置） |
| 2026-09-09 | opc-central-dispatch-hub/app/services/system_settings_service.py | 修（撤销变更） | 初版 hub 配置读写已删；仅保留 `get/set_agent_resource_manifest_url`（缓存语义） | 地址缓存读写 |
| 2026-09-09 | opc-central-dispatch-hub/app/services/resource_sync_service.py | 修 | `parse_resource_key`（key→kind/type）/`resolve_manifest_url`（服务端拉取+state 缓存回退；09-09 改读配置文件）/`keys_for_type`；`get_status_view` 输出 `agent_types` 分组（云端快照优先） | 类型分组与地址下发核心 |
| 2026-09-09 | opc-central-dispatch-hub/app/schemas/resource_settings.py | 修 | `AgentTypeInfo`/`ResourceUpdateRequest.type`；09-09 删初版 `hub_api_configured`/`HubConfigUpdate` | DTO |
| 2026-09-09 | opc-central-dispatch-hub/app/api/v1/endpoints/resource_settings.py | 修 | `check`/`update` 走 `_effective_manifest_url`（服务端解析+DB 持久化缓存）；`update` 支持 type 参数；09-09 删初版 `PUT /hub-config` | 端点改造（用户零配置） |
| 2026-09-09 | opc-central-dispatch-hub/app/services/resource_check_scheduler.py | 修 | 定时检查前先从服务端拉地址（失败回退缓存） | 定时链路同手动 |
| 2026-09-08→09 | opc-central-dispatch-hub/app/services/agent_node_management_service.py | 修 | `get_install_script` 按类型拼接 URL + 本地清单 `install-script-{type}` 存在性校验（缺失 404 报错）；09-09 修正过时 docstring（programming→code） | 安装命令防错配 |
| 2026-09-09 | opc-web-console/src/types/resource.ts + api/resource.ts | 修 | `AgentTypeInfo` 类型；`applyResourceUpdateByType/ByKeys`；09-09 删初版 `updateHubConfig`/`hub_api_configured` | 前端类型与 API |
| 2026-09-09 | opc-web-console/src/pages/ResourceManage.tsx | 修（重写） | 按 agent 类型分组卡片（类型级更新按钮，脚本随包自动）；移除云端仓库地址展示；09-09 删初版「管理后台对接」配置卡；未分组历史条目独立区块 | 页面改造（ADR-025 三项需求，零配置终态） |
| 2026-09-08 | opc-central-dispatch-hub/tests/services/test_resource_type_grouping.py | 增 | parse_resource_key/分组视图/keys_for_type/resolve_manifest_url（成功缓存、失败回退、无配置、无缓存） | 单测（09-09 实跑通过：17 passed） |
| 2026-09-09 | opc-agent-programming/scripts/install-agent-code-linux.sh | 增 | 由 install-agent-programming-linux.sh 复制，头注释更新为 ADR-025 路径 | 新类型绑定脚本（云端迁移源文件） |
| 2026-09-09 | opc-central-dispatch-hub/app/services/resource_sync_service.py | 修 | `_apply_locked` 增幂等跳过：本地 sha 一致且文件在盘的条目不重下载；全部最新时短路返回 up-to-date | 类型更新不重下 67MB 已最新整体包（实测触发 COS 中断后引入） |
| 2026-09-09 | opc-central-dispatch-hub/tests/services/test_resource_sync_service.py | 修 | 新增 `test_apply_skips_up_to_date_entries`/`test_apply_all_up_to_date_short_circuits` | 跳过逻辑单测（19 passed） |
| 2026-09-09 | 云端仓库（COS） | 迁移 | 下架 `install-script-linux`（revision 17:00:13Z）；发布 `install-script-code`（path install-scripts/code/，sha256 24daf03e...，11418B，revision 17:00:18Z）；旧对象文件已删除 | key v1→v2 一次性迁移（决策 4） |
| 2026-09-09 | opc-central-dispatch-hub/static/install-scripts/code/install-agent-code-linux.sh | 增 | 由 opc-agent-programming/scripts/install-agent-code-linux.sh 复制（sha256 24daf03e... 与云端已发布一致） | 内置基线兜底脚本迁移到 ADR-025 类型路径（文档核对发现项） |
| 2026-09-09 | opc-central-dispatch-hub/static/install-scripts/install-agent-programming-linux.sh | 删 | - | 清理旧版内置脚本（路径已废弃，避免 /static 兜底命中过时内容）（文档核对发现项） |
| 2026-09-09 | docs/spec/025-agent-resource-type-binding-spec.md | 修 | §1(4)/US-7/§3.1/§4.1/§4.2/§4.3/§6-6 同步 ADR-025 09-09 修订（配置文件写死 + /portal/open/hub/config + 零配置零展示；删 hubApiConfigured） | Spec 与 ADR 修订对齐（文档核对发现项） |
| 2026-09-09 | docs/spec/025-agent-resource-type-binding-harness.md | 修 | UT-3/UT-4 指向 test_resource_type_grouping.py；UT-5 指向 tests/api/v1/test_agent_nodes.py | Harness 测试文件路径与实际一致（文档核对发现项） |
| 2026-09-09 | Tilapia-web/RuoYi-Vue3/src/views/cloud/cos/index.vue | 修 | 「中枢对接」提示文案改为终态：中枢零配置零展示、改桶/地域/前缀即改中枢更新地址、新位置需已发布资源、Token 轮换需改中枢配置文件并重启 | 清除 ADR-025 初版方案残留文案（用户询问"后台能否改中枢更新地址"时发现） |

## 实施验证记录（2026-09-09）

- RuoYi `GET /portal/open/hub/config`：无 Token 401 / 正确 Token 200 + manifestUrl ✅
- 中枢 `POST /settings/resources/check`：从 RuoYi 实时拉取地址 → 云端比对 → 类型分组仅 code（旧 linux 条目消失）✅
- 中枢 `POST /settings/resources/update {"type":"code"}`：跳过已最新整体包，仅下载脚本（11KB），up-to-date ✅
- 中枢 `GET /agent-nodes/install-scripts?agent_type=code`：返回新路径 URL + sha256 与云端一致；`agent_type=video` 明确 400 拒绝 ✅
- 中枢 `/static/install-scripts/code/install-agent-code-linux.sh`：200，11418 字节 ✅
- pytest：19 passed（含新增跳过逻辑 2 例）✅

（开发过程中持续追加，格式同上）

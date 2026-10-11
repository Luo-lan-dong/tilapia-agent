# 版本发布管理与中枢更新 Changes 变更清单

关联 ADR：[ADR-026](../adr/026-hub-self-update.md)
关联 Spec：[026-release-management-spec.md](./026-release-management-spec.md)
关联 Harness：[026-release-management-harness.md](./026-release-management-harness.md)

> 按 `.trae/rules/decision-documents.md` 要求：编码前仅创建骨架，开发过程中每修改/新增/删除一个文件立即登记。

## 变更清单

| 日期 | 文件路径 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|---------|--------------|---------|
| 2026-09-09 | docs/adr/026-hub-self-update.md | 修 | 决策全面修订（product 维度/云端化/Token 终态/v0.5.0） | ADR 修订 |
| 2026-09-09 | docs/adr/018-system-version-management.md | 修 | 变更记录追加 09-09 修订行 | 本地表降级为当前版本载体 |
| 2026-09-09 | docs/spec/026-release-management-spec.md | 增 | - | Spec |
| 2026-09-09 | docs/spec/026-release-management-harness.md | 增 | - | Harness |
| 2026-09-09 | docs/spec/026-release-management-changes.md | 增 | - | Changes 骨架 |
| 2026-09-09 | docs/INDEX.md | 修 | 1.1/1.2/1.3/六 登记 | 索引登记 |
| 2026-09-09 | opc-central-dispatch-hub/scripts/check_system_versions.py | 增 | 临时排查脚本（system_versions 空表定位） | 故障排查（数据丢失事故定位） |
| 2026-09-09 | Tilapia-web/RuoYi-Vue/ruoyi-system/.../domain/CloudHubRelease.java | 增 | CloudHubRelease 实体（product/version/title/content/releaseDate/downloadUrl） | 版本发布实体 |
| 2026-09-09 | Tilapia-web/RuoYi-Vue/ruoyi-system/.../mapper/CloudHubReleaseMapper.java + mapper/cloud/CloudHubReleaseMapper.xml | 增 | selectList/selectById/selectByProductVersion（唯一校验）/insert/update/deleteByIds | 版本发布持久化 |
| 2026-09-09 | Tilapia-web/RuoYi-Vue/ruoyi-system/.../service/ICloudHubReleaseService.java + impl/CloudHubReleaseServiceImpl.java | 增 | CRUD（PRODUCT_PATTERN/VERSION_PATTERN 校验 + product+version 唯一）+ `uploadInstaller`（COS hub-releases/{version}/） | 版本发布业务（mvn compile 通过） |
| 2026-09-09 | Tilapia-web/RuoYi-Vue/ruoyi-admin/.../cloud/CloudHubReleaseController.java | 增 | /cloud/release list/{id}/POST/PUT/DELETE/{ids}/upload（权限 cloud:release:*） | 版本发布管理端点 |
| 2026-09-09 | Tilapia-web/RuoYi-Vue/ruoyi-admin/.../cloud/CloudHubController.java | 修 | 新增 `GET /portal/open/hub/releases`（X-Hub-Token 认证，401 拒绝） | 中枢版本查询下发接口 |
| 2026-09-09 | Tilapia-web/RuoYi-Vue/scripts/add_hub_release.py | 增 | hub_releases DDL（IF NOT EXISTS）+ sys_menu 菜单 2413「版本发布」+ 4 按钮权限（幂等，已执行） | 数据库初始化（parent 2400 云端资源目录下） |
| 2026-09-09 | Tilapia-web/RuoYi-Vue3/src/api/cloud/release.js | 增 | listRelease/addRelease/updateRelease/delRelease/uploadInstaller | 前端 API |
| 2026-09-09 | Tilapia-web/RuoYi-Vue3/src/views/cloud/release/index.vue | 增 | 搜索（产品线/版本号）+ 分页表格 + 新增/修改对话框（hub 可传安装包）+ 删除二次确认 | 版本发布管理页 |
| 2026-09-09 | opc-central-dispatch-hub/app/services/release_service.py | 增 | ReleaseService：fetch_releases（Token 拉取）/group_releases/check_hub_update；compare_versions（packaging 回退字符串）；releases_api_url（config→releases 推导） | 更新日志云端拉取与检查更新核心 |
| 2026-09-09 | opc-central-dispatch-hub/app/schemas/release.py | 增 | ReleaseItemInfo/ProductGroupInfo/ReleasesResponse/HubUpdateCheckResponse | DTO |
| 2026-09-09 | opc-central-dispatch-hub/app/api/deps.py | 修 | 新增 `get_release_service` | 依赖注入 |
| 2026-09-09 | opc-central-dispatch-hub/app/api/v1/endpoints/settings.py | 修 | 新增 `GET /settings/releases`（分组，失败降级 error 字段）+ `POST /settings/check-hub-update`（三态） | 端点改造 |
| 2026-09-09 | opc-central-dispatch-hub/scripts/insert_initial_version.py | 修 | 改造为「设置当前运行版本」幂等 upsert（默认 v0.5.0，同事务置非当前）；已执行 → system_versions=[('v0.5.0', True)] | 本地版本基线修正（ADR-018 修订） |
| 2026-09-09 | opc-central-dispatch-hub/tests/services/test_release_service.py | 增 | compare_versions/releases_api_url/group_releases/check_hub_update 三态（13 passed） | 单测 |
| 2026-09-09 | opc-web-console/src/types/release.ts + src/api/release.ts | 增 | ReleaseItem/ProductGroup/ReleasesData/HubUpdateCheckResult；fetchReleases/checkHubUpdate | 前端类型与 API |
| 2026-09-09 | opc-web-console/src/pages/Changelog.tsx | 修（重写） | Tabs 按产品线分组 Timeline；中枢 Tab 顶部当前版本+「检查中枢更新」（结果 Modal：日志+下载安装包按钮）；error 降级 Alert+重试 | 更新日志页云端化改造 |
| 2026-09-09 | opc-central-dispatch-hub/tests/api/v1/test_system_versions.py | 修 | `_clean_versions` 改为快照-恢复模式（进入前快照 system_versions，结束后恢复；修复测试清空真实开发库版本数据——本次数据丢失事故根因） | 测试不再污染版本基线（5 passed，跑后数据保全验证通过） |
| 2026-09-09 | opc-central-dispatch-hub/launcher/db_setup.py | 修 | INITIAL_VERSION v0.1.0→v0.5.0；seed 内容改为 ADR-026 基线文案 | 桌面模式初始数据与版本基线一致 |
| 2026-09-09 | Tilapia-web/RuoYi-Vue/scripts/seed_hub_releases.py | 增 | hub v0.5.0 + agent-code 0.5.0 真实历史日志（幂等，已执行） | 云端更新日志种子数据 |
| 2026-09-09 | Tilapia-web/RuoYi-Vue/scripts/toggle_test_release.py | 增 | hub v0.6.0 测试记录插入/删除（联调用，验证后已删除） | update-available 场景联调辅助 |
| 2026-09-09 | opc-central-dispatch-hub/tests/api/v1/test_agent_nodes.py | 修 | `test_get_install_script_success` 改为 monkeypatch 注入本地清单（不依赖真实 data/resources），断言更新为 ADR-025 终态（install-scripts/code/ 路径、version 0.5.0）；修复遗留旧路径+占位数据断言 | 全量回归 4 failed 之一修复（其余 3 例为 WebSocket 心跳时间竞争的既有环境波动，重跑通过） |
| 2026-09-09 | opc-central-dispatch-hub/app/schemas/release.py + app/api/v1/endpoints/settings.py | 修 | 撤销 camelCase alias，响应字段统一 snake_case（release_date/download_url/latest_version/current_version） | 修复弹窗版本号显示 "--"（前端类型为 snake_case，FastAPI 按 alias 序列化导致前端取值 undefined） |
| 2026-09-09 | opc-central-dispatch-hub/app/services/release_service.py | 修 | 新增 `_version_sort_key`（合法 Version 组 1 / 非法字符串组 0）；check_hub_update 的 latest 选取改用它 | 修复多条 hub 记录时 latest 取错（原 compare_versions(x,"") 哨兵使所有版本并列返回 1，max 取列表首条 0.0.6 而非 v0.5.1） |
| 2026-09-09 | opc-central-dispatch-hub/tests/services/test_release_service.py | 修 | 新增 `test_latest_picks_max_version_not_first` 回归用例（14 passed） | 覆盖 latest 选取 bug |
| 2026-09-09 | Tilapia-web/RuoYi-Vue/ruoyi-system/.../impl/CloudHubReleaseServiceImpl.java + ICloudHubReleaseService + CloudHubReleaseController | 修 | `uploadInstaller` 增加 filename 参数（显式文件名优先），去掉 installer.bin 静默兜底（文件名缺失报错），加 .exe/.zip 白名单校验；前端 release/index.vue 同步传 filename | 修复上传后 COS 对象名变 installer.bin（multipart filename 丢失导致 fallback） |
| 2026-09-09 | Tilapia-web/RuoYi-Vue/ruoyi-system/.../impl/CloudHubReleaseServiceImpl.java | 修 | 文件名缺失从报错改为规范名兜底 `tilapia-hub-{version}-setup.exe`（log.warn），白名单校验仅对可识别文件名生效 | 用户反馈旧前端未发 filename 报错阻断——兜底不阻断上传；前端 dev(HMR)/build:prod 均已更新 |

## 实施验证记录（2026-09-09）

- RuoYi `GET /portal/open/hub/releases`：无 Token 401 / 正确 Token 200 + 2 条 release（hub v0.5.0、agent-code 0.5.0）✅
- 中枢 `GET /settings/versions`：v0.5.0（is_current=true）✅
- 中枢 `GET /settings/releases`：按产品线分组（agent-code/hub，label 中文）✅；RuoYi 停机时返回 error 字段降级（不 5xx）✅
- 中枢 `POST /settings/check-hub-update` 三态：update-available（v0.6.0>v0.5.0，含 title/content/downloadUrl）/ up-to-date / failed ✅
- pytest：test_release_service.py 13 passed；test_system_versions.py 5 passed（数据保全）；test_agent_nodes 安装脚本 2 passed（修复后）；全量回归 232 passed / 4 failed（1 例为 ADR-025 遗留过时断言已修复，3 例为 WebSocket 心跳时间竞争既有波动、重跑通过）
- 前端构建：opc-web-console `npm run build` 通过；RuoYi-Vue3 `npm run build:prod` 通过；`mvn compile`/`mvn install` 通过
- 浏览器 E2E：/settings/changelog Tabs 分组、中枢 Tab 当前版本+检查按钮、检查结果「当前已是最新版本」提示 ✅（截图验证）
- Bug 修复验证（用户反馈弹窗版本号 "--"）：API 字段统一 snake_case 后弹窗正确显示「当前版本 v0.5.0 / 最新版本 v0.6.0」，Timeline 日期恢复显示 ✅（截图验证）；latest 选取修复后多条 hub 记录正确取语义化最大版（v0.6.0 > v0.5.1 > 0.0.6）✅

# 中枢端口避让与 Agent 端口探测自愈 Changes 变更清单

关联决策：[ADR-031](../adr/031-hub-port-avoidance-and-agent-self-heal.md) / [Spec](./031-hub-port-avoidance-spec.md)

> 按规则：每修改/新增/删除一个文件立即登记本清单（修/增/减 + 函数级明细），禁止开发完成后一次性补记。

## 变更清单

| # | 文件 | 类型 | 函数/类级明细 | 作用说明 | 日期 |
|---|------|------|--------------|---------|------|
| 1 | docs/adr/031-hub-port-avoidance-and-agent-self-heal.md | 增 | — | 决策：端口探测自愈 + 兜底窗口 + 端口可视化 + URL 校准 | 2026-09-12 |
| 2 | docs/spec/031-hub-port-avoidance-spec.md | 增 | — | 功能规格：用户故事/流程/降级/验收 | 2026-09-12 |
| 3 | docs/spec/031-hub-port-avoidance-harness.md | 增 | — | 测试清单与 CI 门禁 | 2026-09-12 |
| 4 | docs/spec/031-hub-port-avoidance-changes.md | 增 | — | 本清单 | 2026-09-12 |
| 5 | docs/INDEX.md | 修 | 1.1/1.2/1.3 表 +ADR-031 行；六、更新记录 | 登记决策文档 | 2026-09-12 |
| 6 | agent/app/client/hub_client.py | 修 | +`_PORT_PROBE_RANGE`(8000-8010)/`_PROBE_TIMEOUT_SECONDS`(5s)；HubClient `__init__` +`_endpoint_locked`/`_endpoint_index`；+`_candidate_api_urls`/`_active_api_url`/`_advance_endpoint`/`_lock_endpoint`；`register` 未锁定用短超时；`connect_websocket` WS 地址由 `_active_api_url` 派生；`run_forever` 成功 `_lock_endpoint`/失败 `_advance_endpoint` | Agent 端口探测自愈（核心） | 2026-09-12 |
| 7 | agent/tests/test_hub_client_reconnect.py | 修 | +ADR-031 段 5 用例（UT-1~UT-4） | 探测自愈测试 | 2026-09-12 |
| 8 | hub/launcher/desktop.py | 修 | +`_FALLBACK_HTML`/`FallbackApi`/`_open_fallback_window`；`_start_stack`（自 `_run` 抽取，正常与兜底复用）；`_run` 捕获 `PortExhaustedError` 进兜底窗口；+`is_port_free` import | 端口耗尽兜底窗口，UI 不崩溃 | 2026-09-12 |
| 9 | hub/app/main.py | 修 | +import os/urlparse/urlunparse；+`_reconcile_hub_public_url_port`；`lifespan` 调用 | 启动时 Hub URL 端口自动校准 | 2026-09-12 |
| 10 | hub/app/schemas/system_settings.py | 修 | `NetworkSettingsResponse` +`actual_port` | 网络配置返回实际端口 | 2026-09-12 |
| 11 | hub/app/api/v1/endpoints/settings.py | 修 | +`_actual_backend_port`；GET/PATCH `/network` 响应带 `actual_port` | AC-3 数据源 | 2026-09-12 |
| 12 | hub/tests/test_hub_url_reconcile.py | 增 | 7 用例（UT-5~UT-8） | 校准逻辑测试 | 2026-09-12 |
| 13 | hub/tests/api/v1/test_settings.py | 修 | +2 用例（IT-1/IT-2） | actual_port 端点测试 | 2026-09-12 |
| 14 | web/src/types/networkSettings.ts | 修 | `NetworkSettings` +`actual_port` | 前端类型 | 2026-09-12 |
| 15 | web/src/pages/Settings.tsx | 修 | `deriveHubUrlFromOrigin(actualPort)` 去硬编码 8000（actual_port→当前端口非 5173→8000）；+`actualPort` state；+实际端口提示文案（data-testid=actual-port-hint） | US-3/US-4 | 2026-09-12 |
| 16 | agent/app/client/hub_client.py | 修 | `report_file_tree` 签名换行（E501 lint 修复，ADR-028 遗留） | lint 通过 | 2026-09-12 |
| 17 | hub/app/main.py | 修 | `_reconcile_hub_public_url_port` +`APP_ENV=desktop` 门禁（get_settings().app_env 判断，源码模式直接 return） | 修订：临时实例设 PORT 不得静默改写持久化配置（冒烟验证污染事故） | 2026-09-12 |
| 18 | hub/tests/test_hub_url_reconcile.py | 修 | +`desktop_env` fixture（替换 app.main.get_settings 绕开 lru 缓存）应用至全部用例；+`test_reconcile_skipped_when_not_desktop` 回归用例 | 同步收紧条件 | 2026-09-12 |
| 19 | agent/app/client/hub_client.py | 修 | `_active_api_url`：锁定态失败轮转由「others 剔除锁定端点」改为「完整候选环取模（candidates[0] 即锁定端点）」，环回可重试锁定端口 | 修复：中枢重启回原端口（如 8000）后，锁定端点不在轮转序列内导致 Agent 永远离线（现场：code 节点需手工 systemctl restart） | 2026-09-14 |
| 20 | agent/tests/test_hub_client_reconnect.py | 修 | +`test_locked_endpoint_retried_after_full_rotation`（轮转一圈必须再次覆盖锁定端点）；+`test_locked_endpoint_stays_first_priority`（index=0 直连锁定端点） | 回归测试 | 2026-09-14 |

（验证记录 2026-09-14：单测 24 passed；真实端到端复现——模拟 Agent 连接 8000 锁定 → 停中枢（轮转 8001-8010 全失败）→ 中枢重启回 8000 → 约 55s 内环回重试 8000 自动重连成功并重新锁定；修复前该场景将永久离线）

（验证记录 2026-09-12 修订：reconcile 测试 8 passed；实跑源码模式 PORT=8023 实例确认 hub_public_url 保持 :8000 不改写、actual_port=8023 正确返回；开发库误改的 hub_public_url 已用临时 Python 脚本恢复为 :8000，脚本已删除）

## 变更记录

| 日期 | 变更内容 | 变更人 |
|------|---------|--------|
| 2026-09-12 | 初始创建（ADR-031 全量实施登记） | AI助手 |
| 2026-09-14 | 修复端点锁定轮转死角：锁定端点失败后不在轮转序列内，中枢重启回原端口时 Agent 永远离线（code 节点现场）；改为完整候选环 + 2 个回归测试 | AI助手 |

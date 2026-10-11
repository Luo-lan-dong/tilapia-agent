# 中枢端口避让与 Agent 端口探测自愈 Harness

关联决策：[ADR-031](../adr/031-hub-port-avoidance-and-agent-self-heal.md) / [Spec](./031-hub-port-avoidance-spec.md)

## 测试金字塔

```
       E2E（手动/浏览器）        AC-2 兜底窗口、AC-3/4 设置页
      集成（API/组件）           network actual_port、Settings 渲染
     单元（服务/纯逻辑）          reconcile、候选列表/轮转/锁定、find_free_port
```

## 单元测试（UT）

| # | 测试 | 文件 | 覆盖 |
|---|------|------|------|
| UT-1 | 候选地址 = 首选 + 8000-8010 去重（11 个） | agent `tests/test_hub_client_reconnect.py::test_candidate_api_urls_cover_probe_range` | 探测范围展开 |
| UT-2 | 锁定端点排首位且 `_active_api_url` 命中 | 同上 `::test_candidate_api_urls_locked_first` | 锁定优先 |
| UT-3 | 失败轮转 → 锁定 → 锁定后再失败从其余候选轮转 | 同上 `::test_endpoint_rotation_and_lock_on_failure` | 轮转/锁定状态机 |
| UT-4 | 首选 8000 注册失败、8001 成功并锁定 | 同上 `::test_register_failure_rotates_endpoint` | 重连循环集成 |
| UT-5 | PORT 与 URL 端口不一致 → 改写为实际端口 | hub `tests/test_hub_url_reconcile.py` | AC-5 |
| UT-6 | URL 无端口 → 补上实际端口 | 同上 | 边界 |
| UT-7 | 端口一致/无 PORT/无设置行 → no-op | 同上 3 用例 | 降级链路 |
| UT-8 | 校准保留 scheme/path/query | 同上 `::test_reconcile_preserves_scheme_path_query` | 参数化 |
| UT-9 | find_free_port 范围/耗尽抛 PortExhaustedError | hub `tests/launcher/test_port.py`（既有） | launcher 基础 |

## 集成测试（IT）

| # | 测试 | 文件 | 覆盖 |
|---|------|------|------|
| IT-1 | GET /settings/network 返回 actual_port=PORT 值 | hub `tests/api/v1/test_settings.py::test_get_network_settings_reports_actual_port` | AC-3 |
| IT-2 | 无 PORT 时 actual_port=null | 同上 `::test_get_network_settings_actual_port_none_without_env` | 降级链路 3 |
| IT-3 | Settings 页实际端口提示 + 使用当前地址动态端口 | 前端 vitest（后续补）/手动 | AC-3/AC-4 |

## E2E 测试（手动）

| # | 场景 | 步骤 | 预期 |
|---|------|------|------|
| E2E-1 | 端口避让后 Agent 自愈 | ① 占用 8000 启动桌面中枢（避让 8001）② 已装 Agent 保持运行 | Agent 日志出现轮转后 `agent_registered`（8001）并锁定；节点页转在线 |
| E2E-2 | 兜底窗口 | 占用 8000-8010 后双击启动 | 桌面 UI 显示兜底窗口；填 8011 → 后端启动 → 控制台可用；关窗进程退出 |

## CI 门禁

- ruff check（hub + agent）通过
- hub：`pytest tests/test_hub_url_reconcile.py tests/api/v1/test_settings.py tests/launcher/` 绿
- agent：`pytest tests/test_hub_client_reconnect.py` 绿
- web：`tsc --noEmit` + eslint 通过

## 回归范围

- ADR-020 重连回归：`test_hub_client_reconnect.py` 全量（缓冲补报/取消指令不受轮转影响）
- ADR-021 桌面启动回归：`tests/launcher/test_startup_flow.py`

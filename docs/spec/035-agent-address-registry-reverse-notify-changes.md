# Agent 地址上报与中枢重启主动反连 Changes（变更清单）

> 关联：[ADR-035](../adr/035-agent-address-registry-reverse-notify.md) / [Spec](./035-agent-address-registry-reverse-notify-spec.md) / [Harness](./035-agent-address-registry-reverse-notify-harness.md)
>
> 按 `.trae/rules/decision-documents.md` 要求：边开发边实时登记，禁止开发完成后一次性补记。

## 变更清单

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| docs/adr/035-agent-address-registry-reverse-notify.md | 增 | - | 决策文档 |
| docs/spec/035-agent-address-registry-reverse-notify-spec.md | 增 | - | 功能 Spec |
| docs/spec/035-agent-address-registry-reverse-notify-harness.md | 增 | - | 测试清单 |
| docs/spec/035-agent-address-registry-reverse-notify-changes.md | 增 | - | 本变更清单（骨架，随开发登记） |
| docs/INDEX.md | 修 | 1.1/1.2/1.3/六 | 登记本次决策 |

<!-- 开发过程中在此实时追加：文件 → 变更类型（修/增/减） → 函数/类级明细 → 作用说明 -->

## Agent 端（opc-agent-programming）

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| app/client/endpoint_probe.py | 增 | `EndpointProber`（`active_api_url`/`lock`/`unlock`/`candidate_api_urls`/`register`/`_race_register`/`_post_register`）；常量 `PROBE_TIMEOUT_SECONDS=1.0`（原 5.0）、`REGISTER_TIMEOUT_SECONDS=30.0`、`PORT_PROBE_RANGE` | 第一步核心：候选环 + 并行竞速注册（首个成功者胜出并锁定），从 hub_client 拆出（Harness ≤300 行门禁） |
| app/client/hub_client.py | 修 | 删除 `_candidate_api_urls`/`_active_api_url`/`_advance_endpoint`/`_lock_endpoint`/`_REGISTER_TIMEOUT_SECONDS`/`_PROBE_TIMEOUT_SECONDS`/`_PORT_PROBE_RANGE`；新增 `_build_register_payload`、`prober` 属性、`_reconnect_now` 事件、`_wait_before_reconnect`、`apply_hub_notify`；改 `register`/`connect_websocket`/`run_forever`（解锁代替轮转）/`_download_handoff_bundle` | 串行轮转 → 并行竞速；notify 处理（地址更新 + 解锁 + 立即重连）；注册 payload 携带 reported_address |
| app/client/notify_server.py | 增 | `NotifyServer`（`start`/`stop`/`base_url`/`_make_handler`）；端点 `POST /internal/hub-notify`（Bearer 认证，hmac.compare_digest）+ `GET /internal/health` | 第二步：Agent 侧轻量通知 API（标准库 ThreadingHTTPServer，零新依赖） |
| app/config.py | 修 | `AgentConfig` 新增 `agent_api_host`/`agent_api_port`/`api_token` 字段与 `reported_address` 属性；新增 `_detect_lan_ip` | 通知 API 配置 + 注册上报地址构造（无 IP/token 时返回 None 兼容旧装） |
| app/main.py | 修 | `_run_agent` 新增 NotifyServer 装配（无 token 跳过；OSError 降级不阻断）；`_shutdown` 增加 notify_server 参数 | 通知服务接入启动/优雅关闭流程 |
| pyproject.toml | 修 | packages 增加 `app.client` 已含（无新依赖） | 零新依赖（标准库实现通知 API） |

## 中枢（opc-central-dispatch-hub）

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| app/models/agent_node.py | 修 | `AgentNode` 新增 `reported_ip`/`reported_api_port`/`api_token` 列 | 地址注册表三列（ADR-035） |
| alembic/versions/20260915_agent_addr_registry.py | 增 | `upgrade`/`downgrade`（revision: 20260915_addr_registry，down: 20260915_chain_context） | 三列迁移，可回滚 |
| app/schemas/agent_node.py | 修 | 新增 `ReportedAddress`；`AgentNodeRegisterRequest` 新增 `reported_address` 可选字段 | 注册请求协议扩展 |
| app/services/agent_node_service.py | 修 | 新增 `_apply_reported_address`；`register_agent_node` 新建/更新路径调用 | reported_ip/port 跟随更新；api_token 仅首次落库（幂等语义） |
| app/services/agent_reverse_notify.py | 增 | `run_reverse_notify`/`start_reverse_notify`/`_load_reverse_notify_targets`/`_notify_agent`/`_derive_ws_url` | lifespan 启动反连：offline 且有地址节点并发 ≤5 通知新地址，失败静默 |
| app/core/config.py | 修 | `Settings` 新增 `reverse_notify_concurrency`（默认 5）/`reverse_notify_timeout_seconds`（默认 5s） | 反连并发与超时配置（Pydantic Settings） |
| app/main.py | 修 | `lifespan` 启动 `start_reverse_notify`、关闭时 cancel | 反连任务接入生命周期 |
| tests/conftest.py | 修 | 新增 `_noop_start_reverse_notify`；`ws_client` fixture 增加 start_reverse_notify patch | 测试 lifespan 不触发真实反连 |
| tests/test_agent_reverse_notify.py | 增 | UT-7/8/9（注册落库/幂等/旧 Agent 兼容）+ UT-10/11/12（反连目标/失败静默/跳过）+ `_fresh_global_engine_pool` autouse fixture | Harness 中枢侧用例；autouse dispose 规避套件末尾跨事件循环连接池残留 |

## 测试

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| opc-agent-programming/tests/test_endpoint_probe.py | 增 | UT-1（竞速胜出锁定/全败重试）、UT-2（1s 超时不阻塞快端点）、锁定端点直连注册 | 第一步竞速探测用例（respx） |
| opc-agent-programming/tests/test_notify_server.py | 增 | UT-3/4/6（token 认证/401 拒绝/端口配置生效）+ health 无认证 + 非法 payload | 通知 API 用例（真实 ThreadingHTTPServer + 随机端口） |
| opc-agent-programming/tests/test_hub_client_reconnect.py | 修 | ADR-031 段改为 prober API；新增 notify 处理/立即重连打断/UT-5 payload 用例 | 适配竞速重构 + 第二步用例 |
| opc-agent-programming/.env.example | 修 | 通知 API 配置段（AGENT_API_HOST/AGENT_API_PORT/API_TOKEN） | 环境配置规范 §10 |
| opc-central-dispatch-hub/.env.example | 修 | 反连配置段（REVERSE_NOTIFY_CONCURRENCY/REVERSE_NOTIFY_TIMEOUT_SECONDS） | 环境配置规范 §10 |

## 运维/排障记录（2026-09-16 全量验证过程）

- 现象：中枢全量 pytest 在 `test_task_lifecycle` 模块挂起；上一次会话日志（pytest_full.log）中同模块 4 个用例 ERROR（`'NoneType' object has no attribute 'send'`，跨事件循环连接池残留既有问题）。
- 根因：测试库（共享开发库）残留历史被 kill 的运行遗留的 `test-task-*` pending 任务与 `test-agent-*` 节点；lifecycle 的 `dispatch_pending_tasks` 扫到老 pending 任务推送给 offline 老节点异常中断整轮派发，测试 WS `receive_json()` 永久等待。
- 处理：脚本清理残留（14 任务 + 16 节点 + 21 decisions，真实业务数据未动）后该模块 4 passed；教训——**kill 测试进程会导致 fixture teardown 不执行留下脏数据，测试库需定期按 `test-agent-*`/`test-task-*` 前缀清理**。

## 安装脚本

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| opc-central-dispatch-hub/static/install-scripts/code/install-agent-code-linux.sh | 修 | 新增 `detect_network_info`/`prompt_static_ip`/`apply_static_ip`/`open_firewall_port`/`generate_api_token`；`write_env_file` 保留 API_TOKEN 并写 `AGENT_API_PORT`/`API_TOKEN`/`AGENT_API_HOST`；`parse_args` 支持 `--api-port`；`main` 接入交互流程 | 静态 IP 固化交互（y 按显示值/n 手动输入，非 TTY 跳过）、防火墙放行、token 生成落盘 |
| opc-central-dispatch-hub/data/resources/install-scripts/code/install-agent-code-linux.sh | 修 | 同上（副本同步） | /static 运行缓存优先目录同步 |
| opc-agent-programming/scripts/install-agent-code-linux.sh | 修 | 同上（副本同步） | 仓库脚本副本同步 |

## 与初版决策的偏差

- Spec §3.2 notify URL 写 `https://{ip}:{port}`，实现为 `http://`（HTTPS 为 Spec §7 后续迭代项，本期不涉及）。
- 中枢「对比上次记录跳过」简化为：一次性任务 + 仅 offline 节点过滤（同端口重启时 Agent已在 5s 延迟窗口内自愈转 online，零流量；无跨进程持久化记录）。
- `hub_client.py` 已有 300 行豁免注记，本次将端点探测拆出至 `endpoint_probe.py`、通知服务拆出至 `notify_server.py`，主文件继续按豁免保留会话主链路。

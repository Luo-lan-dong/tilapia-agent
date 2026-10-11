# ADR-035: Agent 地址上报与中枢重启主动反连（agent-address-registry + reverse-notify）

## 状态

Accepted

## 日期

2026-09-15

## 问题背景

中枢重启（或端口避让换端口，ADR-031）后，Agent 重连依赖**串行轮转探测**：
候选环「锁定端点 → 首选 → 8000-8010」逐个尝试，单次注册超时 5s，最坏情况
（中枢换了端口且排位靠后）要数分钟才能重连。用户实测反馈"重启后好久都连不上"。

用户方案（2026-09-15 确认，两步走）：

- **第一步（轻量提速）**：候选端口并行探测 + 缩短单次超时，先把重连压到秒级；
- **第二步（本决策核心）**：安装时固化 Agent 静态 IP 并上报地址/认证密钥给
  中枢入库；中枢重启后**主动反连** Agent 侧暴露的带认证 API，通知新的中枢
  地址与端口，Agent 立即重连——消除探测等待。

## 决策

1. **第一步：候选端点并行探测（先实施）**
   - `hub_client._candidate_api_urls` 的注册探测从串行改为并行
     （`asyncio.wait(FIRST_COMPLETED)` 逐个竞速，首个成功者胜出并锁定）；
   - 单次探测超时 5s → 1s（`_PROBE_TIMEOUT_SECONDS`）；
   - 重连最坏时间从分钟级压缩到 ~10s。
2. **Agent 地址注册表**
   - `agent_nodes` 表扩展 3 列：`reported_ip VARCHAR(64)`、
     `reported_api_port INTEGER`、`api_token VARCHAR(128)`（安装时生成，
     首次注册时上报落库；后续注册原 token 不被覆盖，对齐 ADR-020 幂等语义）；
   - `agent.register` payload 新增可选 `reported_address`
     （{ip, api_port, api_token}）。
3. **Agent 侧通知 API（第二步）**
   - Agent 代理层暴露 `POST /internal/hub-notify`：
     - 认证：`Authorization: Bearer <api_token>`（与注册表比对，失败 401）；
     - payload：`{hub_public_url, hub_ws_url}`（中枢当前地址与 WS 地址）；
     - 行为：Agent 立即把该地址设为候选环首选并触发重连（清锁定端点，
       更新 `AGENT_HUB_URL` 运行时值）；
   - Agent HTTP 服务默认端口 `18080`（`AGENT_API_PORT` 可配），仅监听内网
     地址；安装脚本尝试防火墙放行（firewall-cmd/ufw），失败时输出手动命令。
4. **中枢重启主动反连**
   - lifespan 启动完成后异步任务：查询 `agent_nodes` 中有 reported 地址且
     状态非 online 的节点，逐个（并发上限 5）调用其 notify API，携带中枢
     当前 hub_public_url；失败静默（Agent 自身轮转兜底），打结构化日志；
   - 中枢端口未变化时可跳过（对比上次记录）。
5. **安装脚本（第二步）**
   - 安装完成前展示当前 IP/子网掩码/网关，询问是否固化静态 IP
     （y = 按显示值固化；n = 提示手动输入 IP/掩码/网关）；
   - 防火墙放行 Agent API 端口（可自动则自动，否则输出手动命令）；
   - 安装参数/配置新增 `AGENT_API_PORT` 与生成的 `API_TOKEN`（落盘
     `.tilacode/` 配置，同时随首次注册上报）。

## 备选方案

| 方案 | 结论 |
|------|------|
| A. 仅并行探测 | 采纳为第一步（立即缓解），但不治本：中枢换端口后 Agent 仍需全网段尝试 |
| B. Agent 地址上报 + 中枢反连（本决策第二步） | 采纳。治本：中枢重启后秒级通知，无探测等待 |
| C. 组播/广播发现 | 否决。跨网段不可靠且企业网络普遍禁广播 |
| D. 缩短 Agent 重连间隔（5s→1s） | 否决。探测慢的根因是串行候选轮转而非间隔；且高频轮询浪费 |

## 影响

### Agent 端

- `hub_client.py`：并行探测改造；新增 notify API 处理（地址更新 + 立即重连）；
- 新增轻量 HTTP 服务（api_token 认证，仅两个端点：notify + health）；
- `config.py`：`AGENT_API_PORT` / `API_TOKEN` 配置。

### 中枢

- `agent_nodes` 表 +3 列 + Alembic 迁移；
- 注册处理扩展（reported_address 落库）；
- lifespan 启动反连任务（新增 `agent_reverse_notify` service）。

### 安装脚本

- 静态 IP 固化交互 + 防火墙放行 + API_TOKEN 生成落盘。

### 兼容性

- 旧 Agent：不上报地址（列空）→ 中枢跳过反连，行为同现状；
- 未开放防火墙端口：反连失败静默，Agent 轮转兜底（两机制互补）。

## 关联文档

- [ADR-020 Agent 节点断线重连与任务对账](./020-agent-node-reconnection.md)
- [ADR-031 中枢端口避让与 Agent 端口探测自愈](./031-hub-port-avoidance-and-agent-self-heal.md)
- [035-agent-address-registry-reverse-notify-spec.md](../spec/035-agent-address-registry-reverse-notify-spec.md) /
  [harness](../spec/035-agent-address-registry-reverse-notify-harness.md) /
  [changes](../spec/035-agent-address-registry-reverse-notify-changes.md)

## 实际实施情况

2026-09-16 实施完成，与初版决策一致，偏差已在 Changes 变更清单登记：

- **第一步并行竞速**：从 `hub_client.py` 拆出 `endpoint_probe.py`（`EndpointProber`），
  探测超时 5s→1s，`asyncio.wait(FIRST_COMPLETED)` 竞速、首个成功者锁定；
  会话失败改为**解锁**（下一轮全候选竞速），ADR-031 的「锁定端点环回可重试」
  语义由解锁后全环覆盖自然保留。
- **第二步地址注册表 + 反连**：`agent_nodes` +3 列（迁移 `20260915_addr_registry`，
  含 downgrade）；注册 `reported_address` 落库（api_token 仅首次）；lifespan 启动
  `agent_reverse_notify` 一次性任务（5s 延迟留自愈窗口，offline + 有地址节点，
  并发 ≤5，失败静默）。Agent 侧 `notify_server.py` 用标准库
  `ThreadingHTTPServer` 实现（零新依赖），notify 触发「更新首选地址 + 解锁 +
  立即重连信号」，同端点通知跳过（零开销）。
- **安装脚本**：静态 IP 交互（y 按显示值 / n 手动输入；非 TTY 管道安装跳过）、
  netplan/ifcfg 固化、firewalld/ufw 放行（失败输出手动命令）、API_TOKEN 生成
  （重跑保留，防止中枢侧 token 失配 401）、`--api-port` 参数；三份副本同步。
- **验证**：Agent pytest 108 passed；中枢 pytest 全量 338 passed（19 分 24 秒，
  含 task_lifecycle 4 用例与反连 6 用例）；ruff format/check 通过；真实启动
  临时实例（127.0.0.1:8099）确认 lifespan 反连任务正常运行（降级分支日志
  `reverse_notify_skipped_no_hub_url`）、REST 200、无错误。

### 与初版决策的偏差

- notify URL 用 `http://`（Spec 中 https 为后续迭代项）；
- 「对比上次记录跳过」简化为一次性任务 + offline 过滤 + Agent 侧同端点跳过
  （同端口重启时 Agent 已在延迟窗口内自愈，反连零流量）；
- Agent 通知 API 采用标准库轻量实现而非 FastAPI/uvicorn（Agent 无此依赖，
  ADR 要求「轻量 HTTP 服务」）。

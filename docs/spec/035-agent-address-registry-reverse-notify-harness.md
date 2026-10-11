# Agent 地址上报与中枢重启主动反连 Harness（测试清单）

> 关联：[ADR-035](../adr/035-agent-address-registry-reverse-notify.md) / [Spec](./035-agent-address-registry-reverse-notify-spec.md)

## 1. 测试金字塔

- 单元测试：并行探测竞速、notify API 认证与地址更新、注册上报落库。
- 集成测试：中枢重启反连全流程（mock Agent notify 端点）。
- E2E：换端口重启中枢 → Agent 秒级重连。

## 2. 单元测试清单

### Agent（opc-agent-programming tests/）

| 编号 | 用例 | 断言 |
|------|------|------|
| UT-1 | 候选端点并行探测 | 首个成功端点胜出并锁定；全部失败后按间隔重试整环 |
| UT-2 | 并行探测超时 | 单次探测 1s 超时（慢端点不阻塞快端点胜出） |
| UT-3 | notify API：正确 Bearer token | 地址更新为候选首选并立即触发重连 |
| UT-4 | notify API：错误/缺失 token | 401，地址不变 |
| UT-5 | register payload 带 reported_address | 字段完整（ip/api_port/api_token） |
| UT-6 | 通知 API 仅监听配置地址 | AGENT_API_PORT 生效 |

### 中枢（opc-central-dispatch-hub tests/）

| 编号 | 用例 | 断言 |
|------|------|------|
| UT-7 | register 带 reported_address | agent_nodes 三列落库 |
| UT-8 | 重复注册（同 client_id） | api_token 不被覆盖（ADR-020 幂等语义） |
| UT-9 | 旧 Agent 注册（无 reported_address） | 列保持 NULL，注册成功 |
| UT-10 | 启动反连任务 | 对有地址且非 online 节点逐个调用 notify（mock HTTP），并发上限 5 |
| UT-11 | 反连失败 | 静默 + 告警日志，不影响启动 |
| UT-12 | hub 地址未变化 | 跳过反连调用 |

## 3. 集成测试

| 编号 | 场景 | 断言 |
|------|------|------|
| IT-1 | 中枢换端口重启（8000→8001）→ 反连 | mock Agent notify 收到新地址，Agent 重连到 8001，耗时 ≤10s |
| IT-2 | notify 不可达（防火墙） | 中枢启动不受阻；Agent 并行探测兜底重连 |

## 4. E2E 测试场景

| 编号 | 场景 | 步骤 | 预期 |
|------|------|------|------|
| E2E-1 | 换端口重启全流程 | 安装 Agent（固化 IP + 上报）→ 中枢改端口重启 | 中枢日志出现反连调用；Agent 在中枢新端口重连成功；节点列表在线 |
| E2E-2 | 静态 IP 固化交互 | 安装脚本走 y / n 两条路径 | y：IP 固化成功；n：按手动输入配置 |

## 5. CI 门禁

- `pytest`（hub + agent）全量通过；`ruff format --check && ruff check`。
- 安装脚本 bash 语法检查（bash -n）+ UTF-8 无 BOM。
- `hub_client.py` 改后 ≤300 行（并行探测重构注意拆分）。

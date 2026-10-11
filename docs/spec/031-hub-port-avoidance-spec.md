# 中枢端口避让与 Agent 端口探测自愈 Spec

关联决策：[ADR-031](../adr/031-hub-port-avoidance-and-agent-self-heal.md) / [Harness](./031-hub-port-avoidance-harness.md)

## 功能描述

解决中枢端口避让（8000 被占 → 8010）导致的 Agent 全部离线、前端硬编码端口、
端口耗尽桌面崩溃三大问题。核心：Agent 通过**端口探测自愈**自动发现中枢新端口，
桌面端提供**手动指定端口的兜底窗口**，前端展示**实际端口**并从硬编码改为
动态推导。

## 用户故事

- **US-1（Agent 自愈）**：作为运维者，当桌面中枢因端口冲突自动避让到 8001 后，
  希望已安装的 Agent 能自动重连到 8001，无需逐台改配置或重装。
- **US-2（兜底窗口）**：作为用户，当 8000-8010 全部被占时，希望桌面 UI 仍能
  打开并提示原因，让我手动指定一个端口启动后端，而不是启动崩溃。
- **US-3（端口可视化）**：作为用户，我希望系统设置页显示后端实际监听端口，
  避免我不知道避让到了哪个端口。
- **US-4（去硬编码）**：作为用户，点击「使用当前访问地址」应生成与实际端口
  一致的地址，而不是永远写 8000。

## 核心流程

```
桌面中枢启动
  → find_free_port(8000-8010)
    ├─ 命中 → 端口 P，_start_stack(P) → 打开窗口
    └─ 耗尽(PortExhaustedError)
         → 打开兜底窗口（不退出）
            用户填端口 Q → js_api.start_backend(Q)
              ├─ 端口占用/非法 → 回显原因，可重填
              └─ 成功 → 跳转 http://127.0.0.1:Q

lifespan 启动
  → _reconcile_hub_public_url_port
      PORT 与 hub_public_url 端口不一致 → 改写 URL 端口为实际端口
      
Agent run_forever（每轮会话）
  → register() [候选列表：已锁定→首选→8000-8010]
      └─ 失败 → _advance_endpoint() 轮转下一候选，5s 短超时
  → connect_websocket()（同一候选端点派生 WS）
  → 成功 → _lock_endpoint()（后续优先该端点）
  断线/失败 → 轮转 → 等待 reconnect_interval → 下一轮
```

## 降级链路

1. 中枢端口在 8000-8010 之外（兜底手动指定 8011+）：新装 Agent 命令由启动
   校准改写为实际端口；**已装 Agent 探测不到 8011**，需人工更新 `--hub-url`
   一次（兜底窗口文案已提示）。
2. Agent 旧版本未升级：无探测逻辑，连不上仍按原间隔重试，等待升级。
3. PORT 环境变量为 null（源码模式手动 uvicorn）：`actual_port` 返回 null，
   前端隐藏端口提示，「使用当前访问地址」回落当前访问/8000。
4. 校验失败（校准抛异常）：仅记告警，不阻断启动。

## 验收标准

- AC-1：中枢避让到 8001 后，Agent 无需改配置即可在 ≤ 若干轮重试内重连并
  显示在线（注册 + WS 同端点）。
- AC-2：8000-8010 全占时双击启动不崩溃，出现兜底窗口；填 8011 可正常启动
  后端并打开控制台；关闭窗口后后端停止。
- AC-3：设置页「网络配置」显示「后端当前实际监听端口：{actual_port}」。
- AC-4：点击「使用当前访问地址」生成的 Hub URL 端口 = actual_port（有值时）。
- AC-5：lifespan 启动后 `system_settings.hub_public_url` 端口为实际端口。
- AC-6：ruff/tsc/eslint 通过，hub reconcile 用例 + agent 探测用例全绿。
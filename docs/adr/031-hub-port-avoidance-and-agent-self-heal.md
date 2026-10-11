# ADR-031: 中枢端口避让与 Agent 端口探测自愈

## 状态

Accepted

## 日期

2026-09-12

## 问题背景

桌面模式（ADR-021）默认使用 8000 端口，被占用时自动向后避让（8000-8010）。
由此产生一条断链：

1. **中枢端口变化无感知**：用户不知道后端实际避让到了哪个端口；系统设置页
   也不展示实际端口。
2. **已装 Agent 全部离线**：Agent 配置文件中的 `--hub-url` 写死安装时的端口，
   中枢避让后 Agent 仍连旧端口，触发离线，需人工逐台重装/改配置。
3. **前端硬编码**：系统设置页「使用当前访问地址」硬编码 8000 端口，避让后
   生成错误 Hub URL。
4. **极端场景崩溃**：8000-8010 全部被占时 launcher 直接抛错退出，桌面 UI
   无法打开。

用户明确的目标：人只填中枢地址（默认 8000），端口冲突自动避让；Agent 不需要
人工干预即可恢复连接；前端不得硬编码端口。

## 决策

与用户确认（2026-09-12）：**Agent 端口探测自愈**方案，并附加前提——8000-8010
全部耗尽时桌面 UI 仍必须可启动，提供手动指定端口的兜底窗口。

1. **Agent 端口探测自愈（核心）**：`HubClient` 维护候选端点列表
   （已锁定端点 → 配置首选地址 → 同主机 8000-8010 避让范围），注册失败/断线
   后轮转到下一候选重试；注册+WS 建连均成功后**锁定**该端点（后续重连优先）。
   探测轮注册用 5s 短超时（`_PROBE_TIMEOUT_SECONDS`），避免逐端口 30s 阻塞；
   锁定后恢复 30s 常规超时。WS 地址由当前候选 API 地址派生，保证注册与建连
   同端点。
2. **端口耗尽兜底窗口**：launcher `find_free_port` 抛 `PortExhaustedError`
   时不退出进程，打开内嵌 HTML 兜底窗口（后端未启动无法加载正式前端）：
   展示原因 + 端口输入框 + 「启动后端」按钮，经 pywebview `js_api` 以用户
   指定端口拉起组件栈（内嵌 PG → 环境注入 → 迁移 → uvicorn），成功后窗口
   跳转正常界面。关窗统一清理。
3. **实际端口可见化**：launcher 注入 `PORT` 环境变量（本就有），新增
   `GET/PATCH /settings/network` 响应字段 `actual_port`（读 PORT，手动
   uvicorn 启动时为 null）；前端系统设置页展示「后端当前实际监听端口」提示。
4. **Hub URL 端口自动校准**：lifespan 启动时若 PORT 与 `system_settings.
   hub_public_url` 端口不一致，自动把 URL 端口替换为实际端口（保证**新装**
   Agent 安装命令地址正确；已装 Agent 由探测自愈兜底）。校准失败仅告警不阻断
   启动。**仅在 APP_ENV=desktop 时生效**（2026-09-12 修订，见下）。
5. **前端去硬编码**：「使用当前访问地址」按钮优先取 API 返回的
   `actual_port`，其次当前访问端口（桌面模式前端由后端托管，端口即后端端口），
   dev 模式（5173）回落 8000。

明确不做（与用户讨论过的更重方案对比）：

- **不做**「中枢预存各 Agent 地址、端口变化后主动推送配置更新」：需要 Agent
  侧开放入站端口/反向通道，NAT/防火墙场景不可靠；探测自愈为纯出站连接，
  无此问题。
- **不做** 安装时固定静态地址：静态地址无法应对日后端口再次变化。

## 备选方案

| 方案 | 结论 |
|------|------|
| A. Agent 端口探测自愈（本决策） | 采纳。纯出站连接、零人工干预、对现有架构侵入最小 |
| B. 中枢主动推送新端口到各 Agent（预存地址反向更新） | 否决。要求 Agent 可入站到达（NAT/防火墙受限），中枢需维护每台地址，复杂度高收益低 |
| C. 固定端口 + 启动失败报错让人处理 | 否决。用户明确要求自动避让且无需人工干预 |
| D. mDNS/广播自动发现中枢 | 否决。跨网段不可用，调试困难，超出 MVP 需要 |

## 影响

- **Agent**：hub_client 新增端点轮转/锁定逻辑（约 60 行）；重连循环注册失败
  进入轮转。旧版 Agent 行为不变（连不上仍按原间隔重试），升级后获得自愈能力。
- **中枢**：launcher 兜底窗口与 `_start_stack` 复用重构；main.py lifespan
  增加 URL 校准；settings 网络端点返回 `actual_port`。无数据库迁移。
- **前端**：Settings 页实际端口提示 + 使用当前访问地址去硬编码；类型加
  `actual_port`。
- **兼容性**：Agent 配置文件不变；`--hub-url` 语义不变（首选地址，探测在其
  失败时才展开）。中枢在 8000-8010 之外的端口（兜底手动指定）同样被探测
  覆盖不到——此时靠启动校准改写安装命令地址，**已装 Agent 需人工改一次**
  （已在兜底窗口文案中说明）。
- **安全**：探测仅对配置中同一主机的 8000-8010 端口发注册请求，注册接口
  本有 token 校验，无新增攻击面。

## 关联文档

- [Spec](../spec/031-hub-port-avoidance-spec.md) / [Harness](../spec/031-hub-port-avoidance-harness.md) /
  [Changes](../spec/031-hub-port-avoidance-changes.md)
- ADR-021（桌面一体化部署，端口避让的来源）
- ADR-020（Agent 断线重连，本决策在其重连循环上扩展轮转）
- 现状依据：`launcher/port.py` / `launcher/desktop.py` /
  `opc-agent-programming/app/client/hub_client.py`

## 实际实施情况

2026-09-12 实施完成，与初版决策一致：

- Agent 探测自愈：候选列表/轮转/锁定 + 探测短超时，注册与 WS 同端点。
- 兜底窗口：`FallbackApi.start_backend` 校验端口占用与组件栈拉起，失败原因
  回显可重试。
- 启动校准：`_reconcile_hub_public_url_port`（PORT 与 URL 端口不一致时改写，
  保留 scheme/path/query，仅记录日志不阻断）。
- 测试：hub 目标测试 34 passed（含 reconcile 7 用例、network actual_port 2
  用例）；agent hub_client 12 passed（含候选列表/轮转锁定/注册失败轮转 5 个
  ADR-031 用例）；前端 tsc + eslint 通过。

### 修订记录（2026-09-12：校准 desktop-only 收紧）

**事故**：开发者冒烟验证以 `PORT=8021` 启动源码模式临时实例，启动校准把开发库
`system_settings.hub_public_url` 从 `:8000` 静默改写为 `:8021` 并持久化，实例
停止后残留，用户 8000 实例的设置页读到了错误 URL。

**根因**：校准条件只看 PORT 环境变量，无法区分「launcher 注入（桌面避让，
应校准）」与「手动设 PORT 起临时实例（显式行为，不应改写持久化配置）」。

**修订**：`_reconcile_hub_public_url_port` 增加 `APP_ENV=desktop` 门禁——
桌面模式（launcher 同时注入 APP_ENV=desktop 与 PORT）校准照常；源码模式
一律跳过。已用 Python 脚本把误改的 URL 恢复为 `:8000`。

**验证**：新增回归用例 `test_reconcile_skipped_when_not_desktop`（8 passed）；
实跑源码模式 PORT=8023 实例确认 URL 保持 `:8000` 不被改写、`actual_port`
仍正确返回。

# ADR-049：宝塔 MCP 部署一条龙服务（中枢直连多服务器管理 + 工具代理 + 部署技能）

- **状态**：Accepted
- **日期**：2026-09-26
- **关联**：ADR-033（技能框架）、ADR-040（Orchestrator 工具循环）、ADR-030（LLM 多配置管理，仿照其凭据入库模式）、ADR-047（tool_call_logs 审计）、ADR-028（产物交付，部署源）

## 1. 问题背景

1. 现有链路已打通"需求讨论 → 三件套 → 派发 → Code Agent 编码 → 产物回传中枢（ADR-028）"，**最后缺口是部署**：把开发完成的产物部署到生产服务器并接管运维。
2. 选型已定：走**宝塔官方 MCP**（面板 13.0 + MCP 插件，HTTPS 8765，Bearer Token + IP 白名单，98 个工具，风险分级 + 高危工具内置两段式 confirm），不走面板 HTTP API（官方明示不保证稳定性）。
3. 用户已确认的决策：
   - **中枢直连**（唯一 MCP 客户端）：白名单只加中枢 IP、产物已在中枢（可直接 Upload）、部署编排/审计/日志复用现有机制；
   - **权限管控完全放服务器端插件**（用户在宝塔面板管理工具开关），中枢不做重复管控、98 个工具全量透传；
   - **多 MCP 服务器管理**：中枢支持添加多个 MCP 连接（对应不同服务器），部署前先确认目标服务器；
   - 高危确认走服务端 `confirm` 两段式机制，中枢转为对话确认交互。
4. 连通性已实测验证（2026-09-26）：内网地址 + CA 链信任 + 白名单放行后 initialize/tools/list 全通，FastMCP 1.28.1 无状态模式，98 工具全量返回。

## 2. 决策（Decision）

### 2.1 多 MCP 服务器管理（`bt_mcp_servers` 表）

- 新表：`{id, name(unique), local_url, public_url(NULL), address_mode(local/public), api_token, ca_cert(NULL, PEM 全文), enabled, remark, created_at, updated_at}`。
- `address_mode` 记录中枢实际使用的地址（与服务器同网段用 local，跨网段用 public）——宝塔插件生成的 mcp_info 即双地址结构，证书 SAN 只覆盖其一。
- `ca_cert` 存 PEM 证书链全文（自签 IP 证书场景），运行时以 `ssl.SSLContext.load_verify_locations(cadata=...)` 内存加载，**不写临时文件**。
- 凭据入库（仿 ADR-030 llm_providers 模式）：API 响应一律脱敏（token/证书只返回"是否已配置"）。
- 支持**粘贴 mcp_info JSON 一键导入**：POST body 携带插件生成的 mcp_info JSON，后端解析 token/双地址/证书并自动填充。

### 2.2 自研轻量 MCP 客户端（不引入官方 SDK）

- `app/services/bt_mcp_client.py`：httpx 异步实现，协议面仅 3 方法——`initialize` / `tools/list` / `tools/call`（+ `notifications/initialized`）。
- 宝塔服务端为 FastMCP 无状态模式（实测 initialize 不返回 session id）；客户端**兼容有状态**：返回 `Mcp-Session-Id` 时缓存并在后续请求携带。
- TLS：`ca_cert` 存在时构建自签 CA 信任的 SSLContext（不跳过校验）；无 ca_cert 时走系统信任链。
- **错误翻译**（核心收益）：`ip denied` → 白名单排查指引（含中枢出口 IP 提示）；401 → token 失效指引；TLS 错误 → 证书链重新获取指引；连接拒绝 → 端口放行指引。每次 tools/call 写 ADR-047 结构化日志（tool_call_logs 事件，token/参数脱敏）。

### 2.3 工具代理模式（解决 98 工具 schema 30K+ token 注入问题）

不把 98 个工具 schema 全量注入 Orchestrator function calling，注册 5 个技能：

| 技能 | 作用 |
| --- | --- |
| `bt_mcp_servers` | 列出启用的 MCP 服务器（id/name/remark），部署第一步确认目标服务器 |
| `bt_mcp_tools` | 按服务器列工具目录（名称 + 一句话描述 + 风险等级） |
| `bt_mcp_schema` | 查单个工具的完整 inputSchema（调用前不确定参数时使用） |
| `bt_mcp_call` | 统一调用入口 `{server_id, tool, arguments}`，返回宝塔统一结构 `{status, msg, ...}` |
| `bt_deploy_guide` | 返回沉淀自官方 7 个 Skill 的部署 SOP 精要（按需加载知识，详见 2.4） |

- 权限不做本地管控（服务器端插件管理）；工具目录/风险等级仅用于 AI 判断与展示。
- 风险等级映射固化于代码（low/medium/high，来自官方 tools-reference），`bt_mcp_tools` 返回供 AI 自查。

### 2.4 部署 SOP 知识沉淀（`bt_deploy_guide` 技能）

- 将官方 bt-skills 包 7 个 SOP（Go/Html/Java/Node/Python 部署 + 反代 + 排障）提炼为核心规则精要（部署顺序、prep_status 轮询、创建成功≠启动成功、绑域名=外网映射+反代、单任务工具调用上限、禁止事项），**存代码常量、经技能按需返回**——系统提示词只带一句话描述，AI 部署时调用一次即"读入"SOP，避免 token 常驻。
- v1 SOP 覆盖：Python / NodeJS / Go / HTML(静态) 四类项目部署 + 通用排障要点；Java/反代细节列为后续迭代。

### 2.5 高危操作两段式确认（复用服务端机制）

- 宝塔高危工具（Delete 类/Bash/MysqlExecute 等）首次调用（`confirm=false` 或缺省）返回 `waiting_confirm` 且不执行；`bt_mcp_call` 把该状态如实回传 LLM 并附指引："向用户展示操作内容与影响，获得明确同意后以完全相同的业务参数 + confirm=true 重调"。
- 确认交互走现有对话流（AI 提问 → 用户回复），**前端零新增组件**。

### 2.6 触发方式与边界（v1）

- v1 仅**对话内发起**：用户说"部署"→ AI 用 `bt_mcp_servers` 确认目标服务器 → 收集项目类型/产物路径/域名 → 按 SOP 逐步 `bt_mcp_call`。
- 产物上传：中枢 `data/artifacts/{task_id}/export.zip` 已在本地，由 AI 指导用 `Upload` 工具流程（客户端本机执行 curl）或 `Bash` 拉取方式上服务器。
  > **修订（ADR-068，2026-10-08）**：上传机制已两轮演进——① ADR-051 将「本机 curl」改为中枢自动代执行（`maybe_run_local_command`，文件源=会话附件库）；② ADR-068 把文件源扩展到**任务产物缓存目录**（`export.zip` 与文件树文件），并新增 `list_artifacts`/`read_file` 技能让编排 AI 自主感知产物。AI 现可直接 `Upload(file_name="export.zip")` 完成产物上传，无需用户手动传包。
- 任务链自动挂接部署、部署状态持久化视图、Java/反代 SOP：后续迭代。

## 3. 备选方案（Considered Options）

| 方案 | 结论 | 原因 |
| --- | --- | --- |
| Agent 节点直连宝塔 MCP | ❌ 否决 | 每节点都要白名单+令牌下发链路（复刻 ADR-029），风险面大；产物还需二次搬运 |
| 面板 HTTP API 直调 | ❌ 否决 | 官方明示不保证稳定性；MCP 工具带风险分级/确认机制/运维细节（prep_status 等） |
| 官方 mcp Python SDK | ❌ 否决 | 协议面仅 3 方法（实测跑通），SDK 抽象厚、新增依赖、错误需二次翻译；自研 ~200 行可控 |
| 98 工具 schema 全量注入 | ❌ 否决 | 约 30K+ token 常驻 system，压缩频繁、cache 不稳、成本高 |
| 按分类动态注入 schema | ❌ 否决（v1） | 需维护分类映射，复杂度高；代理入口模式 + SOP 已覆盖；后续可作 token 优化迭代 |
| 中枢本地工具权限开关 | ❌ 否决 | 用户明确：权限管控放服务器端插件，本地重复管控徒增维护 |
| 确认交互做前端确认卡片 | ❌ 否决（v1） | 服务端 confirm 两段式天然适配对话流；前端卡片列为后续体验优化 |

## 4. 影响分析

- **中枢**：新表 + 迁移、`bt_mcp_client` 新服务、`/bt-mcp/servers` CRUD+test+tools 端点、5 个新技能注册、错误码 207xxx、前端设置页新增「宝塔 MCP」管理区。
- **Agent 节点**：零改动。
- **安全**：token/证书入库脱敏展示；日志与技能结果中 token 脱敏；高危操作依赖服务端 confirm 两段式 + AI 对话确认。
- **token 成本**：系统提示词仅增 5 条技能描述（~500 token），SOP 按需加载。

## 5. 验收要点

1. 添加服务器（含 mcp_info JSON 导入）→ 列表脱敏展示 → 测试连接返回 serverInfo + 工具统计。
2. 白名单未放行时测试连接 → 错误信息含"IP 白名单"排查指引；证书错误 → 指引重新获取证书链。
3. 编排对话："部署"→ AI 先调 `bt_mcp_servers` 让用户选服务器 → `bt_deploy_guide` 读 SOP → 按 SOP 顺序调用工具。
4. 调用高危工具 → 返回 waiting_confirm + 指引 → 用户同意后 confirm=true 重调成功执行。
5. 实测（对真实服务器）：`SystemInfo` 只读查询返回真实数据；98 工具目录可见。

## 6. 实际实施情况

1. **开发范围**：bt_mcp_servers 表 + Alembic 迁移 + 自研 MCP 客户端 + CRUD + 连接测试 + 工具清单端点 + 6 个技能（含 bt_mcp_setup 自动配置）+ 38 个单测 + 前端 BtMcpPanel 管理界面。
2. **验证结果**：
   - 38/38 bt_mcp 单测全部通过（含客户端协议/错误翻译/API CRUD/技能编排/setup 自动配置）。
   - 全链 API 冒烟通过：`/bt-mcp/servers` 返回 `{"total":0,"items":[]}`，`/skills` 返回 6 个 bt_mcp 技能全部 enabled。
   - 前端构建通过（vite build 3115 modules，含 BtMcpPanel 产物）。
   - Alembic 迁移成功应用到开发库（alembic upgrade head）。
3. **测试修复**：
   - `_patch_client` helper：side_effect 模式未正确处理 return_value=None 导致 TypeError，已修复。
   - `test_setup_success`：token[:8] 截断出的前缀是 `btmcp_se` 而非 `btmcp_set`，断言已对齐。
4. **未完成项**（v1 范围外）：
   - 真实宝塔面板 E2E 冒烟（需面板 13.0 + MCP 插件 + 白名单 + CA 证书已就位）。
   - 任务链自动挂接部署、Java/反代 SOP。
   - 全量 568 个回归测试：某测试在调度器扫描周期处挂起（疑似定时器事件循环争用），非 bt_mcp 模块问题。

# ADR-053：中枢 PTY 交互式终端与命令执行三档权限

- **状态**：Accepted（2026-09-27 用户确认方案并实施）
- **日期**：2026-09-27
- **关联**：ADR-033（技能框架）、ADR-045（Steering 队列，审批结果注入复用其思路）、ADR-049（工具治理与审计）、ADR-021（中枢 Windows 桌面）

## 1. 问题背景

中枢缺少本机终端能力：AI 无法替用户在中枢机器上执行命令（构建/依赖/git 等运维操作全靠用户手动），用户也无法在中枢 Web 界面打开终端。需求：①AI 可执行终端命令；②权限三档（手动审批 / AI 自动审批+必要时询问 / 完全访问）；③顶栏终端入口，用户可随时打开终端；④AI 执行命令时终端面板自动展开可见。

## 2. 决策（Decision）

### 2.1 PTY 交互式终端（用户选型：一步到位）

- **pywinpty**（Windows ConPTY）实现会话级 PTY；Linux 分支预留（pty 模块）。初始 shell：Windows PowerShell / Linux bash。
- 每个对话会话（task_building_session）一个 PTY 会话，初始 cwd = 会话绑定工作目录（TaskNew 已选目录；未绑定时中枢工作目录）。
- 生命周期：懒创建（首次连接/命令时）、空闲超时回收（`TERMINAL_IDLE_TIMEOUT_SECONDS`，默认 1800s）、中枢重启即销毁；输出环形缓冲（最近 200KB）供重连回放。

### 2.2 AI 执行双轨（结构化结果 + 终端可见）

- AI 命令走 `run_command` 技能：subprocess 非交互执行（cwd=会话目录、超时默认 120s、输出截断 4000 字符），stdout/stderr/exit_code 结构化返回给 AI；
- 命令行与输出**回显**到该会话终端流（AI 执行样式块），终端面板统一可见。

### 2.3 三档权限（terminal_permission_mode，存 system_settings 新列，默认 manual）

| 模式 | 行为 |
| --- | --- |
| `manual` 手动审批 | 每条 AI 命令弹对话流确认卡，用户批准才执行 |
| `ai_auto` AI 自动审批 | 模型自评 `risk_level`（提示词定义高危：删除/格式化/下载执行/系统配置/凭据/网络暴露）；low 直接执行，high/unknown 转审批询问用户 |
| `full` 完全访问 | 不经审批直接执行 |

- 审批实现：技能内 `asyncio.Event` 阻塞等待（超时 300s）——技能发出 `command_approval_request` 事件（SSE 透传前端确认卡）→ 用户点批准/拒绝 → `POST /api/v1/terminal/approvals/{id}` 置位 → 技能继续执行并随本轮返回结果；
- 切换入口：终端面板头部下拉（热生效）；GET/PUT `/api/v1/terminal/permission-mode`。

### 2.4 前端

- xterm.js（@xterm/xterm + addon-fit）全局右侧终端抽屉（所有页面可用，可调宽）；
- 顶栏新增终端图标（主题按钮左侧，TaskNew 页 top-toggle 相应再右移）；
- AI 执行命令时（SSE `command_executing` 事件）自动展开终端抽屉；
- WS 通道 `/api/v1/terminal/ws?session_id=`：上行 input/resize，下行 output/exit；同会话多连接广播。

### 2.5 审计与错误码

- tool_call_logs 新增 kind=`terminal_command`（迁移扩充 CheckConstraint），记录命令原文、模式、批准方式（user/ai_auto/full）、exit_code；
- 错误码 210001-210004（PTY_SPAWN_FAILED / TERMINAL_NOT_FOUND / PERMISSION_DENIED / APPROVAL_TIMEOUT）。

## 3. 备选方案（Considered Options）

| 方案 | 结论 | 原因 |
| --- | --- | --- |
| 命令+输出流（非交互） | ❌ 用户否决 | 需求明确一步到位 PTY 交互式 |
| AI 命令直接写 PTY 执行 | ❌ 否决 | 输出无命令边界，AI 拿不到结构化结果；审批不可控 |
| 复用 Agent 节点执行 | ❌ 否决 | 跨机非中枢本机；链路重 |
| 宝塔式 waiting_confirm 两段式重调 | ❌ 否决 | 审批动作在确认卡而非对话输入，AI 重调模型负担重；改用技能内 Event 等待 |

## 4. 影响分析

- 后端：terminal_service + terminal 端点 + run_command 技能 + system_settings 加列迁移 + tool_call_logs kind 扩充 + orchestrator 提示词与事件转发；
- 前端：TerminalDrawer/TerminalPanel + useTerminalWs + 顶栏图标 + 确认卡 + 自动展开；
- 新依赖：pywinpty（后端）、@xterm/xterm + @xterm/addon-fit（前端）；
- 风险：full 模式无审批（面板切换时红色警示）；PTY 输出量大（环形缓冲+节流）。

## 5. 实际实施情况（2026-09-27 实施完成）

按决策全量落地（明细见 [053-hub-terminal-changes.md](../spec/053-hub-terminal-changes.md)）：

- **后端**：terminal_service（pywinpty PTY + 环形缓冲 + 广播）、terminal_approval（Event 审批等待 240s）、/terminal 端点（WS + 权限模式 + 审批决定）、RunCommandSkill（23 技能注册）、system_settings 加列迁移 20260927_terminal（已应用）、错误码 2100xx；
- **前端**：TerminalDrawer（xterm.js，主题色读 CSS 变量符合 ADR-052 防回潮）、顶栏终端图标、useTaskBuildingChat 接入 command_approval_request（对话流确认卡）/command_executing（自动展开）、terminalStore（useSyncExternalStore 全局开关 + 会话/目录同步）、TaskNew 三图标布局协调；
- **实施修复**：Vite 代理补 `ws: true`（dev 环境 WS 不转发导致终端连不上）；
- **验证**：后端 ruff/导入冒烟/迁移/权限接口冒烟/技能测试 8 项过；前端 tsc/build 过、chat-panel 测试 3/3、lint 无新增；浏览器 E2E 终端全链路（打开抽屉→PTY PowerShell→键入回显→执行输出→提示符）与三图标布局通过；
- **已知边界**：AI 真实对话执行审批链路未实测（机制复用既有 SSE 透传与技能审计）；PTY 空闲清扫未启用（重启销毁覆盖 MVP）；terminal_command 审计 kind 预留未单独写入（复用 skill_call/skill_result）。

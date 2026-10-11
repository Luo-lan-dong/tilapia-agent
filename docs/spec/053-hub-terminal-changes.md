# 中枢终端 Changes 变更清单（ADR-053）

> 关联：[ADR-053](../adr/053-hub-terminal-pty.md) · [Spec](./053-hub-terminal-spec.md) · [Harness](./053-hub-terminal-harness.md)
> 边开发边实时登记。

## 变更清单

| 文件 | 类型 | 函数/类级明细 | 作用说明 |
| --- | --- | --- | --- |
| opc-central-dispatch-hub/app/services/terminal_service.py | 增 | `TerminalSession`（PTY 进程 + 环形缓冲 200K + 订阅广播）/ `TerminalService`（get_or_create 懒创建 / _read_loop 线程池阻塞读 / write_input / resize / ai_echo 回显 / close_session）/ `terminal_service` 单例；pywinpty 缺失降级 210001 | PTY 会话管理核心 |
| opc-central-dispatch-hub/app/services/terminal_approval.py | 增 | `PendingApproval` + `TerminalApprovalRegistry`（create/resolve/wait，asyncio.Event 等待 240s 超时，小于技能预算 300s） | manual/ai_auto 审批等待 |
| opc-central-dispatch-hub/app/schemas/terminal.py | 增 | PermissionModeResponse/Update + ApprovalDecisionRequest + TERMINAL_MODES | 接口 Schema |
| opc-central-dispatch-hub/app/api/v1/endpoints/terminal.py | 增 | GET/PUT `/terminal/permission-mode`（system_settings 热读写）+ POST `/terminal/approvals/{id}` + WS `/terminal/ws`（上行 input/resize、下行 output 广播、缓冲回放） | 终端 API 与 WS 通道 |
| opc-central-dispatch-hub/app/api/v1/api.py | 修 | import terminal + `api_router.include_router(terminal.router, prefix="/terminal")` | 路由挂载 |
| opc-central-dispatch-hub/app/core/errors.py | 修 | ErrorCode 加终端域 210001-210004 | 错误码 |
| opc-central-dispatch-hub/app/models/system_settings.py | 修 | 加列 `terminal_permission_mode`（String(16) nullable） | 权限模式存储 |
| opc-central-dispatch-hub/app/models/tool_call_log.py | 修 | ToolCallLogKind 加 TERMINAL_COMMAND + CheckConstraint 白名单扩充 | 审计预留 |
| opc-central-dispatch-hub/alembic/versions/20260927_terminal.py | 增 | 加列 + kind 约束 drop/recreate（down 反向） | 迁移（已应用） |
| opc-central-dispatch-hub/app/skills/terminal_skill.py | 增 | `RunCommandSkill`（command/risk_level/timeout_seconds）：权限读取回退 manual → 审批事件 command_approval_request + Event 等待 → subprocess 非交互执行（超时 5-600s、输出截断 4000）→ ai_echo 终端回显 → 结构化结果 | AI 执行终端命令 |
| opc-central-dispatch-hub/app/skills/registry.py | 修 | import + SKILL_REGISTRY 加 RunCommandSkill（23 技能） | 注册 |
| opc-web-console/vite.config.ts | 修 | `/api` 代理加 `ws: true` | dev 环境 WS 转发（修复终端连不上） |
| opc-web-console/src/stores/terminalStore.ts | 增 | terminalStore（open/sessionId/workDir，useSyncExternalStore）+ useTerminalState | 全局终端 UI 状态 |
| opc-web-console/src/api/terminal.ts | 增 | fetchPermissionMode/updatePermissionMode/decideTerminalApproval | REST 封装 |
| opc-web-console/src/components/TerminalDrawer.tsx + .css | 增 | xterm.js（@xterm/xterm + addon-fit）WS 双向 + 主题色读 CSS 变量 + 权限下拉（full 红色警示）+ 重连 + 审批条 | 终端抽屉 UI |
| opc-web-console/src/components/AppLayout.tsx | 修 | import（CodeOutlined/terminalStore/TerminalDrawer）+ 顶栏终端图标 + `<TerminalDrawer />` 全局挂载 | 终端入口 |
| opc-web-console/src/hooks/useTaskBuildingChat.ts | 修 | import（terminalStore/TerminalApprovalRequest）+ approvalRequest state + case command_approval_request/command_executing（自动展开终端）+ State/Actions 接口与返回值扩充 | SSE 事件接入 |
| opc-web-console/src/components/TaskNewChatPanel.tsx | 修 | props 加 approvalRequest/onApprovalDecide + 对话流审批确认卡渲染 | 对话流审批 UI |
| opc-web-console/src/pages/TaskNew.tsx | 修 | import（decideTerminalApproval/terminalStore）+ sessionId/workspaceDir 同步 terminalStore + ChatPanel 传审批 props（批准调 API） | 审批处理与目录同步 |
| opc-web-console/src/pages/TaskNew.css | 修 | `.task-new__top-toggle` right 96/214（避开终端+主题按钮）+ `.task-new__approval*` 确认卡样式 | 布局协调 |
| opc-web-console/src/types/taskBuilding.ts | 修 | 事件类型加 command_approval_request/command_executing + TerminalApprovalRequest 接口 | 类型 |

## 验证记录（2026-09-27）

- 后端：ruff 0 错、app 导入冒烟过、迁移 20260927_terminal 应用、GET /terminal/permission-mode 冒烟返回 `manual`、tests/skills 8 项过；
- 前端：tsc 0 错、build 过、lint 无新增（存量 2 error 与本次无关）、chat-panel 测试 3/3；
- 浏览器 E2E：顶栏终端图标 → 抽屉展开 → WS 经 Vite 代理（ws:true）连接 → PowerShell 提示符出现（cwd=中枢根）→ 键入回显 → 回车执行输出列表 → 新提示符；TaskNew 三图标（面板收起/终端/主题）错开无重叠。

## 已知边界（后续迭代）

- AI 执行 + 审批全链路未做真实 LLM 对话实测（逻辑已实现：SSE 事件透传机制与 skill_result 审计复用既有链路）；
- PTY 空闲超时回收未启后台清扫（重启销毁已覆盖 MVP）；
- run_command 结果审计复用 orchestrator 的 skill_call/skill_result 记录（terminal_command kind 已预留未单独写入）。

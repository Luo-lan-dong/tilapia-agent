# 中枢终端 Harness（ADR-053）

## 1. 测试金字塔

| 层级 | 范围 |
| --- | --- |
| 单元（中枢 pytest） | 权限模式读写（含回退默认 manual）；审批注册表置位/超时；run_command 执行（subprocess mock：超时/截断/exit_code）；PTY 服务的会话注册表与缓冲（pywinpty 层 mock） |
| 单元（前端 vitest） | 确认卡渲染与 approve 调用；权限下拉切换调用 PUT；AI 回显块渲染 |
| 集成 | WS 端点连接/上行下行/多连接广播；审批全链路（SSE 事件→POST approve→技能返回结果） |
| E2E（人工浏览器） | 见 §2 |

## 2. E2E 走查清单

| 场景 | 检查点 |
| --- | --- |
| 打开终端 | 顶栏图标 → 抽屉展开 → PowerShell 提示符出现 → dir 输出实时 |
| resize | 拖动抽屉宽度 → xterm 自适应不乱版 |
| manual 审批 | AI 请求执行命令 → 确认卡出现 → 拒绝（AI 收到拒绝）→ 再请求 → 批准 → 执行且终端回显 |
| ai_auto | 低危（dir）直行；高危（rm）转确认卡 |
| full | 命令直接执行，切换处有警示 |
| 自动展开 | 对话流中 AI 执行命令 → 抽屉未开时自动打开 |
| 权限持久化 | 切模式后刷新页面仍是该模式 |
| 审计 | tool_call_logs 出现 terminal_command 记录 |

## 3. 回归范围

- 技能注册数变化（+1）；orchestrator 既有测试（事件转发白名单扩充不回归）；
- system_settings 相关接口（新增列不影响既有字段）；
- 前端 tsc/eslint/build 与既有测试。

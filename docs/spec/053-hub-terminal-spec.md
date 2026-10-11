# 中枢终端 Spec（ADR-053）

## 1. 功能描述

中枢获得本机终端能力：用户可在 Web 界面打开交互式终端（PTY）；AI 可通过 run_command 技能执行命令，受三档权限控制；AI 执行时终端面板自动展开并回显命令与输出。

## 2. 用户故事

- US-1：用户点击顶栏终端图标，右侧展开终端抽屉，可直接敲命令（PowerShell），初始目录为当前会话工作目录。
- US-2：AI 需要执行命令时调用 run_command；manual 模式下对话流出现确认卡，批准后执行，命令与输出同时出现在终端面板。
- US-3：用户在终端面板顶部下拉切换三档权限（manual/ai_auto/full），即时生效；full 档有红色警示。
- US-4：ai_auto 模式下 AI 对低危命令直接执行，高危或不确定命令自动转人工确认。
- US-5：会话恢复后打开终端，可回放最近输出（环形缓冲）。

## 3. 核心流程

```
用户终端：
  顶栏终端图标 → 打开终端抽屉 → WS 连接 /terminal/ws?session_id=
    → 服务端懒创建 PTY(cwd=会话目录) → 回放缓冲 → 双向流（input/output/resize）

AI 执行：
  AI 调 run_command(command, risk_level)
    → 读 terminal_permission_mode
      manual → 事件 command_approval_request → 前端确认卡
               → 用户批准 POST /terminal/approvals/{id} → 执行
               → 拒绝 → 返回"用户拒绝"
      ai_auto → risk_level=low → 执行；high/unknown → 同 manual 流程
      full → 直接执行
    → subprocess 执行（超时 120s 默认） → 结果回 AI + 回显终端流
    → SSE command_executing/command_executed → 前端自动展开终端抽屉
```

## 4. 接口与事件

| 类型 | 名称 | 说明 |
| --- | --- | --- |
| WS | `/api/v1/terminal/ws?session_id=` | 上行 `{type:"input",data}` / `{type:"resize",cols,rows}`；下行 `{type:"output",data}` / `{type:"exit"}` / `{type:"ai_echo",command,output,exit_code}` |
| GET | `/api/v1/terminal/permission-mode` | 当前模式 |
| PUT | `/api/v1/terminal/permission-mode` | 切换模式（写 system_settings.terminal_permission_mode） |
| POST | `/api/v1/terminal/approvals/{approval_id}` | `{approve: bool}` 置位审批事件 |
| SSE | `command_approval_request` | payload: approval_id/command/mode/risk_level |
| SSE | `command_executing` / `command_executed` | 前端自动展开终端 + 结果卡 |

## 5. 边界与降级

- pywinpty 安装失败/非 Windows：PTY 创建报 210001，终端面板显示不可用说明；
- 审批等待超时（300s）：技能返回失败提示；
- PTY 空闲超时回收后重连：重新懒创建 + 缓冲为空提示；
- 会话未绑定工作目录：回退中枢工作目录。

## 6. 验收标准

1. 终端抽屉可敲命令（dir/cd/npm -v），输出实时、支持 resize；
2. 三档模式切换生效：manual 拦截、ai_auto 低危直行高危询问、full 直行；
3. AI 执行命令时抽屉自动展开且回显 AI 样式块；
4. tool_call_logs 有 terminal_command 审计记录；
5. 中枢重启后终端会话重建，前端重连可用。

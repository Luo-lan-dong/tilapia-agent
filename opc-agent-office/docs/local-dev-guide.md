# 办公 Agent 节点（opc-agent-office）本地开发测试教程

> 适用场景：在 Windows / macOS 本机把 office 节点跑起来，连接中枢完成
> 「注册 → 派任务 → 执行 → 产物回传」全链路验证，再去 Linux 服务器正式部署。
> 代码天然跨平台，本机与服务器**共用同一套代码，只换 `.env`**（ADR-062）。

## 1. 前置条件

| 依赖 | 要求 | 说明 |
| --- | --- | --- |
| Python | ≥ 3.10 | 本机开发用全局 Python 或 venv 均可 |
| 中枢 | 已运行（dev 实例如 `http://127.0.0.1:8000`） | 节点要连的中枢 |
| LLM Key | 已在中枢「系统设置 → 模型管理」配置并激活 | 任务派发时中枢会把模型+Key 随 payload 下发给节点，节点端**不需要**配 Key |
| 联网 | 可访问外网 | `web_search`（DuckDuckGo）与 `web_fetch` 需要联网 |

安装依赖（工程根目录执行）：

```powershell
cd opc-agent-office
pip install -e .
# 或最小依赖
pip install httpx websockets pydantic-settings psutil structlog python-docx python-pptx scrapling
```

> `scrapling` 基础安装即可满足 Windows 测试；它内置降级逻辑（Scrapling 不可用时
> 搜索走 httpx 兜底），所以即使不装也能跑通主流程。

## 2. 配置 `.env`

在工程根目录创建 `.env`（参考 `.env.example`），本机联调最小配置：

```ini
# 中枢地址（本机 dev 实例）
HUB_API_URL=http://127.0.0.1:8000

# 节点身份（client_id 幂等：同 id 重复注册不会在中枢产生重复节点）
AGENT_CLIENT_ID=office-dev-001
AGENT_NAME=办公节点-Dev
AGENT_TYPE=office

# 注册 Token：首次注册由中枢生成并返回，本机测试随便填即可
AGENT_REGISTRATION_TOKEN=dev-smoke-token

HEARTBEAT_INTERVAL_SECONDS=5

# 任务产物工作目录根（中枢派发带 output_dir 时优先用中枢的）
WORKSPACE_ROOT=D:/bianchen/tilapia-agent/tmp/office-smoke

# 通知 API 端口（ADR-035；与编程节点 18080 错开）
AGENT_API_PORT=18081
```

可选调优项（均有默认值，见 `app/config.py`）：

| 环境变量 | 默认 | 说明 |
| --- | --- | --- |
| `OFFICE_LLM_MAX_ROUNDS` | 30 | 工具循环轮次上限 |
| `OFFICE_LLM_TIMEOUT_SECONDS` | 120 | 单次 LLM 请求超时 |
| `OFFICE_LLM_TOKEN_BUDGET` | 200000 | 单任务累计 token 预算 |
| `OFFICE_WEB_SEARCH_MAX_RESULTS` | 8 | 搜索返回条数上限 |
| `OFFICE_FETCH_TIMEOUT_SECONDS` | 45 | 单页抓取超时 |
| `TASK_NO_PROGRESS_TIMEOUT_SECONDS` | 1800 | 无进度看门狗（ADR-055） |

## 3. 启动节点

```powershell
cd opc-agent-office
python -m app.main
```

启动成功的日志标志（依次出现）：

```text
agent_starting          agent_type=office client_id=office-dev-001
agent_registered        node_id=8966f262-... version=0.1.0      ← 注册成功
websocket_connected     ws_url='ws://.../ws?node_id=...&token=***'
node_status_updated     status=idle                              ← 可接单
```

停止：`Ctrl+C`（Windows 下 SIGTERM 处理自动跳过，SIGINT 优雅退出）。

## 4. 验证注册与心跳

浏览器打开中枢界面「节点管理」，应看到 `办公节点-Dev`（类型 office，状态 idle）；
或调 API：

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/agent-nodes?limit=10" -Method GET
```

## 5. 派发测试任务

创建任务（自然语言由中枢 DeepSeek 规划，办公类描述会判定 `agent_type=office`）：

```powershell
$body = @{ requirement = "请帮我写一份《开源网页抓取工具简介》Word 文档（docx），简要介绍 Scrapling 的定位、核心特性与适用场景" } | ConvertTo-Json
$resp = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/tasks" -Method POST `
  -ContentType "application/json; charset=utf-8" -Body ([System.Text.Encoding]::UTF8.GetBytes($body))
$resp.data | Select-Object id, agent_type, status   # agent_type 应为 office
```

也可以直接在**中枢前端「新建对话」**输入同样需求，效果一致（推荐）。

## 6. 观察执行进度

轮询任务状态（或在前端任务详情看实时进度/当前动作）：

```powershell
$tid = "<上一步返回的任务 id>"
foreach ($i in 1..30) {
  Start-Sleep -Seconds 10
  $t = (Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/tasks/$tid").data
  "[$i] $($t.status) $($t.progress)% $($t.progress_stage) $($t.progress_message)"
  if ($t.status -in @('completed','failed','cancelled')) { break }
}
```

正常执行的 stage 轨迹：`planning（规划大纲）→ searching（web_search/web_fetch）
→ coding（write_docx/write_pptx）→ summarizing → completed`。

节点侧日志会输出每个工具的执行记录（`office_tool_executed`）与最终
`office_task_completed`。

## 7. 验证产物

产物落在工作目录（中枢派发的 `output_dir`，缺省为 `WORKSPACE_ROOT/{task_id前8位}`）：

```powershell
Get-ChildItem D:/bianchen/tilapia-agent/tmp/office-smoke -Recurse -File
```

同时中枢「交付物」聚合页会出现该任务的 docx/pptx/md（节点 `agent.file_tree`
上报 + 中枢增量同步，ADR-059），可直接下载。

## 8. 运行单元测试

```powershell
cd opc-agent-office
python -m pytest tests -q          # 23 项应全过
python -m ruff check app tests
```

测试不联网、不连中枢（LLM/搜索/抓取全部 fake）。

## 9. 常见问题（FAQ）

| 现象 | 原因与处理 |
| --- | --- |
| 任务被判成 `copywriting`/`code` 而非 office | 中枢实例还在跑旧代码——**重启中枢**加载新 `planning.py`（office 判据在提示词里，旧枚举没有 office） |
| 任务 failed：`missing_executor_credentials` | 中枢未配置/未激活 LLM 模型，去「系统设置 → 模型管理」配好 Key |
| 任务 failed：`llm_unavailable` | LLM Key 失效或网络不通，看节点日志中 `LLM 调用失败` 的具体原因 |
| 搜索结果为空 | DuckDuckGo 限流/网络问题；节点自动降级继续执行，summary 会注明「未联网核实」 |
| 抓取失败 | 反爬拦截已自动升级 StealthyFetcher；仍失败则跳过该来源（正常降级，不是故障） |
| PowerShell 中文乱码 | 仅控制台显示问题，`$OutputEncoding = [Text.Encoding]::UTF8` 可缓解；落库/产物内容是正确的 |
| 节点列表里同 id 出现两条 | 不应发生（client_id 幂等）；如出现检查 `.env` 是否被多个进程用不同 id 启动 |

## 10. 远程部署概要（Linux）

机制文档：[ADR-006 节点部署与接入](../../docs/adr/006-agent-node-deployment.md)、
[ADR-022 资源云分发](../../docs/adr/022-agent-resource-cloud-distribution.md)、
[节点开发规范 §2](../../docs/standards/agent-node-standards.md)。office 节点操作步骤：

1. **打包**（开发机）：`cd opc-agent-office && bash scripts/package_office_artifact.sh`
   → 产出 `dist/agent-office-linux-x64-<版本>.tar.gz`；
2. **发布**：中枢「系统设置 → 资源管理」上传该 tar.gz（key `agent-office-linux-x64`）与
   安装脚本 `scripts/install-agent-office-linux.sh`（key `install-script-office`）；
3. **安装**（Linux 节点，root）：从中枢「节点管理」复制 office 类型的安装命令执行——
   脚本自动完成 sha256 校验解压、Python≥3.10 检查、venv 依赖安装、.env 生成、
   systemd 服务注册自启；
4. **验收**：中枢节点列表出现 office 节点且 idle，按 §5 派一个真实任务验证产物回传。

升级 = 重新打包发布 + 节点重跑安装脚本（幂等：sha256 变化才重装，节点身份保留）。

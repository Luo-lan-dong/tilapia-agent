# 端到端开发-编译-更新-测试循环（E2E Dev Loop）操作手册

> 目的：把「自主开发/修复 → 编译 → 更新节点 → 端到端测试」的完整链路固化，
> 新会话按本文即可独立完成全流程，直到任务通过；**只有实在需要用户介入时才提问**。
> 触发规则见 `.trae/rules/e2e-dev-loop.md`。

## 0. 环境拓扑与连通性（2026-10-05 实测）

| 机器 | 地址 | 角色 | 连通性 |
|---|---|---|---|
| Windows 开发机（本机） | 仓库 `d:\bianchen\tilapia-agent` | 改源码；**中枢后端（uvicorn :8000）与前端（vite :5173）都在本机直跑** | — |
| 编译机 `root@192.168.101.101` | 构建目录 `~/tilapia-agent/opc-agent-programming/` | Nuitka 编译节点二进制；**同时承载 DB(:5432)/Redis(:6379)/MinIO(:19000)** | 本机 → 101.101 **免密** |
| 节点 `root@192.168.101.13` | `/opt/opc-agent-code`（systemd 服务 `opc-agent-code`） | code 节点（Nuitka 二进制部署） | 本机 → .13 **免密** |
| 节点 `root@192.168.101.104` | 同上 | code 节点「笔记本」 | 本机 → .104 **无免密**，须经 101.101 跳板（101.101 → .104 免密） |

关键事实：

- 本机 ↔ 节点/DB 之间走 **VPN（10.0.0.x，PPP 拨号）**，单程 RTT ~200ms——hub 单次查库 ~200ms、注册接口 ~2.6s（历史事故见 `docs/issues/2026-10-05-节点离线-注册竞速超时-排查记录.md`）。
- 本机当前**不在** 192.168.101.x 物理网段，所有跨机访问都走 VPN。
- DB / Redis / MinIO 连接串在中枢 `.env.local`（`opc-central-dispatch-hub/.env.local`）。
- 节点上有官方一键更新脚本 **`/root/deploy-opc-agent.sh`**：解压 tarball → 强制覆盖 `bin/`（含 tilacode）→ 清理解压目录 → `systemctl restart` → 校验。用法 `bash deploy-opc-agent.sh [tarball]`，不带参数自动取 `/opt/opc-agent-code/` 下最新的 `opc-agent-code-*-linux-x64.tar.gz`（**动态，无需随版本改**）。
- 构建机有**两个**脚本（均在 `~/tilapia-agent/opc-agent-programming/`）：`scripts/build-code-agent.sh`（Nuitka 构建，版本动态）+ `a.sh`（上传到两台节点，**自动取 artifacts 下最新包**）。`a.sh` 仓库归档副本：`opc-agent-programming/scripts/a.sh`。

## 1. 版本规则

- **中枢后端 / 前端**：在本机直接运行，无需版本化发布；改完热加载/重启本机进程即可。
- **节点二进制（opc-agent-programming/app/）**：必须构建发布。版本号 = 读 `opc-agent-programming/pyproject.toml` 的 `version` 并 **patch +1**（如 0.6.0 → 0.6.1）；发布前先把该值写回 `pyproject.toml` 再上传构建（保证 build-info 一致）。
- 构建默认**不带**浏览器实测能力（不加 `--with-browser`）；需要 M7 前端实测时显式加。

## 2. 流程总览（循环直到通过）

```
① 本机开发（中枢/前端/节点源码 + 单测）
② 本机自测（ruff + pytest / tsc + vitest；中枢后端可直接起服 curl API）
③ 构建节点二进制（仅当改了 opc-agent-programming/app/）
④ 上传 tarball（构建机 `a.sh`）→ 节点更新（`deploy-opc-agent.sh`）
⑤ 端到端验证（心跳/日志/派发真实任务/会话折叠块/@节点）
⑥ 不通过 → 回 ①（同一会话内循环，直到成功或必须用户决策）
```

## 3. 各阶段命令

### 3.1 开发后本机自测

```powershell
# 中枢后端
cd opc-central-dispatch-hub ; python -m ruff check app tests ; python -m pytest tests/services tests/api -q
# 前端
cd opc-web-console ; npx tsc -b ; npx vitest run <相关用例>
# 节点
cd opc-agent-programming ; python -m ruff check app tests ; python -m pytest tests -q
```

中枢后端改完直接在本机起服调 API 验证（先查端口占用，见 §3.5）：

```powershell
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
curl http://localhost:8000/api/v1/health/    # 200 即就绪（注意尾斜杠，无斜杠是 404）
curl http://localhost:8000/openapi.json      # 新端点应出现
```

### 3.2 构建节点二进制

```powershell
# ① 版本号自动 patch+1（读 pyproject.toml → 写回 → 打印新版本号，避免手改漏改/重复）
$v = (Select-String -Path opc-agent-programming\pyproject.toml -Pattern '^version = "(.+)"').Matches[0].Groups[1].Value
$p = $v.Split('.') ; $nv = "$($p[0]).$($p[1]).$([int]$p[2] + 1)"
(Get-Content opc-agent-programming\pyproject.toml -Raw) -replace "version = `"$v`"", "version = `"$nv`"" |
  Set-Content opc-agent-programming\pyproject.toml -NoNewline -Encoding utf8
"新版本号: $nv"
# ② 上传源码（app 整目录 + 构建脚本 + pyproject）
scp -r app root@192.168.101.101:~/tilapia-agent/opc-agent-programming/
scp scripts/build-code-agent.sh pyproject.toml root@192.168.101.101:~/tilapia-agent/opc-agent-programming/
# ③ 后台构建（脚本内置 tilacode 产物预检门禁 C2；日志落 /tmp/build.log）
ssh root@192.168.101.101 "cd ~/tilapia-agent/opc-agent-programming && nohup bash scripts/build-code-agent.sh $nv > /tmp/build.log 2>&1 & echo BUILD_STARTED"
# ④ 轮询（约 3~5 分钟；产物 20MB 左右）
ssh root@192.168.101.101 "tail -4 /tmp/build.log; ls -la ~/tilapia-agent/opc-agent-programming/artifacts/opc-agent-code-$nv-linux-x64.tar.gz"
```

### 3.3 分发与节点更新（构建机两个脚本：build → a.sh → 节点 deploy）

```powershell
# ① 上传（构建机 a.sh：自动取 artifacts 下最新包 → 推送到两台节点）
ssh root@192.168.101.101 "cd ~/tilapia-agent/opc-agent-programming && bash a.sh"
# ② 节点更新（.13 本机直连）
ssh root@192.168.101.13 "systemctl stop opc-agent-code && bash /root/deploy-opc-agent.sh"
# ③ 节点更新（.104 经 101.101 跳板，长命令建议先 scp 脚本文件过去执行，避免多层引号转义）
ssh root@192.168.101.101 "ssh root@192.168.101.104 'systemctl stop opc-agent-code && bash /root/deploy-opc-agent.sh'"
```

> **必须先 `systemctl stop` 再跑 deploy 脚本**：运行中覆盖 onefile 二进制会报
> `cp: 无法创建普通文件"bin/opc-agent": 文本文件忙`，导致替换静默失败、重启的还是旧版。
>
> **必须整体覆盖 `bin/`（含 `tilacode`），不要只手换 `opc-agent`**（2026-10-05 实测教训）：
> 手工 `cp 新包/bin/opc-agent bin/opc-agent` 会漏掉 `tilacode` → 节点留着旧 tilacode →
> 模型目录（catalog）装配失败 → 任务 4 秒内 `failed`（progress 0、无轨迹），
> 日志报 `tilacode_preflight_catalog_empty`。**用官方 `/root/deploy-opc-agent.sh`**（内部
> `cp -rf EXTRACT_DIR/bin/. BIN_DIR/`，两个二进制一起换）。升级后自检：
> `ls -la /opt/opc-agent-code/bin/` 的 `opc-agent` 与 `tilacode` 大小应与解压目录内一致。
>
> **构建机脚本职责**（两个，均在 `~/tilapia-agent/opc-agent-programming/`）：
> - `scripts/build-code-agent.sh`：Nuitka 构建，产出 `artifacts/opc-agent-code-<版本>-linux-x64.tar.gz`（版本动态）。
> - `a.sh`：上传到两台节点。**已修为自动取 `artifacts/` 下最新包**（原版把 `0.5.0` 写死 → 构建新版本后仍传旧包）；
>   仓库归档副本 `opc-agent-programming/scripts/a.sh`，构建机改动后请保持两者一致：
>   `scp opc-agent-programming/scripts/a.sh root@192.168.101.101:~/tilapia-agent/opc-agent-programming/a.sh`。
> - 节点 `/root/deploy-opc-agent.sh`：解压 tarball → 覆盖 `bin/` → 清理 → 重启（**动态**按最新包与派生目录名，无需随版本改）。

### 3.4 更新后验证（三查 + 端到端）

```powershell
# 查 1：节点日志两连击（注册 201 + WS connected）
ssh root@192.168.101.13 "journalctl -u opc-agent-code --since '-1 min' --no-pager | grep -E 'agent_registered|websocket_connected|websocket_session_failed'"
# 查 2：DB 心跳刷新（连接串见中枢 .env.local；用临时 python + asyncpg 脚本）
#   select name, status, last_heartbeat_at from agent_nodes;
# 查 3：中枢侧连接日志（本机 hub 进程输出）
```

端到端：前端 `http://localhost:5173/tasks/new` 派发真实任务 → 会话折叠块应出现节点的思考/正文/工具行（ADR-063 M2）→ 需要「和节点说话」用输入区 `@节点`（执行中→steer；空闲→node-chat 开新一轮）。

### 3.5 中枢/前端本机服务的启停与端口检查（每次开发前先查）

```powershell
netstat -ano | findstr ":8000 :5173 :18080"   # 中枢 / 前端 / 节点 API
# 中枢重启：杀旧 PID 后用【文件重定向】启动（不要用管道 Tee，会话断开即断日志）：
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 1> ..\tmp\hub_stdout.log 2> ..\tmp\hub_stderr.log
```

## 4. 踩坑清单（实录，按遇到顺序）

| 坑 | 现象 | 处置 |
|---|---|---|
| 运行中覆盖二进制 | `cp: Text file busy`，重启后仍是旧版 | 先 `systemctl stop` 再替换/deploy |
| 节点日志 `error=` 空 | `websocket_session_failed error=` 无从定位 | 多为 `TimeoutError`；对比「hub 查库端点耗时 vs 节点超时配置」（0.6.0 起日志已带异常类型名） |
| hub 日志停更 | 进程活着但 stdout 无输出 | 用管道 Tee 启动的 hub 在会话/睡眠后管道断——改用文件重定向重启 |
| PowerShell 发 JSON | 嵌套引号转义坏 → 假 422 | 用 `python + httpx` 发请求 |
| .104 直连 | `Permission denied (publickey)` | 经 101.101 跳板（scp 上去再远程执行；长命令走脚本文件） |
| 构建机残留旧产物 | 版本号没变导致误用旧 tarball | 版本号必须 patch+1，构建后核对文件时间戳 |
| 上传脚本写死版本 | `a.sh` 把 `0.5.0` 写死 → 构建新版本后仍传旧包（"一直更新老的"），节点装的永远不是新版 | 已修为 `ls -1t artifacts/opc-agent-code-*.tar.gz \| head -1` 自动取最新；改 a.sh 后同步仓库归档副本 `opc-agent-programming/scripts/a.sh` |
| 脚本 CRLF 行尾 | Windows 编辑后 scp 上 Linux 报 `\r` 相关错误 | 上传后 `sed -i 's/\r$//' <脚本>` 规范化 |
| 升级漏换 tilacode | 只 `cp opc-agent`、没换 `tilacode` → 模型目录（catalog）装配失败，任务 4 秒内 `failed`（progress 0、轨迹 0 行），节点日志 `tilacode_preflight_catalog_empty` | 用官方 `deploy-opc-agent.sh` 整体覆盖 `bin/`；升级后 `ls -la bin/` 核对 `opc-agent` 与 `tilacode` 大小与解压目录一致 |
| 多层 ssh 引号 | 嵌套 `ssh a "ssh b \"...\""` 被本地 shell 拆坏（`&&`/`|` 被本机解析） | 长/带管道的远程命令写成本地脚本 → scp → 远程 `bash <脚本>`（跳板节点亦同） |
| **PowerShell 写 pyproject 加 BOM** | `Set-Content -Encoding utf8`（PS 5.1）写入 UTF-8 BOM → Linux 构建脚本 tomllib 解析 pyproject **第 1 行第 1 列报 Invalid statement**，构建秒挂 | 用 `[System.IO.File]::WriteAllText($p, $c, (New-Object System.Text.UTF8Encoding($false)))` 无 BOM 写回（2026-10-05 0.6.1 构建实测）；顺带注意 `-replace` 改版本号可能静默不匹配（CRLF 等），改后必须 `Select-String '^version'` 复核 |
| **节点注册 version 与 pyproject 脱节** | 注册日志/agent_nodes 显示 `version=0.4.2`，与构建版本 0.6.x 对不上 | `hub_client.py:108 _AGENT_VERSION` 是硬编码常量（历史遗留，未随 pyproject 联动）——排查功能与版本的关系时**勿信该字段**，以构建 tarball 版本与 bin 时间戳为准；根治方案（读 pyproject 或构建注入）待后续登记 |
| **续聊回复落库有时序窗口** | @节点回复在「轮次结束判定」（step.ended 静默 + session/active 轮询，约 20~40s）之后才落库，轨迹 text 行先于落库出现 | 自动化验证：看到轨迹 text 行后**等 ≥30s 再查** `task_building_sessions.messages`；探针骨架见 `tmp/e2e_probe_064.py`（chat 模式） |
| **PowerShell `.Replace()` 改 pyproject 版本静默失效** | `$c.Replace('version = "0.6.3"', ...)` 写回后 `Select-String '^version'` 复核仍是旧值（2026-10-06 连续两次 0.6.4/0.6.5 均未生效，pyproject 甚至被 stash 还原成 0.6.0，而构建参数版号照常出包——build-info 与包名脱节） | 改后**必须** `Select-String '^version'` 复核；不可靠时改用 python `pathlib` 读改写（UTF-8 无 BOM），节点仓库已留 `opc-agent-programming/bump_version.py`（自动读当前版本 patch+1） |
| **探针裸查 jsonb 列的假阳性** | asyncpg 对 `jsonb` 列返回 **str** 而非 list——`len(row["todos"] or [])` 对空数组返回 2（字符串 `"[]"` 的长度），误判「有 2 项待办」（2026-10-06 E2E 探针 B4 实际踩中） | 判空前先 `json.loads()` 或比对原始值；验证类断言一律基于 API 响应而非裸 DB 读取 |
| **同一测试库并发跑 pytest 互踩** | 两个 pytest 进程同时跑 DB 测试（autouse 清表 fixture 互相删对方的行）→ 对方进程建的 task 行被本进程 delete 时触发 `ForeignKeyViolationError on dispatch_decisions` 等随机 IntegrityError 假失败（2026-10-08 ADR-068 回归踩中；失败与代码无关） | 中枢 DB 测试**串行执行**：等上一个 pytest 进程结束再起下一个；后台跑测试时不要重复启动相同命令，先 `TaskStop`/杀进程再重跑 |

## 5. 极速参考（改了节点源码时，按序照抄）

```powershell
# A. 本机自测（节点侧）
cd d:\bianchen\tilapia-agent\opc-agent-programming ; python -m ruff check app tests ; python -m pytest tests -q

# B. 版本号 patch+1（读 pyproject.toml 写回并打印，记下 $V）
$v = (Select-String -Path opc-agent-programming\pyproject.toml -Pattern '^version = "(.+)"').Matches[0].Groups[1].Value
$p = $v.Split('.') ; $V = "$($p[0]).$($p[1]).$([int]$p[2] + 1)"
(Get-Content opc-agent-programming\pyproject.toml -Raw) -replace "version = `"$v`"", "version = `"$V`"" | Set-Content opc-agent-programming\pyproject.toml -NoNewline -Encoding utf8
"新版本号: $V"

# C. 上传源码 + 构建（3~5 分钟，边等边轮询）
cd d:\bianchen\tilapia-agent\opc-agent-programming
scp -r app root@192.168.101.101:~/tilapia-agent/opc-agent-programming/
scp scripts/build-code-agent.sh pyproject.toml root@192.168.101.101:~/tilapia-agent/opc-agent-programming/
ssh root@192.168.101.101 "cd ~/tilapia-agent/opc-agent-programming && nohup bash scripts/build-code-agent.sh $V > /tmp/build.log 2>&1 & echo STARTED"
ssh root@192.168.101.101 "tail -4 /tmp/build.log; ls -la artifacts/opc-agent-code-$V-linux-x64.tar.gz"

# D. 上传到两台节点（构建机 a.sh 自动取最新包；.104 由 a.sh 内 scp 直达）
ssh root@192.168.101.101 "cd ~/tilapia-agent/opc-agent-programming && bash a.sh"

# E. 更新（先 stop，再跑节点官方一键脚本；.13 直连，.104 经跳板）
ssh root@192.168.101.13 "systemctl stop opc-agent-code && bash /root/deploy-opc-agent.sh"
ssh root@192.168.101.101 "ssh root@192.168.101.104 'systemctl stop opc-agent-code && bash /root/deploy-opc-agent.sh'"

# F. 验证（节点日志两连击；再回前端派发真实任务）
ssh root@192.168.101.13 "journalctl -u opc-agent-code --since '-1 min' --no-pager | grep -E 'agent_registered|websocket_connected|websocket_session_failed'"
```

> 只改了中枢后端 / 前端时，跳过 B~E，直接本机自测 + 起服（§3.1、§3.5）。

## 6. 端到端测试与排查（API 驱动，可全自动）

前端所有操作最终都调中枢 API，因此**整条链路无需点 UI 即可全自动跑通**：
建任务 → 派发 → 轮询状态 → 读节点轨迹 → 断言 → 失败则按 §6.3 定位。
把 §6.2 的片段串起来即是自动化脚本骨架（需要时写到 `tmp/` 下临时执行）。

### 6.1 观测入口（三处，覆盖全链路）

| 入口 | 看什么 | 参考 |
|---|---|---|
| 中枢 REST | 任务状态/进度/轨迹、节点在线 | `GET /api/v1/tasks/{id}`、`/tasks/{id}/agent-trace`、`/agent-nodes` |
| 中枢 DB | 全链路事件与任务字段 | `tool_call_logs`（派发/发言/动作/用量/验收）、`tasks`（status/progress/verify_report…）、`agent_nodes`、`task_agent_trace` |
| 节点日志 | 执行实况 | `journalctl -u opc-agent-code -n 100 --no-pager` |

> **健康检查尾斜杠**：`/api/v1/health/` 是 200，`/api/v1/health`（无尾斜杠）是 404（见 §3.1）。
> DB 连接串见 `opc-central-dispatch-hub/.env.local`；SQL 句式可照抄 [adr061-acceptance-runbook.md](./adr061-acceptance-runbook.md) §0.2。

### 6.2 一条完整闭环（示例场景：改了节点源码，验证功能）

关键接口（前缀 `http://localhost:8000/api/v1`）：

```
GET  /agent-nodes                         # 列节点：status / last_heartbeat_at / id
POST /tasks                               # 建任务 {requirement, specification?, execution_mode?, ...}
POST /tasks/{id}/dispatch                 # 派发 {target:"code", node_id:"<节点ID>", mode:"build"}
GET  /tasks/{id}                          # 任务详情：status/progress/progress_stage/progress_message/todos/...
GET  /tasks/{id}/agent-trace?after_seq=0  # 节点会话轨迹行（reasoning/text/tool/status）
GET  /tasks/{id}/insight                  # 用量/时间线/告警
```

Python 骨架（**用 httpx 发，避免 PowerShell 转义坏 JSON**；`tmp/e2e_probe.py` 形式临时执行即可）：

```python
# -*- coding: utf-8 -*-
"""端到端探针：建任务 → 定向派发 → 轮询 → 读轨迹。用法：python tmp/e2e_probe.py <节点ID>"""
import sys, time, httpx

HUB = "http://localhost:8000/api/v1"
node_id = sys.argv[1]

with httpx.Client(timeout=30) as c:
    # ① 前置：中枢健康 + 目标节点在线
    assert c.get(f"{HUB}/health/").status_code == 200
    node = next(n for n in c.get(f"{HUB}/agent-nodes").json()["data"]["items"] if n["id"] == node_id)
    print("节点:", node["name"], node["status"], node["last_heartbeat_at"])

    # ② 建任务（带 specification 直落库，不走规划 Agent；需求尽量确定性、可观察）
    task = c.post(f"{HUB}/tasks", json={
        "requirement": "在当前工作目录创建 hello.txt，内容为 hello",
        "specification": {"spec": "# 端到端探针\n仅验证链路连通", "tasks": "", "checklist": ""},
        "execution_mode": "build",
    }).json()["data"]
    tid = task["id"]
    print("任务:", tid)

    # ③ 定向派发到目标节点
    d = c.post(f"{HUB}/tasks/{tid}/dispatch", json={"target": "code", "node_id": node_id, "mode": "build"}).json()["data"]
    print("派发:", d)   # 若返回 queued_on_node/hold_reason 说明节点未达准入水位（见 §6.3-②）

    # ④ 轮询至终态
    for _ in range(120):            # 最长约 10 分钟
        t = c.get(f"{HUB}/tasks/{tid}").json()["data"]
        print(f"[{t['status']:>9}] {t['progress']:>3}% {t.get('progress_stage') or ''} {t.get('progress_message') or ''}")
        if t["status"] in ("completed", "failed", "verify_blocked"):
            break
        time.sleep(5)

    # ⑤ 读节点会话轨迹（ADR-063）
    rows = c.get(f"{HUB}/tasks/{tid}/agent-trace", params={"after_seq": 0, "limit": 1000}).json()["data"]["rows"]
    kinds = {}
    for r in rows:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    print("轨迹行统计:", kinds)
    print("终态:", t["status"], "| verify_blocked_at:", t.get("verify_blocked_at"))
```

**通过标准**：节点 `status` 变 `busy`→回 `idle`；任务到 `completed`；`agent-trace` 出现 `status`+`tool`（≥0.6.0 二进制还会有 `reasoning`/`text`）。
更严格可再拉产物校验：`GET /tasks/{id}/artifacts`。

> **验收判据坑**：示例 `specification.checklist=""` 没有任何可执行判据，任务会按 ADR-061 设计正常停在
> `verify_blocked`（「无法验证不得呈现为正常完成」）。要跑到 `completed`，二选一：
> ① spec 里写清验收判据（`checklist` / `verify` 字段）；② 用决策端点收口：
> `POST /tasks/{id}/verify-decision  {"decision":"accept"}`（接受现状→completed）或 `{"decision":"retry"}`（继续修）。

**2026-10-05 实测样本**（探针跑通、含轨迹）：
```
[前置] 节点=笔记本 status=idle
[派发] status=assigned node_id=50127fe1-...
  [2]  running  22% running_command 正在执行 pwd && ls -la
  [7]  running  52% running_command 正在执行 cat hello.txt
  [11] verify_blocked 64% 验收未通过，等待你的决定：未找到任何可执行的验收判据（无法验证）
[轨迹] 共 23 行，统计={'status':1, 'reasoning':5, 'tool':16, 'text':1}    ← ADR-063 轨迹采集生效
→ POST /verify-decision {"decision":"accept"} → status=completed 100%
```

### 6.3 执行中出问题——按症状定位（决策树）

| 症状 | 先看 | 命令/接口 | 常见根因 |
|---|---|---|---|
| ① 节点 `offline` | 节点重连日志 | `ssh <节点> "journalctl -u opc-agent-code --since '-5 min' --no-pager \| grep -E 'register\|websocket'"` | 注册/握手失败（超时、token、网络）——参见 [节点离线排查记录](../issues/2026-10-05-节点离线-注册竞速超时-排查记录.md) |
| ② 派发后一直 `pending` | 派发响应 + 任务字段 | `dispatch` 返回 `queued_on_node`/`hold_reason`；`GET /tasks/{id}` 看 `dispatch_hold_reason`/`last_reject_reason` | 节点未达准入水位（内存/负载双门槛）→ 排队；或调度器未接管（需 `dispatch_type` 有窗口） |
| ③ `assigned/running` 但进度不动 | 节点执行日志 | `ssh <节点> "journalctl -u opc-agent-code --since '-5 min' --no-pager \| grep -E 'task_execution_started\|sse_drain_finished\|ModelUnavailable\|stalled\|preflight'"` | 模型目录/凭据预检失败、`task_startup_stalled`、SSE 未 admitted |
| ③′ 任务**秒失败**（4s 内 `failed`，progress 0，`agent-trace` 0 行） | 节点日志 + 二进制版本 | `ssh <节点> "journalctl -u opc-agent-code -n 120 --no-pager"` 找 `task_bridge_error`；`ls -la /opt/opc-agent-code/bin/` 对比 tilacode 大小 | 多见 `tilacode_preflight_catalog_empty`＝**tilacode 未随升级更新**（只换了 opc-agent）→ 用官方 `deploy-opc-agent.sh` 重装（见 §3.3） |
| ④ 任务 `failed` | 任务详情 + 事件表 | `GET /tasks/{id}`；DB `select kind,name,result_summary from tool_call_logs where task_id='<id>' order by created_at` | 节点上报的 reason（看节点日志 `task_bridge_failed` / `task_timeout`） |
| ⑤ 折叠块无内容 / `agent-trace` 为空 | 二进制版本 + 端点 | `ssh <节点> "grep -ac agent_trace /opt/opc-agent-code/bin/opc-agent"`；`GET /tasks/{id}/agent-trace` | 节点二进制 <0.6.0（无轨迹采集）→ 按 §3 重新构建发布 |
| ⑥ 停在 `verify_blocked` | 验收报告 | `GET /tasks/{id}` 看 `verify_report`；`GET /tasks/{id}/insight` | 必过层验收未过/无法验证 → 用 `POST /tasks/{id}/verify-decision` 决定「接受/重试」 |

**分层定位原则**（从外到内，逐层缩小）：
```
中枢（REST 状态 + DB tool_call_logs） → 节点（journalctl -u opc-agent-code） → 节点内 tilacode（进程/端口存活；其 HTTP API 需 Basic Auth，见 §7.2）
```
先确认「任务在哪一层卡住」，再只查那一层的日志，避免全量翻日志。

## 7. API 端点发现（按需查，不背固定清单）

**原则**：不要硬编码端点表。按当前任务需要，用「机器可读描述 + 源码权威」去发现要用的端点；
本手册 §6.2 的端点只是**常用示例**，不是白名单。

### 7.1 中枢（自助发现，首选）

- `GET /openapi.json`：机器可读的**全量**路径/方法/参数/响应 schema（FastAPI 自动生成）；
- `GET /docs`：Swagger UI，可直接在浏览器里试调；
- **按关键词找端点**（示例：找与「轨迹」相关的端点）：

```powershell
python -c "import httpx; d=httpx.get('http://localhost:8000/openapi.json').json(); [print(m.upper(), p, '-', (i.get('summary') or '')) for p, ms in d['paths'].items() for m, i in ms.items() if 'trace' in p or 'trace' in (i.get('summary') or '')]"
```

- 换关键词即可（`task`/`node`/`artifact`/`steer`/`settings`/`experience`…），也能按 tag 过滤：
  `d['paths']` 里的 `i['tags']` 即分组名（与 `app/api/v1/api.py` 的 include_router 前缀对应）。
- **权威源**（OpenAPI 即由此生成）：`opc-central-dispatch-hub/app/api/v1/endpoints/*.py`；
  路由前缀与分组见 `app/api/v1/api.py`。schema 定义在 `app/schemas/*.py`。

### 7.2 节点侧（无统一 OpenAPI，靠源码发现）

节点侧有**两个层次**的 API，都没有 Swagger/OpenAPI，按下面的源码位置发现：

| 层次 | 说明 | 发现位置 |
|---|---|---|
| 节点自身 API | 仅 2 个端点：`GET /internal/health`、`POST /internal/hub-notify`；**需配 `API_TOKEN` 才启动**（默认未配→不监听） | `opc-agent-programming/app/client/notify_server.py` |
| **tilacode API**（真正的能力，端口 4096，仅绑 127.0.0.1） | 会话/模型/权限/事件等全量能力 | ① **`opc-agent-programming/app/executor/tilacode_client.py`**——agent 实际调用的**全部路径 + 参数 + Basic Auth 实现**，最实用；② 更深：`tilacode/packages/server/src/handlers/*.ts`（端点标识如 `session.prompt`）、`tilacode/packages/protocol/src` |

- **鉴权**：tilacode 全部端点走 **Basic Auth**（用户名固定 `tilacode`，密码为启动时随机生成、仅注入子进程环境变量 `TILACODE_SERVER_PASSWORD`，不落盘、重启即换）。
- 🔒 **已定策略（2026-10-05，用户裁决）：不直调节点 tilacode**。节点侧验证**一律走「经中枢派发任务 + §6.1 三处观测」**；
  需要底层契约/端点信息时，**读源码发现**（`tilacode_client.py` / `tilacode/packages/server/src/handlers/*.ts`），
  不读取/使用进程内凭据、不直连 4096。若将来确需直调，须先改造为**可持久的凭据文件**（或给节点 API 加自描述）并另立决策。

> 需要某个「中枢没有暴露、只有节点内部才有」的能力时：先确认它是否在 `tilacode_client.py` 里已有封装
> （有→复用其 HTTP 契约理解语义）；没有→读 tilacode 源码确认端点。**能通过中枢任务链路间接验证的，就走中枢**（已定策略）；

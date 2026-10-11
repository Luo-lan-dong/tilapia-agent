# 端到端开发-编译-更新-测试循环规则

## 核心原则

本项目的「中枢后端 + 前端」在本机直接运行，「节点二进制」通过编译机构建后发布到节点。
涉及任一端的**新功能开发或 Bug 修复**，都必须走完整的端到端循环并在同一会话内验证通过；
只有实在需要用户决策/介入时才向用户提问。

## 详细操作手册

所有命令、拓扑、免密关系、版本规则与踩坑清单统一见：
[`docs/ops/e2e-dev-loop-runbook.md`](../../docs/ops/e2e-dev-loop-runbook.md)（唯一权威源，本文件只留触发条件与流程骨架）。

## 必须走端到端循环的场景

| 改动位置 | 是否需要构建发布节点二进制 | 验证方式 |
|---|---|---|
| `opc-central-dispatch-hub/`（中枢后端） | 否（本机直跑） | ruff + pytest + 本机起服 curl API |
| `opc-web-console/`（前端） | 否（vite 热更新） | tsc + vitest + 浏览器实测 |
| `opc-agent-programming/app/`（节点） | **是**（构建版本号 patch+1） | 节点自测 → 构建 → deploy → 心跳/日志/派发真实任务 |

## 流程骨架（详见 Runbook）

1. **开发**：改源码 + 补单测（改前先读决策文档与规范）。
2. **自测**：ruff + pytest / tsc + vitest 全绿。
3. **构建**（节点侧改动时）：源码 scp 到编译机 192.168.101.101 → `bash scripts/build-code-agent.sh <patch+1 版本>`（默认不带 `--with-browser`）。
4. **更新**：构建机 `bash a.sh` 上传 tarball 到节点 192.168.101.13 / .104 → **先 `systemctl stop`** → 节点 `bash /root/deploy-opc-agent.sh`（两脚本均动态取最新包，版本自增不受影响）。
5. **验证**：节点日志（register 201 + websocket_connected）→ DB 心跳刷新 → 派发真实任务走会话折叠块 / `@节点`。
6. **循环**：不通过回第 1 步，直到任务成功；需要用户决策时用提问工具。

## 端点发现（按需查，不要背固定清单）

测试/排查要用什么接口，按需求去「发现」，而不是只认手册里列的几个：

- **中枢**：`GET /openapi.json`（机器可读全量路径/schema）+ `GET /docs`（Swagger UI）；
  按关键词或 tag 过滤即可找到端点。权威源：`opc-central-dispatch-hub/app/api/v1/endpoints/*.py`（前缀见 `api/v1/api.py`）。
- **节点自身 API**：读 `opc-agent-programming/app/client/notify_server.py`（仅 `/internal/health`、`/internal/hub-notify`，需 API_TOKEN）。
- **tilacode API**（节点侧真正的能力，127.0.0.1:4096）：读 `opc-agent-programming/app/executor/tilacode_client.py`
  （agent 实际调用的全部路径+参数+Basic Auth 实现）→ 更深读 `tilacode/packages/server/src/handlers/*.ts`。
- 🔒 **已定策略：不直调节点 tilacode**（其 Basic Auth 密码仅存子进程环境）。节点侧验证一律走「经中枢派发任务 + 观测日志/轨迹」；
  需要端点信息时读源码发现（`tilacode_client.py` / `tilacode/packages/server/src/handlers/*.ts`），不读取进程内凭据、不直连 4096。
- 完整方法见 Runbook §6（端到端测试与排查）与 §7（API 端点发现）。

## 红线

- 每次动手前先确认服务在跑与端口占用（`:8000` 中枢 / `:5173` 前端 / `:18080` 节点 API），防止端口冲突导致误判。
- 节点更新**必须先 stop 服务再替换二进制**（运行中覆盖会"文本文件忙"静默失败）。
- 中枢本机进程用**文件重定向**启动，不用管道 Tee（会话断开即断日志）。
- 版本号必须 patch+1 并写回 `pyproject.toml`，避免构建/误用旧产物。

## 变更记录

| 日期 | 变更内容 | 变更人 |
|------|---------|--------|
| 2026-10-05 | 初始创建：固化「开发→编译→更新→测试」端到端循环（源起 2026-10-05 节点离线事故的全链路修复演练） | AI助手 |

# 节点侧工具集（ADR-061 M7）

## 前端探针 `frontend_probe.mjs`

给子 Agent 一份**文本优先**的浏览器反馈（纯文本断言之外还能看到「页面到底长什么样、报什么错」）：

```bash
# 文本反馈（默认）：结构化 JSON —— 标题/结构快照/可见文本摘要/控制台错误/失败请求与状态码
node .tilacode/tools/frontend_probe.mjs --url http://localhost:5173

# 需要视觉记录时再加截图（存到指定目录，可作为任务产物）
node .tilacode/tools/frontend_probe.mjs --url http://localhost:5173 --shots out/shots --full-page
```

**退出码即判据**（可直接写进 `verify.json` 的 L2/L3 检查）：

| 退出码 | 含义 |
|--------|------|
| 0 | 无控制台错误、无页面异常、无失败请求 |
| 1 | 有发现（控制台错误 / 失败请求）——**按未通过计** |
| 2 | 导航失败（服务没起来 / 地址不对） |
| 3 | 依赖缺失（playwright 或 chromium 未安装） |

## 依赖固化（Q16 已定：预装）

**在 Linux 构建服务器上执行一次**（推荐，浏览器随整包分发、离线可用）：

```bash
cd ~/tilapia-agent/opc-agent-programming
bash scripts/prepare-browser-tools.sh     # 下载便携 node + playwright + chromium（PLAYWRIGHT_BROWSERS_PATH=0）
bash scripts/build-code-agent.sh <版本>    # 默认要求浏览器就绪；不需要时加 WITHOUT_BROWSER=1
```

脚本产出三样都放在本目录下，可随整包搬迁：
- `node/`：便携 node 运行时（目标节点默认没有 node；systemd 把它前置进 PATH）
- `node_modules/playwright`：驱动库
- `node_modules/playwright-core/.local-browsers/`：chromium 二进制

节点侧 systemd 注入（已写在 `systemd/opc-agent-code.service`）：
`TILAPIA_TOOLS_DIR=/opt/opc-agent-code/tools`、`PLAYWRIGHT_BROWSERS_PATH=0`、`PATH` 前置 `tools/node/bin`。
chromium 的系统 .so 由安装脚本 `install_browser_deps()` 尝试 apt 安装（失败只告警）。

`--shots` 只在显式指定时产生体积；默认不截图（Q17：文本优先）。

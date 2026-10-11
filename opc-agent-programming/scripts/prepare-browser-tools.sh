#!/usr/bin/env bash
# 构建机一键准备「前端浏览器能力」（ADR-061 M7）
#
# 在 **Linux 构建服务器** 上执行一次（`~/tilapia-agent/opc-agent-programming/`）：
#   bash scripts/prepare-browser-tools.sh
#
# 做两件事（都在 tools/ 目录内，可随整包搬迁）：
#   ① npm install playwright         → tools/node_modules/
#   ② npx playwright install chromium → tools/node_modules/playwright-core/.local-browsers/
#      （PLAYWRIGHT_BROWSERS_PATH=0 让浏览器装在包内，而不是用户目录）
#
# node 运行时的两种模式（默认自动判断）：
#   - 构建机已有 node/npm（最常见）→ **不**打包 node，直接用系统的装依赖；
#     此时 **目标节点必须自带 node**，否则节点上的探针会报「退出码 3 依赖缺失」。
#   - BUNDLE_NODE=1（构建机没有 node，或**目标节点没有 node** 希望离线自包含）
#     → 额外下载便携 node 到 tools/node/，随包分发（systemd 会把它的 bin 前置进 PATH）。
#
# 镜像加速（可选）：
#   NODE_MIRROR=https://npmmirror.com/mirrors/node BUNDLE_NODE=1 bash scripts/prepare-browser-tools.sh
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${PROJECT_ROOT}"

NODE_VERSION="${NODE_VERSION:-v20.18.1}"
NODE_MIRROR="${NODE_MIRROR:-https://nodejs.org/dist}"
TOOLS_DIR="tools"
#: 默认**把 node 一起打进包**（节点零依赖）；用 --no-node 改成用构建机的 node（包更小）
BUNDLE_NODE="1"
#: 仅安装 headless shell（体积小很多；探针默认就是 headless）
SLIM="0"
for arg in "$@"; do
    case "${arg}" in
        --with-node) BUNDLE_NODE="1" ;;
        --no-node)   BUNDLE_NODE="0" ;;
        --slim)      SLIM="1" ;;
        -h|--help)
            cat <<'USAGE'
用法：bash scripts/prepare-browser-tools.sh [选项]

（不带任何选项就能用：会把 node + playwright + chromium 全部准备好并打进包）

  --with-node   把便携 node 一起打进包（默认）：目标节点**不需要**装 node
  --no-node     不打包 node，用构建机已装的 node：包更小，但目标节点需自带 node
  --slim        只装 headless 浏览器（体积小很多）：探针默认就是 headless，够用
  -h, --help    显示本帮助
USAGE
            exit 0
            ;;
        *) echo "[ERROR] 未知参数：${arg}（bash scripts/prepare-browser-tools.sh --help）" >&2; exit 1 ;;
    esac
done
#: 浏览器装进包内（与 systemd 的 PLAYWRIGHT_BROWSERS_PATH=0 一致）
export PLAYWRIGHT_BROWSERS_PATH=0

log() { echo "[browser-tools] $*"; }
log_warn() { echo "[browser-tools][WARN] $*" >&2; }

command -v curl >/dev/null 2>&1 || { echo "[ERROR] 缺少 curl" >&2; exit 1; }
command -v tar  >/dev/null 2>&1 || { echo "[ERROR] 缺少 tar" >&2; exit 1; }

mkdir -p "${TOOLS_DIR}"
log "本次模式：$([ "${BUNDLE_NODE}" = "1" ] && echo "把 node 一起打进包（目标节点零依赖）" || echo "不打包 node（目标节点需自带 node）")$([ "${SLIM}" = "1" ] && echo " · 仅 headless 浏览器")"

# 记录构建机发行版与 glibc：Playwright 官方只支持部分发行版，不支持时会回退到
# ubuntu24.04 构建，**目标节点 glibc 过旧就起不来**（与你们文档 §9 的 Nuitka glibc 坑同类）
OS_NAME="$(grep -m1 '^PRETTY_NAME=' /etc/os-release 2>/dev/null | cut -d'"' -f2 || true)"
GLIBC_VER="$(ldd --version 2>/dev/null | head -n1 || true)"
log "构建机系统：${OS_NAME:-未知} · ${GLIBC_VER:-未知}"

# ---------- ① node 运行时（系统 / 便携） ----------
if [ "${BUNDLE_NODE}" != "1" ] && command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
    NODE_BIN="$(command -v node)"
    NPM_BIN="$(command -v npm)"
    log "使用系统 node $("${NODE_BIN}" -v)：跳过便携 node 下载（不随包分发 node）"
    log_warn "目标节点必须自带 node，否则探针会报依赖缺失；目标节点没有 node 时请用 BUNDLE_NODE=1 重跑"
else
    if [ -x "${TOOLS_DIR}/node/bin/node" ]; then
        log "便携 node 已存在：$("${TOOLS_DIR}/node/bin/node" -v)"
    else
        ARCH="$(uname -m)"
        case "${ARCH}" in
            x86_64|amd64) NODE_ARCH="x64" ;;
            aarch64|arm64) NODE_ARCH="arm64" ;;
            *) echo "[ERROR] 不支持的架构：${ARCH}" >&2; exit 1 ;;
        esac
        PKG="node-${NODE_VERSION}-linux-${NODE_ARCH}"
        URL="${NODE_MIRROR}/${NODE_VERSION}/${PKG}.tar.xz"
        log "下载便携 node（随包分发，供目标节点使用）：${URL}"
        # -C -：支持断点续传（上次中断后重跑不会从头下）
        curl -fL --retry 3 -C - -o "/tmp/${PKG}.tar.xz" "${URL}"
        rm -rf "${TOOLS_DIR}/node" "/tmp/${PKG}"
        tar -xJf "/tmp/${PKG}.tar.xz" -C /tmp
        mv "/tmp/${PKG}" "${TOOLS_DIR}/node"
        rm -f "/tmp/${PKG}.tar.xz"
        log "便携 node 就绪：$("${TOOLS_DIR}/node/bin/node" -v)"
    fi
    NODE_BIN="${PROJECT_ROOT}/${TOOLS_DIR}/node/bin/node"
    NPM_BIN="${PROJECT_ROOT}/${TOOLS_DIR}/node/bin/npm"
fi

# ---------- ② playwright 包 ----------
log "安装 playwright（npm install --omit=dev）"
(cd "${TOOLS_DIR}" && "${NPM_BIN}" install --omit=dev --no-audit --no-fund)

# ---------- ③ chromium ----------
# 直接用 playwright CLI（系统 node 与便携 node 都适用，避免依赖 npx）
PW_CLI="${TOOLS_DIR}/node_modules/playwright/cli.js"
[ -f "${PW_CLI}" ] || { echo "[ERROR] 未找到 ${PW_CLI}（playwright 安装失败）" >&2; exit 1; }
if [ "${SLIM}" = "1" ]; then
    log "下载浏览器（--slim：仅 headless shell，体积小很多）"
    (cd "${TOOLS_DIR}" && "${NODE_BIN}" "node_modules/playwright/cli.js" install chromium --only-shell)
else
    log "下载 chromium（体积较大，首次较慢）"
    (cd "${TOOLS_DIR}" && "${NODE_BIN}" "node_modules/playwright/cli.js" install chromium)
fi

# ---------- ④ 校验 ----------
BROWSER_HOME="${TOOLS_DIR}/node_modules/playwright-core/.local-browsers"
if [ ! -d "${BROWSER_HOME}" ]; then
    echo "[ERROR] 未找到包内浏览器目录：${BROWSER_HOME}" >&2
    echo "        请确认安装时设置了 PLAYWRIGHT_BROWSERS_PATH=0" >&2
    exit 1
fi
PW_VERSION="$((cd "${TOOLS_DIR}" && "${NODE_BIN}" -p "require('playwright/package.json').version") 2>/dev/null || echo 未知)"
case "${OS_NAME}" in
    *Ubuntu*|*Debian*|*Fedora*|*Red\ Hat*|*CentOS*|*Rocky*|*Alma*|*openSUSE*)
        OS_HINT="在 Playwright 官方支持列表内（或接近）" ;;
    *)
        OS_HINT="⚠ 可能不在 Playwright 官方支持列表（会回退为 ubuntu24.04 构建）——**务必在目标节点实测一次探针**（见文末命令）" ;;
esac

echo ""
echo "================ 浏览器工具就绪 ================"
echo "  构建用 node : $("${NODE_BIN}" -v)（$([ "${BUNDLE_NODE}" = "1" ] && echo "便携，随包分发" || echo "系统，不随包分发")）"
echo "  playwright  : ${PW_VERSION}"
echo "  浏览器目录  : ${BROWSER_HOME}（$(du -sm "${BROWSER_HOME}" | awk '{print $1}') MB）"
echo "  tools 总计  : $(du -sm "${TOOLS_DIR}" | awk '{print $1}') MB"
echo "  构建机系统  : ${OS_NAME:-未知} · ${GLIBC_VER:-未知}"
echo "  兼容性      : ${OS_HINT}"
echo ""
if [ "${BUNDLE_NODE}" != "1" ]; then
    echo "  ⚠ 包内不含 node：目标节点需自带 node（否则探针退出码 3）"
fi
echo "  下一步：bash scripts/build-code-agent.sh <版本>（会把 tools/ 打进 tarball）"
echo "  节点侧验证：node /opt/opc-agent-code/tools/frontend_probe.mjs --url http://127.0.0.1:8000/ ; echo exit=\$?"
echo "==============================================="

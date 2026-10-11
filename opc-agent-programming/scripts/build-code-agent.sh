#!/usr/bin/env bash
# Code Agent 统一产物一键构建脚本（ADR-024）
#
# 用途：在 Linux x64 构建服务器上执行，产出单一 tarball：
#   artifacts/opc-agent-code-{VERSION}-linux-x64.tar.gz
#
# 前置条件（首次构建需准备）：
#   1. python3.11 + gcc + patchelf 已安装（Ubuntu 示例）：
#      sudo apt-get install -y python3.11 python3.11-venv gcc patchelf
#   2. tilacode 二进制已放到 artifacts/tilacode（Windows Bun 交叉编译后 scp 上传）：
#      本地: cd tilacode/packages/opencode && bun run build --skip-embed-web-ui --target linux-x64-baseline
#      上传: scp dist/tilacode-linux-x64-baseline/bin/opencode <server>:~/.../artifacts/tilacode
#
# 用法：
#   bash scripts/build-code-agent.sh [版本号]
#   版本号缺省读取 pyproject.toml（当前 0.5.0）
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${PROJECT_ROOT}"

VERSION="${1:-}"
# 允许只传选项（bash scripts/build-code-agent.sh --without-browser）：此时版本号回退读 pyproject.toml
case "${VERSION}" in --*) VERSION="" ;; esac
if [ -z "${VERSION}" ]; then
    # tr -d '\r'：pyproject.toml 若为 CRLF 行尾（Windows 传输/检出），剥离残留回车，
    # 避免产物文件名出现 $'\r' 特殊字符
    VERSION="$(grep -m1 '^version' pyproject.toml | sed 's/version *= *"\(.*\)"/\1/' | tr -d '\r')"
fi
if [ -z "${VERSION}" ]; then
    echo "[ERROR] 无法从 pyproject.toml 解析版本号，请显式传入：bash scripts/build-code-agent.sh 0.5.0" >&2
    exit 1
fi

TILACODE_BIN="artifacts/tilacode"
NUITKA_OUT="artifacts/nuitka"
PKG_DIR="artifacts/pkg/opc-agent-code-${VERSION}-linux-x64"
TARBALL="artifacts/opc-agent-code-${VERSION}-linux-x64.tar.gz"
VENV=".venv"

log() { echo "[build] $*"; }

# ---------- 1. 前置检查 ----------
if [ ! -f "${TILACODE_BIN}" ]; then
    echo "[ERROR] 未找到 tilacode 二进制（${TILACODE_BIN}）。" >&2
    echo "        请先在 Windows 构建机执行：" >&2
    echo "        cd tilacode/packages/opencode && bun run build --skip-embed-web-ui --target linux-x64-baseline" >&2
    echo "        然后 scp dist/tilacode-linux-x64-baseline/bin/opencode 到本服务器 ${TILACODE_BIN}" >&2
    exit 1
fi

# ---------- 1b. tilacode 产物预检门禁（ADR-061 C2，2026-10-01） ----------
# 事故背景：Windows 交叉编译 Linux 产物时，packages/opencode/script/build.ts 的
# 冒烟测试只在「目标平台 == 宿主平台」时执行 —— Linux 产物从未被真正运行验证过。
# 2026-10-01 因此发出过一个模型目录（catalog）装配失效的产物：节点上任务全部
# 卡到 task_startup_stalled，最后只报一句 ModelUnavailableError。
# 本门禁用刚拿到的产物跑一次 debug config / debug v2，断言：
#   ① 产物可执行，且接受节点写入的 permission_mode（配置能力在）
#   ② 模型目录非空并收录探针 provider（catalog 装配可用）
# 不满足即中止——且在 Nuitka 编译之前失败，省下几分钟。
# 应急跳过：bash scripts/build-code-agent.sh <版本> --skip-precheck
SKIP_PRECHECK=0
for arg in "$@"; do
    case "${arg}" in --skip-precheck) SKIP_PRECHECK=1 ;; esac
done
if [ "${SKIP_PRECHECK}" = "1" ]; then
    echo "[WARN] 已跳过 tilacode 产物预检（--skip-precheck）：产物可用性未经验证" >&2
else
    # 历史事故：新产物被上传成 artifacts/opencode，而脚本只读 artifacts/tilacode
    if [ -f "artifacts/opencode" ] && [ "artifacts/opencode" -nt "${TILACODE_BIN}" ]; then
        echo "[ERROR] artifacts/opencode 比 ${TILACODE_BIN} 更新：脚本只读取后者。" >&2
        echo "        请确认上传的产物文件名（新产物叫 opencode、打包用的却是旧 tilacode）。" >&2
        exit 1
    fi
    log "tilacode 产物预检（debug config / debug v2）"
    chmod +x "${TILACODE_BIN}" 2>/dev/null || true
    PROBE_DIR="$(mktemp -d)"
    mkdir -p "${PROBE_DIR}/.tilacode"
    cat > "${PROBE_DIR}/.tilacode/tilacode.jsonc" <<'JSON'
{
  "$schema": "https://opencode.ai/config.json",
  "permission_mode": "unattended",
  "default_agent": "build",
  "provider": {
    "gate-probe": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "gate-probe",
      "options": { "baseURL": "https://example.invalid/v1", "apiKey": "gate-probe-key" },
      "models": { "gate-probe-model": { "name": "gate-probe-model" } }
    }
  }
}
JSON
    # 探针运行使用独立 XDG 目录：不污染构建机 $HOME，且保证缓存是冷的（更接近节点首次运行）
    TILACODE_ABS="${PROJECT_ROOT}/${TILACODE_BIN}"
    probe_run() {
        ( cd "${PROBE_DIR}" \
            && XDG_DATA_HOME="${PROBE_DIR}/data" \
               XDG_CONFIG_HOME="${PROBE_DIR}/config" \
               XDG_CACHE_HOME="${PROBE_DIR}/cache" \
               XDG_STATE_HOME="${PROBE_DIR}/state" \
               "${TILACODE_ABS}" "$@" ) >"${PROBE_DIR}/probe.out" 2>&1
    }
    if ! probe_run debug config; then
        echo "[ERROR] tilacode 产物无法执行或配置加载失败（${TILACODE_BIN}）：" >&2
        tail -n 20 "${PROBE_DIR}/probe.out" >&2
        rm -rf "${PROBE_DIR}"
        exit 1
    fi
    if ! grep -q 'gate-probe' "${PROBE_DIR}/probe.out"; then
        echo "[ERROR] 产物不接受探针 provider 配置（schema 过旧或缺少 permission_mode 能力）：" >&2
        tail -n 20 "${PROBE_DIR}/probe.out" >&2
        rm -rf "${PROBE_DIR}"
        exit 1
    fi
    if ! probe_run debug v2; then
        echo "[ERROR] 产物执行 debug v2 失败（模型目录不可查询）：" >&2
        tail -n 20 "${PROBE_DIR}/probe.out" >&2
        rm -rf "${PROBE_DIR}"
        exit 1
    fi
    if ! grep -q 'gate-probe' "${PROBE_DIR}/probe.out"; then
        echo "[ERROR] tilacode 模型目录未收录探针 provider —— catalog 装配失效（2026-10-01 事故形态）。" >&2
        echo "        常见原因：产物非当前源码构建（缺 ADR-061 修复）；请重编后重试。" >&2
        tail -n 20 "${PROBE_DIR}/probe.out" >&2
        rm -rf "${PROBE_DIR}"
        exit 1
    fi
    TILACODE_BUILD_VERSION="$("${TILACODE_ABS}" --version 2>/dev/null | head -n1 | tr -d '\r' || true)"
    log "预检通过（tilacode ${TILACODE_BUILD_VERSION:-unknown}）：配置能力在、模型目录可用"
    rm -rf "${PROBE_DIR}"
fi

command -v gcc >/dev/null 2>&1 || { echo "[ERROR] 缺少 gcc，请先安装（Ubuntu: apt-get install -y gcc）" >&2; exit 1; }
command -v patchelf >/dev/null 2>&1 || { echo "[ERROR] 缺少 patchelf，请先安装（Ubuntu: apt-get install -y patchelf）" >&2; exit 1; }

PYTHON_BIN="$(command -v python3.11 || command -v python3.12 || command -v python3.10 || true)"
if [ -z "${PYTHON_BIN}" ]; then
    echo "[ERROR] 未找到 python3.10+（构建工具链需要，与节点运行时无关）" >&2
    exit 1
fi

# ---------- 2. 构建虚拟环境（仅构建机需要） ----------
if [ ! -d "${VENV}" ]; then
    log "创建构建虚拟环境（${PYTHON_BIN}）"
    "${PYTHON_BIN}" -m venv "${VENV}"
fi
# shellcheck disable=SC1091
source "${VENV}/bin/activate"

log "安装依赖与 Nuitka"
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -e . "nuitka>=2.4" "ordered-set"

# ---------- 3. Nuitka 编译代理层 ----------
log "Nuitka 编译代理层（首次编译约 3-8 分钟）"
rm -rf "${NUITKA_OUT}"
python -m nuitka \
    --onefile --standalone \
    --output-filename=opc-agent \
    --output-dir="${NUITKA_OUT}" \
    --nofollow-import-to=tests \
    --python-flag=no_site \
    --assume-yes-for-downloads \
    --include-package=websockets \
    app/main.py

AGENT_BIN="${NUITKA_OUT}/opc-agent"
if [ ! -f "${AGENT_BIN}" ]; then
    echo "[ERROR] Nuitka 编译产物未找到：${AGENT_BIN}" >&2
    exit 1
fi

# 冒烟：--version 快速路径（不加载 .env 不建连）
log "二进制冒烟测试"
if ! "${AGENT_BIN}" --version; then
    echo "[ERROR] opc-agent --version 冒烟失败" >&2
    exit 1
fi

# ---------- 4. 组包 ----------
log "组装产物目录"
rm -rf "artifacts/pkg"
mkdir -p "${PKG_DIR}/bin" "${PKG_DIR}/systemd"

install -m 0755 "${AGENT_BIN}" "${PKG_DIR}/bin/opc-agent"
install -m 0755 "${TILACODE_BIN}" "${PKG_DIR}/bin/tilacode"
install -m 0644 systemd/opc-agent-code.service "${PKG_DIR}/systemd/"
install -m 0644 .env.example "${PKG_DIR}/env.example"
install -m 0644 README.md "${PKG_DIR}/README.md"

# ---------- 4b. 浏览器工具打包（ADR-061 M7，**默认不打包**） ----------
# 子 Agent 的前端实测能力（frontend_probe.mjs）需要三样东西随包分发：
#   ① node 运行时  ② playwright 包  ③ chromium 二进制（都在 tools/ 下，共数百 MB）
# **默认不带**（避免误打包体积暴涨）；确实需要时：
#   ① 构建机先执行一次：bash scripts/prepare-browser-tools.sh
#   ② 构建时显式开启：  bash scripts/build-code-agent.sh <版本> --with-browser
WITH_BROWSER="${WITH_BROWSER:-0}"
for arg in "$@"; do
    case "${arg}" in
        --with-browser)    WITH_BROWSER="1" ;;
        --without-browser) WITH_BROWSER="0" ;;
    esac
done
if [ "${WITH_BROWSER}" = "1" ]; then
    if [ ! -d "tools/node_modules/playwright" ]; then
        echo "[ERROR] 未安装 playwright（tools/node_modules/playwright 缺失），本次构建要求带浏览器。" >&2
        echo "        在构建机执行一次：bash scripts/prepare-browser-tools.sh" >&2
        echo "        如本次不要浏览器能力：去掉 --with-browser（默认就是不带）" >&2
        exit 1
    fi
    if [ ! -f "tools/frontend_probe.mjs" ]; then
        echo "[ERROR] 未找到探针脚本（tools/frontend_probe.mjs 缺失）——请确认已上传 tools/ 目录。" >&2
        exit 1
    fi
    if [ ! -x "tools/node/bin/node" ]; then
        # 包内不带 node 运行时：构建机用系统 node 装的依赖，目标节点必须自带 node
        if command -v node >/dev/null 2>&1; then
            log "包内不含 node 运行时：目标节点需自带 node（安装脚本会尝试 apt 安装 nodejs）"
        else
            echo "[ERROR] 包内无 node，且构建机也没有 node——无法确认目标节点可运行探针。" >&2
            echo "        任选其一：① 构建机装 node 后重跑；② bash scripts/prepare-browser-tools.sh（默认把 node 打进包）" >&2
            exit 1
        fi
    fi
    log "打包浏览器工具（node + playwright + chromium，产物体积会明显变大）"
    rm -rf "${PKG_DIR}/tools"
    cp -a tools "${PKG_DIR}/tools"
    # 清理无关文件，避免把源码缓存/测试产物打进包
    find "${PKG_DIR}/tools" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
    find "${PKG_DIR}/tools" -name '*.pyc' -delete 2>/dev/null || true
    TOOLS_SIZE="$(du -sm "${PKG_DIR}/tools" | awk '{print $1}')"
    log "浏览器工具已入包：${TOOLS_SIZE} MB（含 chromium）"
else
    log "本次不带浏览器能力（默认）：节点将不具备前端实测能力；需要时加 --with-browser"
fi

# tilacode 版本读取（供 build-info 展示；文件缺失时置 unknown）
TILACODE_VERSION="$(python -c "import json,pathlib;print(json.loads(pathlib.Path('tilacode/packages/opencode/package.json').read_text())['version'])" 2>/dev/null || echo unknown)"
NUITKA_VERSION="$(python -m nuitka --version | head -n1 | tr -d '\r')"

cat > "${PKG_DIR}/build-info.json" <<EOF
{
  "artifact_version": "${VERSION}",
  "tilacode_version": "${TILACODE_VERSION}",
  "nuitka_version": "${NUITKA_VERSION}",
  "python_version": "$(python -V | cut -d' ' -f2)",
  "built_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "platform": "linux-x64"
}
EOF

log "打包 tarball"
rm -f "${TARBALL}"
tar -czf "${TARBALL}" -C "artifacts/pkg" "opc-agent-code-${VERSION}-linux-x64"

# ---------- 5. 产物校验与发布提示 ----------
SHA256="$(sha256sum "${TARBALL}" | awk '{print $1}')"
SIZE="$(stat -c %s "${TARBALL}")"

echo ""
echo "================ 构建完成 ================"
echo "  产物    : ${TARBALL}"
echo "  版本    : ${VERSION}（tilacode ${TILACODE_VERSION}）"
echo "  大小    : ${SIZE} 字节（$(awk "BEGIN{printf \"%.1f\", ${SIZE}/1024/1024}") MB）"
echo "  sha256  : ${SHA256}"
echo ""
echo "  发布：放入中枢分发目录 data/resources/agent-code/<版本>/（或自建云端资源仓库后台上传）"
echo "        key=agent-code-linux-x64  version=${VERSION}  上传 ${TARBALL}"
echo "==========================================="

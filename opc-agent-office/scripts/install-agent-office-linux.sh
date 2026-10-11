#!/usr/bin/env bash
# OPC Office Agent 一键安装脚本（ADR-062：源码包 + venv 部署）
# ADR-025：脚本与 agent 类型绑定，key=install-script-office，路径 install-scripts/office/
#
# 用法：
#   curl -fsSL http://<hub>/static/install-scripts/office/install-agent-office-linux.sh | sudo bash -s -- --hub-url http://<hub>:8000 --token <token>
#
# 特性：
# - 幂等执行：重复运行会升级整体包、刷新 .env 并重启服务。
# - 源码部署：tar.gz 含 app 源码 + pyproject.toml + systemd unit；
#   节点侧建 venv 并 pip 安装依赖（Python >= 3.10）。
# - 依赖探针：安装后 import 校验 docx/pptx/openpyxl/matplotlib（缺失即失败退出），
#   避免「已声明未安装」的静默失败。
# - sha256 校验：从中枢资源总清单（ADR-022）读取，校验整体包；清单变化即重装。
# - 节点身份迁移：升级保留 AGENT_CLIENT_ID/AGENT_NAME/API_TOKEN。
set -euo pipefail

INSTALL_DIR="/opt/opc-agent-office"
SERVICE_NAME="opc-agent-office"
VERSION="0.1.0"
ARTIFACT_KEY="agent-office-linux-x64"
REQUIRED_PY_MAJOR=3
REQUIRED_PY_MINOR=10

HUB_URL=""
REGISTRATION_TOKEN=""
AGENT_NAME=""
AGENT_API_PORT=18081
STATIC_IFACE=""
STATIC_IP=""
STATIC_PREFIX=""
STATIC_GW=""

_SCRIPT_TEMP_FILE=""
MIGRATED_CLIENT_ID=""

log_info() { echo "[INFO] $*" >&2; }
log_warn() { echo "[WARN] $*" >&2; }
log_error() { echo "[ERROR] $*" >&2; }

usage() {
    cat <<EOF
用法: $0 --hub-url <HUB_URL> --token <REGISTRATION_TOKEN> [--agent-name <NAME>] [--api-port <PORT>]

选项:
  --hub-url     中枢地址，例如 http://192.168.101.101:8000
  --token       节点注册 Token
  --agent-name  节点显示名（可选）；留空则由 Agent 启动时按 OS 平台+版本兜底
  --api-port    通知 API 监听端口（可选，默认 18081）
  -h, --help    显示此帮助
EOF
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --hub-url) HUB_URL="$2"; shift 2 ;;
            --token) REGISTRATION_TOKEN="$2"; shift 2 ;;
            --agent-name) AGENT_NAME="$2"; shift 2 ;;
            --api-port) AGENT_API_PORT="$2"; shift 2 ;;
            -h|--help) usage; exit 0 ;;
            *) log_error "未知参数: $1"; usage; exit 1 ;;
        esac
    done
}

require_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "请使用 root 权限运行（例如 sudo）"
        exit 1
    fi
}

validate_args() {
    if [[ -z "$HUB_URL" ]]; then
        log_error "缺少 --hub-url"; usage; exit 1
    fi
    if [[ -z "$REGISTRATION_TOKEN" ]]; then
        log_error "缺少 --token"; usage; exit 1
    fi
}

detect_platform() {
    local os arch
    os=$(uname -s)
    arch=$(uname -m)
    if [[ "$os" != "Linux" ]] || [[ "$arch" != "x86_64" ]]; then
        log_error "仅支持 linux-x64，当前平台：${os}/${arch}"
        exit 1
    fi
    log_info "平台检测通过：${os}/${arch}"
}

# Python >= 3.10 检查（缺失则尝试 python3.11/3.10，再不行报错退出）
check_python() {
    local candidate best=""
    for candidate in python3.12 python3.11 python3.10 python3; do
        if command -v "$candidate" >/dev/null 2>&1; then
            if "$candidate" -c 'import sys; sys.exit(0 if (sys.version_info.major, sys.version_info.minor) >= (3, 10) else 1)' 2>/dev/null; then
                best="$candidate"
                break
            fi
        fi
    done
    if [[ -z "$best" ]]; then
        log_error "未找到 Python >= ${REQUIRED_PY_MAJOR}.${REQUIRED_PY_MINOR}，请先安装（apt-get install -y python3.10 python3.10-venv 或对应版本）"
        exit 1
    fi
    PYTHON_BIN="$best"
    log_info "Python: $("$PYTHON_BIN" --version)（${best}）"
}

check_system_deps() {
    local missing=()
    command -v curl >/dev/null 2>&1 || missing+=("curl")
    command -v tar >/dev/null 2>&1 || missing+=("tar")
    command -v sha256sum >/dev/null 2>&1 || missing+=("sha256sum")
    command -v systemctl >/dev/null 2>&1 || missing+=("systemd")
    if [[ ${#missing[@]} -gt 0 ]]; then
        log_error "缺少系统依赖: ${missing[*]}"
        log_error "请先安装后重试（Ubuntu/Debian: apt-get install -y ${missing[*]}）"
        exit 1
    fi
}

# 防火墙放行通知 API 端口（ADR-035）
open_firewall_port() {
    local port="$1"
    if command -v firewall-cmd >/dev/null 2>&1 && systemctl is-active firewalld >/dev/null 2>&1; then
        firewall-cmd --permanent --add-port="${port}/tcp" >/dev/null 2>&1 && firewall-cmd --reload >/dev/null 2>&1 \
            && log_info "防火墙已放行 ${port}/tcp（firewalld）" && return 0
    fi
    if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
        ufw allow "${port}/tcp" >/dev/null 2>&1 && log_info "防火墙已放行 ${port}/tcp（ufw）" && return 0
    fi
    log_warn "未自动放行 ${port}/tcp；如启用防火墙请手动放行"
}

# 从中枢资源总清单（ADR-022）提取指定 key 条目的字段
manifest_field() {
    local field="$1" entry
    entry=$(tr '\n\r\t' '   ' < "$_SCRIPT_TEMP_FILE" \
        | grep -o "\"key\"[[:space:]]*:[[:space:]]*\"${ARTIFACT_KEY}\"[^}]*}" \
        | head -n1)
    if [[ -z "$entry" ]]; then
        return 0
    fi
    printf '%s\n' "$entry" \
        | sed -n 's/.*"'"${field}"'"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p'
}

resolve_artifact() {
    _SCRIPT_TEMP_FILE=$(mktemp)
    trap 'rm -f "${_SCRIPT_TEMP_FILE:-}"' EXIT

    local manifest_url="${HUB_URL%/}/static/manifest.json"
    log_info "读取中枢资源总清单: ${manifest_url}"
    if ! curl -fsSL "$manifest_url" -o "$_SCRIPT_TEMP_FILE"; then
        log_error "下载资源总清单失败: ${manifest_url}（中枢可能尚未发布 office 资源，请在中枢资源管理页检查）"
        exit 1
    fi

    ARTIFACT_SHA256=$(manifest_field sha256)
    ARTIFACT_PATH=$(manifest_field path)
    if [[ -z "$ARTIFACT_SHA256" ]] || [[ -z "$ARTIFACT_PATH" ]]; then
        log_error "总清单中未找到 ${ARTIFACT_KEY} 条目（请先在中枢发布 office 安装包）"
        exit 1
    fi
    log_info "解析到整体包: ${ARTIFACT_PATH}（sha256 ${ARTIFACT_SHA256:0:12}...）"

    rm -f "${_SCRIPT_TEMP_FILE:-}"
    trap - EXIT
}

download_and_extract_artifact() {
    local tarball_url="${HUB_URL%/}/static/${ARTIFACT_PATH}"
    local local_stamp="${INSTALL_DIR}/.artifact-sha256"

    if [[ -f "${INSTALL_DIR}/app/main.py" ]] && [[ -f "$local_stamp" ]] \
        && [[ "$(cat "$local_stamp")" == "$ARTIFACT_SHA256" ]] && [[ -z "$FORCE_REINSTALL" ]]; then
        log_info "整体包已是最新，跳过下载"
        return 0
    fi

    log_info "下载整体包: ${tarball_url}"
    _SCRIPT_TEMP_FILE=$(mktemp)
    trap 'rm -f "${_SCRIPT_TEMP_FILE:-}"' EXIT
    curl -fsSL "$tarball_url" -o "$_SCRIPT_TEMP_FILE"

    local actual_sha
    actual_sha=$(sha256sum "$_SCRIPT_TEMP_FILE" | awk '{print $1}')
    if [[ "$actual_sha" != "$ARTIFACT_SHA256" ]]; then
        log_error "整体包 sha256 校验失败（实际 ${actual_sha}，期望 ${ARTIFACT_SHA256}）"
        exit 1
    fi
    log_info "整体包 sha256 校验通过"

    mkdir -p "$INSTALL_DIR"
    tar -xzf "$_SCRIPT_TEMP_FILE" -C "$INSTALL_DIR"
    echo "$ARTIFACT_SHA256" > "$local_stamp"

    rm -f "${_SCRIPT_TEMP_FILE:-}"
    trap - EXIT
}

# venv 依赖安装（幂等：venv 存在且依赖清单未变则跳过）
setup_venv() {
    local venv_dir="${INSTALL_DIR}/venv"
    local deps_stamp="${INSTALL_DIR}/.deps-sha256"
    local deps_hash
    deps_hash=$(sha256sum "${INSTALL_DIR}/pyproject.toml" | awk '{print $1}')

    if [[ -x "${venv_dir}/bin/python" ]] && [[ -f "$deps_stamp" ]] \
        && [[ "$(cat "$deps_stamp")" == "$deps_hash" ]]; then
        log_info "venv 依赖已是最新，跳过安装"
        return 0
    fi

    log_info "创建/更新 venv 并安装依赖（首次可能需要数分钟）..."
    "$PYTHON_BIN" -m venv "$venv_dir"
    "${venv_dir}/bin/pip" install --upgrade pip --quiet
    "${venv_dir}/bin/pip" install "${INSTALL_DIR}" --quiet
    echo "$deps_hash" > "$deps_stamp"
    log_info "依赖安装完成"
}

# 运行时依赖 import 探针（ADR-065 M1）：安装脚本必须自证依赖真的可用，
# 避免「pyproject 声明了但节点 venv 里没有」的静默失败（Scrapling 前例）
verify_runtime_deps() {
    local venv_python="${INSTALL_DIR}/venv/bin/python"
    local module
    for module in docx pptx openpyxl matplotlib; do
        if ! "$venv_python" -c "import ${module}" >/dev/null 2>&1; then
            log_error "依赖探针失败：venv 中无法 import ${module}（${venv_python}）"
            log_error "请修复后在节点重跑：rm -f ${INSTALL_DIR}/.deps-sha256 && 重跑本脚本"
            exit 1
        fi
    done
    log_info "运行时依赖探针通过：docx / pptx / openpyxl / matplotlib（素材读写 + 图表渲染）"
    # scrapling 为网页抓取依赖：缺失仅告警（不影响文档/表格产出），但需人工介入
    if "$venv_python" -c "import scrapling" >/dev/null 2>&1; then
        log_info "运行时依赖探针通过：scrapling（web_search / web_fetch）"
    else
        log_warn "依赖探针：venv 中缺少 scrapling，web_search / web_fetch 将不可用"
    fi
}

generate_agent_client_id() {
    local hostname suffix
    hostname=$(hostname)
    suffix=$(head /dev/urandom | tr -dc 'a-z0-9' | head -c 8 || true)
    echo "${hostname}-${suffix}"
}

generate_api_token() {
    head /dev/urandom | tr -dc 'a-zA-Z0-9' | head -c 32 || true
}

# 生成 .env（幂等：已有身份/Token 保留）
write_env_file() {
    local env_file="${INSTALL_DIR}/.env"
    local agent_name="" agent_client_id="" api_token=""

    if [[ -f "$env_file" ]]; then
        agent_name=$(grep -E "^AGENT_NAME=" "$env_file" 2>/dev/null | cut -d'=' -f2- || true)
        agent_client_id=$(grep -E "^AGENT_CLIENT_ID=" "$env_file" 2>/dev/null | cut -d'=' -f2- || true)
        api_token=$(grep -E "^API_TOKEN=" "$env_file" 2>/dev/null | cut -d'=' -f2- || true)
    fi

    if [[ -n "$AGENT_NAME" ]]; then
        agent_name="$AGENT_NAME"
    fi
    if [[ -z "$agent_client_id" ]] && [[ -n "$MIGRATED_CLIENT_ID" ]]; then
        agent_client_id="$MIGRATED_CLIENT_ID"
    fi
    if [[ -z "$agent_client_id" ]]; then
        agent_client_id=$(generate_agent_client_id)
    fi
    if [[ -z "$api_token" ]]; then
        api_token=$(generate_api_token)
    fi

    log_info "生成 .env 配置文件..."
    cat > "$env_file" <<EOF
HUB_API_URL=${HUB_URL}
AGENT_REGISTRATION_TOKEN=${REGISTRATION_TOKEN}
AGENT_CLIENT_ID=${agent_client_id}
AGENT_TYPE=office
HEARTBEAT_INTERVAL_SECONDS=5
WORKSPACE_ROOT=${INSTALL_DIR}/workspace
AGENT_API_PORT=${AGENT_API_PORT}
API_TOKEN=${api_token}
EOF

    # 可选开关（默认关闭，不改写 .env 内容，仅作部署提示）：ADR-065 M6
    # OFFICE_ENABLE_STEALTH=true 启用 web_fetch 隐身浏览器通道（需 Patchright 浏览器）
    if [[ -n "$agent_name" ]]; then
        echo "AGENT_NAME=${agent_name}" >> "$env_file"
    fi

    echo "$agent_name"
}

install_systemd_service() {
    log_info "安装 systemd 服务..."
    cp "${INSTALL_DIR}/systemd/${SERVICE_NAME}.service" /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"
    systemctl restart "$SERVICE_NAME"
}

main() {
    parse_args "$@"
    require_root
    validate_args
    detect_platform
    check_system_deps
    check_python

    # 检测到旧编程节点环境时迁移身份（同机双类型部署互不影响）
    local legacy_env="/opt/opc-agent-code/.env"
    if [[ -f "$legacy_env" ]] && [[ -z "$MIGRATED_CLIENT_ID" ]]; then
        MIGRATED_CLIENT_ID=$(grep -E "^AGENT_CLIENT_ID=" "$legacy_env" 2>/dev/null | cut -d'=' -f2- || true)
    fi

    open_firewall_port "$AGENT_API_PORT" || true
    resolve_artifact
    download_and_extract_artifact
    setup_venv
    verify_runtime_deps

    local agent_name
    agent_name=$(write_env_file)
    install_systemd_service

    log_info "OPC Office Agent 安装完成并启动成功。"
    log_info "节点名称: ${agent_name:-（启动时自动生成）}"
    log_info "通知 API 端口: ${AGENT_API_PORT}"
    log_info "服务状态: systemctl status ${SERVICE_NAME}"
}

FORCE_REINSTALL=""
main "$@"

#!/usr/bin/env bash
# OPC Code Agent 一键安装脚本（ADR-024：单一产物 + Nuitka 编译，节点零 Python 依赖）
# ADR-025：脚本与 agent 类型绑定，key=install-script-code，路径 install-scripts/code/
#
# 用法：
#   curl -fsSL http://<hub>/static/install-scripts/code/install-agent-code-linux.sh | sudo bash -s -- --hub-url http://<hub>:8000 --token <token>
#
# 特性：
# - 幂等执行：重复运行会升级整体包、刷新 .env 并重启服务。
# - 单一产物：一个 tar.gz 含代理二进制 + tilacode 二进制 + systemd（无需 Python/venv/pip/PyPI）。
# - sha256 校验：从中枢资源总清单（ADR-022）读取，校验整体包。
# - 旧版升级：自动迁移节点身份（AGENT_CLIENT_ID/AGENT_NAME），停用旧 systemd 服务。
set -euo pipefail

INSTALL_DIR="/opt/opc-agent-code"
LEGACY_DIR="/opt/opc-agent-programming"
LEGACY_SERVICE="opc-agent-programming"
SERVICE_NAME="opc-agent-code"
VERSION="0.5.0"
ARTIFACT_KEY="agent-code-linux-x64"
TILACODE_PORT_START=4096
TILACODE_PORT_END=4200
TILACODE_PORT=""

HUB_URL=""
REGISTRATION_TOKEN=""
AGENT_NAME=""
# ADR-035：通知 API（中枢重启主动反连）
AGENT_API_PORT=18080
STATIC_IFACE=""
STATIC_IP=""
STATIC_PREFIX=""
STATIC_GW=""

# 临时文件清理（单一全局变量 + 空值默认，避免 local + EXIT trap 的未绑定变量问题）
_SCRIPT_TEMP_FILE=""

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
  --api-port    通知 API 监听端口（可选，默认 18080，ADR-035 中枢重启主动反连）
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
        log_error "缺少 --hub-url"
        usage
        exit 1
    fi
    if [[ -z "$REGISTRATION_TOKEN" ]]; then
        log_error "缺少 --token"
        usage
        exit 1
    fi
}

# 检测平台：MVP 阶段仅支持 linux-x64
detect_platform() {
    local os arch
    os=$(uname -s)
    arch=$(uname -m)
    if [[ "$os" != "Linux" ]] || [[ "$arch" != "x86_64" ]]; then
        log_error "仅支持 linux-x64，当前平台：${os}/${arch}"
        exit 1
    fi
    log_info "平台检测通过：${os}/${arch}（零 Python 依赖）"
}

# 系统依赖检测（不再检测/安装 Python——ADR-024）
check_system_deps() {
    local missing=()
    command -v curl >/dev/null 2>&1 || missing+=("curl")
    command -v tar >/dev/null 2>&1 || missing+=("tar")
    command -v sha256sum >/dev/null 2>&1 || missing+=("sha256sum")
    command -v systemctl >/dev/null 2>&1 || missing+=("systemd")

    if [[ ${#missing[@]} -gt 0 ]]; then
        log_error "缺少系统依赖: ${missing[*]}"
        log_error "请先安装后重试（Ubuntu/Debian: apt-get install -y ${missing[*]}; RHEL/CentOS: yum install -y ${missing[*]}）"
        exit 1
    fi
}

# 检测默认网卡 / IP / 子网前缀 / 网关（ADR-035 静态 IP 固化交互用）
detect_network_info() {
    STATIC_IFACE=$(ip -4 route show default 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev") print $(i+1)}' | head -n1)
    if [[ -z "$STATIC_IFACE" ]]; then
        log_warn "未检测到默认网卡，跳过静态 IP 固化与防火墙配置"
        return 1
    fi
    local addr_line
    addr_line=$(ip -4 addr show dev "$STATIC_IFACE" scope global 2>/dev/null | grep -oE 'inet [0-9.]+/[0-9]+' | head -n1)
    STATIC_IP="${addr_line#inet }"
    STATIC_PREFIX="${STATIC_IP#*/}"
    STATIC_IP="${STATIC_IP%%/*}"
    STATIC_GW=$(ip -4 route show default 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="via") print $(i+1)}' | head -n1)
    log_info "当前网络: 网卡=${STATIC_IFACE} IP=${STATIC_IP}/${STATIC_PREFIX} 网关=${STATIC_GW:-（无）}"
    [[ -n "$STATIC_IP" && -n "$STATIC_PREFIX" && -n "$STATIC_GW" ]]
}

# 静态 IP 固化交互（ADR-035）：y=按显示值固化；n=手动输入 IP/掩码/网关
# 非 TTY（curl|bash 管道安装）时 stdin 不可交互，默认跳过并提示
prompt_static_ip() {
    if [[ ! -t 0 ]]; then
        log_warn "检测到非交互安装（curl|bash），跳过静态 IP 固化（如需固化请下载脚本后 sudo bash 执行）"
        return 1
    fi
    local answer
    read -r -p "是否固化静态 IP？[y=按当前显示值固化 / n=手动输入 / 其他=跳过] " answer || return 1
    case "$answer" in
        y|Y)
            if ! detect_network_info; then
                log_warn "当前网络信息不完整，无法固化静态 IP"
                return 1
            fi
            ;;
        n|N)
            local input_ip input_prefix input_gw
            read -r -p "请输入 IP 地址: " input_ip
            read -r -p "请输入子网前缀长度（如 24 表示 255.255.255.0）: " input_prefix
            read -r -p "请输入网关: " input_gw
            if [[ -z "$input_ip" || -z "$input_prefix" || -z "$input_gw" ]]; then
                log_warn "输入不完整，跳过静态 IP 固化"
                return 1
            fi
            STATIC_IP="$input_ip"; STATIC_PREFIX="$input_prefix"; STATIC_GW="$input_gw"
            ;;
        *)
            log_info "跳过静态 IP 固化"
            return 1
            ;;
    esac
    return 0
}

# 固化静态 IP（Ubuntu netplan 优先，RHEL/CentOS 走 ifcfg），失败输出手动配置提示
apply_static_ip() {
    local dns
    dns=$(grep -m1 '^nameserver' /etc/resolv.conf 2>/dev/null | awk '{print $2}')
    [[ -n "$dns" ]] || dns="223.5.5.5"
    log_info "固化静态 IP: ${STATIC_IP}/${STATIC_PREFIX} 网关 ${STATIC_GW}（网卡 ${STATIC_IFACE}）"
    if command -v netplan >/dev/null 2>&1; then
        cat > /etc/netplan/99-opc-agent-static.yaml <<EOF
network:
  version: 2
  ethernets:
    ${STATIC_IFACE}:
      dhcp4: no
      addresses: [${STATIC_IP}/${STATIC_PREFIX}]
      routes:
        - to: default
          via: ${STATIC_GW}
      nameservers:
        addresses: [${dns}]
EOF
        chmod 600 /etc/netplan/99-opc-agent-static.yaml
        if netplan apply; then
            log_info "netplan 静态 IP 固化成功"
            return 0
        fi
        log_error "netplan apply 失败，请手动检查 /etc/netplan/99-opc-agent-static.yaml"
        return 1
    fi
    if [[ -d /etc/sysconfig/network-scripts ]]; then
        cat > "/etc/sysconfig/network-scripts/ifcfg-${STATIC_IFACE}" <<EOF
TYPE=Ethernet
BOOTPROTO=none
NAME=${STATIC_IFACE}
DEVICE=${STATIC_IFACE}
ONBOOT=yes
IPADDR=${STATIC_IP}
PREFIX=${STATIC_PREFIX}
GATEWAY=${STATIC_GW}
DNS1=${dns}
EOF
        if systemctl restart NetworkManager 2>/dev/null || systemctl restart network 2>/dev/null; then
            log_info "ifcfg 静态 IP 固化成功"
            return 0
        fi
        log_error "网络服务重启失败，请手动检查 /etc/sysconfig/network-scripts/ifcfg-${STATIC_IFACE}"
        return 1
    fi
    log_warn "未识别的网络配置方式，请手动固化静态 IP：IP=${STATIC_IP}/${STATIC_PREFIX} 网关=${STATIC_GW} 网卡=${STATIC_IFACE}"
    return 1
}

# 防火墙放行通知 API 端口（ADR-035）：firewalld/ufw 自动放行，否则输出手动命令
open_firewall_port() {
    local port="$1"
    if command -v firewall-cmd >/dev/null 2>&1 && systemctl is-active firewalld >/dev/null 2>&1; then
        if firewall-cmd --permanent --add-port="${port}/tcp" >/dev/null 2>&1 && firewall-cmd --reload >/dev/null 2>&1; then
            log_info "防火墙已放行 ${port}/tcp（firewalld）"
            return 0
        fi
        log_warn "firewalld 放行失败，请手动执行: firewall-cmd --permanent --add-port=${port}/tcp && firewall-cmd --reload"
        return 1
    fi
    if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
        if ufw allow "${port}/tcp" >/dev/null 2>&1; then
            log_info "防火墙已放行 ${port}/tcp（ufw）"
            return 0
        fi
        log_warn "ufw 放行失败，请手动执行: ufw allow ${port}/tcp"
        return 1
    fi
    log_warn "未检测到活动防火墙；如已启用 iptables/nftables，请手动放行 ${port}/tcp"
    return 1
}

# 从中枢资源总清单（ADR-022）提取指定 key 条目的字段
# 兼容单行/多行 JSON：中枢 /static/manifest.json 响应经 request_id 中间件
# 会以紧凑单行格式重新序列化（详见 app/core/middleware.py），逐行 awk 会取错字段
# 参数：$1 = 字段名（path 或 sha256）；输出：字段值（stdout）
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

# 下载总清单并解析整体包 URL 与 sha256（写入全局变量）
resolve_artifact() {
    _SCRIPT_TEMP_FILE=$(mktemp)
    trap 'rm -f "${_SCRIPT_TEMP_FILE:-}"' EXIT

    local manifest_url="${HUB_URL%/}/static/manifest.json"
    log_info "读取中枢资源总清单: ${manifest_url}"
    if ! curl -fsSL "$manifest_url" -o "$_SCRIPT_TEMP_FILE"; then
        log_error "下载资源总清单失败: ${manifest_url}（中枢可能尚未同步云端资源，请在中枢设置页「立即检查」）"
        exit 1
    fi

    ARTIFACT_SHA256=$(manifest_field sha256)
    ARTIFACT_PATH=$(manifest_field path)
    if [[ -z "$ARTIFACT_SHA256" ]] || [[ -z "$ARTIFACT_PATH" ]]; then
        log_error "总清单中未找到 ${ARTIFACT_KEY} 条目或字段不完整（sha256=${ARTIFACT_SHA256:-空}, path=${ARTIFACT_PATH:-空}）"
        exit 1
    fi
    log_info "解析到整体包: ${ARTIFACT_PATH}（sha256 ${ARTIFACT_SHA256:0:12}...）"

    rm -f "${_SCRIPT_TEMP_FILE:-}"
    trap - EXIT
}

# 下载整体包并 sha256 校验、解压（幂等：sha256 一致则跳过下载）
download_and_extract_artifact() {
    local tarball_url="${HUB_URL%/}/static/${ARTIFACT_PATH}"
    local local_stamp="${INSTALL_DIR}/.artifact-sha256"

    if [[ -f "${INSTALL_DIR}/bin/opc-agent" ]] && [[ -f "$local_stamp" ]] \
        && [[ "$(cat "$local_stamp")" == "$ARTIFACT_SHA256" ]]; then
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

    log_info "解压到 ${INSTALL_DIR} ..."
    mkdir -p "$INSTALL_DIR"
    tar -xzf "$_SCRIPT_TEMP_FILE" --strip-components=1 -C "$INSTALL_DIR"
    chmod +x "${INSTALL_DIR}/bin/opc-agent" "${INSTALL_DIR}/bin/tilacode"
    echo "$ARTIFACT_SHA256" > "$local_stamp"

    rm -f "${_SCRIPT_TEMP_FILE:-}"
    trap - EXIT
}

# 二进制冒烟：--version 快速路径不加载 .env 不建连
smoke_test() {
    log_info "二进制冒烟测试..."
    local version_out
    if ! version_out=$("${INSTALL_DIR}/bin/opc-agent" --version 2>&1); then
        log_error "opc-agent 二进制无法执行（glibc 过旧？可执行 ldd ${INSTALL_DIR}/bin/opc-agent 检查缺失库）"
        exit 1
    fi
    log_info "冒烟通过：${version_out}"
}

# 旧版（0.4.x 源码部署）升级迁移：保留节点身份 + 停用旧服务
migrate_legacy() {
    if [[ ! -d "$LEGACY_DIR" ]]; then
        return 0
    fi
    log_info "检测到旧版部署目录 ${LEGACY_DIR}，执行升级迁移..."

    local legacy_env="${LEGACY_DIR}/.env"
    if [[ -f "$legacy_env" ]]; then
        if [[ -z "$MIGRATED_CLIENT_ID" ]]; then
            MIGRATED_CLIENT_ID=$(grep -E "^AGENT_CLIENT_ID=" "$legacy_env" 2>/dev/null | cut -d'=' -f2- || true)
        fi
        if [[ -z "$AGENT_NAME" ]]; then
            AGENT_NAME=$(grep -E "^AGENT_NAME=" "$legacy_env" 2>/dev/null | cut -d'=' -f2- || true)
        fi
    fi

    if systemctl list-unit-files 2>/dev/null | grep -q "^${LEGACY_SERVICE}\.service"; then
        log_info "停止并禁用旧服务 ${LEGACY_SERVICE}..."
        systemctl stop "$LEGACY_SERVICE" 2>/dev/null || true
        systemctl disable "$LEGACY_SERVICE" 2>/dev/null || true
    fi
    if [[ -n "$MIGRATED_CLIENT_ID" ]]; then
        log_info "迁移节点身份: AGENT_CLIENT_ID=${MIGRATED_CLIENT_ID}（中枢节点记录保持不变）"
    fi
    log_info "旧目录 ${LEGACY_DIR} 已保留（可回滚；确认稳定后可手动清理）"
}

# 从 TILACODE_PORT_START 起递增探测可用端口（/dev/tcp 探测）
detect_available_port() {
    local port
    for port in $(seq "$TILACODE_PORT_START" "$TILACODE_PORT_END"); do
        if ! (echo > /dev/tcp/127.0.0.1/"$port") 2>/dev/null; then
            log_info "探测到可用端口: ${port}"
            echo "$port"
            return 0
        fi
    done
    log_error "在 ${TILACODE_PORT_START}-${TILACODE_PORT_END} 范围内未找到可用端口"
    exit 1
}

generate_agent_client_id() {
    local hostname suffix
    hostname=$(hostname)
    suffix=$(head /dev/urandom | tr -dc 'a-z0-9' | head -c 8 || true)
    echo "${hostname}-${suffix}"
}

# 生成随机通知 API Token（ADR-035，落盘 .env 并随首次注册上报中枢）
generate_api_token() {
    head /dev/urandom | tr -dc 'a-zA-Z0-9' | head -c 32 || true
}

# 生成 .env（幂等：已有 AGENT_CLIENT_ID/AGENT_NAME/API_TOKEN 保留；
# 优先级 --agent-name > 已有 .env > 旧版迁移 > 兜底生成）
write_env_file() {
    local env_file="${INSTALL_DIR}/.env"
    local agent_name=""
    local agent_client_id=""
    local api_token=""

    if [[ -f "$env_file" ]]; then
        agent_name=$(grep -E "^AGENT_NAME=" "$env_file" 2>/dev/null | cut -d'=' -f2- || true)
        agent_client_id=$(grep -E "^AGENT_CLIENT_ID=" "$env_file" 2>/dev/null | cut -d'=' -f2- || true)
        # API_TOKEN 不覆盖：中枢已按首次注册的 token 反连，更换会导致 401（ADR-035）
        api_token=$(grep -E "^API_TOKEN=" "$env_file" 2>/dev/null | cut -d'=' -f2- || true)
    fi

    if [[ -n "$AGENT_NAME" ]]; then
        agent_name="$AGENT_NAME"
        log_info "使用 --agent-name 指定值: ${agent_name}"
    elif [[ -z "$agent_name" ]]; then
        log_info "未指定 AGENT_NAME，将由 Agent 启动时按 OS 平台+版本生成"
    else
        log_info "检测到已有 AGENT_NAME=${agent_name}，保留原值"
    fi

    if [[ -z "$agent_client_id" ]] && [[ -n "$MIGRATED_CLIENT_ID" ]]; then
        agent_client_id="$MIGRATED_CLIENT_ID"
    fi
    if [[ -z "$agent_client_id" ]]; then
        agent_client_id=$(generate_agent_client_id)
    else
        log_info "检测到已有 AGENT_CLIENT_ID=${agent_client_id}，保留原值"
    fi

    if [[ -z "$api_token" ]]; then
        api_token=$(generate_api_token)
        log_info "生成通知 API Token: ${api_token:0:8}...（随首次注册上报中枢，ADR-035）"
    else
        log_info "检测到已有 API_TOKEN，保留原值（中枢按首次 token 反连）"
    fi

    log_info "生成 .env 配置文件..."
    cat > "$env_file" <<EOF
HUB_API_URL=${HUB_URL}
AGENT_REGISTRATION_TOKEN=${REGISTRATION_TOKEN}
AGENT_CLIENT_ID=${agent_client_id}
AGENT_TYPE=code
HEARTBEAT_INTERVAL_SECONDS=5
TILACODE_BINARY_PATH=${INSTALL_DIR}/bin/tilacode
TILACODE_PORT=${TILACODE_PORT}
TILACODE_HOST=127.0.0.1
TILACODE_WORKSPACE_ROOT=${INSTALL_DIR}/workspace
TILACODE_HEALTH_CHECK_INTERVAL=30
TILACODE_AUTO_RESTART=true
AGENT_API_PORT=${AGENT_API_PORT}
API_TOKEN=${api_token}
EOF

    if [[ -n "$agent_name" ]]; then
        echo "AGENT_NAME=${agent_name}" >> "$env_file"
    fi
    # ADR-035：通知 API 绑定内网地址（探测到 IP 时绑定，避免公网暴露；
    # 绑定失败时 Agent 侧自动降级跳过，不影响主流程）
    if [[ -n "$STATIC_IP" ]]; then
        echo "AGENT_API_HOST=${STATIC_IP}" >> "$env_file"
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

setup_path() {
    local profile_file="/etc/profile.d/opc-agent-code.sh"
    log_info "配置 PATH..."
    cat > "$profile_file" <<EOF
# OPC Code Agent - binaries path
export PATH=\$PATH:${INSTALL_DIR}/bin
EOF
    chmod +x "$profile_file"
    export PATH="$PATH:${INSTALL_DIR}/bin"
    log_info "PATH 已配置到 ${profile_file}"
}

main() {
    parse_args "$@"
    require_root
    validate_args
    detect_platform
    check_system_deps

    # ADR-035：静态 IP 固化交互（y=按显示值 / n=手动输入）+ 防火墙放行通知端口
    # 网络检测/固化/放行失败均不阻断安装（Agent 并行探测兜底重连）
    if detect_network_info; then
        if prompt_static_ip; then
            apply_static_ip || true
        fi
        open_firewall_port "$AGENT_API_PORT" || true
    fi

    migrate_legacy
    resolve_artifact
    download_and_extract_artifact
    smoke_test
    setup_path

    TILACODE_PORT=$(detect_available_port)

    local agent_name
    agent_name=$(write_env_file)
    install_systemd_service

    log_info "OPC Code Agent 安装完成并启动成功。"
    log_info "节点名称: ${agent_name:-（启动时自动生成）}"
    log_info "tilacode 端口: ${TILACODE_PORT}"
    log_info "通知 API 端口: ${AGENT_API_PORT}（中枢重启主动反连，ADR-035）"
    log_info "服务状态: systemctl status ${SERVICE_NAME}"
}

MIGRATED_CLIENT_ID=""
main "$@"

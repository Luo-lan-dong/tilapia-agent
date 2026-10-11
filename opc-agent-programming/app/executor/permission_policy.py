"""节点侧权限策略（ADR-061 M2b）：应用、回读与判定。

**已确认决策（用户 2026-09-28 裁决）**
- Q1 中枢**全局单条策略**随 ``hub.task_dispatch`` 下发 → 本模块负责落到工作区 tilacode 配置。
- Q2 **派发即权威 + 本地为缓存**：payload 带策略就覆盖写；payload 未带就沿用本地已有配置；
  本地也没有（首次）时用模板最严值（``unattended`` + 空白名单）。
  **非法 payload 一律按最严处理**（fail-closed）。
- Q3 仍收到权限询问且不在白名单内 → 回复 ``reject`` 并带**可读原因**（见 :func:`deny_reason`）。

**安全不变量（有测试守卫）**
1. 落地配置里**绝不出现** blanket allow（工具键 ``"*"`` 对应效果 ``"allow"``，或
   ``{"*": {"*": "allow"}}``，或整个 ``permission`` 值为字符串 ``"allow"``）——这类规则经
   V1→V2 迁移后会被追加到 tilacode 内置护栏数组末尾，而求值是 ``findLast``（最后匹配者胜），
   会把「工作区外访问 / ``.env`` 读取 / tool-output 白名单」等护栏**全部覆盖成 allow**
   （ADR-061 M2a 实测）。本模块在应用策略时主动清除它们（含历史残留）。
2. 判定用 :func:`is_allowed` 镜像 tilacode ``Wildcard.match``；宁可多拒不可少拒。

**与中枢 `opc-central-dispatch-hub/app/services/permission_policy_service.py` 的关系**：
两处各自实现同一套归一化规则（两个独立产品、无共享包），**改动必须同步**；
中枢负责「下发什么」，节点负责「怎么落地与怎么判定」。
"""

import logging
import re
import sys
from typing import Any

logger = logging.getLogger(__name__)

#: 缺省（最严）模式
DEFAULT_MODE = "unattended"
#: 允许的模式（与 tilacode `permission_mode` 一致）
ALLOWED_MODES = ("interactive", "unattended")
#: 白名单条目数上限（与中枢一致；超出丢弃）
MAX_ALLOW_ENTRIES = 200
MAX_ACTION_CHARS = 64
MAX_RESOURCE_CHARS = 512
#: blanket allow 的组合：工具键 ``"*"`` + 资源 ``"*"``
BLANKET_ACTION = "*"
BLANKET_RESOURCE = "*"

#: tilacode `Wildcard.match` 的转义字符集（见 `packages/core/src/util/wildcard.ts`）
_WILDCARD_ESCAPE = re.compile(r"[.+^${}()|[\]\\]")


def default_policy() -> dict[str, Any]:
    """返回最严策略（``unattended`` + 空白名单）。"""
    return {"mode": DEFAULT_MODE, "allow": []}


def normalize_policy(raw: Any) -> dict[str, Any] | None:
    """归一化中枢下发的策略。

    Args:
        raw: ``payload.permission_policy`` 的原始值。

    Returns:
        - ``None``：payload **未提供**策略 → 调用方沿用本地已有配置（缓存语义）；
        - 归一化策略字典：合法输入，或**非法输入按最严归一**（fail-closed）。
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        logger.warning(
            "permission_policy_invalid_shape",
            extra={"event": "permission_policy_invalid_shape", "value_type": type(raw).__name__},
        )
        return default_policy()

    mode = raw.get("mode")
    normalized_mode = mode if mode in ALLOWED_MODES else DEFAULT_MODE
    if mode is not None and normalized_mode != mode:
        logger.warning(
            "permission_policy_invalid_mode",
            extra={"event": "permission_policy_invalid_mode", "mode": str(mode)[:32]},
        )

    allow: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    dropped_blanket = 0
    entries = raw.get("allow")
    if isinstance(entries, list):
        for item in entries:
            if not isinstance(item, dict):
                continue
            action = item.get("action")
            resource = item.get("resource")
            if not isinstance(action, str) or not isinstance(resource, str):
                continue
            action = action.strip()
            resource = resource.strip()
            if not action or not resource:
                continue
            if action == BLANKET_ACTION and resource == BLANKET_RESOURCE:
                dropped_blanket += 1
                continue
            if len(action) > MAX_ACTION_CHARS or len(resource) > MAX_RESOURCE_CHARS:
                continue
            key = (action, resource)
            if key in seen:
                continue
            seen.add(key)
            allow.append({"action": action, "resource": resource})
            if len(allow) >= MAX_ALLOW_ENTRIES:
                break

    if dropped_blanket:
        logger.warning(
            "permission_policy_blanket_allow_dropped",
            extra={"event": "permission_policy_blanket_allow_dropped", "dropped": dropped_blanket},
        )
    return {"mode": normalized_mode, "allow": allow}


def apply_to_config(config: dict[str, Any], policy: dict[str, Any]) -> bool:
    """把策略写入工作区 tilacode 配置（就地修改）。

    只追加 allow 条目，并清除一切形式的 blanket allow。

    Args:
        config: 工作区 ``.tilacode/tilacode.jsonc`` 解析后的字典（就地修改）。
        policy: :func:`normalize_policy` 的返回值。

    Returns:
        True 表示配置确有变化（调用方据此决定是否落盘）。
    """
    changed = False
    mode = policy.get("mode")
    mode = mode if mode in ALLOWED_MODES else DEFAULT_MODE
    if config.get("permission_mode") != mode:
        config["permission_mode"] = mode
        changed = True

    raw_permission = config.get("permission")
    if isinstance(raw_permission, str):
        # 整体字符串形式等价于 {"*": <effect>}；"allow" 即 blanket allow → 丢弃
        logger.warning(
            "permission_config_blanket_allow_removed",
            extra={"event": "permission_config_blanket_allow_removed", "effect": raw_permission},
        )
        permission: dict[str, Any] = {} if raw_permission == "allow" else {"*": raw_permission}
        if raw_permission == "allow":
            changed = True
    elif isinstance(raw_permission, dict):
        permission = dict(raw_permission)
    else:
        permission = {}

    # 历史残留 blanket allow：工具键 "*" 为 "allow" 字符串，或 {"*": "allow"} 内层
    tool_wildcard = permission.get(BLANKET_ACTION)
    if tool_wildcard == "allow":
        permission.pop(BLANKET_ACTION, None)
        changed = True
        logger.warning(
            "permission_config_blanket_allow_removed",
            extra={"event": "permission_config_blanket_allow_removed", "effect": "allow"},
        )
    elif isinstance(tool_wildcard, dict) and tool_wildcard.get(BLANKET_RESOURCE) == "allow":
        merged_tool = dict(tool_wildcard)
        merged_tool.pop(BLANKET_RESOURCE, None)
        permission[BLANKET_ACTION] = merged_tool
        changed = True
        logger.warning(
            "permission_config_blanket_allow_removed",
            extra={"event": "permission_config_blanket_allow_removed", "effect": "allow"},
        )

    for entry in policy.get("allow") or []:
        action = entry.get("action")
        resource = entry.get("resource")
        if not isinstance(action, str) or not isinstance(resource, str):
            continue
        current = permission.get(action)
        if isinstance(current, dict):
            merged: dict[str, Any] = dict(current)
        elif isinstance(current, str):
            # 保留既有整体效果：先写 "*"，再追加资源条目 → findLast 命中资源条目
            merged = {BLANKET_RESOURCE: current}
        else:
            merged = {}
        if merged.get(resource) != "allow":
            merged[resource] = "allow"
            changed = True
        permission[action] = merged

    if permission:
        config["permission"] = permission
    return changed


def effective_policy_from_config(config: dict[str, Any]) -> dict[str, Any]:
    """从工作区配置回读**实际生效**的策略（判定与审计的口径来源）。"""
    mode = config.get("permission_mode")
    normalized_mode = mode if mode in ALLOWED_MODES else DEFAULT_MODE
    allow: list[dict[str, str]] = []
    permission = config.get("permission")
    if isinstance(permission, str):
        if permission == "allow":
            allow.append({"action": BLANKET_ACTION, "resource": BLANKET_RESOURCE})
    elif isinstance(permission, dict):
        for action, rule in permission.items():
            if not isinstance(action, str):
                continue
            if isinstance(rule, str):
                if rule == "allow":
                    allow.append({"action": action, "resource": BLANKET_RESOURCE})
                continue
            if not isinstance(rule, dict):
                continue
            for resource, effect in rule.items():
                if isinstance(resource, str) and effect == "allow":
                    allow.append({"action": action, "resource": resource})
    return {"mode": normalized_mode, "allow": allow}


def wildcard_match(value: str, pattern: str) -> bool:
    """镜像 tilacode ``Wildcard.match``（供节点自行判定）。

    与 ``fnmatch`` 不同：反斜杠统一为 ``/``；``*``/``?`` 语义同 glob；Windows 下
    **忽略大小写**；pattern 形如 ``"git *"`` 时可匹配裸 ``git``（``( .*)?``）。
    """
    normalized = value.replace("\\", "/")
    escaped = _WILDCARD_ESCAPE.sub(lambda m: "\\" + m.group(0), pattern.replace("\\", "/"))
    escaped = escaped.replace("*", ".*").replace("?", ".")
    if escaped.endswith(" .*"):
        escaped = escaped[:-3] + "( .*)?"
    flags = re.DOTALL | (re.IGNORECASE if sys.platform == "win32" else 0)
    return re.match("^" + escaped + "$", normalized, flags) is not None


def is_allowed(policy: dict[str, Any], action: str, resource: str) -> bool:
    """判断单个 ``action:resource`` 是否命中白名单。

    严格镜像 :func:`wildcard_match`（即 tilacode ``Wildcard.match``）语义：
    匹配不上就是不允许，**不做任何放宽**（宁可多拒不可少拒）。无白名单 → 一律 False。
    """
    if not isinstance(action, str) or not isinstance(resource, str):
        return False
    for entry in policy.get("allow") or []:
        if not isinstance(entry, dict):
            continue
        pattern_action = entry.get("action")
        pattern_resource = entry.get("resource")
        if not isinstance(pattern_action, str) or not isinstance(pattern_resource, str):
            continue
        # 快路径：完全相等（中枢逐字下发时的最常见场景）
        if pattern_action == action and pattern_resource == resource:
            return True
        if wildcard_match(action, pattern_action) and wildcard_match(resource, pattern_resource):
            return True
    return False


def deny_reason(action: str, resource: str) -> str:
    """构造回复给 tilacode 的**可读拒绝原因**（Q3）。"""
    return (
        f"权限被拒绝：{action}:{resource} 不在中枢下发的权限白名单内。"
        "工作区内操作不受影响；若确需访问工作区外资源，应由中枢在权限策略中显式加入白名单后重试，"
        "请不要重复尝试同一路径或命令。"
    )

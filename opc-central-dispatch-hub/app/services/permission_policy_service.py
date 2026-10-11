"""权限策略（ADR-061 M2b）：中枢侧的唯一定义与下发前归一化。

**已确认决策（用户 2026-09-28 裁决）**
- Q1 **全局单条策略**：真源 = ``system_settings.permission_policy``（单行），派发时随
  ``hub.task_dispatch.payload.permission_policy`` 下发，节点以其为权威。
- Q2 **节点本地缓存 + 派发即权威**：节点把策略写进工作区 tilacode 配置（配置文件天然
  即缓存）；中枢未配置（NULL）或节点本地也没有时，双方都按**最严**（``unattended`` + 空白名单）执行。
- Q3 非白名单的权限询问：节点回复 ``reject`` 并带可读原因（见节点侧 ``permission_policy``）。
- Q4 审计：中枢记「策略下发」（并入既有 dispatch 审计记录），节点记每次自动判定。

**安全不变量（有测试守卫，不得回退）**
1. 策略**只能表达 allow 白名单**，绝不产出 ``{"*": "allow"}`` 这类 blanket allow。
   该规则经 V1→V2 迁移后会追加到 tilacode 内置护栏数组**末尾**，而求值是 ``findLast``
   （最后匹配者胜）→ 会把「工作区外访问 / ``.env`` 读取 / tool-output 白名单」等内置护栏
   全部覆盖成 allow（ADR-061 M2a 实测，见 ``workspace.py`` 模板注释）。
2. 非法、缺失或部分非法的策略一律**按最严归一**（``unattended`` + 空白名单），
   任何解析失败都不放宽权限。
"""

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system_settings import SystemSettings

logger = logging.getLogger(__name__)

#: 缺省（最严）模式：无人值守下 tilacode 把「本该询问人」的请求一律按拒绝处理
DEFAULT_MODE = "unattended"
#: 允许的模式（与 tilacode `permission_mode` 的 Literal 取值一致）
ALLOWED_MODES = ("interactive", "unattended")
#: 白名单条目数与字段长度上限（防单行设置被撑爆；超出部分丢弃并告警）
MAX_ALLOW_ENTRIES = 200
MAX_ACTION_CHARS = 64
MAX_RESOURCE_CHARS = 512
#: blanket allow 的组合（action="*" 且 resource="*"）——安全红线，永不产出
BLANKET_ACTION = "*"
BLANKET_RESOURCE = "*"


def default_policy() -> dict[str, Any]:
    """返回最严策略（``unattended`` + 空白名单）。"""
    return {"mode": DEFAULT_MODE, "allow": []}


def normalize_policy(raw: Any) -> dict[str, Any]:
    """把任意输入归一为合法策略；无法识别时回退最严。

    Args:
        raw: 数据库 JSON 列、API 请求体或节点上报的任意值。

    Returns:
        ``{"mode": "unattended"|"interactive", "allow": [{"action", "resource"}, ...]}``；
        保证**不含** blanket allow 条目。
    """
    if not isinstance(raw, dict):
        if raw is not None:
            logger.warning(
                "permission_policy_invalid_shape",
                extra={
                    "event": "permission_policy_invalid_shape",
                    "value_type": type(raw).__name__,
                },
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
        # 安全红线：blanket allow 会被丢弃并留痕（可能来自误配置或旧版本数据）
        logger.warning(
            "permission_policy_blanket_allow_dropped",
            extra={
                "event": "permission_policy_blanket_allow_dropped",
                "dropped": dropped_blanket,
            },
        )
    return {"mode": normalized_mode, "allow": allow}


async def load_policy(session: AsyncSession) -> dict[str, Any]:
    """读取全局权限策略（未配置时返回最严策略）。"""
    from sqlalchemy import select

    result = await session.execute(
        select(SystemSettings).order_by(SystemSettings.created_at.asc()).limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return default_policy()
    return normalize_policy(row.permission_policy)


def audit_summary(policy: Any) -> dict[str, Any]:
    """构造「策略下发」审计摘要（并入既有 dispatch 记录，Q4）。

    只记条目数与白名单本体（策略本身不含凭据，可安全入审计表）。
    入参可为任意值（含 ``None``/线上 payload 原样回读），内部先归一，
    保证审计摘要在任何输入下都可用。
    """
    normalized = normalize_policy(policy)
    return {
        "permission_policy_mode": normalized["mode"],
        "permission_policy_allow_count": len(normalized["allow"]),
        "permission_policy_allow": normalized["allow"],
    }


async def save_policy(session: AsyncSession, raw: Any) -> dict[str, Any]:
    """归一化并保存全局权限策略（设置行不存在时创建），返回保存后的策略。"""
    from sqlalchemy import select

    policy = normalize_policy(raw)
    result = await session.execute(
        select(SystemSettings).order_by(SystemSettings.created_at.asc()).limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = SystemSettings()
        session.add(row)
    row.permission_policy = policy
    await session.commit()
    await session.refresh(row)
    logger.info(
        "permission_policy_saved",
        extra={
            "event": "permission_policy_saved",
            "mode": policy["mode"],
            "allow_count": len(policy["allow"]),
        },
    )
    return policy

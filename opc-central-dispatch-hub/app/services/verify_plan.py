"""验收判据 `verify.json` 的归一化与校验（ADR-061 M4）。

**判据来源（用户已定优先级）**：`verify.json` > 项目自带配置（package.json scripts /
pyproject / Makefile）> 按文件类型推导；**无可用检查必须显式上报「无法验证」**。

**必过层规则（用户已定）**：L0/L1 **必过**；L2 **存在即必过**；L3/L4 **仅当判据显式标注必过**
（本模块在归一化时把该规则**固化为 `must_pass` 字段**，节点执行器据此判定完成闸）。

**每层超时（用户已定）**：L0 30s / L1 60s / L2 5min / L3 10min / L4 20min；单个 check 可覆盖，
但被限制在 `[1, MAX_CHECK_TIMEOUT_S]` 内。

本模块与节点侧 `app/executor/verify_runner.py` 的同名规则**必须同步**（两个独立产品、无共享包）。
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: 分层封闭枚举（L0 编译/语法 → L4 端到端）
VERIFY_LAYERS = ("L0", "L1", "L2", "L3", "L4")
#: 各层默认超时（秒），用户已定
LAYER_TIMEOUTS: dict[str, int] = {"L0": 30, "L1": 60, "L2": 300, "L3": 600, "L4": 1200}
#: 单条 check 超时上限（防止把看门狗拖死）
MAX_CHECK_TIMEOUT_S = 3600
#: 判据条数上限（超出丢弃并告警）
MAX_CHECKS = 30
#: 命令/名称长度上限
MAX_CMD_CHARS = 2000
MAX_NAME_CHARS = 200
#: 默认必过层（L2 存在即必过；L3/L4 需显式标注，见模块 docstring）
DEFAULT_MUST_PASS = {"L0": True, "L1": True, "L2": True, "L3": False, "L4": False}


def default_must_pass(layer: str) -> bool:
    """返回某层的默认必过标记（L3/L4 默认非必过）。"""
    return DEFAULT_MUST_PASS.get(layer, False)


def normalize_verify_plan(raw: Any) -> dict[str, Any] | None:
    """把 LLM 产出的判据归一为可执行计划；无有效检查时返回 ``None``。

    返回结构：``{"checks": [{"layer","name","cmd","cwd","timeout_s","must_pass"}]}``；
    调用方（中枢）把 ``None`` 视为**没有判据**，节点端会据此上报「无法验证」。
    """
    if not isinstance(raw, dict):
        if raw is not None:
            logger.warning(
                "verify_plan_invalid_shape",
                extra={"event": "verify_plan_invalid_shape", "value_type": type(raw).__name__},
            )
        return None

    entries = raw.get("checks")
    if not isinstance(entries, list):
        return None

    checks: list[dict[str, Any]] = []
    dropped = 0
    for item in entries:
        if not isinstance(item, dict):
            dropped += 1
            continue
        layer = str(item.get("layer") or "").strip().upper()
        if layer not in VERIFY_LAYERS:
            dropped += 1
            continue
        cmd = str(item.get("cmd") or "").strip()
        if not cmd or len(cmd) > MAX_CMD_CHARS:
            dropped += 1
            continue
        name = str(item.get("name") or "").strip()[:MAX_NAME_CHARS] or f"{layer} 检查"
        cwd = item.get("cwd")
        cwd = str(cwd).strip() if isinstance(cwd, str) and cwd.strip() else "."
        must_pass = item.get("must_pass")
        must_pass = bool(must_pass) if isinstance(must_pass, bool) else default_must_pass(layer)
        timeout_raw = item.get("timeout_s")
        timeout_s = LAYER_TIMEOUTS[layer]
        if isinstance(timeout_raw, (int, float)) and timeout_raw > 0:
            timeout_s = min(int(timeout_raw), MAX_CHECK_TIMEOUT_S)
        checks.append(
            {
                "layer": layer,
                "name": name,
                "cmd": cmd,
                "cwd": cwd,
                "timeout_s": timeout_s,
                "must_pass": must_pass,
            }
        )
        if len(checks) >= MAX_CHECKS:
            break

    if dropped:
        logger.warning(
            "verify_plan_checks_dropped",
            extra={"event": "verify_plan_checks_dropped", "dropped": dropped},
        )
    if not checks:
        return None
    # 按层稳定排序，便于执行顺序与展示
    checks.sort(key=lambda item: VERIFY_LAYERS.index(item["layer"]))
    return {"checks": checks}


def must_pass_checks(plan: dict[str, Any] | None) -> list[dict[str, Any]]:
    """取必过检查（完成闸只认这些）。"""
    if not isinstance(plan, dict):
        return []
    return [item for item in plan.get("checks") or [] if item.get("must_pass")]


def summarize_plan(plan: dict[str, Any] | None) -> str:
    """生成人类可读的判据摘要（回传 LLM / 前端展示用）。"""
    if not isinstance(plan, dict) or not plan.get("checks"):
        return "未提供验收判据（将按「无法验证」处理）"
    lines = [
        f"- [{item['layer']}]{'必过' if item['must_pass'] else '参考'} {item['name']}：`{item['cmd']}`"
        for item in plan["checks"]
    ]
    return "\n".join(lines)

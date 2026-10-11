"""节点环境签名（ADR-061 M6，Q14）。

把本机环境压成一个**短哈希**，随心跳上报中枢（`agent_nodes.environment_signature`），
作为经验记忆**跨机复用**的匹配键：只有同环境的经验才会被派发前召回，
避免把 Windows 专属解法喂给 Linux 节点。

**签名纳入什么**（刻意取「粗粒度但稳定」）：
- 操作系统与架构（`platform.system()` / `machine()`）；
- Python 主次版本与平台标识（`sys.platform`）；
- 可用时补上 Node/Bun 主版本（前端类项目的关键差异源）。

**不纳入**：内核补丁版本、主机名、路径、任何凭据——它们只会让同构机器互相错过。
若运维需要把同构机器群**强制视为同环境**，可设环境变量 ``TILAPIA_ENV_SIGNATURE`` 覆盖。
"""

import hashlib
import logging
import os
import platform
import shutil
import subprocess
import sys
from functools import lru_cache

logger = logging.getLogger(__name__)

#: 签名长度（短哈希，够区分且便于目视比对）
SIGNATURE_LENGTH = 16
#: 环境变量覆盖（同构机器群可显式统一）
OVERRIDE_ENV = "TILAPIA_ENV_SIGNATURE"


def _runtime_major(binary: str) -> str:
    """取运行时主版本（如 node v20 / bun 1.1）；不可用时返回空串。"""
    path = shutil.which(binary)
    if not path:
        return ""
    try:
        completed = subprocess.run(  # noqa: S603 — 固定二进制名，非用户输入
            [path, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    raw = (completed.stdout or "").strip().lstrip("v")
    return raw.split(".")[0] if raw else ""


@lru_cache(maxsize=1)
def compute_environment_signature() -> str | None:
    """计算并缓存环境签名；任何异常都返回 ``None``（心跳照发，不阻断）。

    计算只在进程内做一次（``lru_cache``）：心跳频率高，重复探测没有意义。
    """
    override = os.environ.get(OVERRIDE_ENV)
    if override and override.strip():
        return override.strip()[:64]
    try:
        parts = [
            platform.system(),
            platform.machine(),
            f"py{platform.python_version()}",
            sys.platform,
            f"node{_runtime_major('node')}",
            f"bun{_runtime_major('bun')}",
        ]
    except Exception:  # noqa: BLE001 — 探测失败不影响节点运行
        logger.warning("environment_signature_probe_failed", exc_info=True)
        return None
    raw = "|".join(parts).lower()
    signature = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:SIGNATURE_LENGTH]
    logger.info(
        "environment_signature_ready",
        extra={"event": "environment_signature_ready", "environment_signature": signature},
    )
    return signature

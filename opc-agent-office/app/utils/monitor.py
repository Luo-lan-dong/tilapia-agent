"""Agent 节点系统资源监控采集。

参考 ``linux-python/services/monitor.py`` 的采集思路，为当前编程 Agent 提供
心跳上报所需的系统资源快照与综合负载得分。
"""

import logging
import platform
import time
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# 综合负载各维度权重
_LOAD_WEIGHTS = {
    "cpu": 0.35,
    "memory": 0.25,
    "disk_io": 0.15,
    "disk_space": 0.10,
    "load_avg": 0.10,
    "network": 0.05,
}

# 负载等级阈值
_LOAD_LEVELS = [
    (30, "idle"),
    (60, "normal"),
    (80, "high"),
    (100, "critical"),
]

# 网络/磁盘 IO 速率归一化上限（字节/秒），用于折算成 0~100 分
_MAX_IO_BYTES_PER_SEC = 100 * 1024 * 1024
_MAX_NET_BYTES_PER_SEC = 100 * 1024 * 1024

# 上一次 IO 采样状态，用于计算速率
_prev_disk_io: Any = None
_prev_net_io: Any = None
_prev_io_time: float | None = None

# 最近一次 collect_metrics 算出的综合负载得分（供准入自评复用，ADR-059 D1）
_last_load_score: float | None = None


def _level(score: float) -> str:
    """根据得分返回负载等级。"""
    for threshold, name in _LOAD_LEVELS:
        if score <= threshold:
            return name
    return "critical"


def _try_psutil_import() -> Any:
    """延迟导入 psutil；缺失时返回 None 并由调用方降级处理。"""
    try:
        import psutil

        return psutil
    except ImportError:
        return None


def _get_cpu(psutil: Any) -> dict[str, Any]:
    """采集 CPU 使用率与核心数。"""
    # 先预热，避免首次返回 0.0
    psutil.cpu_percent(interval=None)
    time.sleep(0.3)
    total = psutil.cpu_percent(interval=None)
    per_core = psutil.cpu_percent(interval=None, percpu=True)
    return {
        "percent": round(total, 1),
        "count_physical": psutil.cpu_count(logical=False),
        "count_logical": psutil.cpu_count(logical=True),
        "per_core": [round(v, 1) for v in per_core],
    }


def _get_memory(psutil: Any) -> dict[str, Any]:
    """采集内存与交换空间使用情况。"""
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    return {
        "total_gb": round(mem.total / 1024**3, 2),
        "used_gb": round(mem.used / 1024**3, 2),
        "available_gb": round(mem.available / 1024**3, 2),
        "percent": mem.percent,
        "swap_total_gb": round(swap.total / 1024**3, 2),
        "swap_used_gb": round(swap.used / 1024**3, 2),
        "swap_percent": swap.percent,
    }


def _get_disk(psutil: Any) -> dict[str, Any]:
    """采集根分区磁盘使用情况。"""
    disk = psutil.disk_usage("/")
    return {
        "total_gb": round(disk.total / 1024**3, 2),
        "used_gb": round(disk.used / 1024**3, 2),
        "free_gb": round(disk.free / 1024**3, 2),
        "percent": disk.percent,
    }


def _get_network(psutil: Any) -> dict[str, Any]:
    """采集网络累计流量。"""
    net = psutil.net_io_counters()
    return {
        "bytes_sent": net.bytes_sent,
        "bytes_recv": net.bytes_recv,
        "packets_sent": net.packets_sent,
        "packets_recv": net.packets_recv,
        "mb_sent": round(net.bytes_sent / 1024**2, 2),
        "mb_recv": round(net.bytes_recv / 1024**2, 2),
    }


def _get_load_average(psutil: Any) -> dict[str, float]:
    """采集系统 1/5/15 分钟平均负载。"""
    load1, load5, load15 = psutil.getloadavg()
    return {
        "1m": round(load1, 2),
        "5m": round(load5, 2),
        "15m": round(load15, 2),
    }


def _calc_io_speeds_and_scores(psutil: Any) -> dict[str, float]:
    """计算磁盘 IO 与网络速率，并折算成负载得分。

    需要维护上一次采样状态以计算差值；首次调用时所有速率与得分均为 0.0。
    """
    global _prev_disk_io, _prev_net_io, _prev_io_time

    now = time.time()
    result = {
        "disk_read_speed_mb": 0.0,
        "disk_write_speed_mb": 0.0,
        "net_send_speed_mb": 0.0,
        "net_recv_speed_mb": 0.0,
        "disk_io_score": 0.0,
        "net_score": 0.0,
    }

    disk_io = psutil.disk_io_counters()
    net_io = psutil.net_io_counters()

    if _prev_disk_io is not None and _prev_net_io is not None and _prev_io_time is not None:
        dt = now - _prev_io_time
        if dt > 0:
            read_speed = (disk_io.read_bytes - _prev_disk_io.read_bytes) / dt
            write_speed = (disk_io.write_bytes - _prev_disk_io.write_bytes) / dt
            result["disk_read_speed_mb"] = round(read_speed / 1024**2, 2)
            result["disk_write_speed_mb"] = round(write_speed / 1024**2, 2)
            disk_io_score = min((read_speed + write_speed) / _MAX_IO_BYTES_PER_SEC * 100, 100)
            result["disk_io_score"] = disk_io_score

            recv_speed = (net_io.bytes_recv - _prev_net_io.bytes_recv) / dt
            send_speed = (net_io.bytes_sent - _prev_net_io.bytes_sent) / dt
            result["net_send_speed_mb"] = round(send_speed / 1024**2, 2)
            result["net_recv_speed_mb"] = round(recv_speed / 1024**2, 2)
            net_score = min((recv_speed + send_speed) / _MAX_NET_BYTES_PER_SEC * 100, 100)
            result["net_score"] = net_score

    _prev_disk_io = disk_io
    _prev_net_io = net_io
    _prev_io_time = now

    return result


def get_available_memory_gb() -> float | None:
    """获取当前**绝对可用内存**（GB）；psutil 不可用或读取失败时返回 None。

    ADR-059 D1：准入自评改用绝对可用内存而非内存百分比——百分比的分母随机器
    规格变化，4G 机器 82.7%（仅剩 ~0.7G）会与 8 核机器 43%（剩 2.3G）不可比，
    小内存机器被系统性低估风险。``psutil.virtual_memory().available`` 由内核
    直接给出可用量，天然平滑，不存在瞬时抖动问题。
    """
    psutil = _try_psutil_import()
    if psutil is None:
        return None
    try:
        return round(psutil.virtual_memory().available / 1024**3, 2)
    except Exception:
        logger.warning("available_memory_read_failed", exc_info=True)
        return None


def get_load_score() -> float | None:
    """获取最近一次**综合负载得分**（0~100）；未采集过时返回 None。

    ADR-059 D1：准入自评的负载维度与中枢同口径——复用心跳 ``collect_metrics``
    已算出的 ``load_score``（含 CPU/内存/IO/磁盘/网络加权），避免节点与中枢
    各自造一套评分导致结论不一致。心跳每 ``heartbeat_interval_seconds`` 刷新一次，
    因此该值天然平滑（不反映亚秒级波动，正合准入判定需要）。
    """
    score = _last_load_score
    return round(score, 1) if score is not None else None


def get_resource_usage() -> dict[str, float | None]:
    """获取当前 CPU/内存实时占用百分比（非阻塞）。

    首次调用或 psutil 不可用时可能返回 ``None``；调用方应按「缺省不拒绝」
    处理，避免误伤无 metrics 的环境。

    Returns:
        {"cpu_percent": float|None, "memory_percent": float|None}
    """
    psutil = _try_psutil_import()
    if psutil is None:
        return {"cpu_percent": None, "memory_percent": None}

    try:
        # cpu_percent(interval=None) 非阻塞，依赖 collect_metrics 周期性预热
        cpu_percent = psutil.cpu_percent(interval=None)
        memory_percent = psutil.virtual_memory().percent
        return {
            "cpu_percent": round(cpu_percent, 1),
            "memory_percent": round(memory_percent, 1),
        }
    except Exception:
        logger.warning("resource_usage_read_failed", exc_info=True)
        return {"cpu_percent": None, "memory_percent": None}


def collect_metrics() -> dict[str, Any]:
    """采集当前节点系统资源监控快照。

    Returns:
        包含 CPU、内存、磁盘、网络、系统负载与综合负载得分的字典；
        若当前平台无法导入 psutil 或非 Linux，则返回仅包含 platform 与
        timestamp 的降级快照。
    """
    psutil = _try_psutil_import()
    if psutil is None:
        logger.warning("psutil_not_available", extra={"event": "psutil_not_available"})
        return {
            "platform": platform.platform(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    cpu = _get_cpu(psutil)
    memory = _get_memory(psutil)
    disk = _get_disk(psutil)
    network = _get_network(psutil)
    load_average = _get_load_average(psutil)
    io_metrics = _calc_io_speeds_and_scores(psutil)

    cpu_score = cpu["percent"]
    mem_score = memory["percent"]
    disk_score = disk["percent"]

    cpu_count = psutil.cpu_count() or 1
    load_score = min((load_average["1m"] / cpu_count) * 100, 100)

    network["send_speed_mb"] = io_metrics["net_send_speed_mb"]
    network["recv_speed_mb"] = io_metrics["net_recv_speed_mb"]

    total = (
        cpu_score * _LOAD_WEIGHTS["cpu"]
        + mem_score * _LOAD_WEIGHTS["memory"]
        + io_metrics["disk_io_score"] * _LOAD_WEIGHTS["disk_io"]
        + disk_score * _LOAD_WEIGHTS["disk_space"]
        + load_score * _LOAD_WEIGHTS["load_avg"]
        + io_metrics["net_score"] * _LOAD_WEIGHTS["network"]
    )

    # ADR-059 D1：缓存综合负载得分，供接单前准入自评复用（与中枢同口径）
    global _last_load_score
    _last_load_score = total

    return {
        "hostname": platform.node(),
        "platform": platform.platform(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "cpu": cpu,
        "memory": memory,
        "disk": disk,
        "network": network,
        "load_average": load_average,
        "load_score": {
            "score": round(total, 1),
            "level": _level(total),
            "weights": _LOAD_WEIGHTS,
            "breakdown": {
                "cpu": {"score": round(cpu_score, 1), "weight": _LOAD_WEIGHTS["cpu"]},
                "memory": {
                    "score": round(mem_score, 1),
                    "weight": _LOAD_WEIGHTS["memory"],
                },
                "disk_io": {
                    "score": round(io_metrics["disk_io_score"], 1),
                    "weight": _LOAD_WEIGHTS["disk_io"],
                },
                "disk_space": {
                    "score": round(disk_score, 1),
                    "weight": _LOAD_WEIGHTS["disk_space"],
                },
                "load_avg": {
                    "score": round(load_score, 1),
                    "weight": _LOAD_WEIGHTS["load_avg"],
                },
                "network": {
                    "score": round(io_metrics["net_score"], 1),
                    "weight": _LOAD_WEIGHTS["network"],
                },
            },
        },
    }

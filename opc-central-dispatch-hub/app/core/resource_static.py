"""Agent 资源静态服务（ADR-022）。

``/static`` 由三处候选目录按优先级提供：

1. 运行期缓存 ``data/resources/``（云端同步/zip 导入产物，运行期动态出现）
2. 安装目录内置基线 ``resource_root/static/``（过渡期兼容/离线回退）
3. 均无则 404（Agent 安装功能降级，其余功能正常）

用 catch-all 函数路由（而非 StaticFiles mount）实现运行期动态 fallback：
缓存目录在首次同步后才出现，静态 mount 无法表达这一回退顺序。
"""

import logging
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

logger = logging.getLogger(__name__)

STATIC_PREFIX = "/static"


def resolve_baseline_static_dir() -> Path | None:
    """内置基线 static 目录：frozen 时为 _MEIPASS/static，源码运行为仓库 static/。"""
    meipass = getattr(sys, "_MEIPASS", None)
    base = Path(meipass) if meipass else Path(__file__).resolve().parent.parent.parent
    candidate = base / "static"
    return candidate if candidate.is_dir() else None


def _resolve_under(base: Path, relative: str) -> Path | None:
    """解析 base/relative 并确保结果不越出 base（防目录穿越）；不存在返回 None。"""
    candidate = (base / relative).resolve()
    if base.resolve() != candidate and base.resolve() not in candidate.parents:
        return None
    return candidate if candidate.is_file() else None


def register_resource_static(app: FastAPI, cache_dir: Path, baseline_dir: Path | None) -> None:
    """注册 /static catch-all 路由（缓存优先 → 内置基线回退）。

    必须在 SPA fallback 之前注册；API 路由按更具体的 /api 前缀匹配不受影响。
    """

    @app.get(STATIC_PREFIX + "/{file_path:path}", include_in_schema=False)
    async def serve_resource_static(file_path: str) -> FileResponse:
        """按缓存 → 基线顺序查找静态资源文件。"""
        if file_path:
            for base in (cache_dir, baseline_dir):
                if base is None:
                    continue
                found = _resolve_under(base, file_path)
                if found is not None:
                    return FileResponse(found)
        raise HTTPException(status_code=404, detail="Static resource not found")

    logger.info(
        "resource_static_registered",
        extra={"cache_dir": str(cache_dir), "baseline_dir": str(baseline_dir)},
    )

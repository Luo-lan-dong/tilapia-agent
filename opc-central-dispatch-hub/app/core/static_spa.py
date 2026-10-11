"""前端静态产物托管与 SPA fallback（ADR-021）。

桌面形态下前端 build 产物由 FastAPI 托管（单端口 8000）：
- ``web/`` 目录存在时：挂载 ``/assets`` 静态目录，并注册 catch-all
  路由实现 SPA history 路由回退（未命中文件返回 ``index.html``）。
- ``web/`` 目录不存在时：不挂载，保持纯后端行为（根路径返回服务信息 JSON）。

必须在所有 API 路由注册之后调用，保证 API 路由优先匹配。
"""

import logging
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

# catch-all 中需要排除的前缀：这些路径未被 API 匹配时属于错误请求，
# 不应回退到 index.html（如未知 /api/xxx 应返回 404 JSON）
NON_FALLBACK_PREFIXES = ("api/", "ws/")


def resolve_web_dist_dir() -> Path | None:
    """定位前端 build 产物目录；不存在或缺少 index.html 时返回 None。

    - PyInstaller frozen：``sys._MEIPASS`` 下的 ``web/``（onedir datas 位于 _internal）。
    - 源码运行：hub 根目录下的 ``web/``（由 ``scripts/copy_web_dist.py`` 生成）。
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        web_dir = Path(meipass) / "web"
    else:
        # app/core/static_spa.py -> hub 根目录
        web_dir = Path(__file__).resolve().parent.parent.parent / "web"
    if (web_dir / "index.html").is_file():
        return web_dir
    return None


def mount_web_console(app: FastAPI, web_dir: Path) -> None:
    """挂载前端静态产物与 SPA fallback。

    Args:
        app: FastAPI 应用实例（API 路由应已注册完毕）。
        web_dir: 前端 build 产物目录（含 index.html）。
    """
    assets_dir = web_dir / "assets"
    index_file = web_dir / "index.html"

    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="web-assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> FileResponse:
        """SPA history 路由回退：命中真实文件则返回文件，否则返回 index.html。"""
        if full_path.startswith(NON_FALLBACK_PREFIXES):
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = (web_dir / full_path).resolve()
        # 防目录穿越：仅返回 web/ 目录内的真实文件
        if candidate.is_file() and web_dir.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index_file)

    logger.info(
        "web_console_mounted",
        extra={"web_dir": str(web_dir)},
    )

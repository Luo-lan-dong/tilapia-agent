"""OPC 中央调度中枢入口文件。"""

import uvicorn

from app.core.config import get_settings
from app.main import create_app

app = create_app()


if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        # 排除日志与静态资源目录：文件日志持续写入会不断触发 reload 监控
        reload_excludes=["logs/*", "static/*", "assets/*"],
    )

"""复制前端 build 产物到中枢 web/ 目录（ADR-021）。

用法（仓库根执行）：
    python opc-central-dispatch-hub/scripts/copy_web_dist.py

前提：已先在 opc-web-console 执行 ``npm run build``。
产物目录 web/ 被 FastAPI 托管（见 app/core/static_spa.py），
不存在时后端保持纯 API 行为。
"""

import shutil
import sys
from pathlib import Path

HUB_ROOT = Path(__file__).resolve().parent.parent
WEB_CONSOLE_DIST = HUB_ROOT.parent / "opc-web-console" / "dist"
WEB_TARGET = HUB_ROOT / "web"


def main() -> int:
    """复制 dist → web，返回退出码。"""
    if not (WEB_CONSOLE_DIST / "index.html").is_file():
        print(f"[错误] 前端产物不存在：{WEB_CONSOLE_DIST}")
        print("请先执行：cd opc-web-console && npm run build")
        return 1

    if WEB_TARGET.exists():
        shutil.rmtree(WEB_TARGET)
    shutil.copytree(WEB_CONSOLE_DIST, WEB_TARGET)
    copied = sum(1 for _ in WEB_TARGET.rglob("*") if _.is_file())
    print(f"[完成] 已复制 {copied} 个文件到 {WEB_TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

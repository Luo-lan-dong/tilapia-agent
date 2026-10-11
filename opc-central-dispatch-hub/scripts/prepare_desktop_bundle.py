"""准备桌面版打包素材：下载并解压 PostgreSQL 便携版（ADR-021）。

用法（在 opc-central-dispatch-hub 目录）：
    .venv\\Scripts\\python.exe scripts\\prepare_desktop_bundle.py

产物：pgsql/（含 bin、lib、share），供本地验证与 PyInstaller/Inno Setup 打包。
已存在时跳过下载（幂等）；支持 --zip 指定本地 zip 离线安装。
"""

import argparse
import shutil
import sys
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen

HUB_ROOT = Path(__file__).resolve().parent.parent
PGSQL_DIR = HUB_ROOT / "pgsql"
ZIP_CACHE = HUB_ROOT / "build" / "pgsql-zip"
PG_VERSION = "16.9-1"
PG_URL = f"https://get.enterprisedb.com/postgresql/postgresql-{PG_VERSION}-windows-x64-binaries.zip"


def download(url: str, target: Path) -> None:
    """流式下载文件到目标路径。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=60) as resp, target.open("wb") as fh:  # noqa: S310 官方固定地址
        shutil.copyfileobj(resp, fh, length=1024 * 1024)


def main() -> int:
    """下载/解压 PostgreSQL 便携版，返回退出码。"""
    parser = argparse.ArgumentParser(description="准备 PostgreSQL 便携版")
    parser.add_argument("--zip", help="使用本地 zip 文件（离线）", default=None)
    args = parser.parse_args()

    if (PGSQL_DIR / "bin" / "initdb.exe").is_file():
        print(f"[跳过] {PGSQL_DIR} 已存在")
        return 0

    if args.zip:
        zip_path = Path(args.zip)
    else:
        zip_path = ZIP_CACHE / f"postgresql-{PG_VERSION}-windows-x64-binaries.zip"
        if not zip_path.is_file():
            print(f"[下载] {PG_URL}")
            download(PG_URL, zip_path)

    print(f"[解压] {zip_path}")
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(HUB_ROOT / "build" / "pgsql-extract")

    # zip 内顶层目录为 pgsql/，移动到预期位置
    extracted = HUB_ROOT / "build" / "pgsql-extract" / "pgsql"
    if not (extracted / "bin" / "initdb.exe").is_file():
        print(f"[错误] 解压内容不符合预期：{extracted}")
        return 1
    if PGSQL_DIR.exists():
        shutil.rmtree(PGSQL_DIR)
    shutil.move(str(extracted), str(PGSQL_DIR))
    shutil.rmtree(HUB_ROOT / "build" / "pgsql-extract", ignore_errors=True)
    print(f"[完成] PostgreSQL 便携版就绪：{PGSQL_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""TilapiaHub 桌面版一键构建脚本（ADR-021）。

将完整打包流程串联为一条命令：

    .venv\\Scripts\\python.exe scripts\\build_desktop.py

流程：
1. 检查内嵌 PostgreSQL 便携版（pgsql/）就绪
2. （--npm-build 时）执行前端 npm run build
3. 复制前端产物 dist/ → web/
4. PyInstaller 打包（dist/tilapia-hub/）
5. 重建 dist 内 pgsql 链接（PyInstaller 每次打包会清空 dist）

可选参数：
    --npm-build    打包前重新构建前端（默认复用现有 web/ 产物）
    --copy-pgsql   用真实复制代替目录链接（较慢，约 1.2GB）

用法（在 opc-central-dispatch-hub 目录）：
    .venv\\Scripts\\python.exe scripts\\build_desktop.py [--npm-build] [--copy-pgsql]
"""

import argparse
import subprocess
import sys
from pathlib import Path

HUB_ROOT = Path(__file__).resolve().parent.parent
WEB_CONSOLE = HUB_ROOT.parent / "opc-web-console"
DIST_APP = HUB_ROOT / "dist" / "tilapia-hub"


def run(cmd: list[str], cwd: Path | None = None) -> None:
    """执行命令，失败即终止。"""
    print(f"[执行] {' '.join(cmd)}")
    result = subprocess.run(  # noqa: S603
        cmd, cwd=str(cwd) if cwd else None,
    )
    if result.returncode != 0:
        print(f"[失败] 命令退出码 {result.returncode}：{' '.join(cmd)}")
        sys.exit(1)


def step_stop_running() -> None:
    """停止运行中的实例并清理产物内旧数据（避免文件占用导致打包失败）。"""
    subprocess.run(  # noqa: S603
        ["taskkill", "/IM", "tilapia-hub.exe", "/F"],
        check=False, capture_output=True,
    )
    subprocess.run(  # noqa: S603
        ["taskkill", "/IM", "postgres.exe", "/F"],
        check=False, capture_output=True,
    )
    import time

    time.sleep(2)
    stale_data = DIST_APP / "data"
    if stale_data.exists():
        import shutil

        shutil.rmtree(stale_data, ignore_errors=True)
    print("[0/5] 运行中实例已停止，产物内旧数据已清理")


def step_pgsql_ready() -> None:
    """检查内嵌 PostgreSQL 便携版就绪。"""
    if (HUB_ROOT / "pgsql" / "bin" / "initdb.exe").is_file():
        print("[1/5] PostgreSQL 便携版就绪")
        return
    print("[1/5] 缺少 pgsql/，执行 prepare_desktop_bundle.py ...")
    run([str(HUB_ROOT / ".venv" / "Scripts" / "python.exe"),
         str(HUB_ROOT / "scripts" / "prepare_desktop_bundle.py")], cwd=HUB_ROOT)


def step_npm_build() -> None:
    """前端构建（可选步骤）。

    Windows 下 npm 实际是 npm.cmd，subprocess 不带 shell 时无法解析，
    必须用 shutil.which 定位完整路径。
    """
    import shutil

    npm = shutil.which("npm")
    if not npm:
        print("[失败] 未找到 npm，请确认 Node.js 已安装且在 PATH 中")
        sys.exit(1)
    run([npm, "run", "build"], cwd=WEB_CONSOLE)
    print("[2/5] 前端构建完成")


def step_copy_web() -> None:
    """复制前端产物到中枢 web/。"""
    run([str(HUB_ROOT / ".venv" / "Scripts" / "python.exe"),
         str(HUB_ROOT / "scripts" / "copy_web_dist.py")], cwd=HUB_ROOT)
    print("[3/5] 前端产物已复制到 web/")


def step_pyinstaller() -> None:
    """PyInstaller 打包。"""
    run([
        str(HUB_ROOT / ".venv" / "Scripts" / "python.exe"),
        "-m", "PyInstaller", str(HUB_ROOT / "launcher" / "tilapia-hub.spec"),
        "--noconfirm",
        "--distpath", str(HUB_ROOT / "dist"),
        "--workpath", str(HUB_ROOT / "build" / "pyinstaller"),
    ], cwd=HUB_ROOT)
    print("[4/5] PyInstaller 打包完成")


def step_link_pgsql(copy_mode: bool) -> None:
    """把 pgsql 集成进产物目录（PyInstaller 会清空 dist，故每次重建）。"""
    if not DIST_APP.is_dir():
        print("[失败] 产物目录不存在")
        sys.exit(1)
    target = DIST_APP / "pgsql"
    if target.is_symlink() or target.is_dir():
        import shutil

        shutil.rmtree(target, ignore_errors=True)
    if copy_mode:
        shutil.copytree(HUB_ROOT / "pgsql", target)
        print("[5/5] pgsql 已复制进产物（真实文件）")
    else:
        # 开发验证用目录链接：零拷贝、秒级；安装包由 Inno Setup 直接复制源 pgsql/
        subprocess.run(  # noqa: S603
            ["cmd", "/c", "mklink", "/J", str(target), str(HUB_ROOT / "pgsql")],
            check=True, capture_output=True,
        )
        print("[5/5] pgsql 已链接进产物（junction）")


def main() -> int:
    """一键构建入口。"""
    parser = argparse.ArgumentParser(description="TilapiaHub 桌面版一键构建")
    parser.add_argument("--npm-build", action="store_true", help="打包前重新构建前端")
    parser.add_argument("--copy-pgsql", action="store_true", help="复制 pgsql 而非目录链接")
    args = parser.parse_args()

    step_stop_running()
    step_pgsql_ready()
    if args.npm_build:
        step_npm_build()
    else:
        print("[2/5] 跳过前端构建（复用现有 web/；如前端有改动请加 --npm-build）")
    step_copy_web()
    step_pyinstaller()
    step_link_pgsql(args.copy_pgsql)

    print("\n[完成] 产物：dist/tilapia-hub/tilapia-hub.exe")
    print("验证：双击运行，或发版用 Inno Setup 编译 installer/tilapia-hub.iss")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""launcher 路径定位。

frozen（PyInstaller）时以 exe 所在目录为应用根；
源码运行时以 hub 根目录（launcher/ 的上级）为应用根。
"""

import sys
from pathlib import Path


def app_root() -> Path:
    """应用根目录：用户数据（data/）、pgsql/、快捷方式的基准目录。

    frozen 时为 exe 所在目录；源码运行时为 hub 根目录。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_root() -> Path:
    """只读资源根目录（alembic、web、static、assets）。

    PyInstaller onedir 模式下 datas 位于 _internal/（sys._MEIPASS），
    与 exe 同级的用户数据目录分离。
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return app_root()


def data_dir() -> Path:
    """运行期数据目录（PG 数据、连接信息、日志）。"""
    return app_root() / "data"


def pg_data_dir() -> Path:
    """内嵌 PostgreSQL 数据目录。"""
    return data_dir() / "pg"


def pg_bin_dir() -> Path:
    """内嵌 PostgreSQL 便携版 bin 目录。"""
    return app_root() / "pgsql" / "bin"


def log_dir() -> Path:
    """运行日志目录。"""
    return data_dir() / "logs"


def pg_env_file() -> Path:
    """内嵌 PG 连接信息持久化文件（含随机密码）。"""
    return data_dir() / "pg_env.json"

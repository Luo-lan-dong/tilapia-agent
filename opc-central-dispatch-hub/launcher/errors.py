"""launcher 自定义异常层级（coding-standards §6：禁止裸抛 Exception）。"""


class LauncherError(Exception):
    """launcher 基础异常，信息面向用户展示。"""


class AlreadyRunningError(LauncherError):
    """已有实例在运行（单实例锁被占用）。"""


class PortExhaustedError(LauncherError):
    """端口探测范围内（8000-8010）无可用端口。"""

    def __init__(self, first: int, last: int) -> None:
        super().__init__(f"端口 {first}-{last} 均被占用，无法启动服务")
        self.first = first
        self.last = last


class PgError(LauncherError):
    """内嵌 PostgreSQL 初始化/启动/停止失败。"""


class MigrationError(LauncherError):
    """Alembic 迁移或初始数据执行失败。"""

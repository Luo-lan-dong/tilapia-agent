"""单实例锁（ADR-021：避免双开导致端口冲突与数据目录竞争）。

Windows 使用命名互斥体（进程退出自动释放，无残留）；
其他平台（测试/Linux）回退到 data 目录下的锁文件。
"""

import atexit
import ctypes
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from launcher.errors import AlreadyRunningError

MUTEX_NAME = "Local\\TilapiaHubSingleton"
ERROR_ALREADY_EXISTS = 183


@contextmanager
def singleton_lock(data_dir: Path) -> Iterator[None]:
    """获取单实例锁；已有实例运行时抛 AlreadyRunningError。

    Args:
        data_dir: 数据目录（非 Windows 平台锁文件所在目录）。
    """
    if sys.platform == "win32":
        _win32_lock(data_dir)
    else:
        _file_lock(data_dir)
    try:
        yield
    finally:
        pass  # Windows 互斥体随进程退出释放；文件锁由 _file_lock 内部管理


def _win32_lock(data_dir: Path) -> None:
    """Windows 命名互斥体实现。"""
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        raise AlreadyRunningError("创建单实例互斥体失败")
    if ctypes.GetLastError() == ERROR_ALREADY_EXISTS:
        ctypes.windll.kernel32.CloseHandle(handle)
        raise AlreadyRunningError("中枢已在运行，请勿重复启动")
    data_dir.mkdir(parents=True, exist_ok=True)
    # 持有句柄至进程退出（不显式 CloseHandle，保证生命周期覆盖整个运行期）
    atexit.register(lambda: ctypes.windll.kernel32.CloseHandle(handle))


def _file_lock(data_dir: Path) -> None:
    """文件锁回退实现（非 Windows 平台测试用）。"""
    data_dir.mkdir(parents=True, exist_ok=True)
    lock_file = data_dir / "hub.lock"
    if lock_file.exists():
        raise AlreadyRunningError("中枢已在运行（锁文件存在），请勿重复启动")
    lock_file.write_text(str(os.getpid()), encoding="utf-8")
    atexit.register(lambda: lock_file.unlink(missing_ok=True))

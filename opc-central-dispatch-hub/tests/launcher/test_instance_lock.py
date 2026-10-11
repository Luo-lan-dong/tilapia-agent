"""单实例锁单元测试（Harness UT-6，文件锁回退路径跨平台可测）。"""

import pytest

from launcher.errors import AlreadyRunningError
from launcher.instance_lock import _file_lock


def test_file_lock_second_acquire_rejected(tmp_path) -> None:
    """UT-6：首次获取成功；持锁期间二次获取抛 AlreadyRunningError。"""
    _file_lock(tmp_path)  # 第一次获取成功（锁文件创建）
    with pytest.raises(AlreadyRunningError):
        _file_lock(tmp_path)


def test_file_lock_released_allows_reacquire(tmp_path, monkeypatch) -> None:
    """UT-6：释放锁文件后可重新获取。"""
    lock_file = tmp_path / "hub.lock"
    registered = []
    monkeypatch.setattr(
        "launcher.instance_lock.atexit",
        type("FakeAtexit", (), {"register": staticmethod(lambda fn: registered.append(fn))}),
    )

    _file_lock(tmp_path)
    assert lock_file.exists()
    for fn in registered:
        fn()  # 模拟进程退出清理
    assert not lock_file.exists()

    _file_lock(tmp_path)  # 可重新获取
    assert lock_file.exists()

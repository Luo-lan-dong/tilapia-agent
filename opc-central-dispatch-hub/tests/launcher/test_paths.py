"""路径定位单元测试（Harness UT-5）。"""

from launcher import paths


def test_app_root_is_hub_directory() -> None:
    """UT-5：源码运行时应用根为 hub 根目录（launcher/ 的上级）。"""
    root = paths.app_root()
    assert (root / "launcher").is_dir()
    assert (root / "app" / "main.py").is_file()


def test_sub_paths_layout() -> None:
    """子路径按约定布局组合。"""
    root = paths.app_root()
    assert paths.data_dir() == root / "data"
    assert paths.pg_data_dir() == root / "data" / "pg"
    assert paths.pg_bin_dir() == root / "pgsql" / "bin"
    assert paths.log_dir() == root / "data" / "logs"
    assert paths.pg_env_file() == root / "data" / "pg_env.json"


def test_frozen_root_uses_executable_dir(monkeypatch) -> None:
    """UT-5：frozen 模式下以 exe 所在目录为根。"""
    import sys

    fake_exe = paths.app_root() / "tilapia-hub.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe))
    assert paths.app_root() == fake_exe.parent

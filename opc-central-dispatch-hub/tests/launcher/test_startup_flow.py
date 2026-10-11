"""启动流程编排测试（Harness IT-6：mock 外部依赖，验证调用顺序与清理）。"""

from unittest.mock import MagicMock, patch

import pytest

from launcher import desktop
from launcher.errors import MigrationError, PgError
from launcher.pg_manager import PgEnv


def _patch_world(pg_fail: Exception | None = None, migrate_fail: Exception | None = None):
    """统一打桩：单实例锁/端口/PG/迁移/服务器/窗口全部 mock。"""
    pg_env = PgEnv(port=15432, password="testpassword")
    pg_instance = MagicMock()
    pg_instance.ensure_started.return_value = pg_env
    if pg_fail is not None:
        pg_instance.ensure_started.side_effect = pg_fail

    server_instance = MagicMock()
    patches = [
        patch("launcher.desktop.singleton_lock", MagicMock(return_value=_NullContext())),
        patch("launcher.desktop.find_free_port", return_value=8000),
        patch("launcher.desktop.EmbeddedPostgres", return_value=pg_instance),
        patch("launcher.desktop.setup_database"),
        patch("launcher.desktop.UvicornServer", return_value=server_instance),
        patch("launcher.desktop._open_window"),
    ]
    if migrate_fail is not None:
        patches.append(patch("launcher.desktop.setup_database", side_effect=migrate_fail))
    return patches, pg_instance, server_instance


class _NullContext:
    """空上下文管理器，替换单实例锁。"""

    def __enter__(self):
        return None

    def __exit__(self, *_args):
        return False


def test_startup_sequence_and_cleanup(tmp_path, monkeypatch) -> None:
    """正常路径：端口探测 → PG → 环境注入 → 迁移 → 服务 → 窗口，关窗后清理。"""
    patches, pg_instance, server_instance = _patch_world()
    monkeypatch.setattr(desktop.paths, "data_dir", lambda: tmp_path / "data")
    for p in patches:
        p.start()
    try:
        with patch.dict("os.environ"):  # 隔离 _inject_env 写入的环境变量
            desktop._run()
    finally:
        for p in patches:
            p.stop()

    pg_instance.ensure_started.assert_called_once()
    server_instance.start.assert_called_once()
    server_instance.wait_ready.assert_called_once()
    server_instance.stop.assert_called_once()  # 窗口关闭后停止服务
    pg_instance.stop.assert_called_once()  # 窗口关闭后停止 PG


def test_pg_failure_aborts_before_server(tmp_path, monkeypatch) -> None:
    """降级链路：PG 启动失败时不启动服务，抛 PgError 且 PG 仍被清理。"""
    patches, pg_instance, server_instance = _patch_world(pg_fail=PgError("initdb 失败"))
    monkeypatch.setattr(desktop.paths, "data_dir", lambda: tmp_path / "data")
    for p in patches:
        p.start()
    try:
        # 隔离 _inject_env 对进程环境的写入
        with (
            patch.dict("os.environ"),
            pytest.raises(PgError),
        ):
            desktop._run()
    finally:
        for p in patches:
            p.stop()

    server_instance.start.assert_not_called()
    pg_instance.stop.assert_called_once()  # finally 清理仍执行


def test_migration_failure_stops_before_window(tmp_path, monkeypatch) -> None:
    """降级链路：迁移失败时不打开窗口；服务未启动、PG 仍被清理。"""
    patches, _pg_instance, server_instance = _patch_world(migrate_fail=MigrationError("迁移失败"))
    monkeypatch.setattr(desktop.paths, "data_dir", lambda: tmp_path / "data")
    for p in patches:
        p.start()
    try:
        # 隔离 _inject_env 对进程环境的写入
        with (
            patch.dict("os.environ"),
            pytest.raises(MigrationError),
        ):
            desktop._run()
    finally:
        for p in patches:
            p.stop()

    server_instance.start.assert_not_called()  # 迁移失败时服务未启动
    server_instance.stop.assert_not_called()

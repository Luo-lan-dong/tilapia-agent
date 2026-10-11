"""内嵌 PG 管理器单元测试（Harness UT-3 / UT-4，不启动真实 PG）。"""

import json

from launcher.pg_manager import PASSWORD_LENGTH, PgEnv, generate_password


def test_password_strength() -> None:
    """UT-3：密码长度达标、仅含字母数字、随机不重复。"""
    passwords = {generate_password() for _ in range(10)}
    for password in passwords:
        assert len(password) == PASSWORD_LENGTH
        assert password.isalnum()
    assert len(passwords) == 10


def test_pg_env_database_url_format() -> None:
    """PgEnv 拼接的连接 URL 符合 asyncpg 方言格式。"""
    env = PgEnv(port=15432, password="abc123XYZ", user="opc", database="opc")
    assert env.database_url == "postgresql+asyncpg://opc:abc123XYZ@127.0.0.1:15432/opc"


def test_pg_env_file_roundtrip(tmp_path) -> None:
    """UT-4：连接信息写入后读出字段一致。"""
    from launcher.pg_manager import EmbeddedPostgres

    env_file = tmp_path / "pg_env.json"
    pg = EmbeddedPostgres(
        pg_bin_dir=tmp_path / "bin",
        pg_data_dir=tmp_path / "pg",
        env_file=env_file,
    )
    env = pg._load_or_create_env()
    loaded = json.loads(env_file.read_text(encoding="utf-8"))
    assert loaded["port"] == env.port
    assert loaded["password"] == env.password

    # 二次读取复用同一份连接信息
    assert pg._load_or_create_env() == env


def test_pg_env_file_corrupted_regenerated(tmp_path) -> None:
    """UT-4：pg_env.json 损坏时重新生成而非崩溃。"""
    from launcher.pg_manager import EmbeddedPostgres

    env_file = tmp_path / "pg_env.json"
    env_file.write_text("{not valid json", encoding="utf-8")
    pg = EmbeddedPostgres(
        pg_bin_dir=tmp_path / "bin",
        pg_data_dir=tmp_path / "pg",
        env_file=env_file,
    )
    env = pg._load_or_create_env()
    assert len(env.password) == PASSWORD_LENGTH
    assert json.loads(env_file.read_text(encoding="utf-8"))["password"] == env.password

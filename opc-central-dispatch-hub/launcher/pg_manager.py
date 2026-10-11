"""内嵌 PostgreSQL 生命周期管理（ADR-021）。

使用 PostgreSQL Windows 便携版（pgsql/ 目录，zip 解压即用）：
- 不注册系统服务、不写注册表，随 launcher 进程启停。
- 仅监听 127.0.0.1，端口 15432（避开本机默认 5432）。
- 连接信息（随机密码）持久化到 data/pg_env.json，首次初始化后复用。

所有子进程统一携带 ``CREATE_NO_WINDOW``：GUI 程序（console=False 打包）启动
控制台子进程时会弹出额外终端窗口，误关该窗口会杀死数据库进程。
"""

import json
import logging
import os
import secrets
import string
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from launcher.errors import PgError

logger = logging.getLogger(__name__)

PG_PORT = 15432
READY_TIMEOUT_SECONDS = 60
READY_POLL_INTERVAL_SECONDS = 1
PASSWORD_LENGTH = 32
_STARTUP_RETRY = 1
# Windows：阻止子进程创建控制台窗口（GUI 子进程调控制台工具必加）
CREATE_NO_WINDOW = 0x08000000


@dataclass
class PgEnv:
    """内嵌 PG 连接信息（持久化到 pg_env.json）。"""

    port: int
    password: str
    user: str = "opc"
    database: str = "opc"

    @property
    def database_url(self) -> str:
        """SQLAlchemy asyncpg 连接 URL。"""
        return (
            f"postgresql+asyncpg://{self.user}:{self.password}"
            f"@127.0.0.1:{self.port}/{self.database}"
        )


def generate_password(length: int = PASSWORD_LENGTH) -> str:
    """生成随机密码（大小写字母 + 数字，无特殊字符避免 URL 转义问题）。"""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


class EmbeddedPostgres:
    """内嵌 PostgreSQL 管理器。"""

    def __init__(self, pg_bin_dir: Path, pg_data_dir: Path, env_file: Path) -> None:
        self._pg_bin = pg_bin_dir
        self._pg_data = pg_data_dir
        self._env_file = env_file

    def ensure_started(self) -> PgEnv:
        """确保 PG 已初始化并处于运行状态，返回连接信息。

        流程：读/生成连接信息 → initdb（如数据目录不存在）→
        pg_ctl start（如未运行）→ pg_isready 等待就绪。

        端口避让：若 15432 已被其他实例（不同数据目录/密码）占用，
        自动顺延端口（15433+）重启本实例，避免多副本认证冲突。
        """
        initdb_exe = self._pg_bin / "initdb.exe"
        if not initdb_exe.is_file():
            raise PgError(
                f"未找到内嵌 PostgreSQL 组件（{initdb_exe}）。\n"
                "请运行 scripts/prepare_desktop_bundle.py 准备 pgsql/，"
                "或重新安装 TilapiaHub。"
            )
        env = self._load_or_create_env()
        if not self._pg_data.exists():
            self._initdb(env)
        if not self._is_ready(env.port):
            self._start(env)
        elif not self._credentials_match(env):
            # 端口被其他实例占用：顺延端口重启本实例
            env.port = self._next_free_port(env.port + 1)
            self._save_env(env)
            logger.info("desktop_pg_port_moved", extra={"port": env.port})
            self._start(env)
        self._wait_ready(env.port)
        self._ensure_database(env)
        return env

    def stop(self) -> None:
        """停止 PG（fast 模式；未运行时静默忽略）。"""
        if not self._pg_data.exists():
            return
        out_file = self._env_file.parent / "pg_ctl_stop.log"
        with out_file.open("wb") as fh:
            result = subprocess.run(  # noqa: S603
                [
                    self._pg_tool("pg_ctl"),
                    "-D", str(self._pg_data),
                    "stop", "-m", "fast",
                ],
                stdout=fh, stderr=subprocess.STDOUT,
                timeout=30,
                creationflags=CREATE_NO_WINDOW,
            )
        logger.info("desktop_pg_stopped", extra={"returncode": result.returncode})

    def _pg_tool(self, name: str) -> str:
        """返回 pgsql/bin 下工具的完整路径。"""
        return str(self._pg_bin / f"{name}.exe")

    def _save_env(self, env: PgEnv) -> None:
        """连接信息变化（如端口避让）后写回 pg_env.json。"""
        self._env_file.parent.mkdir(parents=True, exist_ok=True)
        self._env_file.write_text(json.dumps(asdict(env), indent=2), encoding="utf-8")

    def _credentials_match(self, env: PgEnv) -> bool:
        """验证当前凭据能否连接端口上的 PG（判断是否本数据目录实例）。"""
        result = subprocess.run(  # noqa: S603
            [
                self._pg_tool("psql"),
                "-h", "127.0.0.1", "-p", str(env.port), "-U", env.user, "-d", "postgres",
                "-tAc", "SELECT 1",
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=15,
            env={**os.environ, "PGPASSWORD": env.password},
            creationflags=CREATE_NO_WINDOW,
        )
        return result.returncode == 0

    def _next_free_port(self, start: int) -> int:
        """从 start 起找第一个未被占用的端口（顺延避让）。"""
        from launcher.port import is_port_free

        port = start
        while not is_port_free(port):
            port += 1
            if port > start + 100:
                raise PgError(f"端口 {start}-{start + 100} 均被占用，无法为内嵌 PG 分配端口")
        return port

    def _run_quiet(self, *args: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
        """执行 PG 工具命令（不校验退出码），返回原始结果。"""
        return subprocess.run(  # noqa: S603
            [*args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )

    def _run(self, *args: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
        """执行 PG 工具命令，非零退出码抛 PgError。"""
        result = self._run_quiet(*args, timeout=timeout)
        if result.returncode != 0:
            raise PgError(
                f"命令 {' '.join(args)} 失败：{result.stderr.strip() or result.stdout.strip()}"
            )
        return result

    def _load_or_create_env(self) -> PgEnv:
        """读取已保存的连接信息；不存在则生成并落盘。"""
        if self._env_file.exists():
            try:
                raw = json.loads(self._env_file.read_text(encoding="utf-8"))
                return PgEnv(**raw)
            except (json.JSONDecodeError, TypeError, ValueError):
                logger.warning("desktop_pg_env_corrupted", extra={"file": str(self._env_file)})
        env = PgEnv(port=PG_PORT, password=generate_password())
        self._env_file.parent.mkdir(parents=True, exist_ok=True)
        self._env_file.write_text(json.dumps(asdict(env), indent=2), encoding="utf-8")
        logger.info("desktop_pg_env_created", extra={"port": env.port})
        return env

    def _initdb(self, env: PgEnv) -> None:
        """初始化数据目录：超级用户 opc + scram-sha-256 认证 + UTF8。"""
        pwfile = self._env_file.parent / "pg_init_password.tmp"
        pwfile.write_text(env.password, encoding="utf-8")
        try:
            self._run(
                self._pg_tool("initdb"),
                "-D",
                str(self._pg_data),
                "-U",
                env.user,
                "-A",
                "scram-sha-256",
                "--pwfile",
                str(pwfile),
                "-E",
                "UTF8",
                "--locale=C",
            )
        finally:
            pwfile.unlink(missing_ok=True)
        logger.info("desktop_pg_initialized", extra={"data_dir": str(self._pg_data)})

    def _start(self, env: PgEnv) -> None:
        """pg_ctl start（仅监听 127.0.0.1），失败重试一次。

        注意：输出必须重定向到文件而非管道。pg_ctl start 拉起的 postmaster
        会继承输出句柄且不关闭，若使用 capture_output 管道，
        subprocess.run 将永远等待 EOF 导致卡死（Windows 经典坑）。
        """
        out_file = self._env_file.parent / "pg_ctl_start.log"
        last_error: PgError | None = None
        for _ in range(_STARTUP_RETRY + 1):
            with out_file.open("wb") as fh:
                result = subprocess.run(  # noqa: S603
                    [
                        self._pg_tool("pg_ctl"),
                        "-D", str(self._pg_data),
                        "-l", str(self._env_file.parent / "pg.log"),
                        "start",
                        "-o", f"-p {env.port} -c listen_addresses=127.0.0.1",
                    ],
                    stdout=fh, stderr=subprocess.STDOUT,
                    timeout=60,
                    creationflags=CREATE_NO_WINDOW,
                )
            if result.returncode == 0:
                logger.info("desktop_pg_started", extra={"port": env.port})
                return
            detail = out_file.read_text(encoding="utf-8", errors="replace").strip()
            last_error = PgError(f"pg_ctl start 失败：{detail}")
        raise last_error  # type: ignore[misc]

    def _is_ready(self, port: int) -> bool:
        """pg_isready 快速探测。"""
        result = self._run_quiet(
            self._pg_tool("pg_isready"),
            "-h",
            "127.0.0.1",
            "-p",
            str(port),
            timeout=10,
        )
        return result.returncode == 0

    def _wait_ready(self, port: int) -> None:
        """轮询等待 PG 就绪，超时抛 PgError。"""
        deadline = time.monotonic() + READY_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if self._is_ready(port):
                return
            time.sleep(READY_POLL_INTERVAL_SECONDS)
        raise PgError(f"PostgreSQL 在 {READY_TIMEOUT_SECONDS} 秒内未就绪（端口 {port}）")

    def _ensure_database(self, env: PgEnv) -> None:
        """确保应用数据库存在（幂等）。"""
        pg_env_vars = {**os.environ, "PGPASSWORD": env.password}
        result = subprocess.run(  # noqa: S603
            [
                self._pg_tool("psql"),
                "-h", "127.0.0.1", "-p", str(env.port), "-U", env.user, "-d", "postgres",
                "-tAc", f"SELECT 1 FROM pg_database WHERE datname='{env.database}'",
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30,
            env=pg_env_vars,
            creationflags=CREATE_NO_WINDOW,
        )
        if result.returncode != 0:
            raise PgError(f"查询数据库列表失败：{result.stderr.strip()}")
        if result.stdout.strip() == "1":
            return
        # createdb 无 PGPASSWORD 时认证会失败，显式注入密码环境变量
        result = subprocess.run(  # noqa: S603
            [
                self._pg_tool("createdb"),
                "-h", "127.0.0.1", "-p", str(env.port), "-U", env.user, env.database,
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30,
            env=pg_env_vars,
            creationflags=CREATE_NO_WINDOW,
        )
        if result.returncode != 0:
            raise PgError(f"创建数据库失败：{result.stderr.strip() or result.stdout.strip()}")
        logger.info("desktop_pg_database_created", extra={"database": env.database})

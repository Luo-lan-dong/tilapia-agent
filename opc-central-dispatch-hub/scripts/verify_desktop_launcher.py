"""桌面模式启动流程无窗口验证（ADR-021）。

依次执行：内嵌 PG 启动 → 环境变量注入 → 迁移 + 初始数据，
并打印连接信息。不含 uvicorn 与 pywebview 步骤。

注意：全程保持同步上下文（与 desktop._run 一致），避免嵌套事件循环；
Alembic env.py 内部自行 asyncio.run。

用法（在 opc-central-dispatch-hub 目录）：
    .venv\\Scripts\\python.exe scripts\\verify_desktop_launcher.py
"""

import os
import sys
from pathlib import Path

HUB_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HUB_ROOT))
os.chdir(HUB_ROOT)

from launcher import paths  # noqa: E402
from launcher.db_setup import setup_database  # noqa: E402
from launcher.desktop import _inject_env  # noqa: E402
from launcher.pg_manager import EmbeddedPostgres  # noqa: E402


def main() -> None:
    """按桌面模式顺序验证 PG、环境注入、迁移与初始数据。"""
    pg = EmbeddedPostgres(
        pg_bin_dir=paths.pg_bin_dir(),
        pg_data_dir=paths.pg_data_dir(),
        env_file=paths.pg_env_file(),
    )
    pg_env = pg.ensure_started()
    print(f"[1/3] PG 就绪：{pg_env.database_url}")

    _inject_env(port=8000, database_url=pg_env.database_url)
    print(f"[2/3] 环境变量已注入（APP_ENV={os.environ['APP_ENV']}）")

    setup_database(str(paths.app_root()))
    print("[3/3] 迁移与初始数据完成")


if __name__ == "__main__":
    main()

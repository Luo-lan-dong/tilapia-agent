"""将远程库 pg_dump 导出文件导入本地内嵌 PostgreSQL（ADR-021 数据镜像）。

用途：
1. 远程库（192.168.101.101，PG 18）的本地备份恢复验证；
2. 桌面/本地模式继承远程库历史数据（任务、节点、LLM 配置等）。

处理要点：
- pg_dump 18 输出的 ``\\restrict`` 元命令为 PG 18 psql 新特性，
  PG 16 客户端不识别，导入前过滤；
- dump 含 DROP/CREATE/COPY 全流程，直接覆盖目标库同名表；
- 导入后输出各表行数用于核对。

用法（在 opc-central-dispatch-hub 目录，内嵌 PG 已启动）：
    .venv\\Scripts\\python.exe scripts\\import_remote_dump.py <dump.sql路径>
"""

import json
import os
import subprocess
import sys
from pathlib import Path

HUB_ROOT = Path(__file__).resolve().parent.parent
RESTRICT_PREFIX = "\\restrict"
# PG 17+ 新增内容（transaction_timeout 参数、\restrict/\unrestrict 元命令），PG 16 不识别
SKIP_PATTERNS = ("\\restrict", "\\unrestrict", "SET transaction_timeout")


def filter_dump(src: Path, dst: Path) -> None:
    """过滤 PG 18 专属内容（\\restrict 元命令、transaction_timeout 参数）。"""
    lines = src.read_text(encoding="utf-8", errors="replace").splitlines()
    kept = [
        ln for ln in lines
        if not any(ln.startswith(prefix) for prefix in SKIP_PATTERNS)
    ]
    dst.write_text("\n".join(kept) + "\n", encoding="utf-8")
    print(f"[过滤] {src.name}: {len(lines)} -> {len(kept)} 行")


def import_dump(dump_file: Path, env: dict) -> None:
    """使用内嵌 psql 将 dump 导入本地 opc 库。"""
    pg_env = json.loads((HUB_ROOT / "data" / "pg_env.json").read_text(encoding="utf-8"))
    result = subprocess.run(  # noqa: S603
        [
            str(HUB_ROOT / "pgsql" / "bin" / "psql.exe"),
            "-h", "127.0.0.1", "-p", str(pg_env["port"]),
            "-U", pg_env["user"], "-d", pg_env["database"],
            "-v", "ON_ERROR_STOP=1",
            "-f", str(dump_file),
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300,
        env={**os.environ, "PGPASSWORD": pg_env["password"]},
    )
    if result.returncode != 0:
        print(f"[失败] psql 退出码 {result.returncode}")
        print(result.stderr[-3000:])
        sys.exit(1)
    print("[导入] 完成")


def verify_counts(env: dict) -> None:
    """导入后核对各表行数。"""
    pg_env = json.loads((HUB_ROOT / "data" / "pg_env.json").read_text(encoding="utf-8"))
    tables = [
        "agent_nodes", "tasks", "task_building_sessions",
        "llm_providers", "system_settings", "system_versions", "alembic_version",
    ]
    for table in tables:
        result = subprocess.run(  # noqa: S603
            [
                str(HUB_ROOT / "pgsql" / "bin" / "psql.exe"),
                "-h", "127.0.0.1", "-p", str(pg_env["port"]),
                "-U", pg_env["user"], "-d", pg_env["database"],
                "-tAc", f"SELECT COUNT(*) FROM {table}",
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30,
            env={**os.environ, "PGPASSWORD": pg_env["password"]},
        )
        count = result.stdout.strip() if result.returncode == 0 else f"查询失败: {result.stderr.strip()[:80]}"
        print(f"  {table}: {count}")


def main() -> int:
    """入口：过滤 → 导入 → 核对。"""
    if len(sys.argv) != 2:
        print("用法: python scripts/import_remote_dump.py <dump.sql路径>")
        return 1
    src = Path(sys.argv[1])
    if not src.is_file():
        print(f"[错误] 文件不存在: {src}")
        return 1

    filtered = HUB_ROOT / "build" / "filtered_dump.sql"
    filtered.parent.mkdir(parents=True, exist_ok=True)
    filter_dump(src, filtered)
    import_dump(filtered, {})
    print("[核对] 导入后各表行数:")
    verify_counts({})
    filtered.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

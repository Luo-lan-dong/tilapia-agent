"""清洗任务目录 tilacode.jsonc 顶层毒键 _hub_managed_providers 并验证根因。

背景（2026-09-27 二次排查）：ConfigParse.schema（parse.ts topLevelExtraKeys）
对配置文件顶层任一未知键抛 unrecognized_keys，整个文件不参与 merge——
Agent 旧版本写入的 _hub_managed_providers 管理键正是毒键，导致单数
provider 键修复后 provider 依然加载不到（ModelUnavailableError）。

步骤：
1. tilacode 日志中找 unrecognized_keys / InvalidError 证据
2. 定位 639abf4e 任务目录，python3 原子清洗毒键
3. 回读验证 JSON 结构
"""

import asyncio
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.models.base import AsyncSessionLocal  # noqa: E402
from app.models.bt_mcp_server import BtMcpServer  # noqa: E402
from app.services.bt_mcp_client import BtMcpError, timed_call  # noqa: E402
from node_diag_20260927 import _build  # noqa: E402

SERVER_ID = "3fc19d1d-2a75-4a78-8ef8-28ba1d87822b"

# 原子清洗：读 json → 删毒键 → 写回；顺带列出 workspace 下所有任务目录状态
CLEAN_SCRIPT = r"""
import json, os, glob

logs = []
log_path = "/root/.local/share/tilacode/log/tilacode.log"
try:
    with open(log_path, "r", errors="ignore") as fh:
        for line in fh:
            if ("unrecognized" in line.lower()
                    or "_hub_managed_providers" in line
                    or "InvalidError" in line
                    or "invalid_config" in line.lower()):
                if "apiKey" in line or "sk-" in line:
                    continue
                logs.append(line.strip()[:400])
except FileNotFoundError:
    logs.append("(tilacode log not found)")
print("=== tilacode 日志 unrecognized/invalid 证据（最近 12 条）===")
for entry in logs[-12:]:
    print(entry)

print("\n=== 任务目录配置文件状态 ===")
for cfg in sorted(glob.glob("/opt/opc-agent-code/workspace/*/.tilacode/tilacode.jsonc")):
    try:
        with open(cfg, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        poisoned = "_hub_managed_providers" in data
        has_provider = isinstance(data.get("provider"), dict) and bool(data["provider"])
        if poisoned:
            tmp = cfg + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, cfg)
            print(f"CLEANED {cfg} (had poison key; provider_present={has_provider})")
        else:
            print(f"OK      {cfg} (poison=no, provider_present={has_provider})")
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR   {cfg}: {exc}")
"""

COMMANDS = [
    (
        "清洗前：tilacode 日志证据 + 各任务目录毒键状态（含即时清洗）",
        f"python3 - <<'PYEOF'\n{CLEAN_SCRIPT}\nPYEOF",
    ),
]


async def main() -> None:
    async with AsyncSessionLocal() as session:
        server = (
            await session.execute(
                select(BtMcpServer).where(BtMcpServer.id == uuid.UUID(SERVER_ID))
            )
        ).scalars().first()

    for title, command in COMMANDS:
        print(f"\n===== {title} =====")
        client = _build(server)
        try:
            result, _ms = await timed_call(client, "Bash", {"command": command})
            data = result.get("data") or {}
            print(str(data.get("stdout") or result.get("msg") or "")[:6000])
        except BtMcpError as exc:
            print(f"[MCP 错误] {exc}")
        finally:
            await client.close()


if __name__ == "__main__":
    asyncio.run(main())

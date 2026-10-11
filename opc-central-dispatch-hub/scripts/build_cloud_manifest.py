"""云端资源仓库 manifest.json 生成工具（ADR-022 配套）。

扫描本地资源目录（与中枢 static 同构），自动计算每个资源的
sha256/size 并生成 manifest.json（revision 取当前 UTC 时间或 --revision）。

用法：
    .venv\\Scripts\\python.exe scripts\\build_cloud_manifest.py <资源目录> [--revision <时间戳>]

示例（资源目录即中枢 static/ 或其副本）：
    .venv\\Scripts\\python.exe scripts\\build_cloud_manifest.py static
生成：static/manifest.json（上传云端时连同资源文件一起上传）
"""

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

# 资源 key 约定（与 ADR-022 / 中枢资源管理页一致）
KEY_RULES = [
    (Path("install-scripts/install-agent-programming-linux.sh"), "install-script-linux", None),
]

TEMPLATE_KEY_PREFIX = "agent-template-programming"


def sha256_of(path: Path) -> str:
    """计算文件 SHA-256。"""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_entries(root: Path) -> list[dict]:
    """扫描资源目录，按约定生成资源条目。"""
    entries: list[dict] = []

    # 安装脚本（固定 key）
    for rel, key, _ in KEY_RULES:
        file_path = root / rel
        if file_path.is_file():
            entries.append(
                {
                    "key": key,
                    "path": rel.as_posix(),
                    "sha256": sha256_of(file_path),
                    "size": file_path.stat().st_size,
                }
            )

    # Agent 模板 tarball（key 带版本）
    templates_dir = root / "agent-templates"
    if templates_dir.is_dir():
        for tarball in sorted(templates_dir.rglob("*.tar.gz")):
            rel = tarball.relative_to(root)
            version = tarball.parent.name
            entries.append(
                {
                    "key": f"{TEMPLATE_KEY_PREFIX}-{version}",
                    "path": rel.as_posix(),
                    "sha256": sha256_of(tarball),
                    "size": tarball.stat().st_size,
                    "version": version,
                }
            )

    # tilacode 二进制（key 带 tilacode 版本，读取同目录 manifest.json）
    binaries_dir = root / "binaries" / "tilacode"
    if binaries_dir.is_dir():
        for version_dir in sorted(p for p in binaries_dir.iterdir() if p.is_dir()):
            for binary in sorted(version_dir.glob("tilacode-*")):
                if binary.suffix or binary.name.startswith("manifest"):
                    continue
                rel = binary.relative_to(root)
                entries.append(
                    {
                        "key": binary.name,
                        "path": rel.as_posix(),
                        "sha256": sha256_of(binary),
                        "size": binary.stat().st_size,
                        "version": version_dir.name,
                    }
                )
    return entries


def main() -> int:
    """入口：扫描 → 生成 manifest.json。"""
    parser = argparse.ArgumentParser(description="生成云端资源仓库 manifest.json")
    parser.add_argument("root", help="资源目录（如 opc-central-dispatch-hub/static）")
    parser.add_argument("--revision", help="仓库版本（默认当前 UTC 时间戳）")
    args = parser.parse_args()

    root = Path(args.root)
    if not root.is_dir():
        print(f"[错误] 目录不存在: {root}")
        return 1

    entries = collect_entries(root)
    if not entries:
        print("[错误] 未在目录中找到任何资源文件")
        return 1

    manifest = {
        "revision": args.revision or datetime.now(UTC).isoformat(timespec="seconds"),
        "resources": entries,
    }
    out = root / "manifest.json"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[完成] {out}")
    for entry in entries:
        size_kb = round(entry["size"] / 1024, 1)
        print(f"  - {entry['key']}: {entry['path']} ({size_kb} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

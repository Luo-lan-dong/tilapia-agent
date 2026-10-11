"""打包 Agent 模板 tarball 并更新 manifest。

按 BUILD.md 4.4 节的流程，在 Windows 环境下使用 Python tarfile 替代 tar 命令，
排除 tilacode 源码、虚拟环境、缓存与 Git 目录。
"""

import hashlib
import json
import os
import tarfile
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
HUB_STATIC_DIR = PROJECT_ROOT.parent / "opc-central-dispatch-hub" / "static"
VERSION = "0.4.0"
TEMPLATE_DIR = HUB_STATIC_DIR / "agent-templates" / "programming" / VERSION
TARBALL_NAME = f"opc-agent-programming-{VERSION}.tar.gz"
TARBALL_PATH = TEMPLATE_DIR / TARBALL_NAME
MANIFEST_PATH = TEMPLATE_DIR / "manifest.json"

EXCLUDES = {
    "tilacode",
    ".venv",
    "__pycache__",
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    "scripts",  # 打包脚本本身不需要进模板
}
EXCLUDE_SUFFIXES = (".pyc", ".pyo")


def _should_include(path: Path, root: Path) -> bool:
    """判断路径是否应该被打包。"""
    rel_parts = path.relative_to(root).parts
    for part in rel_parts:
        if part in EXCLUDES:
            return False
    if path.is_file() and path.name.endswith(EXCLUDE_SUFFIXES):
        return False
    return True


def _calc_sha256(file_path: Path) -> str:
    """计算文件 sha256。"""
    hasher = hashlib.sha256()
    with file_path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def package_template() -> None:
    """执行打包并更新 manifest。"""
    TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)

    with tarfile.open(TARBALL_PATH, "w:gz") as tar:
        for item in PROJECT_ROOT.iterdir():
            if item.name in EXCLUDES:
                continue
            if item.is_dir():
                for sub_item in item.rglob("*"):
                    if _should_include(sub_item, PROJECT_ROOT):
                        tar.add(sub_item, arcname=sub_item.relative_to(PROJECT_ROOT))
            elif _should_include(item, PROJECT_ROOT):
                tar.add(item, arcname=item.relative_to(PROJECT_ROOT))

    tarball_size = TARBALL_PATH.stat().st_size
    tarball_sha256 = _calc_sha256(TARBALL_PATH)

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest["tarball"] = {
        "filename": TARBALL_NAME,
        "sha256": tarball_sha256,
        "size": tarball_size,
    }
    manifest["built_at"] = datetime.now(timezone.utc).isoformat()
    manifest["changes"] = (
        "Agent 节点真实系统资源监控（ADR-014）：心跳上报 metrics 快照与 load_score，"
        "中枢持久化 metrics_snapshot 并提供 /api/v1/agent-nodes/{node_id}/metrics 查询接口"
    )

    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + os.linesep,
        encoding="utf-8",
    )

    print(f"打包完成: {TARBALL_PATH}")
    print(f"  size: {tarball_size}")
    print(f"  sha256: {tarball_sha256}")
    print(f"  manifest: {MANIFEST_PATH}")


if __name__ == "__main__":
    package_template()

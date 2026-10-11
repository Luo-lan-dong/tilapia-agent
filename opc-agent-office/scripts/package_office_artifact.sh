#!/usr/bin/env bash
# 办公 Agent 整体包打包脚本（ADR-062）：生成 agent-office-linux-x64 发布 tar.gz
#
# 用法（在 opc-agent-office 目录）：
#   bash scripts/package_office_artifact.sh
# 产物：dist/agent-office-linux-x64-<version>.tar.gz（上传中枢资源管理页发布）
set -euo pipefail

cd "$(dirname "$0")/.."
VERSION=$(grep -E '^version' pyproject.toml | head -n1 | sed 's/.*"\(.*\)"/\1/')
OUT_NAME="agent-office-linux-x64-${VERSION}.tar.gz"

mkdir -p dist
tar -czf "dist/${OUT_NAME}" \
    --exclude='__pycache__' \
    --exclude='.pytest_cache' \
    --exclude='.ruff_cache' \
    --exclude='dist' \
    --exclude='data' \
    --exclude='.env' \
    app pyproject.toml systemd README.md .env.example

echo "打包完成: dist/${OUT_NAME}"
sha256sum "dist/${OUT_NAME}"

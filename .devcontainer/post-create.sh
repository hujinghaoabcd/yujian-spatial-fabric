#!/usr/bin/env bash
set -euo pipefail

WORKSPACE="/workspaces/yujian-spatial-fabric"
cd "$WORKSPACE"

echo "[codespaces] 安装 Python 开发依赖..."
uv sync --group dev

echo "[codespaces] 执行正式 Django migrations..."
uv run python backend/manage.py migrate --noinput

echo "[codespaces] 执行 Django system check..."
uv run python backend/manage.py check

echo "[codespaces] 初始化完成。API 将由 postStartCommand 自动启动在 8000 端口。"

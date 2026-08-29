#!/usr/bin/env bash
set -euo pipefail

WORKSPACE="/workspaces/yujian-spatial-fabric"
PID_FILE="/tmp/spatial-fabric-codespaces-api.pid"
LOG_FILE="/tmp/spatial-fabric-codespaces-api.log"

cd "$WORKSPACE"

# Codespace 重启时 postStartCommand 会再次执行。先清理我们自己记录的旧进程，
# 避免多个 Uvicorn 同时争用 8000 端口；不会使用模糊 pkill 误伤其他 Python 进程。
if [[ -f "$PID_FILE" ]]; then
  old_pid="$(cat "$PID_FILE" || true)"
  if [[ -n "$old_pid" ]] && kill -0 "$old_pid" 2>/dev/null; then
    kill "$old_pid" || true
    sleep 1
  fi
  rm -f "$PID_FILE"
fi

# 使用开发设置启动 ASGI。--app-dir 明确把 backend 放进 import path，
# 与生产 Docker 镜像保持同一 config.asgi 入口，但不复制生产启动策略。
nohup uv run uvicorn \
  --app-dir backend \
  config.asgi:application \
  --host 0.0.0.0 \
  --port 8000 \
  >"$LOG_FILE" 2>&1 &

api_pid=$!
echo "$api_pid" > "$PID_FILE"

echo "[codespaces] Spatial Fabric API 已启动，PID=$api_pid"
echo "[codespaces] 日志：$LOG_FILE"
echo "[codespaces] 打开 Codespaces 的 PORTS 面板访问 8000 端口。"

#!/bin/sh
set -eu

# Render Free Web Service 当前不提供付费服务才有的 pre-deploy migration 步骤。
# Preview 环境因此在进程启动前执行 Django 的幂等 migration。
# 这只是演示环境策略：正式生产部署应把 schema migration 恢复为独立 release/deploy 阶段，
# 避免多个 Web 副本并发承担数据库变更职责。
python backend/manage.py migrate --noinput

# Render 会通过 PORT 注入平台监听端口；本地直接运行脚本时回退到 8000。
# exec 让 Uvicorn 直接成为容器主进程，从而能正确接收 SIGTERM 并完成优雅关闭。
exec uvicorn config.asgi:application \
  --app-dir backend \
  --host 0.0.0.0 \
  --port "${PORT:-8000}"

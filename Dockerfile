# syntax=docker/dockerfile:1.7
#
# Spatial Fabric Control Plane 生产基础镜像。
# 重要：GeoDjango/PostGIS backend 在 Python 启动阶段需要宿主环境可加载 GDAL/GEOS，
# 因此这些不是“本地开发工具”，而是运行时依赖，必须显式写入镜像。
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates \
       curl \
       gdal-bin \
       libgdal-dev \
       libgeos-dev \
    && rm -rf /var/lib/apt/lists/*

# uv 只负责依赖安装，不进入核心领域设计。
COPY --from=ghcr.io/astral-sh/uv:0.12.7 /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml README.md ./
COPY backend ./backend

# 生产镜像不安装 dev group，并关闭 editable 安装，减少运行环境的不确定性。
RUN uv sync --no-dev --no-editable --frozen=false

ENV PATH="/app/.venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=config.settings.production

EXPOSE 8000

# 第一阶段直接使用 Uvicorn ASGI。后续如果引入反向代理、进程管理或 K8s，
# 通过 Deployment Profile 调整，不改变 Django Domain Model。
CMD ["uvicorn", "config.asgi:application", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8000"]

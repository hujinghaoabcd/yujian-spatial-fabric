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
COPY scripts ./scripts

# 生产镜像不安装 dev group，并关闭 editable 安装，减少运行环境的不确定性。
# 当前仓库尚未固化 uv.lock，因此这里不使用 --frozen；待锁文件正式纳入仓库后再切换为冻结安装。
RUN uv sync --no-dev --no-editable \
    && chmod +x /app/scripts/start-preview.sh

ENV PATH="/app/.venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=config.settings.production

EXPOSE 8000

# 基础镜像仍保持普通生产启动命令。Render Preview 通过 render.yaml 的 dockerCommand
# 显式选择 start-preview.sh，避免把“启动时 migrate”的临时策略污染所有生产部署。
CMD ["uvicorn", "config.asgi:application", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8000"]

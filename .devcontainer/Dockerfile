FROM mcr.microsoft.com/devcontainers/python:1-3.12-bookworm

# GeoDjango 在开发容器中同样依赖 GDAL/GEOS 动态库；
# postgresql-client 便于排查数据库连接，curl 用于 readiness/API 自检。
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        curl \
        gdal-bin \
        libgdal-dev \
        libgeos-dev \
        postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Codespaces 只作为开发/预览环境。生产镜像仍使用仓库根目录 Dockerfile。
# uv 使用有界版本范围，避免开发容器突然跨主版本升级。
RUN python -m pip install --no-cache-dir "uv>=0.8,<1"

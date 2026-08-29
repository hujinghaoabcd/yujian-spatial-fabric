"""Django PostgreSQL/PostGIS 连接配置构造器。

Spatial Fabric 的核心只依赖 PostgreSQL/PostGIS 能力，不应该知道 Render、Neon、AWS、
阿里云等具体托管厂商。因此这里同时支持两种 provider-neutral 输入：

1. ``DATABASE_URL``：适合托管平台、SecretProvider 与十二要素应用；
2. ``POSTGRES_*``：适合本地 Docker Compose、CI 和传统服务器部署。

无论输入来自哪里，最终都转换为同一个 GeoDjango PostGIS backend 配置。
密码等敏感字段只存在于进程内配置，禁止写入日志。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from django.core.exceptions import ImproperlyConfigured

POSTGRES_SCHEMES = frozenset({"postgres", "postgresql"})


def _positive_int(raw_value: str, *, field_name: str) -> int:
    """把环境变量中的正整数转换为 int，并给出可定位的配置错误。"""

    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ImproperlyConfigured(f"{field_name} 必须是整数。") from exc
    if value < 0:
        raise ImproperlyConfigured(f"{field_name} 不能小于 0。")
    return value


def parse_database_url(url: str, *, conn_max_age: int = 60) -> dict[str, Any]:
    """把 PostgreSQL URL 转换成 Django PostGIS DATABASES['default'] 配置。

    URL 查询参数会透传给 psycopg ``OPTIONS``，因此诸如 ``sslmode=require``、
    ``channel_binding=require`` 等托管数据库连接要求可以保持在 Secret URL 中，而不用在
    Spatial Fabric 核心增加某个厂商专属字段。
    """

    parsed = urlsplit(url)
    if parsed.scheme.lower() not in POSTGRES_SCHEMES:
        raise ImproperlyConfigured("DATABASE_URL 必须使用 postgres:// 或 postgresql://。")

    database_name = unquote(parsed.path.lstrip("/"))
    if not database_name:
        raise ImproperlyConfigured("DATABASE_URL 必须包含数据库名称。")
    if not parsed.hostname:
        raise ImproperlyConfigured("DATABASE_URL 必须包含数据库主机。")

    try:
        port = parsed.port or 5432
    except ValueError as exc:
        raise ImproperlyConfigured("DATABASE_URL 中的端口无效。") from exc

    options: dict[str, Any] = {
        key: values[-1]
        for key, values in parse_qs(parsed.query, keep_blank_values=False).items()
        if values
    }
    # 避免没有显式超时的数据库连接长期挂住 Web Worker；URL 若明确提供则尊重 URL。
    options.setdefault("connect_timeout", 5)

    return {
        "ENGINE": "django.contrib.gis.db.backends.postgis",
        "NAME": database_name,
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname,
        "PORT": str(port),
        "CONN_MAX_AGE": conn_max_age,
        "OPTIONS": options,
    }


def build_database_config(environment: Mapping[str, str] | None = None) -> dict[str, Any]:
    """从环境变量构造数据库配置，优先使用 DATABASE_URL。

    ``environment`` 参数主要用于单元测试，也让这个函数保持无隐式全局状态的可测试性。
    生产调用不传参数时读取 ``os.environ``。
    """

    env = os.environ if environment is None else environment
    conn_max_age = _positive_int(
        env.get("POSTGRES_CONN_MAX_AGE", "60"), field_name="POSTGRES_CONN_MAX_AGE"
    )

    database_url = env.get("DATABASE_URL", "").strip()
    if database_url:
        return parse_database_url(database_url, conn_max_age=conn_max_age)

    return {
        "ENGINE": "django.contrib.gis.db.backends.postgis",
        "NAME": env.get("POSTGRES_DB", "spatial_fabric"),
        "USER": env.get("POSTGRES_USER", "spatial_fabric"),
        "PASSWORD": env.get("POSTGRES_PASSWORD", ""),
        "HOST": env.get("POSTGRES_HOST", "127.0.0.1"),
        "PORT": env.get("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": conn_max_age,
        "OPTIONS": {"connect_timeout": 5},
    }

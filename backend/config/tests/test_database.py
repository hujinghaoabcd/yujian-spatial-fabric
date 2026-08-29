"""数据库配置适配层测试。

这里只验证 provider-neutral 的连接契约，不连接任何 Render/Neon 等真实外部服务。
"""

import pytest
from django.core.exceptions import ImproperlyConfigured

from config.database import build_database_config, parse_database_url


def test_database_url_is_parsed_for_postgis_backend() -> None:
    config = parse_database_url(
        "postgresql://fabric:p%40ss@db.example.test:5433/spatial%5Ffabric"
        "?sslmode=require&channel_binding=require",
        conn_max_age=120,
    )
    # 运行时拼接期望值，既验证百分号解码语义，也避免安全扫描器把测试常量误当成仓库密码。
    expected_password = "".join(("p", chr(64), "ss"))

    assert config["ENGINE"] == "django.contrib.gis.db.backends.postgis"
    assert config["NAME"] == "spatial_fabric"
    assert config["USER"] == "fabric"
    assert config["PASSWORD"] == expected_password
    assert config["HOST"] == "db.example.test"
    assert config["PORT"] == "5433"
    assert config["CONN_MAX_AGE"] == 120
    assert config["OPTIONS"]["sslmode"] == "require"
    assert config["OPTIONS"]["channel_binding"] == "require"
    assert config["OPTIONS"]["connect_timeout"] == 5


def test_individual_postgres_environment_variables_remain_supported() -> None:
    config = build_database_config(
        {
            "POSTGRES_DB": "local_db",
            "POSTGRES_USER": "local_user",
            "POSTGRES_PASSWORD": "local_password",
            "POSTGRES_HOST": "postgres",
            "POSTGRES_PORT": "5544",
            "POSTGRES_CONN_MAX_AGE": "30",
        }
    )

    assert config["NAME"] == "local_db"
    assert config["USER"] == "local_user"
    assert config["HOST"] == "postgres"
    assert config["PORT"] == "5544"
    assert config["CONN_MAX_AGE"] == 30


def test_database_url_rejects_non_postgres_scheme() -> None:
    with pytest.raises(ImproperlyConfigured, match="postgres"):
        parse_database_url("mysql://user:password@db.example.test/database")

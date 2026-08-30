"""Spatial Fabric 全环境共享的 Django 设置。

设计原则：
1. base.py 只放所有环境都成立的默认值；
2. local/test/production 只覆盖差异；
3. 业务代码不得直接读取某个云厂商或 GIS Provider 的配置；
4. Secret 一律来自环境变量/SecretProvider，禁止提交到仓库。
"""

from __future__ import annotations

import os
from pathlib import Path

from config.database import build_database_config

BASE_DIR = Path(__file__).resolve().parents[3]
SECRET_KEY = os.getenv("SF_SECRET_KEY", "unsafe-development-key")
DEBUG = False
ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv("SF_ALLOWED_HOSTS", "localhost").split(",")
    if host.strip()
]
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("SF_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]

# 只注册已经开始实现并具备模型/迁移计划的 Fabric 模块；其余模块按 Phase 正式启用。
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.gis",
    "rest_framework",
    "drf_spectacular",
    "spatial_fabric.common",
    "spatial_fabric.iam",
    "spatial_fabric.tenancy",
    "spatial_fabric.governance",
    "spatial_fabric.sharing",
    "spatial_fabric.commercial",
    "spatial_fabric.assets",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "spatial_fabric.common.middleware.request_id.RequestIdMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# PostgreSQL/PostGIS 是默认 System of Record。DATABASE_URL 与 POSTGRES_* 都只是部署输入，
# 由 provider-neutral 适配函数统一转换，业务领域模型不感知具体数据库托管厂商。
DATABASES = {"default": build_database_config()}

# 必须在第一次 migration 前固定；Account 与领域 Principal 保持分离。
AUTH_USER_MODEL = "iam.Account"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_ROOT = BASE_DIR / "media"
MEDIA_URL = "/media/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# 默认仅允许认证用户进入 API；资源级授权后续由 Fabric AuthZ 执行。
REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
}
SPECTACULAR_SETTINGS = {
    "TITLE": "Yujian Spatial Fabric API",
    "DESCRIPTION": "Spatial Fabric enterprise control-plane API",
    "VERSION": "0.1.0-alpha.0",
    "SERVE_INCLUDE_SCHEMA": False,
}
LOG_LEVEL = os.getenv("SF_LOG_LEVEL", "INFO")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"json": {"()": "spatial_fabric.common.logging.JsonFormatter"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "json"}},
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
}
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
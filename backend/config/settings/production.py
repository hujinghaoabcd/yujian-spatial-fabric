from __future__ import annotations

import os

from django.core.exceptions import ImproperlyConfigured

from .base import *

# 不比较 SECRET_KEY 的某个硬编码“哨兵值”，而是直接要求生产环境显式提供 Secret。
# 这样既避免把开发占位值当成安全规则，也能让 Secret 管理边界更清楚。
if not os.getenv("SF_SECRET_KEY"):
    raise ImproperlyConfigured("生产环境必须显式设置 SF_SECRET_KEY")

DEBUG = False
SECURE_SSL_REDIRECT = os.getenv("SF_SECURE_SSL_REDIRECT", "true").lower() == "true"
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = int(os.getenv("SF_HSTS_SECONDS", "31536000"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = False

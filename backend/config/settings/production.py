from __future__ import annotations

import os
from django.core.exceptions import ImproperlyConfigured
from .base import *  # noqa: F403

if SECRET_KEY == "unsafe-development-key":  # noqa: F405
    raise ImproperlyConfigured("生产环境必须显式设置 SF_SECRET_KEY")

DEBUG = False
SECURE_SSL_REDIRECT = os.getenv("SF_SECURE_SSL_REDIRECT", "true").lower() == "true"
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = int(os.getenv("SF_HSTS_SECONDS", "31536000"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = False

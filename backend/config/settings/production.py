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

# 只有明确声明前方存在可信反向代理时，才接受 X-Forwarded-Proto 判断原始请求协议。
# 这使 Render/Nginx/Ingress 等 HTTPS 终止场景不会发生重定向循环，同时避免默认信任客户端
# 可伪造的代理头。正式自建部署应由受信代理覆盖/清洗该请求头后再开启此开关。
if os.getenv("SF_TRUST_PROXY_HEADERS", "false").lower() == "true":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = int(os.getenv("SF_HSTS_SECONDS", "31536000"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = False

from .base import *

DEBUG = False
# 仅测试环境使用快速密码哈希，避免单测在密码计算上浪费时间。
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

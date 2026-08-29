from .base import *

# 本地开发才开启 DEBUG 和 DRF Browsable API；生产设置禁止继承这些行为。
DEBUG = True
REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
}

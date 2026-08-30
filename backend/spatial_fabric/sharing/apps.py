"""Resource Sharing Django application configuration."""

from django.apps import AppConfig


class SharingConfig(AppConfig):
    """单资源显式分享、访问请求与候选授权解析 bounded context。"""

    default_auto_field = "django.db.models.BigAutoField"
    name = "spatial_fabric.sharing"
    verbose_name = "Spatial Fabric 资源分享"

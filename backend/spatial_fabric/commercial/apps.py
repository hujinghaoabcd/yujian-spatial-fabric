"""Commercial Controls Django application configuration."""

from django.apps import AppConfig


class CommercialConfig(AppConfig):
    """商业许可、技术配额、成本预算与使用 reservation/ledger bounded context。"""

    default_auto_field = "django.db.models.BigAutoField"
    name = "spatial_fabric.commercial"
    verbose_name = "Spatial Fabric 商业控制"

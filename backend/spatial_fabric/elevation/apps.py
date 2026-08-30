"""Django app configuration for Phase B2.4 Elevated Access。"""

from django.apps import AppConfig


class ElevationConfig(AppConfig):
    """高风险/临时访问治理 bounded context。"""

    default_auto_field = "django.db.models.BigAutoField"
    name = "spatial_fabric.elevation"
    verbose_name = "Elevated Access｜高风险与临时访问治理"

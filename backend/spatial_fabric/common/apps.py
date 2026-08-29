from django.apps import AppConfig


class CommonConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "spatial_fabric.common"
    verbose_name = "平台公共基础"

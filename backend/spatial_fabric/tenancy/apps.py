from django.apps import AppConfig


class TenancyConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "spatial_fabric.tenancy"
    verbose_name = "租户与项目空间"

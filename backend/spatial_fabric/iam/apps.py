from django.apps import AppConfig


class IamConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "spatial_fabric.iam"
    verbose_name = "身份与访问管理"

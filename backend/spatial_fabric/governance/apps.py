"""Governance Django application configuration."""

from django.apps import AppConfig


class GovernanceConfig(AppConfig):
    """Policy、sharing、entitlement、quota 等治理能力的 Django app。"""

    default_auto_field = "django.db.models.BigAutoField"
    name = "spatial_fabric.governance"
    verbose_name = "Spatial Fabric 治理"

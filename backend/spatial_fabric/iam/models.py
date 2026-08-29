"""身份与访问管理领域第一批模型。

Account 负责 Django 登录认证；Principal 才是 Fabric 权限主体。未来 ServiceAccount、GeoAgent、
ExternalApplication 都可以拥有 Principal，但不应伪装成 Django User。
"""

from __future__ import annotations
from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractUser
from django.db import models
from spatial_fabric.common.ids import uuid7
from spatial_fabric.common.models import ConcurrentModel, TimeStampedModel, UUID7Model


class AccountManager(BaseUserManager["Account"]):
    use_in_migrations = True

    def create_user(self, email: str, password: str | None = None, **extra_fields: object) -> "Account":
        if not email:
            raise ValueError("必须提供邮箱地址。")
        email = self.normalize_email(email)
        account = self.model(email=email, **extra_fields)
        account.set_password(password)
        account.save(using=self._db)
        return account

    def create_superuser(self, email: str, password: str | None = None, **extra_fields: object) -> "Account":
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("超级管理员必须设置 is_staff=True。")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("超级管理员必须设置 is_superuser=True。")
        return self.create_user(email=email, password=password, **extra_fields)


class Account(AbstractUser):
    """Django 登录账户；不要向这里堆 Tenant、Role、Quota、Agent 等领域字段。"""
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    username = None
    email = models.EmailField("邮箱", unique=True)
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []
    objects = AccountManager()

    def __str__(self) -> str:
        return self.email


class PrincipalType(models.TextChoices):
    HUMAN_USER = "HUMAN_USER", "人员"
    SERVICE_ACCOUNT = "SERVICE_ACCOUNT", "服务账户"
    AGENT = "AGENT", "智能体"
    EXTERNAL_APPLICATION = "EXTERNAL_APPLICATION", "外部应用"
    FEDERATED = "FEDERATED", "联合身份"


class PrincipalStatus(models.TextChoices):
    INVITED = "INVITED", "待激活"
    ACTIVE = "ACTIVE", "正常"
    SUSPENDED = "SUSPENDED", "已暂停"
    LOCKED = "LOCKED", "已锁定"
    DEPROVISIONED = "DEPROVISIONED", "已注销"


class Principal(UUID7Model, TimeStampedModel, ConcurrentModel):
    """Fabric 所有授权判断的统一主体。tenant 允许为空以容纳平台级系统主体。"""
    tenant = models.ForeignKey("tenancy.Tenant", on_delete=models.PROTECT, related_name="principals", null=True, blank=True, verbose_name="所属租户")
    account = models.OneToOneField(Account, on_delete=models.PROTECT, related_name="principal", null=True, blank=True, verbose_name="登录账户")
    principal_type = models.CharField("主体类型", max_length=32, choices=PrincipalType.choices)
    status = models.CharField("状态", max_length=24, choices=PrincipalStatus.choices, default=PrincipalStatus.ACTIVE)
    display_name = models.CharField("显示名称", max_length=200)
    description = models.TextField("说明", blank=True)

    class Meta:
        db_table = "sf_principal"
        verbose_name = "权限主体"
        verbose_name_plural = "权限主体"
        indexes = [
            models.Index(fields=["tenant", "status"], name="sf_princ_tenant_status_idx"),
            models.Index(fields=["tenant", "principal_type"], name="sf_princ_tenant_type_idx"),
        ]

    def __str__(self) -> str:
        return self.display_name

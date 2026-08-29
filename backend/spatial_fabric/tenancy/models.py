"""租户、工作空间、项目与环境领域模型。

Workspace/Project/Environment 有意显式冗余 tenant_id，虽然可从父对象推导，但这样更有利于
PostgreSQL RLS、复合索引、查询防护和审计。clean() 只是应用层防线，正式写入还必须经过
Application Service，后续数据库阶段继续评估 RLS/复合约束。
"""

from __future__ import annotations
from django.core.exceptions import ValidationError
from django.db import models
from spatial_fabric.common.models import ConcurrentModel, TimeStampedModel, UUID7Model


class TenantStatus(models.TextChoices):
    PROVISIONING = "PROVISIONING", "开通中"
    ACTIVE = "ACTIVE", "正常"
    SUSPENDED = "SUSPENDED", "已暂停"
    OFFBOARDING = "OFFBOARDING", "下线处理中"
    CLOSED = "CLOSED", "已关闭"


class WorkspaceStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "正常"
    READ_ONLY = "READ_ONLY", "只读"
    ARCHIVED = "ARCHIVED", "已归档"
    TRASHED = "TRASHED", "回收站"


class ProjectStatus(models.TextChoices):
    DRAFT = "DRAFT", "草稿"
    ACTIVE = "ACTIVE", "进行中"
    ON_HOLD = "ON_HOLD", "暂停"
    COMPLETED = "COMPLETED", "已完成"
    ARCHIVED = "ARCHIVED", "已归档"
    TRASHED = "TRASHED", "回收站"


class EnvironmentType(models.TextChoices):
    DEVELOPMENT = "DEVELOPMENT", "开发"
    TEST = "TEST", "测试"
    STAGING = "STAGING", "预发布"
    PRODUCTION = "PRODUCTION", "生产"
    SANDBOX = "SANDBOX", "沙箱"


class EnvironmentStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "正常"
    FROZEN = "FROZEN", "冻结"
    ARCHIVED = "ARCHIVED", "已归档"


class ProtectionLevel(models.TextChoices):
    NORMAL = "NORMAL", "普通"
    PROTECTED = "PROTECTED", "受保护"
    CRITICAL = "CRITICAL", "关键"


class Tenant(UUID7Model, TimeStampedModel, ConcurrentModel):
    """商业、数据隔离、许可、审计和安全治理的最高客户边界。"""
    name = models.CharField("租户名称", max_length=200)
    slug = models.SlugField("租户标识", max_length=80, unique=True)
    status = models.CharField("状态", max_length=24, choices=TenantStatus.choices, default=TenantStatus.PROVISIONING)
    default_locale = models.CharField("默认语言", max_length=32, default="zh-hans")
    default_timezone = models.CharField("默认时区", max_length=64, default="Asia/Shanghai")
    data_residency_policy = models.JSONField("数据驻留策略", default=dict, blank=True)

    class Meta:
        db_table = "sf_tenant"
        verbose_name = "租户"
        verbose_name_plural = "租户"
        indexes = [models.Index(fields=["status"], name="sf_tenant_status_idx")]

    def __str__(self) -> str:
        return self.name


class Workspace(UUID7Model, TimeStampedModel, ConcurrentModel):
    """长期协作空间；不是组织部门，也不是 GeoServer Workspace。"""
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="workspaces", verbose_name="所属租户")
    name = models.CharField("工作空间名称", max_length=200)
    slug = models.SlugField("工作空间标识", max_length=80)
    status = models.CharField("状态", max_length=20, choices=WorkspaceStatus.choices, default=WorkspaceStatus.ACTIVE)
    description = models.TextField("说明", blank=True)

    class Meta:
        db_table = "sf_workspace"
        verbose_name = "工作空间"
        verbose_name_plural = "工作空间"
        constraints = [models.UniqueConstraint(fields=["tenant", "slug"], name="sf_ws_tenant_slug_uniq")]
        indexes = [
            models.Index(fields=["tenant", "status"], name="sf_ws_tenant_status_idx"),
            models.Index(fields=["tenant", "name"], name="sf_ws_tenant_name_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.tenant.slug}/{self.slug}"


class Project(UUID7Model, TimeStampedModel, ConcurrentModel):
    """具体业务、合同、交付或研发项目边界。"""
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="projects", verbose_name="所属租户")
    workspace = models.ForeignKey(Workspace, on_delete=models.PROTECT, related_name="projects", verbose_name="所属工作空间")
    name = models.CharField("项目名称", max_length=200)
    slug = models.SlugField("项目标识", max_length=80)
    status = models.CharField("状态", max_length=20, choices=ProjectStatus.choices, default=ProjectStatus.DRAFT)
    description = models.TextField("说明", blank=True)

    class Meta:
        db_table = "sf_project"
        verbose_name = "项目"
        verbose_name_plural = "项目"
        constraints = [models.UniqueConstraint(fields=["workspace", "slug"], name="sf_project_ws_slug_uniq")]
        indexes = [
            models.Index(fields=["tenant", "status"], name="sf_proj_tenant_status_idx"),
            models.Index(fields=["workspace", "status"], name="sf_proj_ws_status_idx"),
        ]

    def clean(self) -> None:
        super().clean()
        if self.workspace_id and self.tenant_id and self.workspace.tenant_id != self.tenant_id:
            raise ValidationError({"tenant": "Project.tenant 必须与 Workspace.tenant 保持一致。"})

    def __str__(self) -> str:
        return f"{self.workspace}/{self.slug}"


class Environment(UUID7Model, TimeStampedModel, ConcurrentModel):
    """Project 内的 dev/test/staging/production/sandbox 运行环境。"""
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="environments", verbose_name="所属租户")
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="environments", verbose_name="所属项目")
    name = models.CharField("环境名称", max_length=120)
    slug = models.SlugField("环境标识", max_length=64)
    environment_type = models.CharField("环境类型", max_length=20, choices=EnvironmentType.choices)
    status = models.CharField("状态", max_length=20, choices=EnvironmentStatus.choices, default=EnvironmentStatus.ACTIVE)
    protection_level = models.CharField("保护级别", max_length=20, choices=ProtectionLevel.choices, default=ProtectionLevel.NORMAL)

    class Meta:
        db_table = "sf_environment"
        verbose_name = "环境"
        verbose_name_plural = "环境"
        constraints = [models.UniqueConstraint(fields=["project", "slug"], name="sf_env_project_slug_uniq")]
        indexes = [
            models.Index(fields=["tenant", "status"], name="sf_env_tenant_status_idx"),
            models.Index(fields=["project", "environment_type"], name="sf_env_proj_type_idx"),
        ]

    def clean(self) -> None:
        super().clean()
        if self.project_id and self.tenant_id and self.project.tenant_id != self.tenant_id:
            raise ValidationError({"tenant": "Environment.tenant 必须与 Project.tenant 保持一致。"})

    def __str__(self) -> str:
        return f"{self.project}/{self.slug}"

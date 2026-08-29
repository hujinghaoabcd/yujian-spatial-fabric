"""Phase A 跨领域不变量测试。

这些测试优先验证“不能发生什么”：不能跨租户挂 Project、不能让 Alias 指向其他 Asset、
不能让 Distribution 读取其他租户 Artifact。此类约束比普通 CRUD 测试更能保护长期架构。
"""

from uuid import uuid4

import pytest
from django.core.exceptions import ValidationError

from spatial_fabric.assets.models import Artifact, Asset, AssetAlias, AssetVersion, Distribution, DistributionType
from spatial_fabric.iam.models import Account, Principal, PrincipalType
from spatial_fabric.tenancy.models import Environment, EnvironmentType, Project, Tenant, Workspace


def create_principal(tenant: Tenant, name: str) -> Principal:
    """创建测试用机器主体，避免测试依赖登录 Account。"""
    return Principal.objects.create(
        tenant=tenant,
        principal_type=PrincipalType.SERVICE_ACCOUNT,
        display_name=name,
    )


@pytest.mark.django_db
def test_project_must_use_workspace_from_same_tenant() -> None:
    tenant_a = Tenant.objects.create(name="租户 A", slug="tenant-a")
    tenant_b = Tenant.objects.create(name="租户 B", slug="tenant-b")
    workspace = Workspace.objects.create(tenant=tenant_a, name="A 工作空间", slug="workspace-a")

    project = Project(tenant=tenant_b, workspace=workspace, name="错误跨租户项目", slug="invalid-project")
    with pytest.raises(ValidationError):
        project.full_clean()


@pytest.mark.django_db
def test_environment_must_use_project_from_same_tenant() -> None:
    tenant_a = Tenant.objects.create(name="租户 A", slug="tenant-a")
    tenant_b = Tenant.objects.create(name="租户 B", slug="tenant-b")
    workspace = Workspace.objects.create(tenant=tenant_a, name="A 工作空间", slug="workspace-a")
    project = Project.objects.create(tenant=tenant_a, workspace=workspace, name="A 项目", slug="project-a")

    environment = Environment(
        tenant=tenant_b,
        project=project,
        name="错误生产环境",
        slug="prod",
        environment_type=EnvironmentType.PRODUCTION,
    )
    with pytest.raises(ValidationError):
        environment.full_clean()


@pytest.mark.django_db
def test_account_and_principal_have_independent_domain_identity() -> None:
    account = Account.objects.create_user(email="user@example.com", password="safe-test-password")
    tenant = Tenant.objects.create(name="示例租户", slug="example")
    principal = Principal.objects.create(
        tenant=tenant,
        account=account,
        principal_type=PrincipalType.HUMAN_USER,
        display_name="示例用户",
    )

    assert principal.account_id == account.id
    assert principal.id != account.id


@pytest.mark.django_db
def test_asset_scope_cannot_cross_tenant() -> None:
    tenant_a = Tenant.objects.create(name="租户 A", slug="asset-tenant-a")
    tenant_b = Tenant.objects.create(name="租户 B", slug="asset-tenant-b")
    workspace = Workspace.objects.create(tenant=tenant_a, name="A 工作空间", slug="asset-a")
    project = Project.objects.create(tenant=tenant_a, workspace=workspace, name="A 项目", slug="asset-a")
    principal_b = create_principal(tenant_b, "B 服务主体")

    asset = Asset(
        tenant=tenant_b,
        workspace=workspace,
        project=project,
        asset_type="dataset",
        name="非法跨租户数据",
        slug="invalid-dataset",
        owner_principal=principal_b,
        created_by=principal_b,
    )
    with pytest.raises(ValidationError):
        asset.full_clean()


@pytest.mark.django_db
def test_alias_can_only_point_to_version_of_same_asset() -> None:
    tenant = Tenant.objects.create(name="别名租户", slug="alias-tenant")
    principal = create_principal(tenant, "服务主体")
    asset_a = Asset.objects.create(
        tenant=tenant,
        asset_type="dataset",
        name="数据 A",
        slug="dataset-a",
        owner_principal=principal,
        created_by=principal,
    )
    asset_b = Asset.objects.create(
        tenant=tenant,
        asset_type="dataset",
        name="数据 B",
        slug="dataset-b",
        owner_principal=principal,
        created_by=principal,
    )
    version_b = AssetVersion.objects.create(
        tenant=tenant,
        asset=asset_b,
        version_seq=1,
        created_by=principal,
    )

    alias = AssetAlias(asset=asset_a, alias="stable", target_version=version_b, updated_by=principal)
    with pytest.raises(ValidationError):
        alias.full_clean()


@pytest.mark.django_db
def test_distribution_artifact_must_not_cross_tenant() -> None:
    tenant_a = Tenant.objects.create(name="分发租户 A", slug="dist-tenant-a")
    tenant_b = Tenant.objects.create(name="分发租户 B", slug="dist-tenant-b")
    principal_a = create_principal(tenant_a, "A 主体")
    asset = Asset.objects.create(
        tenant=tenant_a,
        asset_type="dataset",
        name="数据 A",
        slug="dist-dataset-a",
        owner_principal=principal_a,
        created_by=principal_a,
    )
    version = AssetVersion.objects.create(
        tenant=tenant_a,
        asset=asset,
        version_seq=1,
        created_by=principal_a,
    )
    foreign_artifact = Artifact.objects.create(
        tenant=tenant_b,
        digest_algorithm="sha256",
        digest="0" * 64,
        size=1,
        storage_pool_ref=uuid4(),
        storage_object_ref="objects/test.bin",
    )

    distribution = Distribution(
        tenant=tenant_a,
        asset_version=version,
        distribution_type=DistributionType.FILE,
        format="binary",
        artifact=foreign_artifact,
    )
    with pytest.raises(ValidationError):
        distribution.full_clean()

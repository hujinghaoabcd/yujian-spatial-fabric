"""Phase B1 IAM / RBAC 领域不变量测试。

这里优先验证“不能发生什么”，而不是只测试普通 CRUD：

- Group / Role / Scope 不能跨 Tenant；
- RoleAssignment 必须 principal/group 二选一；
- Tenant / Workspace / Project / Environment scope 不能混写；
- ``execute`` 与 ``download`` 必须保持独立；
- Django superuser 不能自动变成 Fabric admin；
- Agent 与 Human 使用同一 Principal/RoleAssignment 体系。
"""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from spatial_fabric.iam.models import (
    Account,
    Group,
    GroupMembership,
    GroupType,
    MembershipStatus,
    Principal,
    PrincipalType,
    Privilege,
    RoleAssignment,
    RoleAssignmentStatus,
    RoleDefinition,
    RolePrivilege,
    RoleScopeType,
)
from spatial_fabric.tenancy.models import Environment, EnvironmentType, Project, Tenant, Workspace


def create_principal(
    tenant: Tenant,
    name: str,
    *,
    principal_type: PrincipalType = PrincipalType.HUMAN_USER,
) -> Principal:
    """创建不依赖 Django Account 的 Fabric Principal。"""

    return Principal.objects.create(
        tenant=tenant,
        principal_type=principal_type,
        display_name=name,
    )


def create_tenant_tree(slug: str) -> tuple[Tenant, Workspace, Project, Environment]:
    """创建完整 Tenant → Workspace → Project → Environment 测试层级。"""

    tenant = Tenant.objects.create(name=f"租户 {slug}", slug=slug)
    workspace = Workspace.objects.create(
        tenant=tenant,
        name=f"工作空间 {slug}",
        slug=f"ws-{slug}",
    )
    project = Project.objects.create(
        tenant=tenant,
        workspace=workspace,
        name=f"项目 {slug}",
        slug=f"project-{slug}",
    )
    environment = Environment.objects.create(
        tenant=tenant,
        project=project,
        name=f"生产环境 {slug}",
        slug="prod",
        environment_type=EnvironmentType.PRODUCTION,
    )
    return tenant, workspace, project, environment


def create_role(
    *,
    tenant: Tenant | None,
    key: str,
    created_by: Principal | None = None,
    scopes: list[str] | None = None,
) -> RoleDefinition:
    """创建平台或 Tenant Role。"""

    return RoleDefinition.objects.create(
        tenant=tenant,
        key=key,
        name=key,
        created_by=created_by,
        allowed_scope_types=scopes or [],
    )


@pytest.mark.django_db
def test_execute_and_download_are_independent_privileges() -> None:
    """能执行专业模型，不代表能下载模型或数据制品。

    ``execute`` / ``download`` 已由 ``iam/0003_seed_core_privileges`` 注册为平台参考数据，
    测试必须复用该稳定词汇，不能重新创建同名 Privilege。这里真正验证的是：Role 只获得
    显式绑定的 ``execute``，不会因为另一个核心动作 ``download`` 存在就自动继承它。
    """

    tenant, _, _, _ = create_tenant_tree("priv-separation")
    admin = create_principal(tenant, "租户管理员")
    execute = Privilege.objects.get(key="execute")
    download = Privilege.objects.get(key="download")
    role = create_role(tenant=tenant, key="model-operator", created_by=admin)
    RolePrivilege.objects.create(role=role, privilege=execute)

    role_keys = set(role.role_privileges.values_list("privilege__key", flat=True))
    assert "execute" in role_keys
    assert "download" not in role_keys
    assert execute.id != download.id


@pytest.mark.django_db
def test_group_membership_cannot_cross_tenant() -> None:
    tenant_a, _, _, _ = create_tenant_tree("group-a")
    tenant_b, _, _, _ = create_tenant_tree("group-b")
    admin_a = create_principal(tenant_a, "A 管理员")
    member_b = create_principal(tenant_b, "B 成员")
    group = Group.objects.create(
        tenant=tenant_a,
        name="A 安全组",
        slug="security",
        group_type=GroupType.SECURITY,
        created_by=admin_a,
    )

    membership = GroupMembership(
        tenant=tenant_a,
        group=group,
        principal=member_b,
        added_by=admin_a,
    )
    with pytest.raises(ValidationError):
        membership.full_clean()


@pytest.mark.django_db
def test_group_membership_rejects_invalid_time_window() -> None:
    tenant, _, _, _ = create_tenant_tree("group-window")
    admin = create_principal(tenant, "管理员")
    member = create_principal(tenant, "成员")
    group = Group.objects.create(
        tenant=tenant,
        name="临时成员组",
        slug="temporary",
        created_by=admin,
    )
    now = timezone.now()
    membership = GroupMembership(
        tenant=tenant,
        group=group,
        principal=member,
        added_by=admin,
        valid_from=now,
        valid_until=now - timedelta(minutes=1),
    )

    with pytest.raises(ValidationError):
        membership.full_clean()


@pytest.mark.django_db
def test_tenant_role_cannot_be_assigned_in_other_tenant() -> None:
    tenant_a, _, _, _ = create_tenant_tree("role-a")
    tenant_b, _, _, _ = create_tenant_tree("role-b")
    admin_a = create_principal(tenant_a, "A 管理员")
    admin_b = create_principal(tenant_b, "B 管理员")
    role_a = create_role(tenant=tenant_a, key="tenant-a-admin", created_by=admin_a)

    assignment = RoleAssignment(
        tenant=tenant_b,
        principal=admin_b,
        role=role_a,
        scope_type=RoleScopeType.TENANT,
        granted_by=admin_b,
    )
    with pytest.raises(ValidationError):
        assignment.full_clean()


@pytest.mark.django_db
def test_platform_role_can_be_used_in_tenant_scope() -> None:
    tenant, _, _, _ = create_tenant_tree("platform-role")
    tenant_admin = create_principal(tenant, "租户管理员")
    platform_role = create_role(
        tenant=None,
        key="platform-viewer-template",
        created_by=None,
        scopes=[RoleScopeType.TENANT],
    )
    assignment = RoleAssignment(
        tenant=tenant,
        principal=tenant_admin,
        role=platform_role,
        scope_type=RoleScopeType.TENANT,
        granted_by=tenant_admin,
    )

    assignment.full_clean()


@pytest.mark.django_db
def test_role_assignment_requires_exactly_one_subject() -> None:
    tenant, _, _, _ = create_tenant_tree("subject-xor")
    admin = create_principal(tenant, "管理员")
    member = create_principal(tenant, "成员")
    group = Group.objects.create(
        tenant=tenant,
        name="双主体测试组",
        slug="xor-group",
        created_by=admin,
    )
    role = create_role(tenant=tenant, key="viewer", created_by=admin)

    both = RoleAssignment(
        tenant=tenant,
        principal=member,
        group=group,
        role=role,
        scope_type=RoleScopeType.TENANT,
        granted_by=admin,
    )
    with pytest.raises(ValidationError):
        both.full_clean()

    neither = RoleAssignment(
        tenant=tenant,
        role=role,
        scope_type=RoleScopeType.TENANT,
        granted_by=admin,
    )
    with pytest.raises(ValidationError):
        neither.full_clean()


@pytest.mark.django_db
def test_role_assignment_scope_shape_is_strict() -> None:
    tenant, workspace, project, _ = create_tenant_tree("scope-shape")
    admin = create_principal(tenant, "管理员")
    role = create_role(tenant=tenant, key="project-reader", created_by=admin)

    # PROJECT scope 不能同时填写 Workspace；scope 只能表达一个层级。
    assignment = RoleAssignment(
        tenant=tenant,
        principal=admin,
        role=role,
        scope_type=RoleScopeType.PROJECT,
        workspace=workspace,
        project=project,
        granted_by=admin,
    )
    with pytest.raises(ValidationError):
        assignment.full_clean()


@pytest.mark.django_db
def test_role_assignment_scope_must_belong_to_same_tenant() -> None:
    tenant_a, _, _, _ = create_tenant_tree("scope-a")
    tenant_b, workspace_b, _, _ = create_tenant_tree("scope-b")
    admin_a = create_principal(tenant_a, "A 管理员")
    role = create_role(tenant=tenant_a, key="workspace-reader", created_by=admin_a)

    assignment = RoleAssignment(
        tenant=tenant_a,
        principal=admin_a,
        role=role,
        scope_type=RoleScopeType.WORKSPACE,
        workspace=workspace_b,
        granted_by=admin_a,
    )
    with pytest.raises(ValidationError):
        assignment.full_clean()

    assert workspace_b.tenant_id == tenant_b.id


@pytest.mark.django_db
def test_role_can_limit_allowed_scope_types() -> None:
    tenant, workspace, project, _ = create_tenant_tree("allowed-scope")
    admin = create_principal(tenant, "管理员")
    role = create_role(
        tenant=tenant,
        key="project-only-operator",
        created_by=admin,
        scopes=[RoleScopeType.PROJECT],
    )

    invalid = RoleAssignment(
        tenant=tenant,
        principal=admin,
        role=role,
        scope_type=RoleScopeType.WORKSPACE,
        workspace=workspace,
        granted_by=admin,
    )
    with pytest.raises(ValidationError):
        invalid.full_clean()

    valid = RoleAssignment(
        tenant=tenant,
        principal=admin,
        role=role,
        scope_type=RoleScopeType.PROJECT,
        project=project,
        granted_by=admin,
    )
    valid.full_clean()


@pytest.mark.django_db
def test_revoked_or_expired_assignment_is_not_effective() -> None:
    tenant, _, _, _ = create_tenant_tree("assignment-window")
    admin = create_principal(tenant, "管理员")
    role = create_role(tenant=tenant, key="temporary-operator", created_by=admin)
    now = timezone.now()

    active = RoleAssignment(
        tenant=tenant,
        principal=admin,
        role=role,
        scope_type=RoleScopeType.TENANT,
        granted_by=admin,
        valid_from=now - timedelta(minutes=5),
        valid_until=now + timedelta(minutes=5),
    )
    assert active.is_currently_effective(at=now)

    active.status = RoleAssignmentStatus.REVOKED
    assert not active.is_currently_effective(at=now)

    active.status = RoleAssignmentStatus.ACTIVE
    active.valid_until = now - timedelta(seconds=1)
    assert not active.is_currently_effective(at=now)


@pytest.mark.django_db
def test_group_role_is_not_copied_to_member_direct_assignment() -> None:
    tenant, _, project, _ = create_tenant_tree("group-role")
    admin = create_principal(tenant, "管理员")
    member = create_principal(tenant, "分析员")
    group = Group.objects.create(
        tenant=tenant,
        name="分析团队",
        slug="analysts",
        created_by=admin,
    )
    GroupMembership.objects.create(
        tenant=tenant,
        group=group,
        principal=member,
        added_by=admin,
        status=MembershipStatus.ACTIVE,
    )
    role = create_role(tenant=tenant, key="project-analyst", created_by=admin)
    RoleAssignment.objects.create(
        tenant=tenant,
        group=group,
        role=role,
        scope_type=RoleScopeType.PROJECT,
        project=project,
        granted_by=admin,
    )

    assert not RoleAssignment.objects.filter(principal=member, role=role).exists()
    assert RoleAssignment.objects.filter(group=group, role=role).count() == 1


@pytest.mark.django_db
def test_django_superuser_is_not_automatically_fabric_admin() -> None:
    """Django Admin 技术超级用户与 Fabric 业务授权必须保持分离。"""

    account = Account.objects.create_superuser(
        email="django-admin@example.com",
        password="not-a-production-secret",
    )
    tenant, _, _, _ = create_tenant_tree("superuser-boundary")
    principal = Principal.objects.create(
        tenant=tenant,
        account=account,
        principal_type=PrincipalType.HUMAN_USER,
        display_name="Django 管理员",
    )

    assert account.is_superuser is True
    assert principal.direct_role_assignments.count() == 0


@pytest.mark.django_db
def test_agent_uses_same_principal_role_assignment_model() -> None:
    tenant, _, project, _ = create_tenant_tree("agent-role")
    admin = create_principal(tenant, "管理员")
    agent = create_principal(
        tenant,
        "项目 GeoAgent",
        principal_type=PrincipalType.AGENT,
    )
    role = create_role(tenant=tenant, key="agent-executor", created_by=admin)
    assignment = RoleAssignment(
        tenant=tenant,
        principal=agent,
        role=role,
        scope_type=RoleScopeType.PROJECT,
        project=project,
        granted_by=admin,
    )

    assignment.full_clean()
    assert agent.principal_type == PrincipalType.AGENT

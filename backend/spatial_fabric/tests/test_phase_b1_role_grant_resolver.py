"""Phase B1 RoleGrantResolver 行为与安全边界测试。"""

from datetime import timedelta

import pytest
from django.utils import timezone

from spatial_fabric.iam.models import (
    Account,
    Group,
    GroupMembership,
    GroupStatus,
    MembershipStatus,
    Principal,
    PrincipalType,
    Privilege,
    PrivilegeStatus,
    RoleAssignment,
    RoleAssignmentStatus,
    RoleDefinition,
    RolePrivilege,
    RoleScopeType,
    RoleStatus,
)
from spatial_fabric.iam.services import (
    AuthorizationScope,
    GrantSourceType,
    InvalidAuthorizationScope,
    RoleGrantResolver,
)
from spatial_fabric.tenancy.models import Environment, EnvironmentType, Project, Tenant, Workspace


def create_principal(
    tenant: Tenant,
    name: str,
    *,
    principal_type: PrincipalType = PrincipalType.HUMAN_USER,
    account: Account | None = None,
) -> Principal:
    return Principal.objects.create(
        tenant=tenant,
        account=account,
        principal_type=principal_type,
        display_name=name,
    )


def create_tree(slug: str) -> tuple[Tenant, Workspace, Project, Environment]:
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


def create_role_with_privilege(
    *,
    tenant: Tenant,
    admin: Principal,
    key: str,
    privilege_key: str,
    status: RoleStatus = RoleStatus.ACTIVE,
) -> RoleDefinition:
    role = RoleDefinition.objects.create(
        tenant=tenant,
        key=key,
        name=key,
        created_by=admin,
        status=status,
    )
    RolePrivilege.objects.create(role=role, privilege=Privilege.objects.get(key=privilege_key))
    return role


def assign_direct(
    *,
    tenant: Tenant,
    principal: Principal,
    role: RoleDefinition,
    admin: Principal,
    scope_type: RoleScopeType,
    workspace: Workspace | None = None,
    project: Project | None = None,
    environment: Environment | None = None,
    status: RoleAssignmentStatus = RoleAssignmentStatus.ACTIVE,
    conditions: dict[str, object] | None = None,
) -> RoleAssignment:
    return RoleAssignment.objects.create(
        tenant=tenant,
        principal=principal,
        role=role,
        scope_type=scope_type,
        workspace=workspace,
        project=project,
        environment=environment,
        granted_by=admin,
        status=status,
        conditions=conditions or {},
    )


@pytest.mark.django_db
def test_tenant_grant_inherits_to_all_descendant_scopes() -> None:
    tenant, workspace, project, environment = create_tree("tenant-inherit")
    admin = create_principal(tenant, "管理员")
    user = create_principal(tenant, "分析员")
    role = create_role_with_privilege(
        tenant=tenant,
        admin=admin,
        key="tenant-reader",
        privilege_key="read",
    )
    assign_direct(
        tenant=tenant,
        principal=user,
        role=role,
        admin=admin,
        scope_type=RoleScopeType.TENANT,
    )
    resolver = RoleGrantResolver()

    tenant_result = resolver.resolve(
        principal_id=user.id,
        scope=AuthorizationScope(tenant_id=tenant.id),
    )
    workspace_result = resolver.resolve(
        principal_id=user.id,
        scope=AuthorizationScope(tenant_id=tenant.id, workspace_id=workspace.id),
    )
    project_result = resolver.resolve(
        principal_id=user.id,
        scope=AuthorizationScope(tenant_id=tenant.id, project_id=project.id),
    )
    environment_result = resolver.resolve(
        principal_id=user.id,
        scope=AuthorizationScope(tenant_id=tenant.id, environment_id=environment.id),
    )

    assert tenant_result.effective_privilege_keys == frozenset({"read"})
    assert workspace_result.effective_privilege_keys == frozenset({"read"})
    assert project_result.effective_privilege_keys == frozenset({"read"})
    assert environment_result.effective_privilege_keys == frozenset({"read"})
    assert tenant_result.grants[0].inherited_from is None
    assert environment_result.grants[0].inherited_from is not None
    assert environment_result.grants[0].inherited_from.scope_type == RoleScopeType.TENANT


@pytest.mark.django_db
def test_workspace_grant_inherits_only_inside_its_workspace() -> None:
    tenant, workspace_a, project_a, environment_a = create_tree("workspace-inherit")
    workspace_b = Workspace.objects.create(tenant=tenant, name="工作空间 B", slug="ws-b")
    project_b = Project.objects.create(
        tenant=tenant,
        workspace=workspace_b,
        name="项目 B",
        slug="project-b",
    )
    environment_b = Environment.objects.create(
        tenant=tenant,
        project=project_b,
        name="B 生产环境",
        slug="prod",
        environment_type=EnvironmentType.PRODUCTION,
    )
    admin = create_principal(tenant, "管理员")
    user = create_principal(tenant, "空间分析员")
    role = create_role_with_privilege(
        tenant=tenant,
        admin=admin,
        key="workspace-query",
        privilege_key="query",
    )
    assign_direct(
        tenant=tenant,
        principal=user,
        role=role,
        admin=admin,
        scope_type=RoleScopeType.WORKSPACE,
        workspace=workspace_a,
    )
    resolver = RoleGrantResolver()

    in_project = resolver.resolve(
        principal_id=user.id,
        scope=AuthorizationScope(tenant_id=tenant.id, project_id=project_a.id),
    )
    in_environment = resolver.resolve(
        principal_id=user.id,
        scope=AuthorizationScope(tenant_id=tenant.id, environment_id=environment_a.id),
    )
    other_project = resolver.resolve(
        principal_id=user.id,
        scope=AuthorizationScope(tenant_id=tenant.id, project_id=project_b.id),
    )
    other_environment = resolver.resolve(
        principal_id=user.id,
        scope=AuthorizationScope(tenant_id=tenant.id, environment_id=environment_b.id),
    )

    assert "query" in in_project.effective_privilege_keys
    assert "query" in in_environment.effective_privilege_keys
    assert "query" not in other_project.effective_privilege_keys
    assert "query" not in other_environment.effective_privilege_keys


@pytest.mark.django_db
def test_project_and_environment_scope_inheritance_stays_directional() -> None:
    tenant, _, project, environment_a = create_tree("project-env-inherit")
    environment_b = Environment.objects.create(
        tenant=tenant,
        project=project,
        name="测试环境",
        slug="test",
        environment_type=EnvironmentType.TEST,
    )
    admin = create_principal(tenant, "管理员")
    user = create_principal(tenant, "模型操作员")
    project_role = create_role_with_privilege(
        tenant=tenant,
        admin=admin,
        key="project-executor",
        privilege_key="execute",
    )
    environment_role = create_role_with_privilege(
        tenant=tenant,
        admin=admin,
        key="environment-editor",
        privilege_key="edit",
    )
    assign_direct(
        tenant=tenant,
        principal=user,
        role=project_role,
        admin=admin,
        scope_type=RoleScopeType.PROJECT,
        project=project,
    )
    assign_direct(
        tenant=tenant,
        principal=user,
        role=environment_role,
        admin=admin,
        scope_type=RoleScopeType.ENVIRONMENT,
        environment=environment_a,
    )
    resolver = RoleGrantResolver()

    result_a = resolver.resolve(
        principal_id=user.id,
        scope=AuthorizationScope(tenant_id=tenant.id, environment_id=environment_a.id),
    )
    result_b = resolver.resolve(
        principal_id=user.id,
        scope=AuthorizationScope(tenant_id=tenant.id, environment_id=environment_b.id),
    )

    assert result_a.effective_privilege_keys == frozenset({"execute", "edit"})
    assert result_b.effective_privilege_keys == frozenset({"execute"})


@pytest.mark.django_db
def test_direct_and_group_grants_merge_without_losing_evidence() -> None:
    tenant, _, project, _ = create_tree("evidence")
    admin = create_principal(tenant, "管理员")
    user = create_principal(tenant, "成员")
    group = Group.objects.create(
        tenant=tenant,
        name="分析组",
        slug="analysts",
        created_by=admin,
    )
    GroupMembership.objects.create(
        tenant=tenant,
        group=group,
        principal=user,
        added_by=admin,
    )
    direct_role = create_role_with_privilege(
        tenant=tenant,
        admin=admin,
        key="direct-reader",
        privilege_key="read",
    )
    group_role = create_role_with_privilege(
        tenant=tenant,
        admin=admin,
        key="group-reader",
        privilege_key="read",
    )
    assign_direct(
        tenant=tenant,
        principal=user,
        role=direct_role,
        admin=admin,
        scope_type=RoleScopeType.TENANT,
    )
    RoleAssignment.objects.create(
        tenant=tenant,
        group=group,
        role=group_role,
        scope_type=RoleScopeType.PROJECT,
        project=project,
        granted_by=admin,
    )

    result = RoleGrantResolver().resolve(
        principal_id=user.id,
        scope=AuthorizationScope(tenant_id=tenant.id, project_id=project.id),
    )
    read_evidence = [grant for grant in result.grants if grant.privilege_key == "read"]

    assert result.effective_privilege_keys == frozenset({"read"})
    assert len(read_evidence) == 2
    assert {grant.source_type for grant in read_evidence} == {
        GrantSourceType.DIRECT,
        GrantSourceType.GROUP,
    }
    assert any(grant.group_id == group.id for grant in read_evidence)


@pytest.mark.django_db
def test_expired_membership_and_inactive_group_do_not_produce_grants() -> None:
    tenant, _, project, _ = create_tree("membership-window")
    admin = create_principal(tenant, "管理员")
    user = create_principal(tenant, "成员")
    group = Group.objects.create(
        tenant=tenant,
        name="临时组",
        slug="temporary",
        created_by=admin,
    )
    membership = GroupMembership.objects.create(
        tenant=tenant,
        group=group,
        principal=user,
        added_by=admin,
        valid_until=timezone.now() - timedelta(seconds=1),
    )
    role = create_role_with_privilege(
        tenant=tenant,
        admin=admin,
        key="group-share",
        privilege_key="share",
    )
    RoleAssignment.objects.create(
        tenant=tenant,
        group=group,
        role=role,
        scope_type=RoleScopeType.PROJECT,
        project=project,
        granted_by=admin,
    )
    resolver = RoleGrantResolver()
    scope = AuthorizationScope(tenant_id=tenant.id, project_id=project.id)

    assert "share" not in resolver.resolve(principal_id=user.id, scope=scope).effective_privilege_keys

    membership.valid_until = None
    membership.status = MembershipStatus.ACTIVE
    membership.save(update_fields=["valid_until", "status"])
    group.status = GroupStatus.SUSPENDED
    group.save(update_fields=["status"])

    assert "share" not in resolver.resolve(principal_id=user.id, scope=scope).effective_privilege_keys


@pytest.mark.django_db
def test_revoked_assignment_deprecated_role_and_privilege_fail_closed() -> None:
    tenant, _, project, _ = create_tree("inactive-grants")
    admin = create_principal(tenant, "管理员")
    user = create_principal(tenant, "成员")
    revoked_role = create_role_with_privilege(
        tenant=tenant,
        admin=admin,
        key="revoked-reader",
        privilege_key="read",
    )
    assign_direct(
        tenant=tenant,
        principal=user,
        role=revoked_role,
        admin=admin,
        scope_type=RoleScopeType.PROJECT,
        project=project,
        status=RoleAssignmentStatus.REVOKED,
    )
    deprecated_role = create_role_with_privilege(
        tenant=tenant,
        admin=admin,
        key="deprecated-query",
        privilege_key="query",
        status=RoleStatus.DEPRECATED,
    )
    assign_direct(
        tenant=tenant,
        principal=user,
        role=deprecated_role,
        admin=admin,
        scope_type=RoleScopeType.PROJECT,
        project=project,
    )
    download = Privilege.objects.get(key="download")
    download.status = PrivilegeStatus.DEPRECATED
    download.save(update_fields=["status"])
    active_role = create_role_with_privilege(
        tenant=tenant,
        admin=admin,
        key="deprecated-privilege",
        privilege_key="download",
    )
    assign_direct(
        tenant=tenant,
        principal=user,
        role=active_role,
        admin=admin,
        scope_type=RoleScopeType.PROJECT,
        project=project,
    )

    result = RoleGrantResolver().resolve(
        principal_id=user.id,
        scope=AuthorizationScope(tenant_id=tenant.id, project_id=project.id),
    )

    assert "read" not in result.effective_privilege_keys
    assert "query" not in result.effective_privilege_keys
    assert "download" not in result.effective_privilege_keys


@pytest.mark.django_db
def test_django_superuser_without_fabric_role_still_has_no_grants() -> None:
    account = Account.objects.create_superuser(
        email="resolver-admin@example.com",
        password="not-a-production-secret",
    )
    tenant, _, _, _ = create_tree("resolver-superuser")
    principal = create_principal(tenant, "Django 管理员", account=account)

    result = RoleGrantResolver().resolve(
        principal_id=principal.id,
        scope=AuthorizationScope(tenant_id=tenant.id),
    )

    assert account.is_superuser is True
    assert result.grants == ()
    assert result.effective_privilege_keys == frozenset()


@pytest.mark.django_db
def test_agent_uses_same_role_grant_resolution_algorithm() -> None:
    tenant, _, project, _ = create_tree("resolver-agent")
    admin = create_principal(tenant, "管理员")
    agent = create_principal(tenant, "GeoAgent", principal_type=PrincipalType.AGENT)
    role = create_role_with_privilege(
        tenant=tenant,
        admin=admin,
        key="agent-executor",
        privilege_key="execute",
    )
    assign_direct(
        tenant=tenant,
        principal=agent,
        role=role,
        admin=admin,
        scope_type=RoleScopeType.PROJECT,
        project=project,
    )

    result = RoleGrantResolver().resolve(
        principal_id=agent.id,
        scope=AuthorizationScope(tenant_id=tenant.id, project_id=project.id),
    )

    assert agent.principal_type == PrincipalType.AGENT
    assert result.effective_privilege_keys == frozenset({"execute"})


@pytest.mark.django_db
def test_cross_tenant_scope_or_principal_is_rejected() -> None:
    tenant_a, _, _, _ = create_tree("resolver-cross-a")
    tenant_b, workspace_b, _, _ = create_tree("resolver-cross-b")
    principal_a = create_principal(tenant_a, "A 用户")
    principal_b = create_principal(tenant_b, "B 用户")
    resolver = RoleGrantResolver()

    with pytest.raises(InvalidAuthorizationScope):
        resolver.resolve(
            principal_id=principal_a.id,
            scope=AuthorizationScope(tenant_id=tenant_a.id, workspace_id=workspace_b.id),
        )

    with pytest.raises(InvalidAuthorizationScope):
        resolver.resolve(
            principal_id=principal_b.id,
            scope=AuthorizationScope(tenant_id=tenant_a.id),
        )


@pytest.mark.django_db
def test_nonempty_assignment_conditions_are_fail_closed_until_evaluator_exists() -> None:
    tenant, _, project, _ = create_tree("resolver-condition")
    admin = create_principal(tenant, "管理员")
    user = create_principal(tenant, "成员")
    role = create_role_with_privilege(
        tenant=tenant,
        admin=admin,
        key="conditional-exporter",
        privilege_key="export",
    )
    assign_direct(
        tenant=tenant,
        principal=user,
        role=role,
        admin=admin,
        scope_type=RoleScopeType.PROJECT,
        project=project,
        conditions={"network_zone": "trusted"},
    )

    result = RoleGrantResolver().resolve(
        principal_id=user.id,
        scope=AuthorizationScope(tenant_id=tenant.id, project_id=project.id),
    )

    assert "export" not in result.effective_privilege_keys

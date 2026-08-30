"""Phase B1 RBAC grant 解析服务。

RoleGrantResolver 是 Fabric 领域代码解析层级 RBAC 的唯一入口。它只回答：

    在给定 Principal、时间点和管理 Scope 下，哪些 Role/Privilege GRANT 有效？

它**不**回答最终动作是否允许。Policy、Entitlement、Quota、Approval、Delegation、ShareGrant
属于后续 AuthorizationService 的其他输入，禁止为了方便提前塞进本模块。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from django.db.models import Q
from django.utils import timezone

from spatial_fabric.iam.models import (
    GroupMembership,
    GroupStatus,
    MembershipStatus,
    Principal,
    PrivilegeStatus,
    RoleAssignment,
    RoleAssignmentStatus,
    RoleDefinition,
    RolePrivilege,
    RoleScopeType,
    RoleStatus,
)
from spatial_fabric.tenancy.models import Environment, Project, Tenant, Workspace


class RoleGrantResolutionError(ValueError):
    """RoleGrantResolver 输入或持久化授权事实无法安全解析。"""


class InvalidAuthorizationScope(RoleGrantResolutionError):
    """AuthorizationScope 不是同一 Tenant 下的一条合法层级链。"""


class GrantSourceType(StrEnum):
    """一条有效 Grant 的主体来源。"""

    DIRECT = "DIRECT"
    GROUP = "GROUP"


@dataclass(frozen=True, slots=True)
class AuthorizationScope:
    """授权解析目标 Scope。

    调用方可以只给最深层 ID；Resolver 会从数据库推导其祖先层级。若调用方同时提供祖先 ID，
    则必须与真实 Tenant → Workspace → Project → Environment 链一致，否则 fail closed。
    """

    tenant_id: UUID
    workspace_id: UUID | None = None
    project_id: UUID | None = None
    environment_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class ResolvedScopeRef:
    """已经校验过的一个管理 Scope 引用。"""

    scope_type: RoleScopeType
    scope_id: UUID


@dataclass(frozen=True, slots=True)
class ResolvedRoleGrant:
    """一条可解释的 Role → Privilege 授权证据。"""

    role_id: UUID
    role_key: str
    privilege_id: UUID
    privilege_key: str
    source_type: GrantSourceType
    assignment_id: UUID
    group_id: UUID | None
    assignment_scope: ResolvedScopeRef
    inherited_from: ResolvedScopeRef | None


@dataclass(frozen=True, slots=True)
class RoleGrantResolution:
    """一次 B1 RBAC 解析结果；重复 Privilege evidence 不会被丢失。"""

    principal_id: UUID
    target_scope: ResolvedScopeRef
    resolved_at: datetime
    grants: tuple[ResolvedRoleGrant, ...]

    @property
    def effective_privilege_keys(self) -> frozenset[str]:
        """返回去重后的有效 Privilege key，供后续 AuthorizationService 组合判断。"""

        return frozenset(grant.privilege_key for grant in self.grants)

    @property
    def effective_role_ids(self) -> frozenset[UUID]:
        """返回去重后的有效 Role ID。"""

        return frozenset(grant.role_id for grant in self.grants)


@dataclass(frozen=True, slots=True)
class _ResolvedTargetScope:
    """Resolver 内部使用的完整祖先链。"""

    tenant_id: UUID
    workspace_id: UUID | None
    project_id: UUID | None
    environment_id: UUID | None
    target: ResolvedScopeRef


class RoleGrantResolver:
    """解析 Direct + Group + hierarchical scope inheritance 的 B1 GRANT。"""

    def resolve(
        self,
        *,
        principal_id: UUID,
        scope: AuthorizationScope,
        at: datetime | None = None,
    ) -> RoleGrantResolution:
        """解析某 Principal 在目标 Scope 上的全部有效 RBAC grant。

        B1 对 ``RoleAssignment.conditions`` 采用 fail-closed：只有空条件的无条件 assignment 会在
        本解析器中生效。未来如引入正式低复杂度条件语义，应使用独立条件 evaluator；在条件尚未
        定义时绝不能把非空 JSON 当作无条件允许。
        """

        moment = at or timezone.now()
        target = self._resolve_target_scope(scope)
        self._validate_principal(principal_id=principal_id, tenant_id=target.tenant_id)

        group_ids = self._effective_group_ids(
            principal_id=principal_id,
            tenant_id=target.tenant_id,
            at=moment,
        )
        assignments = self._effective_assignments(
            principal_id=principal_id,
            group_ids=group_ids,
            target=target,
            at=moment,
        )
        grants = self._expand_role_privileges(assignments=assignments, target=target)

        return RoleGrantResolution(
            principal_id=principal_id,
            target_scope=target.target,
            resolved_at=moment,
            grants=grants,
        )

    def _resolve_target_scope(self, scope: AuthorizationScope) -> _ResolvedTargetScope:
        """校验并补全目标 Scope 的真实祖先链。"""

        try:
            Tenant.objects.only("id").get(pk=scope.tenant_id)
        except Tenant.DoesNotExist as exc:
            raise InvalidAuthorizationScope("AuthorizationScope.tenant_id 不存在。") from exc

        workspace_id = scope.workspace_id
        project_id = scope.project_id
        environment_id = scope.environment_id

        if environment_id is not None:
            try:
                environment = Environment.objects.select_related("project__workspace").get(
                    pk=environment_id
                )
            except Environment.DoesNotExist as exc:
                raise InvalidAuthorizationScope("AuthorizationScope.environment_id 不存在。") from exc

            if environment.tenant_id != scope.tenant_id:
                raise InvalidAuthorizationScope("Environment 不属于 AuthorizationScope.tenant。")
            if project_id is not None and project_id != environment.project_id:
                raise InvalidAuthorizationScope("Environment 与显式 project_id 不属于同一层级链。")
            inferred_workspace_id = environment.project.workspace_id
            if workspace_id is not None and workspace_id != inferred_workspace_id:
                raise InvalidAuthorizationScope("Environment 与显式 workspace_id 不属于同一层级链。")

            project_id = environment.project_id
            workspace_id = inferred_workspace_id
            target_ref = ResolvedScopeRef(RoleScopeType.ENVIRONMENT, environment.id)

        elif project_id is not None:
            try:
                project = Project.objects.get(pk=project_id)
            except Project.DoesNotExist as exc:
                raise InvalidAuthorizationScope("AuthorizationScope.project_id 不存在。") from exc

            if project.tenant_id != scope.tenant_id:
                raise InvalidAuthorizationScope("Project 不属于 AuthorizationScope.tenant。")
            if workspace_id is not None and workspace_id != project.workspace_id:
                raise InvalidAuthorizationScope("Project 与显式 workspace_id 不属于同一层级链。")

            workspace_id = project.workspace_id
            target_ref = ResolvedScopeRef(RoleScopeType.PROJECT, project.id)

        elif workspace_id is not None:
            try:
                workspace = Workspace.objects.get(pk=workspace_id)
            except Workspace.DoesNotExist as exc:
                raise InvalidAuthorizationScope("AuthorizationScope.workspace_id 不存在。") from exc

            if workspace.tenant_id != scope.tenant_id:
                raise InvalidAuthorizationScope("Workspace 不属于 AuthorizationScope.tenant。")
            target_ref = ResolvedScopeRef(RoleScopeType.WORKSPACE, workspace.id)

        else:
            target_ref = ResolvedScopeRef(RoleScopeType.TENANT, scope.tenant_id)

        return _ResolvedTargetScope(
            tenant_id=scope.tenant_id,
            workspace_id=workspace_id,
            project_id=project_id,
            environment_id=environment_id,
            target=target_ref,
        )

    def _validate_principal(self, *, principal_id: UUID, tenant_id: UUID) -> None:
        """B1 RoleAssignment 的 subject 必须属于目标 Tenant。"""

        try:
            principal = Principal.objects.only("id", "tenant_id").get(pk=principal_id)
        except Principal.DoesNotExist as exc:
            raise RoleGrantResolutionError("principal_id 不存在。") from exc

        if principal.tenant_id != tenant_id:
            raise InvalidAuthorizationScope("Principal 与 AuthorizationScope 不属于同一 Tenant。")

    def _effective_group_ids(
        self,
        *,
        principal_id: UUID,
        tenant_id: UUID,
        at: datetime,
    ) -> tuple[UUID, ...]:
        """返回 Principal 当前有效且 Group 自身仍 ACTIVE 的 Group ID。"""

        window = (Q(valid_from__isnull=True) | Q(valid_from__lte=at)) & (
            Q(valid_until__isnull=True) | Q(valid_until__gt=at)
        )
        return tuple(
            GroupMembership.objects.filter(
                tenant_id=tenant_id,
                principal_id=principal_id,
                status=MembershipStatus.ACTIVE,
                group__status=GroupStatus.ACTIVE,
            )
            .filter(window)
            .values_list("group_id", flat=True)
        )

    def _effective_assignments(
        self,
        *,
        principal_id: UUID,
        group_ids: tuple[UUID, ...],
        target: _ResolvedTargetScope,
        at: datetime,
    ) -> tuple[RoleAssignment, ...]:
        """一次查询取回目标 Scope 祖先链上的 Direct/Group RoleAssignment。"""

        subject_filter = Q(principal_id=principal_id)
        if group_ids:
            subject_filter |= Q(group_id__in=group_ids)

        scope_filter = Q(scope_type=RoleScopeType.TENANT)
        if target.workspace_id is not None:
            scope_filter |= Q(
                scope_type=RoleScopeType.WORKSPACE,
                workspace_id=target.workspace_id,
            )
        if target.project_id is not None:
            scope_filter |= Q(
                scope_type=RoleScopeType.PROJECT,
                project_id=target.project_id,
            )
        if target.environment_id is not None:
            scope_filter |= Q(
                scope_type=RoleScopeType.ENVIRONMENT,
                environment_id=target.environment_id,
            )

        window = (Q(valid_from__isnull=True) | Q(valid_from__lte=at)) & (
            Q(valid_until__isnull=True) | Q(valid_until__gt=at)
        )

        return tuple(
            RoleAssignment.objects.filter(
                tenant_id=target.tenant_id,
                status=RoleAssignmentStatus.ACTIVE,
                role__status=RoleStatus.ACTIVE,
                conditions={},
            )
            .filter(subject_filter, scope_filter, window)
            .select_related("role")
            .order_by("id")
        )

    def _expand_role_privileges(
        self,
        *,
        assignments: tuple[RoleAssignment, ...],
        target: _ResolvedTargetScope,
    ) -> tuple[ResolvedRoleGrant, ...]:
        """批量展开 RolePrivilege，不产生按 assignment 的 N+1 查询。"""

        if not assignments:
            return ()

        role_ids = frozenset(assignment.role_id for assignment in assignments)
        privileges_by_role: dict[UUID, list[RolePrivilege]] = {}
        links = RolePrivilege.objects.filter(
            role_id__in=role_ids,
            privilege__status=PrivilegeStatus.ACTIVE,
        ).select_related("privilege")
        for link in links:
            privileges_by_role.setdefault(link.role_id, []).append(link)

        resolved: list[ResolvedRoleGrant] = []
        for assignment in assignments:
            assignment_scope = self._assignment_scope_ref(assignment)
            inherited_from = assignment_scope if assignment_scope != target.target else None
            source_type = (
                GrantSourceType.DIRECT
                if assignment.principal_id is not None
                else GrantSourceType.GROUP
            )

            for link in privileges_by_role.get(assignment.role_id, []):
                resolved.append(
                    ResolvedRoleGrant(
                        role_id=assignment.role_id,
                        role_key=assignment.role.key,
                        privilege_id=link.privilege_id,
                        privilege_key=link.privilege.key,
                        source_type=source_type,
                        assignment_id=assignment.id,
                        group_id=assignment.group_id,
                        assignment_scope=assignment_scope,
                        inherited_from=inherited_from,
                    )
                )

        return tuple(resolved)

    def _assignment_scope_ref(self, assignment: RoleAssignment) -> ResolvedScopeRef:
        """把数据库 RoleAssignment 的单层 Scope 形状转换为不可变引用。"""

        scope_type = RoleScopeType(assignment.scope_type)
        if scope_type == RoleScopeType.TENANT:
            return ResolvedScopeRef(scope_type, assignment.tenant_id)
        if scope_type == RoleScopeType.WORKSPACE and assignment.workspace_id is not None:
            return ResolvedScopeRef(scope_type, assignment.workspace_id)
        if scope_type == RoleScopeType.PROJECT and assignment.project_id is not None:
            return ResolvedScopeRef(scope_type, assignment.project_id)
        if scope_type == RoleScopeType.ENVIRONMENT and assignment.environment_id is not None:
            return ResolvedScopeRef(scope_type, assignment.environment_id)
        raise RoleGrantResolutionError("持久化 RoleAssignment 的 scope shape 非法，拒绝继续授权解析。")

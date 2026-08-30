"""Phase B2.4 Elevated Access 领域服务与 Resolver。

安全原则：

- Boundary 只能裁剪，不能产生 grant；
- Approval 决策、Break-glass 激活、Delegation 源权限都必须经注入式 authority checker；
- checker 缺失、异常或返回不足时 fail closed；
- JIT/Break-glass/Delegation 都只返回 candidate/evidence，最终仍交给 AuthorizationService 组合。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol
from uuid import UUID

from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from spatial_fabric.elevation.models import (
    ApprovalDecision,
    ApprovalDecisionValue,
    ApprovalPurpose,
    ApprovalRequest,
    ApprovalRequestPrivilege,
    ApprovalRequestStatus,
    ApprovalTargetType,
    DelegationGrant,
    DelegationGrantPrivilege,
    ElevatedScopeType,
    EvidenceStatus,
    PermissionBoundary,
    PermissionBoundaryPrivilege,
    TemporaryAccessGrant,
    TemporaryAccessGrantPrivilege,
    TemporaryAccessMode,
)
from spatial_fabric.iam.models import Principal, PrincipalStatus, Privilege, PrivilegeStatus
from spatial_fabric.tenancy.models import Environment, Project, Tenant, Workspace

BREAK_GLASS_MAX_TTL = timedelta(minutes=60)


class ElevatedAccessError(ValueError):
    """B2.4 稳定的公共领域错误基类。"""


class BoundaryControlError(ElevatedAccessError):
    """PermissionBoundary 输入或 evidence 无法安全解析。"""


class ApprovalControlError(ElevatedAccessError):
    """Approval request / decision 生命周期错误。"""


class TemporaryAccessControlError(ElevatedAccessError):
    """JIT / Break-glass 激活或撤销错误。"""


class DelegationControlError(ElevatedAccessError):
    """Delegation 创建、撤销或 authority revalidation 错误。"""


@dataclass(frozen=True, slots=True)
class ElevatedScopeRef:
    """Tenant hierarchy 上的值语义 Scope 引用。"""

    tenant_id: UUID
    scope_type: ElevatedScopeType
    scope_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class ApprovalTargetRef:
    """Approval target；RESOURCE 使用 provider-neutral ResourceRef 值语义。"""

    tenant_id: UUID
    target_type: ApprovalTargetType
    target_id: UUID | None = None
    resource_kind: str | None = None
    resource_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class AuthorityCheck:
    """对某个治理动作的 authority checker 结果。"""

    allowed: bool
    evidence: dict[str, object]


@dataclass(frozen=True, slots=True)
class PrivilegeAuthorityCheck:
    """Delegator 当前能够向外委托的权限集合及解释证据。"""

    allowed_privilege_keys: tuple[str, ...]
    evidence: dict[str, object]


class ApprovalAuthorityChecker(Protocol):
    """决定某 Principal 是否可对 ApprovalRequest 作最终决定。"""

    def can_decide(
        self, *, approver_id: UUID, approval_request: ApprovalRequest, at: object
    ) -> AuthorityCheck: ...


class BreakGlassAuthorityChecker(Protocol):
    """决定某 actor 是否可激活指定 Break-glass 请求。"""

    def can_activate(
        self,
        *,
        actor_id: UUID,
        beneficiary_id: UUID,
        scope_ref: ElevatedScopeRef,
        privilege_keys: tuple[str, ...],
        at: object,
    ) -> AuthorityCheck: ...


class DelegationAuthorityChecker(Protocol):
    """返回 delegator 在指定 Scope 当前可委托的有效 Privilege。"""

    def allowed_privileges(
        self,
        *,
        delegator_id: UUID,
        scope_ref: ElevatedScopeRef,
        requested_privilege_keys: tuple[str, ...],
        at: object,
    ) -> PrivilegeAuthorityCheck: ...


@dataclass(frozen=True, slots=True)
class BoundaryResolution:
    constrained: bool
    candidate_privilege_keys: tuple[str, ...]
    allowed_privilege_keys: tuple[str, ...]
    boundary_ids: tuple[UUID, ...]
    invalid_boundary_ids: tuple[UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class ApprovalResolution:
    approved: bool
    request_ids: tuple[UUID, ...]
    decision_ids: tuple[UUID, ...]
    privilege_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TemporaryAccessResolution:
    effective_privilege_keys: tuple[str, ...]
    grant_ids: tuple[UUID, ...]
    modes: tuple[str, ...]
    boundary_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class DelegationResolution:
    effective_privilege_keys: tuple[str, ...]
    delegation_ids: tuple[UUID, ...]
    boundary_ids: tuple[UUID, ...]
    fail_closed: bool


@dataclass(frozen=True, slots=True)
class _ScopeContext:
    tenant_id: UUID
    exact_type: ElevatedScopeType
    exact_id: UUID | None
    workspace_id: UUID | None
    project_id: UUID | None
    environment_id: UUID | None


class _ScopeLoader:
    """把 ScopeRef 解析成自身 + ancestors，用于继承查询。"""

    @staticmethod
    def load(scope_ref: ElevatedScopeRef) -> _ScopeContext:
        if not Tenant.objects.filter(id=scope_ref.tenant_id).exists():
            raise ElevatedAccessError("Tenant 不存在。")
        if scope_ref.scope_type == ElevatedScopeType.TENANT:
            if scope_ref.scope_id is not None:
                raise ElevatedAccessError("TENANT scope 不应携带 scope_id。")
            return _ScopeContext(scope_ref.tenant_id, ElevatedScopeType.TENANT, None, None, None, None)
        if scope_ref.scope_id is None:
            raise ElevatedAccessError("非 TENANT scope 必须提供 scope_id。")
        if scope_ref.scope_type == ElevatedScopeType.WORKSPACE:
            try:
                workspace = Workspace.objects.get(id=scope_ref.scope_id, tenant_id=scope_ref.tenant_id)
            except Workspace.DoesNotExist as exc:
                raise ElevatedAccessError("Workspace 不存在或不属于指定 Tenant。") from exc
            return _ScopeContext(
                scope_ref.tenant_id,
                ElevatedScopeType.WORKSPACE,
                workspace.id,
                workspace.id,
                None,
                None,
            )
        if scope_ref.scope_type == ElevatedScopeType.PROJECT:
            try:
                project = Project.objects.select_related("workspace").get(
                    id=scope_ref.scope_id, tenant_id=scope_ref.tenant_id
                )
            except Project.DoesNotExist as exc:
                raise ElevatedAccessError("Project 不存在或不属于指定 Tenant。") from exc
            return _ScopeContext(
                scope_ref.tenant_id,
                ElevatedScopeType.PROJECT,
                project.id,
                project.workspace_id,
                project.id,
                None,
            )
        if scope_ref.scope_type == ElevatedScopeType.ENVIRONMENT:
            try:
                environment = Environment.objects.select_related("project__workspace").get(
                    id=scope_ref.scope_id, tenant_id=scope_ref.tenant_id
                )
            except Environment.DoesNotExist as exc:
                raise ElevatedAccessError("Environment 不存在或不属于指定 Tenant。") from exc
            return _ScopeContext(
                scope_ref.tenant_id,
                ElevatedScopeType.ENVIRONMENT,
                environment.id,
                environment.project.workspace_id,
                environment.project_id,
                environment.id,
            )
        raise ElevatedAccessError("未知 scope_type。")


class _ElevationQuery:
    """B2.4 共享查询片段；只做 Scope/时间/主体事实，不做最终 Authorization。"""

    @staticmethod
    def active_principal(*, principal_id: UUID, tenant_id: UUID) -> Principal:
        try:
            return Principal.objects.get(
                id=principal_id,
                tenant_id=tenant_id,
                status=PrincipalStatus.ACTIVE,
            )
        except Principal.DoesNotExist as exc:
            raise ElevatedAccessError("Principal 不存在、非 ACTIVE 或不属于指定 Tenant。") from exc

    @staticmethod
    def governance_actor(*, principal_id: UUID, tenant_id: UUID) -> Principal:
        try:
            actor = Principal.objects.get(id=principal_id, status=PrincipalStatus.ACTIVE)
        except Principal.DoesNotExist as exc:
            raise ElevatedAccessError("治理主体不存在或非 ACTIVE。") from exc
        if actor.tenant_id not in (None, tenant_id):
            raise ElevatedAccessError("治理主体必须属于同一 Tenant 或平台。")
        return actor

    @staticmethod
    def scope_q(context: _ScopeContext) -> Q:
        clauses = Q(
            scope_type=ElevatedScopeType.TENANT,
            workspace__isnull=True,
            project__isnull=True,
            environment__isnull=True,
        )
        if context.workspace_id is not None:
            clauses |= Q(scope_type=ElevatedScopeType.WORKSPACE, workspace_id=context.workspace_id)
        if context.project_id is not None:
            clauses |= Q(scope_type=ElevatedScopeType.PROJECT, project_id=context.project_id)
        if context.environment_id is not None:
            clauses |= Q(
                scope_type=ElevatedScopeType.ENVIRONMENT,
                environment_id=context.environment_id,
            )
        return clauses

    @staticmethod
    def valid_at_q(moment: object) -> Q:
        return (Q(valid_from__isnull=True) | Q(valid_from__lte=moment)) & (
            Q(valid_until__isnull=True) | Q(valid_until__gt=moment)
        )

    @staticmethod
    def exact_scope_defaults(context: _ScopeContext) -> dict[str, UUID | None]:
        return {
            "workspace_id": context.exact_id
            if context.exact_type == ElevatedScopeType.WORKSPACE
            else None,
            "project_id": context.exact_id
            if context.exact_type == ElevatedScopeType.PROJECT
            else None,
            "environment_id": context.exact_id
            if context.exact_type == ElevatedScopeType.ENVIRONMENT
            else None,
        }

    @staticmethod
    def active_privileges(privilege_keys: tuple[str, ...]) -> tuple[Privilege, ...]:
        keys = tuple(sorted(set(privilege_keys)))
        if not keys:
            raise ElevatedAccessError("Privilege 集不能为空。")
        privileges = tuple(Privilege.objects.filter(key__in=keys).order_by("key"))
        found = {privilege.key for privilege in privileges}
        if found != set(keys):
            missing = sorted(set(keys) - found)
            raise ElevatedAccessError(f"存在未知 Privilege：{', '.join(missing)}。")
        deprecated = [p.key for p in privileges if p.status != PrivilegeStatus.ACTIVE]
        if deprecated:
            raise ElevatedAccessError(f"存在非 ACTIVE Privilege：{', '.join(deprecated)}。")
        return privileges


class PermissionBoundaryResolver:
    """将 candidate privilege set 按全部 applicable Boundary 取交集。"""

    def resolve(
        self,
        *,
        principal_id: UUID,
        scope_ref: ElevatedScopeRef,
        candidate_privilege_keys: tuple[str, ...],
        at: object | None = None,
    ) -> BoundaryResolution:
        moment = at or timezone.now()
        context = _ScopeLoader.load(scope_ref)
        _ElevationQuery.active_principal(
            principal_id=principal_id,
            tenant_id=scope_ref.tenant_id,
        )
        candidates = tuple(sorted(set(candidate_privilege_keys)))
        boundaries = list(
            PermissionBoundary.objects.filter(
                tenant_id=scope_ref.tenant_id,
                principal_id=principal_id,
                status=EvidenceStatus.ACTIVE,
            )
            .filter(_ElevationQuery.scope_q(context), _ElevationQuery.valid_at_q(moment))
            .order_by("id")
        )
        if not boundaries:
            return BoundaryResolution(False, candidates, candidates, ())

        allowed: set[str] | None = None
        invalid_ids: list[UUID] = []
        for boundary in boundaries:
            links = list(
                PermissionBoundaryPrivilege.objects.filter(boundary=boundary)
                .select_related("privilege")
                .order_by("privilege__key")
            )
            if any(link.privilege.status != PrivilegeStatus.ACTIVE for link in links):
                invalid_ids.append(boundary.id)
                allowed = set()
                continue
            boundary_allowed = {link.privilege.key for link in links}
            allowed = boundary_allowed if allowed is None else allowed & boundary_allowed

        permitted = set(candidates) & (allowed or set())
        return BoundaryResolution(
            constrained=True,
            candidate_privilege_keys=candidates,
            allowed_privilege_keys=tuple(sorted(permitted)),
            boundary_ids=tuple(boundary.id for boundary in boundaries),
            invalid_boundary_ids=tuple(invalid_ids),
        )


class _ApprovalTargetLoader:
    @staticmethod
    def validate(target_ref: ApprovalTargetRef) -> dict[str, object]:
        if target_ref.target_type == ApprovalTargetType.RESOURCE:
            if target_ref.target_id is not None:
                raise ApprovalControlError("RESOURCE target 使用 resource_id，不使用 target_id。")
            if not target_ref.resource_kind or target_ref.resource_id is None:
                raise ApprovalControlError("RESOURCE target 必须提供 resource_kind + resource_id。")
            if not Tenant.objects.filter(id=target_ref.tenant_id).exists():
                raise ApprovalControlError("Tenant 不存在。")
            return {
                "workspace_id": None,
                "project_id": None,
                "environment_id": None,
                "resource_kind": target_ref.resource_kind,
                "resource_id": target_ref.resource_id,
            }
        if target_ref.resource_kind is not None or target_ref.resource_id is not None:
            raise ApprovalControlError("非 RESOURCE target 不能携带 ResourceRef。")
        try:
            scope_type = ElevatedScopeType(target_ref.target_type)
        except ValueError as exc:
            raise ApprovalControlError("未知 Approval target_type。") from exc
        context = _ScopeLoader.load(
            ElevatedScopeRef(target_ref.tenant_id, scope_type, target_ref.target_id)
        )
        defaults = _ElevationQuery.exact_scope_defaults(context)
        return {
            **defaults,
            "resource_kind": "",
            "resource_id": None,
        }

    @staticmethod
    def exact_q(target_ref: ApprovalTargetRef) -> Q:
        defaults = _ApprovalTargetLoader.validate(target_ref)
        return Q(
            target_type=target_ref.target_type,
            workspace_id=defaults["workspace_id"],
            project_id=defaults["project_id"],
            environment_id=defaults["environment_id"],
            resource_kind=defaults["resource_kind"],
            resource_id=defaults["resource_id"],
        )


class ApprovalService:
    """创建和终结 ApprovalRequest；decide 必须有 authority checker。"""

    def __init__(self, *, authority_checker: ApprovalAuthorityChecker | None = None) -> None:
        self.authority_checker = authority_checker

    def request(
        self,
        *,
        requester_id: UUID,
        beneficiary_id: UUID,
        purpose: ApprovalPurpose,
        target_ref: ApprovalTargetRef,
        privilege_keys: tuple[str, ...],
        reason: str,
        expires_at: object,
        requested_valid_until: object | None = None,
        at: object | None = None,
    ) -> ApprovalRequest:
        moment = at or timezone.now()
        requester = _ElevationQuery.active_principal(
            principal_id=requester_id, tenant_id=target_ref.tenant_id
        )
        beneficiary = _ElevationQuery.active_principal(
            principal_id=beneficiary_id, tenant_id=target_ref.tenant_id
        )
        privileges = _ElevationQuery.active_privileges(privilege_keys)
        if not reason.strip():
            raise ApprovalControlError("ApprovalRequest 必须记录申请原因。")
        if expires_at <= moment:
            raise ApprovalControlError("ApprovalRequest.expires_at 必须晚于当前时间。")
        if purpose == ApprovalPurpose.JIT_ELEVATION:
            if target_ref.target_type == ApprovalTargetType.RESOURCE:
                raise ApprovalControlError("B2.4 v1 JIT 只支持 Tenant hierarchy Scope。")
            if requested_valid_until is None or requested_valid_until <= moment:
                raise ApprovalControlError("JIT 必须声明未来 requested_valid_until。")
        defaults = _ApprovalTargetLoader.validate(target_ref)
        with transaction.atomic():
            approval_request = ApprovalRequest(
                tenant_id=target_ref.tenant_id,
                purpose=purpose,
                requester=requester,
                beneficiary=beneficiary,
                target_type=target_ref.target_type,
                reason=reason.strip(),
                requested_at=moment,
                expires_at=expires_at,
                requested_valid_until=requested_valid_until,
                **defaults,
            )
            approval_request.full_clean()
            approval_request.save()
            for privilege in privileges:
                link = ApprovalRequestPrivilege(
                    approval_request=approval_request,
                    privilege=privilege,
                )
                link.full_clean()
                link.save()
            return approval_request

    def decide(
        self,
        *,
        approval_request_id: UUID,
        approver_id: UUID,
        decision: ApprovalDecisionValue,
        comment: str = "",
        at: object | None = None,
    ) -> ApprovalDecision:
        moment = at or timezone.now()
        with transaction.atomic():
            try:
                request = ApprovalRequest.objects.select_for_update().get(id=approval_request_id)
            except ApprovalRequest.DoesNotExist as exc:
                raise ApprovalControlError("ApprovalRequest 不存在。") from exc
            if request.status != ApprovalRequestStatus.PENDING:
                try:
                    existing = request.decision
                except ApprovalDecision.DoesNotExist as exc:
                    raise ApprovalControlError("非 PENDING request 缺失 final decision evidence。") from exc
                if existing.decision == decision and existing.approver_id == approver_id:
                    return existing
                raise ApprovalControlError("ApprovalRequest 已有终态，不能再次决策。")
            if moment >= request.expires_at:
                raise ApprovalControlError("ApprovalRequest 已过期，不能决策。")
            approver = _ElevationQuery.governance_actor(
                principal_id=approver_id,
                tenant_id=request.tenant_id,
            )
            if approver.id == request.requester_id:
                raise ApprovalControlError("Requester 不能自审批。")
            if self.authority_checker is None:
                raise ApprovalControlError("未配置 ApprovalAuthorityChecker，审批必须 fail closed。")
            try:
                authority = self.authority_checker.can_decide(
                    approver_id=approver.id,
                    approval_request=request,
                    at=moment,
                )
            except Exception as exc:
                raise ApprovalControlError("Approval authority checker 执行失败，已 fail closed。") from exc
            if not authority.allowed:
                raise ApprovalControlError("Approver 当前没有该审批 authority。")

            evidence = ApprovalDecision(
                approval_request=request,
                decision=decision,
                approver=approver,
                comment=comment,
                decided_at=moment,
            )
            evidence.full_clean()
            evidence.save()
            request.status = (
                ApprovalRequestStatus.APPROVED
                if decision == ApprovalDecisionValue.APPROVE
                else ApprovalRequestStatus.REJECTED
            )
            request.lock_version += 1
            request.save(update_fields=["status", "lock_version", "updated_at"])
            return evidence

    def cancel(
        self, *, approval_request_id: UUID, requester_id: UUID, at: object | None = None
    ) -> ApprovalRequest:
        moment = at or timezone.now()
        with transaction.atomic():
            try:
                request = ApprovalRequest.objects.select_for_update().get(id=approval_request_id)
            except ApprovalRequest.DoesNotExist as exc:
                raise ApprovalControlError("ApprovalRequest 不存在。") from exc
            if request.requester_id != requester_id:
                raise ApprovalControlError("只有原 requester 可以撤回 ApprovalRequest。")
            if request.status == ApprovalRequestStatus.CANCELLED:
                return request
            if request.status != ApprovalRequestStatus.PENDING:
                raise ApprovalControlError("只有 PENDING ApprovalRequest 可以撤回。")
            request.status = ApprovalRequestStatus.CANCELLED
            request.cancelled_by_id = requester_id
            request.cancelled_at = moment
            request.lock_version += 1
            request.save(
                update_fields=[
                    "status",
                    "cancelled_by",
                    "cancelled_at",
                    "lock_version",
                    "updated_at",
                ]
            )
            return request


class ApprovalResolver:
    """解析指定 target/action 是否存在当前有效 APPROVED evidence。"""

    def resolve(
        self,
        *,
        beneficiary_id: UUID,
        target_ref: ApprovalTargetRef,
        privilege_key: str,
        purpose: ApprovalPurpose,
        at: object | None = None,
    ) -> ApprovalResolution:
        moment = at or timezone.now()
        _ElevationQuery.active_principal(
            principal_id=beneficiary_id,
            tenant_id=target_ref.tenant_id,
        )
        target_q = _ApprovalTargetLoader.exact_q(target_ref)
        requests = list(
            ApprovalRequest.objects.filter(
                tenant_id=target_ref.tenant_id,
                beneficiary_id=beneficiary_id,
                purpose=purpose,
                status=ApprovalRequestStatus.APPROVED,
                expires_at__gt=moment,
                privilege_links__privilege__key=privilege_key,
                privilege_links__privilege__status=PrivilegeStatus.ACTIVE,
                decision__decision=ApprovalDecisionValue.APPROVE,
            )
            .filter(target_q)
            .select_related("decision")
            .distinct()
            .order_by("id")
        )
        return ApprovalResolution(
            approved=bool(requests),
            request_ids=tuple(request.id for request in requests),
            decision_ids=tuple(request.decision.id for request in requests),
            privilege_keys=(privilege_key,) if requests else (),
        )


class TemporaryAccessService:
    """JIT / Break-glass 激活与撤销服务。"""

    def __init__(
        self,
        *,
        boundary_resolver: PermissionBoundaryResolver | None = None,
        break_glass_checker: BreakGlassAuthorityChecker | None = None,
    ) -> None:
        self.boundary_resolver = boundary_resolver or PermissionBoundaryResolver()
        self.break_glass_checker = break_glass_checker

    def activate_jit(
        self,
        *,
        approval_request_id: UUID,
        activated_by_id: UUID,
        at: object | None = None,
    ) -> TemporaryAccessGrant:
        moment = at or timezone.now()
        with transaction.atomic():
            request = ApprovalRequest.objects.select_for_update().filter(id=approval_request_id).first()
            if request is None:
                raise TemporaryAccessControlError("JIT 来源 ApprovalRequest 不存在。")
            existing = TemporaryAccessGrant.objects.filter(
                source_approval_request=request,
                mode=TemporaryAccessMode.JIT,
            ).first()
            if existing is not None:
                return existing
            if request.purpose != ApprovalPurpose.JIT_ELEVATION:
                raise TemporaryAccessControlError("JIT 来源 request purpose 必须为 JIT_ELEVATION。")
            if request.status != ApprovalRequestStatus.APPROVED or moment >= request.expires_at:
                raise TemporaryAccessControlError("JIT 来源 ApprovalRequest 未批准或已过期。")
            try:
                decision = request.decision
            except ApprovalDecision.DoesNotExist as exc:
                raise TemporaryAccessControlError("APPROVED request 缺失 ApprovalDecision evidence。") from exc
            if decision.decision != ApprovalDecisionValue.APPROVE:
                raise TemporaryAccessControlError("JIT 来源 decision 不是 APPROVE。")
            if request.target_type == ApprovalTargetType.RESOURCE:
                raise TemporaryAccessControlError("B2.4 v1 JIT 不支持 RESOURCE target。")
            if request.requested_valid_until is None or request.requested_valid_until <= moment:
                raise TemporaryAccessControlError("JIT requested_valid_until 已失效。")
            beneficiary = _ElevationQuery.active_principal(
                principal_id=request.beneficiary_id,
                tenant_id=request.tenant_id,
            )
            _ElevationQuery.governance_actor(
                principal_id=activated_by_id,
                tenant_id=request.tenant_id,
            )
            privilege_keys = tuple(
                ApprovalRequestPrivilege.objects.filter(approval_request=request)
                .order_by("privilege__key")
                .values_list("privilege__key", flat=True)
            )
            _ElevationQuery.active_privileges(privilege_keys)
            scope_ref = ElevatedScopeRef(
                request.tenant_id,
                ElevatedScopeType(request.target_type),
                self._request_scope_id(request),
            )
            boundary = self.boundary_resolver.resolve(
                principal_id=beneficiary.id,
                scope_ref=scope_ref,
                candidate_privilege_keys=privilege_keys,
                at=moment,
            )
            if set(boundary.allowed_privilege_keys) != set(privilege_keys):
                raise TemporaryAccessControlError("JIT 请求超出 beneficiary PermissionBoundary。")
            context = _ScopeLoader.load(scope_ref)
            grant = TemporaryAccessGrant(
                tenant_id=request.tenant_id,
                beneficiary=beneficiary,
                mode=TemporaryAccessMode.JIT,
                scope_type=context.exact_type,
                source_approval_request=request,
                valid_from=moment,
                valid_until=request.requested_valid_until,
                reason=request.reason,
                emergency_reason="",
                notification_required=False,
                activated_by_id=activated_by_id,
                **_ElevationQuery.exact_scope_defaults(context),
            )
            grant.full_clean()
            grant.save()
            privileges = _ElevationQuery.active_privileges(privilege_keys)
            for privilege in privileges:
                link = TemporaryAccessGrantPrivilege(grant=grant, privilege=privilege)
                link.full_clean()
                link.save()
            return grant

    def activate_break_glass(
        self,
        *,
        beneficiary_id: UUID,
        activated_by_id: UUID,
        scope_ref: ElevatedScopeRef,
        privilege_keys: tuple[str, ...],
        emergency_reason: str,
        ttl: timedelta,
        at: object | None = None,
    ) -> TemporaryAccessGrant:
        moment = at or timezone.now()
        if ttl <= timedelta(0) or ttl > BREAK_GLASS_MAX_TTL:
            raise TemporaryAccessControlError("Break-glass TTL 必须大于 0 且不超过 60 分钟。")
        if not emergency_reason.strip():
            raise TemporaryAccessControlError("Break-glass 必须记录紧急访问强理由。")
        context = _ScopeLoader.load(scope_ref)
        beneficiary = _ElevationQuery.active_principal(
            principal_id=beneficiary_id,
            tenant_id=scope_ref.tenant_id,
        )
        _ElevationQuery.governance_actor(
            principal_id=activated_by_id,
            tenant_id=scope_ref.tenant_id,
        )
        privileges = _ElevationQuery.active_privileges(privilege_keys)
        keys = tuple(privilege.key for privilege in privileges)
        if self.break_glass_checker is None:
            raise TemporaryAccessControlError("未配置 BreakGlassAuthorityChecker，必须 fail closed。")
        try:
            authority = self.break_glass_checker.can_activate(
                actor_id=activated_by_id,
                beneficiary_id=beneficiary_id,
                scope_ref=scope_ref,
                privilege_keys=keys,
                at=moment,
            )
        except Exception as exc:
            raise TemporaryAccessControlError("Break-glass authority checker 失败，已 fail closed。") from exc
        if not authority.allowed:
            raise TemporaryAccessControlError("激活主体当前没有 Break-glass authority。")
        boundary = self.boundary_resolver.resolve(
            principal_id=beneficiary.id,
            scope_ref=scope_ref,
            candidate_privilege_keys=keys,
            at=moment,
        )
        if set(boundary.allowed_privilege_keys) != set(keys):
            raise TemporaryAccessControlError("Break-glass 请求超出 beneficiary PermissionBoundary。")
        with transaction.atomic():
            grant = TemporaryAccessGrant(
                tenant_id=scope_ref.tenant_id,
                beneficiary=beneficiary,
                mode=TemporaryAccessMode.BREAK_GLASS,
                scope_type=context.exact_type,
                valid_from=moment,
                valid_until=moment + ttl,
                reason=emergency_reason.strip(),
                emergency_reason=emergency_reason.strip(),
                notification_required=True,
                activated_by_id=activated_by_id,
                **_ElevationQuery.exact_scope_defaults(context),
            )
            grant.full_clean()
            grant.save()
            for privilege in privileges:
                link = TemporaryAccessGrantPrivilege(grant=grant, privilege=privilege)
                link.full_clean()
                link.save()
            return grant

    def revoke(
        self,
        *,
        grant_id: UUID,
        revoked_by_id: UUID,
        at: object | None = None,
    ) -> TemporaryAccessGrant:
        moment = at or timezone.now()
        with transaction.atomic():
            grant = TemporaryAccessGrant.objects.select_for_update().filter(id=grant_id).first()
            if grant is None:
                raise TemporaryAccessControlError("TemporaryAccessGrant 不存在。")
            if grant.status == EvidenceStatus.REVOKED:
                return grant
            _ElevationQuery.governance_actor(
                principal_id=revoked_by_id,
                tenant_id=grant.tenant_id,
            )
            grant.status = EvidenceStatus.REVOKED
            grant.revoked_by_id = revoked_by_id
            grant.revoked_at = moment
            grant.lock_version += 1
            grant.save(
                update_fields=[
                    "status",
                    "revoked_by",
                    "revoked_at",
                    "lock_version",
                    "updated_at",
                ]
            )
            return grant

    @staticmethod
    def _request_scope_id(request: ApprovalRequest) -> UUID | None:
        if request.target_type == ApprovalTargetType.WORKSPACE:
            return request.workspace_id
        if request.target_type == ApprovalTargetType.PROJECT:
            return request.project_id
        if request.target_type == ApprovalTargetType.ENVIRONMENT:
            return request.environment_id
        return None


class TemporaryAccessResolver:
    """返回当前短时 JIT/Break-glass candidate privileges，并再次套用 Boundary。"""

    def __init__(self, *, boundary_resolver: PermissionBoundaryResolver | None = None) -> None:
        self.boundary_resolver = boundary_resolver or PermissionBoundaryResolver()

    def resolve(
        self,
        *,
        principal_id: UUID,
        scope_ref: ElevatedScopeRef,
        at: object | None = None,
    ) -> TemporaryAccessResolution:
        moment = at or timezone.now()
        context = _ScopeLoader.load(scope_ref)
        _ElevationQuery.active_principal(
            principal_id=principal_id,
            tenant_id=scope_ref.tenant_id,
        )
        grants = list(
            TemporaryAccessGrant.objects.filter(
                tenant_id=scope_ref.tenant_id,
                beneficiary_id=principal_id,
                status=EvidenceStatus.ACTIVE,
                valid_from__lte=moment,
                valid_until__gt=moment,
            )
            .filter(_ElevationQuery.scope_q(context))
            .order_by("id")
        )
        candidate: set[str] = set()
        contributing_ids: list[UUID] = []
        modes: list[str] = []
        for grant in grants:
            links = list(
                TemporaryAccessGrantPrivilege.objects.filter(grant=grant)
                .select_related("privilege")
                .order_by("privilege__key")
            )
            if any(link.privilege.status != PrivilegeStatus.ACTIVE for link in links):
                continue
            candidate.update(link.privilege.key for link in links)
            contributing_ids.append(grant.id)
            modes.append(grant.mode)
        boundary = self.boundary_resolver.resolve(
            principal_id=principal_id,
            scope_ref=scope_ref,
            candidate_privilege_keys=tuple(sorted(candidate)),
            at=moment,
        )
        return TemporaryAccessResolution(
            effective_privilege_keys=boundary.allowed_privilege_keys,
            grant_ids=tuple(contributing_ids),
            modes=tuple(modes),
            boundary_ids=boundary.boundary_ids,
        )


class DelegationService:
    """创建和撤销 Delegation；创建时必须检查 delegator current authority。"""

    def __init__(
        self,
        *,
        authority_checker: DelegationAuthorityChecker | None = None,
        boundary_resolver: PermissionBoundaryResolver | None = None,
    ) -> None:
        self.authority_checker = authority_checker
        self.boundary_resolver = boundary_resolver or PermissionBoundaryResolver()

    def create(
        self,
        *,
        delegator_id: UUID,
        delegatee_id: UUID,
        scope_ref: ElevatedScopeRef,
        privilege_keys: tuple[str, ...],
        reason: str,
        valid_until: object,
        created_by_id: UUID,
        at: object | None = None,
    ) -> DelegationGrant:
        moment = at or timezone.now()
        if delegator_id == delegatee_id:
            raise DelegationControlError("Delegator 与 Delegatee 必须不同。")
        if valid_until <= moment:
            raise DelegationControlError("Delegation.valid_until 必须晚于当前时间。")
        if not reason.strip():
            raise DelegationControlError("Delegation 必须记录委托原因。")
        context = _ScopeLoader.load(scope_ref)
        delegator = _ElevationQuery.active_principal(
            principal_id=delegator_id,
            tenant_id=scope_ref.tenant_id,
        )
        delegatee = _ElevationQuery.active_principal(
            principal_id=delegatee_id,
            tenant_id=scope_ref.tenant_id,
        )
        _ElevationQuery.governance_actor(
            principal_id=created_by_id,
            tenant_id=scope_ref.tenant_id,
        )
        privileges = _ElevationQuery.active_privileges(privilege_keys)
        requested = tuple(privilege.key for privilege in privileges)
        if self.authority_checker is None:
            raise DelegationControlError("未配置 DelegationAuthorityChecker，必须 fail closed。")
        try:
            authority = self.authority_checker.allowed_privileges(
                delegator_id=delegator.id,
                scope_ref=scope_ref,
                requested_privilege_keys=requested,
                at=moment,
            )
        except Exception as exc:
            raise DelegationControlError("Delegation authority checker 失败，已 fail closed。") from exc
        if not set(requested).issubset(set(authority.allowed_privilege_keys)):
            raise DelegationControlError("Delegation 请求超出 delegator 当前有效 authority。")
        delegatee_boundary = self.boundary_resolver.resolve(
            principal_id=delegatee.id,
            scope_ref=scope_ref,
            candidate_privilege_keys=requested,
            at=moment,
        )
        if set(delegatee_boundary.allowed_privilege_keys) != set(requested):
            raise DelegationControlError("Delegation 请求超出 delegatee PermissionBoundary。")

        with transaction.atomic():
            grant = DelegationGrant(
                tenant_id=scope_ref.tenant_id,
                delegator=delegator,
                delegatee=delegatee,
                scope_type=context.exact_type,
                valid_from=moment,
                valid_until=valid_until,
                reason=reason.strip(),
                authority_snapshot={
                    "allowed_privilege_keys": list(authority.allowed_privilege_keys),
                    "evidence": authority.evidence,
                },
                authority_checked_at=moment,
                created_by_id=created_by_id,
                **_ElevationQuery.exact_scope_defaults(context),
            )
            grant.full_clean()
            grant.save()
            for privilege in privileges:
                link = DelegationGrantPrivilege(delegation=grant, privilege=privilege)
                link.full_clean()
                link.save()
            return grant

    def revoke(
        self,
        *,
        delegation_id: UUID,
        revoked_by_id: UUID,
        at: object | None = None,
    ) -> DelegationGrant:
        moment = at or timezone.now()
        with transaction.atomic():
            grant = DelegationGrant.objects.select_for_update().filter(id=delegation_id).first()
            if grant is None:
                raise DelegationControlError("DelegationGrant 不存在。")
            if grant.status == EvidenceStatus.REVOKED:
                return grant
            actor = _ElevationQuery.governance_actor(
                principal_id=revoked_by_id,
                tenant_id=grant.tenant_id,
            )
            if actor.id not in {grant.delegator_id, grant.created_by_id} and actor.tenant_id is not None:
                raise DelegationControlError("Tenant 内只有 delegator/创建主体可直接撤销该 Delegation。")
            grant.status = EvidenceStatus.REVOKED
            grant.revoked_by = actor
            grant.revoked_at = moment
            grant.lock_version += 1
            grant.save(
                update_fields=[
                    "status",
                    "revoked_by",
                    "revoked_at",
                    "lock_version",
                    "updated_at",
                ]
            )
            return grant


class DelegationResolver:
    """每次解析都重新验证 delegator current authority，然后再套用 delegatee Boundary。"""

    def __init__(
        self,
        *,
        authority_checker: DelegationAuthorityChecker | None = None,
        boundary_resolver: PermissionBoundaryResolver | None = None,
    ) -> None:
        self.authority_checker = authority_checker
        self.boundary_resolver = boundary_resolver or PermissionBoundaryResolver()

    def resolve(
        self,
        *,
        delegatee_id: UUID,
        scope_ref: ElevatedScopeRef,
        at: object | None = None,
    ) -> DelegationResolution:
        moment = at or timezone.now()
        context = _ScopeLoader.load(scope_ref)
        _ElevationQuery.active_principal(
            principal_id=delegatee_id,
            tenant_id=scope_ref.tenant_id,
        )
        if self.authority_checker is None:
            return DelegationResolution((), (), (), True)
        grants = list(
            DelegationGrant.objects.filter(
                tenant_id=scope_ref.tenant_id,
                delegatee_id=delegatee_id,
                status=EvidenceStatus.ACTIVE,
                valid_from__lte=moment,
                valid_until__gt=moment,
            )
            .filter(_ElevationQuery.scope_q(context))
            .order_by("id")
        )
        candidate: set[str] = set()
        evidence_ids: list[UUID] = []
        try:
            for grant in grants:
                links = list(
                    DelegationGrantPrivilege.objects.filter(delegation=grant)
                    .select_related("privilege")
                    .order_by("privilege__key")
                )
                if any(link.privilege.status != PrivilegeStatus.ACTIVE for link in links):
                    continue
                requested = tuple(link.privilege.key for link in links)
                authority = self.authority_checker.allowed_privileges(
                    delegator_id=grant.delegator_id,
                    scope_ref=ElevatedScopeRef(
                        grant.tenant_id,
                        ElevatedScopeType(grant.scope_type),
                        self._grant_scope_id(grant),
                    ),
                    requested_privilege_keys=requested,
                    at=moment,
                )
                current_allowed = set(authority.allowed_privilege_keys)
                candidate.update(key for key in requested if key in current_allowed)
                evidence_ids.append(grant.id)
        except Exception:
            return DelegationResolution((), (), (), True)

        boundary = self.boundary_resolver.resolve(
            principal_id=delegatee_id,
            scope_ref=scope_ref,
            candidate_privilege_keys=tuple(sorted(candidate)),
            at=moment,
        )
        return DelegationResolution(
            effective_privilege_keys=boundary.allowed_privilege_keys,
            delegation_ids=tuple(evidence_ids),
            boundary_ids=boundary.boundary_ids,
            fail_closed=False,
        )

    @staticmethod
    def _grant_scope_id(grant: DelegationGrant) -> UUID | None:
        if grant.scope_type == ElevatedScopeType.WORKSPACE:
            return grant.workspace_id
        if grant.scope_type == ElevatedScopeType.PROJECT:
            return grant.project_id
        if grant.scope_type == ElevatedScopeType.ENVIRONMENT:
            return grant.environment_id
        return None

"""Phase B2.4 Elevated Access 不变量、Resolver 与高风险访问生命周期测试。"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from spatial_fabric.elevation.models import (
    ApprovalDecisionValue,
    ApprovalPurpose,
    ApprovalRequestStatus,
    ApprovalTargetType,
    DelegationGrantPrivilege,
    ElevatedScopeType,
    EvidenceStatus,
    PermissionBoundary,
    PermissionBoundaryPrivilege,
    TemporaryAccessGrant,
    TemporaryAccessMode,
)
from spatial_fabric.elevation.services import (
    ApprovalAuthorityChecker,
    ApprovalControlError,
    ApprovalService,
    ApprovalTargetRef,
    AuthorityCheck,
    BreakGlassAuthorityChecker,
    DelegationAuthorityChecker,
    DelegationControlError,
    DelegationResolver,
    DelegationService,
    ElevatedScopeRef,
    PermissionBoundaryResolver,
    PrivilegeAuthorityCheck,
    TemporaryAccessControlError,
    TemporaryAccessResolver,
    TemporaryAccessService,
)
from spatial_fabric.iam.models import (
    Principal,
    PrincipalType,
    Privilege,
    PrivilegeCategory,
    PrivilegeRiskLevel,
    PrivilegeStatus,
)
from spatial_fabric.tenancy.models import (
    Environment,
    EnvironmentType,
    Project,
    Tenant,
    Workspace,
)


def create_tenant(slug: str) -> Tenant:
    return Tenant.objects.create(name=f"租户 {slug}", slug=slug)


def create_principal(tenant: Tenant, name: str) -> Principal:
    return Principal.objects.create(
        tenant=tenant,
        principal_type=PrincipalType.HUMAN_USER,
        display_name=name,
    )


def create_scope_tree(tenant: Tenant, suffix: str) -> tuple[Workspace, Project, Environment]:
    workspace = Workspace.objects.create(
        tenant=tenant,
        name=f"工作空间 {suffix}",
        slug=f"ws-{suffix}",
    )
    project = Project.objects.create(
        tenant=tenant,
        workspace=workspace,
        name=f"项目 {suffix}",
        slug=f"project-{suffix}",
    )
    environment = Environment.objects.create(
        tenant=tenant,
        project=project,
        name=f"环境 {suffix}",
        slug=f"env-{suffix}",
        environment_type=EnvironmentType.PRODUCTION,
    )
    return workspace, project, environment


def create_privilege(key: str) -> Privilege:
    return Privilege.objects.create(
        key=key,
        name=key,
        category=PrivilegeCategory.GOVERNANCE,
        risk_level=PrivilegeRiskLevel.HIGH,
    )


def create_boundary(
    *,
    tenant: Tenant,
    principal: Principal,
    actor: Principal,
    privilege_keys: tuple[str, ...],
    project: Project | None = None,
) -> PermissionBoundary:
    boundary = PermissionBoundary(
        tenant=tenant,
        principal=principal,
        scope_type=(ElevatedScopeType.PROJECT if project else ElevatedScopeType.TENANT),
        project=project,
        reason="限制高风险权限",
        created_by=actor,
    )
    boundary.full_clean()
    boundary.save()
    for key in privilege_keys:
        link = PermissionBoundaryPrivilege(
            boundary=boundary,
            privilege=Privilege.objects.get(key=key),
        )
        link.full_clean()
        link.save()
    return boundary


class AllowApprovalChecker(ApprovalAuthorityChecker):
    def can_decide(
        self,
        *,
        approver_id: object,
        approval_request: object,
        at: datetime,
    ) -> AuthorityCheck:
        return AuthorityCheck(True, {"source": "test-approval-authority", "at": at.isoformat()})


class AllowBreakGlassChecker(BreakGlassAuthorityChecker):
    def can_activate(
        self,
        *,
        actor_id: object,
        beneficiary_id: object,
        scope_ref: ElevatedScopeRef,
        privilege_keys: tuple[str, ...],
        at: datetime,
    ) -> AuthorityCheck:
        return AuthorityCheck(
            True,
            {
                "source": "test-break-glass-authority",
                "scope": scope_ref.scope_type,
                "keys": list(privilege_keys),
                "at": at.isoformat(),
            },
        )


class StaticDelegationChecker(DelegationAuthorityChecker):
    def __init__(self, allowed_keys: tuple[str, ...], *, fail: bool = False) -> None:
        self.allowed_keys = allowed_keys
        self.fail = fail

    def allowed_privileges(
        self,
        *,
        delegator_id: object,
        scope_ref: ElevatedScopeRef,
        requested_privilege_keys: tuple[str, ...],
        at: datetime,
    ) -> PrivilegeAuthorityCheck:
        if self.fail:
            raise RuntimeError("authority backend unavailable")
        return PrivilegeAuthorityCheck(
            self.allowed_keys,
            {
                "source": "test-delegator-authority",
                "requested": list(requested_privilege_keys),
                "at": at.isoformat(),
            },
        )


@pytest.mark.django_db
def test_permission_boundary_cross_tenant_principal_is_rejected() -> None:
    tenant_a = create_tenant("bound-a")
    tenant_b = create_tenant("bound-b")
    actor_a = create_principal(tenant_a, "A 管理员")
    user_b = create_principal(tenant_b, "B 用户")
    boundary = PermissionBoundary(
        tenant=tenant_a,
        principal=user_b,
        scope_type=ElevatedScopeType.TENANT,
        reason="非法跨租户",
        created_by=actor_a,
    )

    with pytest.raises(ValidationError) as exc_info:
        boundary.full_clean()

    assert "principal" in exc_info.value.message_dict


@pytest.mark.django_db
def test_multiple_permission_boundaries_intersect_and_never_expand_candidates() -> None:
    tenant = create_tenant("bound-intersection")
    actor = create_principal(tenant, "管理员")
    user = create_principal(tenant, "用户")
    _, project, _ = create_scope_tree(tenant, "bound-int")
    create_privilege("elev.read")
    create_privilege("elev.execute")
    create_privilege("elev.admin")
    create_boundary(
        tenant=tenant,
        principal=user,
        actor=actor,
        privilege_keys=("elev.read", "elev.execute"),
    )
    create_boundary(
        tenant=tenant,
        principal=user,
        actor=actor,
        privilege_keys=("elev.execute", "elev.admin"),
        project=project,
    )

    result = PermissionBoundaryResolver().resolve(
        principal_id=user.id,
        scope_ref=ElevatedScopeRef(tenant.id, ElevatedScopeType.PROJECT, project.id),
        candidate_privilege_keys=("elev.read", "elev.execute"),
    )

    assert result.constrained
    assert result.allowed_privilege_keys == ("elev.execute",)
    assert set(result.allowed_privilege_keys).issubset(set(result.candidate_privilege_keys))
    assert len(result.boundary_ids) == 2


@pytest.mark.django_db
def test_no_permission_boundary_leaves_candidate_set_unchanged() -> None:
    tenant = create_tenant("bound-none")
    user = create_principal(tenant, "用户")

    result = PermissionBoundaryResolver().resolve(
        principal_id=user.id,
        scope_ref=ElevatedScopeRef(tenant.id, ElevatedScopeType.TENANT),
        candidate_privilege_keys=("not-yet-materialized", "another-candidate"),
    )

    assert not result.constrained
    assert result.allowed_privilege_keys == ("another-candidate", "not-yet-materialized")


@pytest.mark.django_db
def test_deprecated_boundary_privilege_fails_closed() -> None:
    tenant = create_tenant("bound-deprecated")
    actor = create_principal(tenant, "管理员")
    user = create_principal(tenant, "用户")
    privilege = create_privilege("elev.deprecated")
    boundary = create_boundary(
        tenant=tenant,
        principal=user,
        actor=actor,
        privilege_keys=(privilege.key,),
    )
    privilege.status = PrivilegeStatus.DEPRECATED
    privilege.save(update_fields=["status", "updated_at"])

    result = PermissionBoundaryResolver().resolve(
        principal_id=user.id,
        scope_ref=ElevatedScopeRef(tenant.id, ElevatedScopeType.TENANT),
        candidate_privilege_keys=(privilege.key,),
    )

    assert result.allowed_privilege_keys == ()
    assert result.invalid_boundary_ids == (boundary.id,)


@pytest.mark.django_db
def test_approval_requires_authority_checker_and_rejects_self_approval() -> None:
    tenant = create_tenant("approval-auth")
    requester = create_principal(tenant, "申请人")
    approver = create_principal(tenant, "审批人")
    privilege = create_privilege("elev.approve-test")
    moment = timezone.now()
    request = ApprovalService().request(
        requester_id=requester.id,
        beneficiary_id=requester.id,
        purpose=ApprovalPurpose.HIGH_RISK_ACTION,
        target_ref=ApprovalTargetRef(tenant.id, ApprovalTargetType.TENANT),
        privilege_keys=(privilege.key,),
        reason="需要执行高风险操作",
        expires_at=moment + timedelta(hours=1),
        at=moment,
    )

    with pytest.raises(ApprovalControlError, match="AuthorityChecker"):
        ApprovalService().decide(
            approval_request_id=request.id,
            approver_id=approver.id,
            decision=ApprovalDecisionValue.APPROVE,
            at=moment,
        )

    with pytest.raises(ApprovalControlError, match="自审批"):
        ApprovalService(authority_checker=AllowApprovalChecker()).decide(
            approval_request_id=request.id,
            approver_id=requester.id,
            decision=ApprovalDecisionValue.APPROVE,
            at=moment,
        )


@pytest.mark.django_db
def test_approval_decision_preserves_authority_snapshot() -> None:
    tenant = create_tenant("approval-evidence")
    requester = create_principal(tenant, "申请人")
    approver = create_principal(tenant, "审批人")
    privilege = create_privilege("elev.approval-evidence")
    moment = timezone.now()
    request = ApprovalService().request(
        requester_id=requester.id,
        beneficiary_id=requester.id,
        purpose=ApprovalPurpose.HIGH_RISK_ACTION,
        target_ref=ApprovalTargetRef(tenant.id, ApprovalTargetType.TENANT),
        privilege_keys=(privilege.key,),
        reason="需要审批证据",
        expires_at=moment + timedelta(hours=1),
        at=moment,
    )

    decision = ApprovalService(authority_checker=AllowApprovalChecker()).decide(
        approval_request_id=request.id,
        approver_id=approver.id,
        decision=ApprovalDecisionValue.APPROVE,
        at=moment,
    )
    request.refresh_from_db()

    assert request.status == ApprovalRequestStatus.APPROVED
    assert decision.authority_snapshot["source"] == "test-approval-authority"


@pytest.mark.django_db
def test_jit_requires_approved_request_and_is_activation_idempotent() -> None:
    tenant = create_tenant("jit-idempotent")
    requester = create_principal(tenant, "申请人")
    approver = create_principal(tenant, "审批人")
    _, project, _ = create_scope_tree(tenant, "jit")
    privilege = create_privilege("elev.jit")
    create_boundary(
        tenant=tenant,
        principal=requester,
        actor=approver,
        privilege_keys=(privilege.key,),
        project=project,
    )
    moment = timezone.now()
    request = ApprovalService().request(
        requester_id=requester.id,
        beneficiary_id=requester.id,
        purpose=ApprovalPurpose.JIT_ELEVATION,
        target_ref=ApprovalTargetRef(
            tenant.id,
            ApprovalTargetType.PROJECT,
            target_id=project.id,
        ),
        privilege_keys=(privilege.key,),
        reason="临时处理生产问题",
        expires_at=moment + timedelta(hours=1),
        requested_valid_until=moment + timedelta(minutes=30),
        at=moment,
    )
    service = TemporaryAccessService()
    with pytest.raises(TemporaryAccessControlError, match="未批准"):
        service.activate_jit(
            approval_request_id=request.id,
            activated_by_id=approver.id,
            at=moment,
        )

    ApprovalService(authority_checker=AllowApprovalChecker()).decide(
        approval_request_id=request.id,
        approver_id=approver.id,
        decision=ApprovalDecisionValue.APPROVE,
        at=moment,
    )
    first = service.activate_jit(
        approval_request_id=request.id,
        activated_by_id=approver.id,
        at=moment,
    )
    second = service.activate_jit(
        approval_request_id=request.id,
        activated_by_id=approver.id,
        at=moment + timedelta(seconds=5),
    )

    assert first.id == second.id
    assert TemporaryAccessGrant.objects.filter(source_approval_request=request).count() == 1


@pytest.mark.django_db
def test_jit_cannot_cross_beneficiary_boundary() -> None:
    tenant = create_tenant("jit-boundary")
    requester = create_principal(tenant, "申请人")
    approver = create_principal(tenant, "审批人")
    privilege_allowed = create_privilege("elev.jit-allowed")
    privilege_blocked = create_privilege("elev.jit-blocked")
    create_boundary(
        tenant=tenant,
        principal=requester,
        actor=approver,
        privilege_keys=(privilege_allowed.key,),
    )
    moment = timezone.now()
    request = ApprovalService().request(
        requester_id=requester.id,
        beneficiary_id=requester.id,
        purpose=ApprovalPurpose.JIT_ELEVATION,
        target_ref=ApprovalTargetRef(tenant.id, ApprovalTargetType.TENANT),
        privilege_keys=(privilege_allowed.key, privilege_blocked.key),
        reason="请求超出边界",
        expires_at=moment + timedelta(hours=1),
        requested_valid_until=moment + timedelta(minutes=20),
        at=moment,
    )
    ApprovalService(authority_checker=AllowApprovalChecker()).decide(
        approval_request_id=request.id,
        approver_id=approver.id,
        decision=ApprovalDecisionValue.APPROVE,
        at=moment,
    )

    with pytest.raises(TemporaryAccessControlError, match="PermissionBoundary"):
        TemporaryAccessService().activate_jit(
            approval_request_id=request.id,
            activated_by_id=approver.id,
            at=moment,
        )


@pytest.mark.django_db
def test_break_glass_requires_checker_and_enforces_60_minute_ttl() -> None:
    tenant = create_tenant("breakglass-guard")
    actor = create_principal(tenant, "安全管理员")
    beneficiary = create_principal(tenant, "值班用户")
    privilege = create_privilege("elev.breakglass")
    create_boundary(
        tenant=tenant,
        principal=beneficiary,
        actor=actor,
        privilege_keys=(privilege.key,),
    )
    scope = ElevatedScopeRef(tenant.id, ElevatedScopeType.TENANT)

    with pytest.raises(TemporaryAccessControlError, match="BreakGlassAuthorityChecker"):
        TemporaryAccessService().activate_break_glass(
            beneficiary_id=beneficiary.id,
            activated_by_id=actor.id,
            scope_ref=scope,
            privilege_keys=(privilege.key,),
            emergency_reason="紧急恢复生产",
            ttl=timedelta(minutes=30),
            idempotency_key="bg-no-checker-001",
        )

    with pytest.raises(TemporaryAccessControlError, match="60 分钟"):
        TemporaryAccessService(break_glass_checker=AllowBreakGlassChecker()).activate_break_glass(
            beneficiary_id=beneficiary.id,
            activated_by_id=actor.id,
            scope_ref=scope,
            privilege_keys=(privilege.key,),
            emergency_reason="紧急恢复生产",
            ttl=timedelta(minutes=61),
            idempotency_key="bg-too-long-001",
        )


@pytest.mark.django_db
def test_break_glass_is_idempotent_and_preserves_authority_snapshot() -> None:
    tenant = create_tenant("breakglass-idem")
    actor = create_principal(tenant, "安全管理员")
    beneficiary = create_principal(tenant, "值班用户")
    privilege = create_privilege("elev.breakglass-idem")
    create_boundary(
        tenant=tenant,
        principal=beneficiary,
        actor=actor,
        privilege_keys=(privilege.key,),
    )
    scope = ElevatedScopeRef(tenant.id, ElevatedScopeType.TENANT)
    service = TemporaryAccessService(break_glass_checker=AllowBreakGlassChecker())
    moment = timezone.now()

    first = service.activate_break_glass(
        beneficiary_id=beneficiary.id,
        activated_by_id=actor.id,
        scope_ref=scope,
        privilege_keys=(privilege.key,),
        emergency_reason="数据库故障，需要紧急恢复",
        ttl=timedelta(minutes=30),
        idempotency_key="bg-idem-001",
        at=moment,
    )
    second = service.activate_break_glass(
        beneficiary_id=beneficiary.id,
        activated_by_id=actor.id,
        scope_ref=scope,
        privilege_keys=(privilege.key,),
        emergency_reason="数据库故障，需要紧急恢复",
        ttl=timedelta(minutes=30),
        idempotency_key="bg-idem-001",
        at=moment + timedelta(seconds=10),
    )

    assert first.id == second.id
    assert first.mode == TemporaryAccessMode.BREAK_GLASS
    assert first.notification_required
    assert first.activation_authority_snapshot["source"] == "test-break-glass-authority"
    assert TemporaryAccessGrant.objects.filter(idempotency_key="bg-idem-001").count() == 1


@pytest.mark.django_db
def test_break_glass_idempotency_key_cannot_be_reused_for_different_fingerprint() -> None:
    tenant = create_tenant("breakglass-conflict")
    actor = create_principal(tenant, "安全管理员")
    beneficiary = create_principal(tenant, "值班用户")
    privilege = create_privilege("elev.breakglass-conflict")
    create_boundary(
        tenant=tenant,
        principal=beneficiary,
        actor=actor,
        privilege_keys=(privilege.key,),
    )
    service = TemporaryAccessService(break_glass_checker=AllowBreakGlassChecker())
    scope = ElevatedScopeRef(tenant.id, ElevatedScopeType.TENANT)
    moment = timezone.now()
    service.activate_break_glass(
        beneficiary_id=beneficiary.id,
        activated_by_id=actor.id,
        scope_ref=scope,
        privilege_keys=(privilege.key,),
        emergency_reason="故障 A",
        ttl=timedelta(minutes=20),
        idempotency_key="bg-conflict-001",
        at=moment,
    )

    with pytest.raises(TemporaryAccessControlError, match="fingerprint"):
        service.activate_break_glass(
            beneficiary_id=beneficiary.id,
            activated_by_id=actor.id,
            scope_ref=scope,
            privilege_keys=(privilege.key,),
            emergency_reason="故障 B",
            ttl=timedelta(minutes=20),
            idempotency_key="bg-conflict-001",
            at=moment + timedelta(seconds=5),
        )


@pytest.mark.django_db
def test_temporary_access_resolver_reapplies_boundary_after_grant_creation() -> None:
    tenant = create_tenant("temp-recheck")
    actor = create_principal(tenant, "安全管理员")
    beneficiary = create_principal(tenant, "用户")
    privilege = create_privilege("elev.temp-recheck")
    create_boundary(
        tenant=tenant,
        principal=beneficiary,
        actor=actor,
        privilege_keys=(privilege.key,),
    )
    scope = ElevatedScopeRef(tenant.id, ElevatedScopeType.TENANT)
    grant = TemporaryAccessService(break_glass_checker=AllowBreakGlassChecker()).activate_break_glass(
        beneficiary_id=beneficiary.id,
        activated_by_id=actor.id,
        scope_ref=scope,
        privilege_keys=(privilege.key,),
        emergency_reason="临时操作",
        ttl=timedelta(minutes=30),
        idempotency_key="bg-recheck-001",
    )
    new_boundary = PermissionBoundary(
        tenant=tenant,
        principal=beneficiary,
        scope_type=ElevatedScopeType.TENANT,
        reason="后续收紧到空集合",
        created_by=actor,
    )
    new_boundary.full_clean()
    new_boundary.save()

    result = TemporaryAccessResolver().resolve(
        principal_id=beneficiary.id,
        scope_ref=scope,
    )

    assert grant.id in result.grant_ids
    assert result.effective_privilege_keys == ()
    assert len(result.boundary_ids) == 2


@pytest.mark.django_db
def test_delegation_creation_checks_delegator_authority_and_delegatee_boundary() -> None:
    tenant = create_tenant("deleg-create")
    delegator = create_principal(tenant, "委托人")
    delegatee = create_principal(tenant, "受托人")
    privilege = create_privilege("elev.delegate")
    create_boundary(
        tenant=tenant,
        principal=delegatee,
        actor=delegator,
        privilege_keys=(privilege.key,),
    )
    scope = ElevatedScopeRef(tenant.id, ElevatedScopeType.TENANT)
    moment = timezone.now()

    with pytest.raises(DelegationControlError, match="DelegationAuthorityChecker"):
        DelegationService().create(
            delegator_id=delegator.id,
            delegatee_id=delegatee.id,
            scope_ref=scope,
            privilege_keys=(privilege.key,),
            reason="临时委托",
            valid_until=moment + timedelta(hours=1),
            created_by_id=delegator.id,
            at=moment,
        )

    with pytest.raises(DelegationControlError, match="delegator"):
        DelegationService(authority_checker=StaticDelegationChecker(())).create(
            delegator_id=delegator.id,
            delegatee_id=delegatee.id,
            scope_ref=scope,
            privilege_keys=(privilege.key,),
            reason="临时委托",
            valid_until=moment + timedelta(hours=1),
            created_by_id=delegator.id,
            at=moment,
        )

    grant = DelegationService(
        authority_checker=StaticDelegationChecker((privilege.key,))
    ).create(
        delegator_id=delegator.id,
        delegatee_id=delegatee.id,
        scope_ref=scope,
        privilege_keys=(privilege.key,),
        reason="临时委托",
        valid_until=moment + timedelta(hours=1),
        created_by_id=delegator.id,
        at=moment,
    )

    assert grant.authority_snapshot["allowed_privilege_keys"] == [privilege.key]
    assert DelegationGrantPrivilege.objects.filter(delegation=grant).count() == 1


@pytest.mark.django_db
def test_delegation_resolver_revalidates_current_delegator_authority_and_fails_closed() -> None:
    tenant = create_tenant("deleg-revalidate")
    delegator = create_principal(tenant, "委托人")
    delegatee = create_principal(tenant, "受托人")
    privilege = create_privilege("elev.delegate-revalidate")
    create_boundary(
        tenant=tenant,
        principal=delegatee,
        actor=delegator,
        privilege_keys=(privilege.key,),
    )
    scope = ElevatedScopeRef(tenant.id, ElevatedScopeType.TENANT)
    grant = DelegationService(
        authority_checker=StaticDelegationChecker((privilege.key,))
    ).create(
        delegator_id=delegator.id,
        delegatee_id=delegatee.id,
        scope_ref=scope,
        privilege_keys=(privilege.key,),
        reason="短期委托",
        valid_until=timezone.now() + timedelta(hours=1),
        created_by_id=delegator.id,
    )

    removed = DelegationResolver(authority_checker=StaticDelegationChecker(())).resolve(
        delegatee_id=delegatee.id,
        scope_ref=scope,
    )
    failed = DelegationResolver(
        authority_checker=StaticDelegationChecker((privilege.key,), fail=True)
    ).resolve(
        delegatee_id=delegatee.id,
        scope_ref=scope,
    )

    assert grant.id in removed.delegation_ids
    assert removed.effective_privilege_keys == ()
    assert not removed.fail_closed
    assert failed.effective_privilege_keys == ()
    assert failed.delegation_ids == ()
    assert failed.fail_closed


@pytest.mark.django_db
def test_delegation_cross_tenant_is_rejected() -> None:
    tenant_a = create_tenant("deleg-cross-a")
    tenant_b = create_tenant("deleg-cross-b")
    delegator = create_principal(tenant_a, "A 委托人")
    delegatee = create_principal(tenant_b, "B 受托人")
    privilege = create_privilege("elev.delegate-cross")

    with pytest.raises(DelegationControlError):
        DelegationService(
            authority_checker=StaticDelegationChecker((privilege.key,))
        ).create(
            delegator_id=delegator.id,
            delegatee_id=delegatee.id,
            scope_ref=ElevatedScopeRef(tenant_a.id, ElevatedScopeType.TENANT),
            privilege_keys=(privilege.key,),
            reason="非法跨租户委托",
            valid_until=timezone.now() + timedelta(hours=1),
            created_by_id=delegator.id,
        )


@pytest.mark.django_db
def test_break_glass_model_requires_notification_and_emergency_reason() -> None:
    tenant = create_tenant("breakglass-model")
    actor = create_principal(tenant, "管理员")
    beneficiary = create_principal(tenant, "用户")
    grant = TemporaryAccessGrant(
        tenant=tenant,
        beneficiary=beneficiary,
        mode=TemporaryAccessMode.BREAK_GLASS,
        scope_type=ElevatedScopeType.TENANT,
        valid_until=timezone.now() + timedelta(minutes=10),
        reason="紧急",
        emergency_reason="",
        notification_required=False,
        idempotency_key=f"bg-model-{uuid4()}",
        activated_by=actor,
    )

    with pytest.raises(ValidationError) as exc_info:
        grant.full_clean()

    assert "emergency_reason" in exc_info.value.message_dict
    assert "notification_required" in exc_info.value.message_dict

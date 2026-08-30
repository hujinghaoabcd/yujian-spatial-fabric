"""Phase B2.3 Commercial Controls 不变量、Evaluator 与 reservation accounting 测试。"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from spatial_fabric.commercial.models import (
    Budget,
    BudgetWindowType,
    CommercialGrantStatus,
    CommercialScopeType,
    CommercialSubjectType,
    EnforcementMode,
    EntitlementGrant,
    Quota,
    QuotaMeasurementType,
    QuotaWindowType,
    UsageCounter,
    UsageEventType,
    UsageRecord,
    UsageReservation,
    UsageReservationStatus,
)
from spatial_fabric.commercial.services import (
    BudgetEvaluator,
    CommercialScopeRef,
    EntitlementEvaluator,
    QuotaControlError,
    QuotaExceededError,
    UsageReservationService,
)
from spatial_fabric.iam.models import Principal, PrincipalType
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


def create_quota(
    *,
    tenant: Tenant,
    actor: Principal,
    metric_key: str,
    unit: str,
    limit_value: int,
    measurement_type: QuotaMeasurementType,
    window_type: QuotaWindowType,
    enforcement_mode: EnforcementMode = EnforcementMode.HARD,
    scope_type: CommercialScopeType = CommercialScopeType.TENANT,
    principal: Principal | None = None,
    project: Project | None = None,
) -> Quota:
    quota = Quota(
        tenant=tenant,
        metric_key=metric_key,
        unit=unit,
        subject_type=(
            CommercialSubjectType.PRINCIPAL if principal else CommercialSubjectType.TENANT
        ),
        principal=principal,
        scope_type=scope_type,
        project=project,
        measurement_type=measurement_type,
        limit_value=limit_value,
        window_type=window_type,
        enforcement_mode=enforcement_mode,
        created_by=actor,
    )
    quota.full_clean()
    quota.save()
    return quota


@pytest.mark.django_db
def test_entitlement_subject_shape_is_database_enforced() -> None:
    tenant = create_tenant("ent-shape")
    actor = create_principal(tenant, "管理员")
    user = create_principal(tenant, "用户")

    with pytest.raises(IntegrityError), transaction.atomic():
        EntitlementGrant.objects.create(
            tenant=tenant,
            entitlement_key="geophysics.aermod",
            subject_type=CommercialSubjectType.TENANT,
            principal=user,
            scope_type=CommercialScopeType.TENANT,
            granted_by=actor,
        )


@pytest.mark.django_db
def test_entitlement_inherits_from_tenant_to_environment() -> None:
    tenant = create_tenant("ent-inherit")
    actor = create_principal(tenant, "管理员")
    user = create_principal(tenant, "用户")
    _, _, environment = create_scope_tree(tenant, "inherit")
    grant = EntitlementGrant(
        tenant=tenant,
        entitlement_key="geophysics.aermod",
        subject_type=CommercialSubjectType.TENANT,
        scope_type=CommercialScopeType.TENANT,
        granted_by=actor,
    )
    grant.full_clean()
    grant.save()

    resolution = EntitlementEvaluator().resolve(
        principal_id=user.id,
        entitlement_key="geophysics.aermod",
        scope_ref=CommercialScopeRef(
            tenant.id,
            CommercialScopeType.ENVIRONMENT,
            environment.id,
        ),
    )

    assert resolution.entitled
    assert resolution.grant_ids == (grant.id,)


@pytest.mark.django_db
def test_entitlement_cross_tenant_principal_fails_clean() -> None:
    tenant_a = create_tenant("ent-cross-a")
    tenant_b = create_tenant("ent-cross-b")
    actor_a = create_principal(tenant_a, "A 管理员")
    user_b = create_principal(tenant_b, "B 用户")
    grant = EntitlementGrant(
        tenant=tenant_a,
        entitlement_key="geoagent.pro",
        subject_type=CommercialSubjectType.PRINCIPAL,
        principal=user_b,
        scope_type=CommercialScopeType.TENANT,
        granted_by=actor_a,
    )
    with pytest.raises(ValidationError) as exc_info:
        grant.full_clean()
    assert "principal" in exc_info.value.message_dict


@pytest.mark.django_db
def test_quota_measurement_window_is_database_enforced() -> None:
    tenant = create_tenant("quota-window")
    actor = create_principal(tenant, "管理员")

    with pytest.raises(IntegrityError), transaction.atomic():
        Quota.objects.create(
            tenant=tenant,
            metric_key="concurrent_jobs",
            unit="count",
            subject_type=CommercialSubjectType.TENANT,
            scope_type=CommercialScopeType.TENANT,
            measurement_type=QuotaMeasurementType.CONCURRENCY,
            limit_value=2,
            window_type=QuotaWindowType.CALENDAR_DAY,
            created_by=actor,
        )


@pytest.mark.django_db
def test_budget_validates_amount_currency_and_fixed_term() -> None:
    tenant = create_tenant("budget-shape")
    actor = create_principal(tenant, "管理员")
    budget = Budget(
        tenant=tenant,
        budget_key="monthly.compute",
        name="计算预算",
        scope_type=CommercialScopeType.TENANT,
        currency_code="cny",
        amount_limit=Decimal("0"),
        window_type=BudgetWindowType.FIXED_TERM,
        created_by=actor,
    )
    with pytest.raises(ValidationError) as exc_info:
        budget.full_clean()
    assert "currency_code" in exc_info.value.message_dict
    assert "amount_limit" in exc_info.value.message_dict
    assert "window_type" in exc_info.value.message_dict


@pytest.mark.django_db
def test_budget_evaluator_inherits_parent_scope_evidence() -> None:
    tenant = create_tenant("budget-inherit")
    actor = create_principal(tenant, "管理员")
    _, project, _ = create_scope_tree(tenant, "budget")
    tenant_budget = Budget.objects.create(
        tenant=tenant,
        budget_key="monthly.compute",
        name="租户预算",
        scope_type=CommercialScopeType.TENANT,
        currency_code="CNY",
        amount_limit=Decimal("10000.0000"),
        window_type=BudgetWindowType.CALENDAR_MONTH,
        created_by=actor,
    )
    project_budget = Budget.objects.create(
        tenant=tenant,
        budget_key="monthly.compute.project",
        name="项目预算",
        scope_type=CommercialScopeType.PROJECT,
        project=project,
        currency_code="CNY",
        amount_limit=Decimal("2000.0000"),
        window_type=BudgetWindowType.CALENDAR_MONTH,
        created_by=actor,
    )

    result = BudgetEvaluator().resolve(
        scope_ref=CommercialScopeRef(tenant.id, CommercialScopeType.PROJECT, project.id),
        currency_code="CNY",
    )
    assert set(result.budget_ids) == {tenant_budget.id, project_budget.id}


@pytest.mark.django_db
def test_multiple_hard_quotas_use_most_restrictive_effect() -> None:
    tenant = create_tenant("quota-multi-hard")
    actor = create_principal(tenant, "管理员")
    user = create_principal(tenant, "用户")
    _, project, _ = create_scope_tree(tenant, "hard")
    create_quota(
        tenant=tenant,
        actor=actor,
        metric_key="concurrent_jobs",
        unit="count",
        limit_value=10,
        measurement_type=QuotaMeasurementType.CONCURRENCY,
        window_type=QuotaWindowType.NONE,
    )
    create_quota(
        tenant=tenant,
        actor=actor,
        metric_key="concurrent_jobs",
        unit="count",
        limit_value=5,
        measurement_type=QuotaMeasurementType.CONCURRENCY,
        window_type=QuotaWindowType.NONE,
        scope_type=CommercialScopeType.PROJECT,
        project=project,
    )

    before_reservations = UsageReservation.objects.count()
    with pytest.raises(QuotaExceededError) as exc_info:
        UsageReservationService().reserve_for_context(
            principal_id=user.id,
            metric_key="concurrent_jobs",
            measurement_type=QuotaMeasurementType.CONCURRENCY,
            unit="count",
            scope_ref=CommercialScopeRef(tenant.id, CommercialScopeType.PROJECT, project.id),
            amount=6,
            idempotency_key="job-hard-001",
        )

    assert any(item.limit_value == 5 for item in exc_info.value.evidence)
    assert UsageReservation.objects.count() == before_reservations
    assert UsageCounter.objects.filter(reserved_value__gt=0).count() == 0


@pytest.mark.django_db
def test_soft_quota_allows_overage_and_preserves_evidence() -> None:
    tenant = create_tenant("quota-soft")
    actor = create_principal(tenant, "管理员")
    user = create_principal(tenant, "用户")
    quota = create_quota(
        tenant=tenant,
        actor=actor,
        metric_key="storage_bytes",
        unit="bytes",
        limit_value=5,
        measurement_type=QuotaMeasurementType.GAUGE,
        window_type=QuotaWindowType.NONE,
        enforcement_mode=EnforcementMode.SOFT,
    )

    result = UsageReservationService().reserve_for_context(
        principal_id=user.id,
        metric_key="storage_bytes",
        measurement_type=QuotaMeasurementType.GAUGE,
        unit="bytes",
        scope_ref=CommercialScopeRef(tenant.id, CommercialScopeType.TENANT),
        amount=6,
        idempotency_key="storage-soft-001",
    )

    assert result.exceeded_quota_ids == (quota.id,)
    counter = UsageCounter.objects.get(quota=quota)
    assert counter.reserved_value == 6


@pytest.mark.django_db
def test_reservation_idempotency_does_not_double_reserve() -> None:
    tenant = create_tenant("quota-idem")
    actor = create_principal(tenant, "管理员")
    user = create_principal(tenant, "用户")
    quota = create_quota(
        tenant=tenant,
        actor=actor,
        metric_key="concurrent_jobs",
        unit="count",
        limit_value=10,
        measurement_type=QuotaMeasurementType.CONCURRENCY,
        window_type=QuotaWindowType.NONE,
    )
    service = UsageReservationService()
    UsageCounter.objects.create(
        tenant=tenant,
        quota=quota,
        consumed_value=3,
        reserved_value=1,
    )
    first = service.reserve_for_context(
        principal_id=user.id,
        metric_key="concurrent_jobs",
        measurement_type=QuotaMeasurementType.CONCURRENCY,
        unit="count",
        scope_ref=CommercialScopeRef(tenant.id, CommercialScopeType.TENANT),
        amount=2,
        idempotency_key="idem-001",
    )
    second = service.reserve_for_context(
        principal_id=user.id,
        metric_key="concurrent_jobs",
        measurement_type=QuotaMeasurementType.CONCURRENCY,
        unit="count",
        scope_ref=CommercialScopeRef(tenant.id, CommercialScopeType.TENANT),
        amount=2,
        idempotency_key="idem-001",
    )

    assert second.reservation_id == first.reservation_id
    assert second.evidence == first.evidence
    assert first.evidence[0].consumed_value == 3
    assert first.evidence[0].reserved_value == 1
    assert UsageReservation.objects.count() == 1
    assert UsageCounter.objects.get(quota=quota).reserved_value == 3


@pytest.mark.django_db
def test_idempotency_fingerprint_conflict_fails_closed() -> None:
    tenant = create_tenant("quota-idem-conflict")
    actor = create_principal(tenant, "管理员")
    user = create_principal(tenant, "用户")
    create_quota(
        tenant=tenant,
        actor=actor,
        metric_key="concurrent_jobs",
        unit="count",
        limit_value=10,
        measurement_type=QuotaMeasurementType.CONCURRENCY,
        window_type=QuotaWindowType.NONE,
    )
    service = UsageReservationService()
    service.reserve_for_context(
        principal_id=user.id,
        metric_key="concurrent_jobs",
        measurement_type=QuotaMeasurementType.CONCURRENCY,
        unit="count",
        scope_ref=CommercialScopeRef(tenant.id, CommercialScopeType.TENANT),
        amount=1,
        idempotency_key="idem-conflict",
    )

    with pytest.raises(QuotaControlError):
        service.reserve_for_context(
            principal_id=user.id,
            metric_key="concurrent_jobs",
            measurement_type=QuotaMeasurementType.CONCURRENCY,
            unit="count",
            scope_ref=CommercialScopeRef(tenant.id, CommercialScopeType.TENANT),
            amount=2,
            idempotency_key="idem-conflict",
        )


@pytest.mark.django_db
def test_commit_moves_reserved_to_consumed_and_is_idempotent() -> None:
    tenant = create_tenant("quota-commit")
    actor = create_principal(tenant, "管理员")
    user = create_principal(tenant, "用户")
    quota = create_quota(
        tenant=tenant,
        actor=actor,
        metric_key="concurrent_jobs",
        unit="count",
        limit_value=3,
        measurement_type=QuotaMeasurementType.CONCURRENCY,
        window_type=QuotaWindowType.NONE,
    )
    service = UsageReservationService()
    decision = service.reserve_for_context(
        principal_id=user.id,
        metric_key="concurrent_jobs",
        measurement_type=QuotaMeasurementType.CONCURRENCY,
        unit="count",
        scope_ref=CommercialScopeRef(tenant.id, CommercialScopeType.TENANT),
        amount=1,
        idempotency_key="commit-001",
    )

    committed = service.commit(reservation_id=decision.reservation_id)
    committed_again = service.commit(reservation_id=decision.reservation_id)
    counter = UsageCounter.objects.get(quota=quota)

    assert committed.status == UsageReservationStatus.COMMITTED
    assert committed_again.status == UsageReservationStatus.COMMITTED
    assert counter.reserved_value == 0
    assert counter.consumed_value == 1
    assert UsageRecord.objects.filter(
        reservation_id=decision.reservation_id,
        event_type=UsageEventType.CONSUME,
    ).count() == 1


@pytest.mark.django_db
def test_release_committed_concurrency_returns_capacity() -> None:
    tenant = create_tenant("quota-release")
    actor = create_principal(tenant, "管理员")
    user = create_principal(tenant, "用户")
    quota = create_quota(
        tenant=tenant,
        actor=actor,
        metric_key="concurrent_jobs",
        unit="count",
        limit_value=2,
        measurement_type=QuotaMeasurementType.CONCURRENCY,
        window_type=QuotaWindowType.NONE,
    )
    service = UsageReservationService()
    decision = service.reserve_for_context(
        principal_id=user.id,
        metric_key="concurrent_jobs",
        measurement_type=QuotaMeasurementType.CONCURRENCY,
        unit="count",
        scope_ref=CommercialScopeRef(tenant.id, CommercialScopeType.TENANT),
        amount=1,
        idempotency_key="release-001",
    )
    service.commit(reservation_id=decision.reservation_id)
    released = service.release(reservation_id=decision.reservation_id)
    released_again = service.release(reservation_id=decision.reservation_id)
    counter = UsageCounter.objects.get(quota=quota)

    assert released.status == UsageReservationStatus.RELEASED
    assert released_again.status == UsageReservationStatus.RELEASED
    assert counter.consumed_value == 0
    assert UsageRecord.objects.filter(
        reservation_id=decision.reservation_id,
        event_type=UsageEventType.RELEASE,
    ).count() == 1


@pytest.mark.django_db
def test_consumption_cannot_be_released_after_commit() -> None:
    tenant = create_tenant("quota-consume")
    actor = create_principal(tenant, "管理员")
    user = create_principal(tenant, "用户")
    create_quota(
        tenant=tenant,
        actor=actor,
        metric_key="ai_tokens",
        unit="tokens",
        limit_value=100,
        measurement_type=QuotaMeasurementType.CONSUMPTION,
        window_type=QuotaWindowType.CALENDAR_DAY,
    )
    service = UsageReservationService()
    decision = service.reserve_for_context(
        principal_id=user.id,
        metric_key="ai_tokens",
        measurement_type=QuotaMeasurementType.CONSUMPTION,
        unit="tokens",
        scope_ref=CommercialScopeRef(tenant.id, CommercialScopeType.TENANT),
        amount=20,
        idempotency_key="tokens-001",
    )
    service.commit(reservation_id=decision.reservation_id)

    with pytest.raises(QuotaControlError):
        service.release(reservation_id=decision.reservation_id)
    reservation = UsageReservation.objects.get(pk=decision.reservation_id)
    assert reservation.status == UsageReservationStatus.COMMITTED


@pytest.mark.django_db
def test_expired_reservation_reclaims_reserved_capacity() -> None:
    tenant = create_tenant("quota-expire")
    actor = create_principal(tenant, "管理员")
    user = create_principal(tenant, "用户")
    quota = create_quota(
        tenant=tenant,
        actor=actor,
        metric_key="concurrent_jobs",
        unit="count",
        limit_value=1,
        measurement_type=QuotaMeasurementType.CONCURRENCY,
        window_type=QuotaWindowType.NONE,
    )
    service = UsageReservationService()
    base = timezone.now()
    first = service.reserve_for_context(
        principal_id=user.id,
        metric_key="concurrent_jobs",
        measurement_type=QuotaMeasurementType.CONCURRENCY,
        unit="count",
        scope_ref=CommercialScopeRef(tenant.id, CommercialScopeType.TENANT),
        amount=1,
        idempotency_key="expire-001",
        ttl=timedelta(seconds=1),
        at=base,
    )
    second = service.reserve_for_context(
        principal_id=user.id,
        metric_key="concurrent_jobs",
        measurement_type=QuotaMeasurementType.CONCURRENCY,
        unit="count",
        scope_ref=CommercialScopeRef(tenant.id, CommercialScopeType.TENANT),
        amount=1,
        idempotency_key="expire-002",
        at=base + timedelta(seconds=2),
    )

    assert UsageReservation.objects.get(pk=first.reservation_id).status == UsageReservationStatus.EXPIRED
    assert UsageReservation.objects.get(pk=second.reservation_id).status == UsageReservationStatus.RESERVED
    assert UsageCounter.objects.get(quota=quota).reserved_value == 1


@pytest.mark.django_db
def test_cross_tenant_usage_scope_fails_closed() -> None:
    tenant_a = create_tenant("usage-cross-a")
    tenant_b = create_tenant("usage-cross-b")
    user_a = create_principal(tenant_a, "A 用户")
    _, project_b, _ = create_scope_tree(tenant_b, "cross")

    with pytest.raises(QuotaControlError):
        UsageReservationService().reserve_for_context(
            principal_id=user_a.id,
            metric_key="concurrent_jobs",
            measurement_type=QuotaMeasurementType.CONCURRENCY,
            unit="count",
            scope_ref=CommercialScopeRef(
                tenant_a.id,
                CommercialScopeType.PROJECT,
                project_b.id,
            ),
            amount=1,
            idempotency_key="cross-001",
        )


@pytest.mark.django_db
def test_revoke_shape_is_database_enforced_for_quota() -> None:
    tenant = create_tenant("quota-revoke-shape")
    actor = create_principal(tenant, "管理员")

    with pytest.raises(IntegrityError), transaction.atomic():
        Quota.objects.create(
            tenant=tenant,
            metric_key="storage_bytes",
            unit="bytes",
            subject_type=CommercialSubjectType.TENANT,
            scope_type=CommercialScopeType.TENANT,
            measurement_type=QuotaMeasurementType.GAUGE,
            limit_value=100,
            window_type=QuotaWindowType.NONE,
            status=CommercialGrantStatus.REVOKED,
            created_by=actor,
        )

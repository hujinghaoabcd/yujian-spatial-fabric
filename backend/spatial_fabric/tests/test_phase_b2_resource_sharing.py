"""Phase B2.2 Resource Sharing 不变量、事务服务与 Resolver 测试。"""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from spatial_fabric.iam.models import (
    Group,
    GroupMembership,
    MembershipStatus,
    Principal,
    PrincipalType,
    Privilege,
    PrivilegeStatus,
)
from spatial_fabric.sharing.models import (
    AccessRequest,
    AccessRequestStatus,
    ShareGrant,
    ShareGranteeType,
    ShareGrantStatus,
)
from spatial_fabric.sharing.services import (
    AccessRequestError,
    AccessRequestService,
    ResourceRef,
    ShareGrantError,
    ShareGrantResolver,
    ShareGrantService,
    ShareGrantSourceType,
)
from spatial_fabric.tenancy.models import Tenant


def create_tenant(slug: str) -> Tenant:
    return Tenant.objects.create(name=f"租户 {slug}", slug=slug)


def create_principal(tenant: Tenant, name: str) -> Principal:
    return Principal.objects.create(
        tenant=tenant,
        principal_type=PrincipalType.HUMAN_USER,
        display_name=name,
    )


def create_group(*, tenant: Tenant, actor: Principal, slug: str) -> Group:
    return Group.objects.create(
        tenant=tenant,
        name=f"组 {slug}",
        slug=slug,
        created_by=actor,
    )


@pytest.mark.django_db
def test_share_grant_grantee_shape_is_database_enforced() -> None:
    tenant = create_tenant("share-shape")
    actor = create_principal(tenant, "管理员")
    user = create_principal(tenant, "用户")
    group = create_group(tenant=tenant, actor=actor, slug="team")

    with pytest.raises(IntegrityError), transaction.atomic():
        ShareGrant.objects.create(
            tenant=tenant,
            resource_kind="asset",
            resource_id=uuid4(),
            grantee_type=ShareGranteeType.PRINCIPAL,
            principal=user,
            group=group,
            granted_by=actor,
        )


@pytest.mark.django_db
def test_share_grant_cross_tenant_subject_and_actor_fail_clean() -> None:
    tenant_a = create_tenant("share-cross-a")
    tenant_b = create_tenant("share-cross-b")
    actor_a = create_principal(tenant_a, "A 管理员")
    actor_b = create_principal(tenant_b, "B 管理员")
    user_b = create_principal(tenant_b, "B 用户")

    grant = ShareGrant(
        tenant=tenant_a,
        resource_kind="asset",
        resource_id=uuid4(),
        grantee_type=ShareGranteeType.PRINCIPAL,
        principal=user_b,
        granted_by=actor_b,
    )
    with pytest.raises(ValidationError) as exc_info:
        grant.full_clean()

    assert "principal" in exc_info.value.message_dict
    assert "granted_by" in exc_info.value.message_dict
    assert actor_a.tenant_id == tenant_a.id


@pytest.mark.django_db
def test_only_one_active_direct_grant_per_resource_and_subject() -> None:
    tenant = create_tenant("share-unique")
    actor = create_principal(tenant, "管理员")
    user = create_principal(tenant, "用户")
    resource_id = uuid4()
    service = ShareGrantService()

    first = service.create_grant(
        tenant_id=tenant.id,
        resource_kind="map",
        resource_id=resource_id,
        privilege_keys=["tile_read"],
        granted_by_id=actor.id,
        principal_id=user.id,
    )

    with pytest.raises(ValidationError):
        service.create_grant(
            tenant_id=tenant.id,
            resource_kind="map",
            resource_id=resource_id,
            privilege_keys=["view_metadata"],
            granted_by_id=actor.id,
            principal_id=user.id,
        )

    service.revoke_grant(grant_id=first.id, actor_id=actor.id)
    second = service.create_grant(
        tenant_id=tenant.id,
        resource_kind="map",
        resource_id=resource_id,
        privilege_keys=["view_metadata"],
        granted_by_id=actor.id,
        principal_id=user.id,
    )
    assert second.id != first.id


@pytest.mark.django_db
def test_share_grant_service_rejects_unknown_privilege_atomically() -> None:
    tenant = create_tenant("share-unknown")
    actor = create_principal(tenant, "管理员")
    user = create_principal(tenant, "用户")

    before = ShareGrant.objects.count()
    with pytest.raises(ShareGrantError):
        ShareGrantService().create_grant(
            tenant_id=tenant.id,
            resource_kind="asset",
            resource_id=uuid4(),
            privilege_keys=["read", "does_not_exist"],
            granted_by_id=actor.id,
            principal_id=user.id,
        )
    assert ShareGrant.objects.count() == before


@pytest.mark.django_db
def test_share_grant_revoke_is_idempotent_and_preserves_first_evidence() -> None:
    tenant = create_tenant("share-revoke")
    actor = create_principal(tenant, "管理员")
    user = create_principal(tenant, "用户")
    service = ShareGrantService()
    grant = service.create_grant(
        tenant_id=tenant.id,
        resource_kind="asset",
        resource_id=uuid4(),
        privilege_keys=["read"],
        granted_by_id=actor.id,
        principal_id=user.id,
    )

    first = service.revoke_grant(grant_id=grant.id, actor_id=actor.id)
    first_revoked_at = first.revoked_at
    second = service.revoke_grant(grant_id=grant.id, actor_id=actor.id)

    assert second.status == ShareGrantStatus.REVOKED
    assert second.revoked_by_id == actor.id
    assert second.revoked_at == first_revoked_at


@pytest.mark.django_db
def test_share_grant_resolver_combines_direct_and_group_evidence() -> None:
    tenant = create_tenant("share-resolve")
    actor = create_principal(tenant, "管理员")
    user = create_principal(tenant, "用户")
    group = create_group(tenant=tenant, actor=actor, slug="analysts")
    GroupMembership.objects.create(
        tenant=tenant,
        group=group,
        principal=user,
        added_by=actor,
    )
    resource_id = uuid4()
    service = ShareGrantService()
    service.create_grant(
        tenant_id=tenant.id,
        resource_kind="map",
        resource_id=resource_id,
        privilege_keys=["view_metadata"],
        granted_by_id=actor.id,
        principal_id=user.id,
    )
    service.create_grant(
        tenant_id=tenant.id,
        resource_kind="map",
        resource_id=resource_id,
        privilege_keys=["tile_read"],
        granted_by_id=actor.id,
        group_id=group.id,
    )

    result = ShareGrantResolver().resolve(
        principal_id=user.id,
        resource_ref=ResourceRef(tenant.id, "map", resource_id),
    )

    assert result.effective_privilege_keys == frozenset({"view_metadata", "tile_read"})
    assert {grant.source_type for grant in result.grants} == {
        ShareGrantSourceType.DIRECT,
        ShareGrantSourceType.GROUP,
    }


@pytest.mark.django_db
def test_share_grant_resolver_fails_closed_for_inactive_facts() -> None:
    tenant = create_tenant("share-closed")
    actor = create_principal(tenant, "管理员")
    user = create_principal(tenant, "用户")
    group = create_group(tenant=tenant, actor=actor, slug="temporary")
    membership = GroupMembership.objects.create(
        tenant=tenant,
        group=group,
        principal=user,
        added_by=actor,
    )
    resource_id = uuid4()
    service = ShareGrantService()
    group_grant = service.create_grant(
        tenant_id=tenant.id,
        resource_kind="dataset",
        resource_id=resource_id,
        privilege_keys=["query"],
        granted_by_id=actor.id,
        group_id=group.id,
    )
    service.create_grant(
        tenant_id=tenant.id,
        resource_kind="dataset",
        resource_id=uuid4(),
        privilege_keys=["read"],
        granted_by_id=actor.id,
        principal_id=user.id,
        valid_until=timezone.now() + timedelta(seconds=1),
    )

    membership.status = MembershipStatus.SUSPENDED
    membership.save(update_fields=["status", "updated_at"])
    assert not ShareGrantResolver().resolve(
        principal_id=user.id,
        resource_ref=ResourceRef(tenant.id, "dataset", resource_id),
    ).grants

    membership.status = MembershipStatus.ACTIVE
    membership.save(update_fields=["status", "updated_at"])
    service.revoke_grant(grant_id=group_grant.id, actor_id=actor.id)
    assert not ShareGrantResolver().resolve(
        principal_id=user.id,
        resource_ref=ResourceRef(tenant.id, "dataset", resource_id),
    ).grants


@pytest.mark.django_db
def test_share_grant_resolver_ignores_nonempty_conditions_and_deprecated_privilege() -> None:
    tenant = create_tenant("share-condition")
    actor = create_principal(tenant, "管理员")
    user = create_principal(tenant, "用户")
    resource_a = uuid4()
    resource_b = uuid4()
    service = ShareGrantService()
    service.create_grant(
        tenant_id=tenant.id,
        resource_kind="asset",
        resource_id=resource_a,
        privilege_keys=["read"],
        granted_by_id=actor.id,
        principal_id=user.id,
        conditions={"network_zone": "internal"},
    )
    deprecated_grant = service.create_grant(
        tenant_id=tenant.id,
        resource_kind="asset",
        resource_id=resource_b,
        privilege_keys=["download"],
        granted_by_id=actor.id,
        principal_id=user.id,
    )
    privilege = Privilege.objects.get(key="download")
    privilege.status = PrivilegeStatus.DEPRECATED
    privilege.save(update_fields=["status", "updated_at"])

    assert not ShareGrantResolver().resolve(
        principal_id=user.id,
        resource_ref=ResourceRef(tenant.id, "asset", resource_a),
    ).grants
    result = ShareGrantResolver().resolve(
        principal_id=user.id,
        resource_ref=ResourceRef(tenant.id, "asset", resource_b),
    )
    assert result.grants == ()
    assert deprecated_grant.status == ShareGrantStatus.ACTIVE


@pytest.mark.django_db
def test_share_grant_resolver_rejects_cross_tenant_principal() -> None:
    tenant_a = create_tenant("share-resolver-a")
    tenant_b = create_tenant("share-resolver-b")
    user_b = create_principal(tenant_b, "B 用户")

    with pytest.raises(ShareGrantError):
        ShareGrantResolver().resolve(
            principal_id=user_b.id,
            resource_ref=ResourceRef(tenant_a.id, "asset", uuid4()),
        )


@pytest.mark.django_db
def test_access_request_submit_is_atomic_and_uses_privilege_fk() -> None:
    tenant = create_tenant("access-submit")
    user = create_principal(tenant, "用户")
    service = AccessRequestService()
    request = service.submit_request(
        tenant_id=tenant.id,
        requester_id=user.id,
        resource_kind="dataset",
        resource_id=uuid4(),
        privilege_keys=["query", "export"],
        justification="分析项目需要",
    )

    assert request.status == AccessRequestStatus.PENDING
    assert set(request.privilege_links.values_list("privilege__key", flat=True)) == {
        "query",
        "export",
    }

    before = AccessRequest.objects.count()
    with pytest.raises(AccessRequestError):
        service.submit_request(
            tenant_id=tenant.id,
            requester_id=user.id,
            resource_kind="dataset",
            resource_id=uuid4(),
            privilege_keys=["does_not_exist"],
        )
    assert AccessRequest.objects.count() == before


@pytest.mark.django_db
def test_access_request_fulfillment_creates_formal_share_grant() -> None:
    tenant = create_tenant("access-fulfill")
    actor = create_principal(tenant, "资源管理员")
    user = create_principal(tenant, "申请人")
    resource_id = uuid4()
    service = AccessRequestService()
    request = service.submit_request(
        tenant_id=tenant.id,
        requester_id=user.id,
        resource_kind="map",
        resource_id=resource_id,
        privilege_keys=["view_metadata", "tile_read"],
    )

    fulfilled = service.fulfill_request(access_request_id=request.id, actor_id=actor.id)
    assert fulfilled.status == AccessRequestStatus.FULFILLED
    assert fulfilled.fulfilled_by_grant is not None
    assert fulfilled.fulfilled_by_grant.principal_id == user.id
    assert fulfilled.fulfilled_by_grant.resource_id == resource_id

    result = ShareGrantResolver().resolve(
        principal_id=user.id,
        resource_ref=ResourceRef(tenant.id, "map", resource_id),
    )
    assert result.effective_privilege_keys == frozenset({"view_metadata", "tile_read"})


@pytest.mark.django_db
def test_access_request_does_not_silently_expand_existing_grant() -> None:
    tenant = create_tenant("access-expand")
    actor = create_principal(tenant, "资源管理员")
    user = create_principal(tenant, "申请人")
    resource_id = uuid4()
    ShareGrantService().create_grant(
        tenant_id=tenant.id,
        resource_kind="dataset",
        resource_id=resource_id,
        privilege_keys=["read"],
        granted_by_id=actor.id,
        principal_id=user.id,
    )
    request = AccessRequestService().submit_request(
        tenant_id=tenant.id,
        requester_id=user.id,
        resource_kind="dataset",
        resource_id=resource_id,
        privilege_keys=["read", "export"],
    )

    with pytest.raises(AccessRequestError):
        AccessRequestService().fulfill_request(
            access_request_id=request.id,
            actor_id=actor.id,
        )
    request.refresh_from_db()
    assert request.status == AccessRequestStatus.PENDING


@pytest.mark.django_db
def test_access_request_reject_and_cancel_are_terminal_without_grant() -> None:
    tenant = create_tenant("access-terminal")
    actor = create_principal(tenant, "管理员")
    user = create_principal(tenant, "用户")
    service = AccessRequestService()

    rejected = service.submit_request(
        tenant_id=tenant.id,
        requester_id=user.id,
        resource_kind="asset",
        resource_id=uuid4(),
        privilege_keys=["read"],
    )
    rejected = service.reject_request(access_request_id=rejected.id, actor_id=actor.id)
    assert rejected.status == AccessRequestStatus.REJECTED
    assert rejected.fulfilled_by_grant_id is None

    cancelled = service.submit_request(
        tenant_id=tenant.id,
        requester_id=user.id,
        resource_kind="asset",
        resource_id=uuid4(),
        privilege_keys=["read"],
    )
    cancelled = service.cancel_request(
        access_request_id=cancelled.id,
        requester_id=user.id,
    )
    assert cancelled.status == AccessRequestStatus.CANCELLED
    assert cancelled.fulfilled_by_grant_id is None


@pytest.mark.django_db
def test_access_request_status_shape_is_database_enforced() -> None:
    tenant = create_tenant("access-shape")
    user = create_principal(tenant, "用户")

    with pytest.raises(IntegrityError), transaction.atomic():
        AccessRequest.objects.create(
            tenant=tenant,
            requester=user,
            resource_kind="asset",
            resource_id=uuid4(),
            status=AccessRequestStatus.FULFILLED,
        )

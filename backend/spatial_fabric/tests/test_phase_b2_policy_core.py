"""Phase B2.1 Policy Core 不变量与发布事务测试。"""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from spatial_fabric.governance.models import (
    PolicyAttachment,
    PolicyDefinition,
    PolicySubjectType,
    PolicyTargetType,
    PolicyVersion,
    PolicyVersionStatus,
)
from spatial_fabric.governance.services import PolicyPublicationError, PolicyPublicationService
from spatial_fabric.iam.models import Group, Principal, PrincipalType, RoleDefinition
from spatial_fabric.tenancy.models import Environment, EnvironmentType, Project, Tenant, Workspace


VALID_SPEC: dict[str, object] = {
    "statements": [
        {
            "sid": "deny-export",
            "effect": "DENY",
            "actions": ["export"],
            "conditions": {},
        }
    ]
}


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


def create_principal(tenant: Tenant | None, name: str) -> Principal:
    return Principal.objects.create(
        tenant=tenant,
        principal_type=PrincipalType.HUMAN_USER,
        display_name=name,
    )


def create_published_policy(
    *,
    tenant: Tenant | None,
    actor: Principal,
    key: str,
) -> tuple[PolicyDefinition, PolicyVersion]:
    policy = PolicyDefinition.objects.create(
        tenant=tenant,
        key=key,
        name=key,
        created_by=actor,
    )
    version = PolicyVersion.objects.create(
        policy=policy,
        version_seq=1,
        spec=VALID_SPEC,
        created_by=actor,
    )
    version = PolicyPublicationService().publish(
        policy_version_id=version.id,
        actor_id=actor.id,
    )
    return policy, version


@pytest.mark.django_db
def test_policy_definition_platform_and_tenant_keys_have_separate_uniqueness() -> None:
    tenant_a, _, _, _ = create_tree("policy-key-a")
    tenant_b, _, _, _ = create_tree("policy-key-b")
    platform_actor = create_principal(None, "平台治理主体")
    actor_a = create_principal(tenant_a, "A 管理员")
    actor_b = create_principal(tenant_b, "B 管理员")

    PolicyDefinition.objects.create(
        tenant=None,
        key="data-export-policy",
        name="平台策略",
        created_by=platform_actor,
    )
    PolicyDefinition.objects.create(
        tenant=tenant_a,
        key="data-export-policy",
        name="A 策略",
        created_by=actor_a,
    )
    PolicyDefinition.objects.create(
        tenant=tenant_b,
        key="data-export-policy",
        name="B 策略",
        created_by=actor_b,
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        PolicyDefinition.objects.create(
            tenant=tenant_a,
            key="data-export-policy",
            name="A 重复策略",
            created_by=actor_a,
        )


@pytest.mark.django_db
def test_policy_publication_is_atomic_and_hashes_canonical_spec() -> None:
    tenant, _, _, _ = create_tree("policy-publish")
    actor = create_principal(tenant, "策略管理员")
    policy = PolicyDefinition.objects.create(
        tenant=tenant,
        key="export-guard",
        name="导出保护",
        created_by=actor,
    )
    version = PolicyVersion.objects.create(
        policy=policy,
        version_seq=1,
        spec=VALID_SPEC,
        created_by=actor,
    )

    published = PolicyPublicationService().publish(
        policy_version_id=version.id,
        actor_id=actor.id,
    )

    assert published.status == PolicyVersionStatus.PUBLISHED
    assert published.published_by_id == actor.id
    assert published.published_at is not None
    assert len(published.content_hash) == 64


@pytest.mark.django_db
def test_published_policy_version_cannot_be_modified_through_domain_service() -> None:
    tenant, _, _, _ = create_tree("policy-immutable")
    actor = create_principal(tenant, "策略管理员")
    _, version = create_published_policy(
        tenant=tenant,
        actor=actor,
        key="immutable-policy",
    )

    with pytest.raises(PolicyPublicationError):
        PolicyPublicationService().update_draft_spec(
            policy_version_id=version.id,
            actor_id=actor.id,
            spec={"statements": []},
        )


@pytest.mark.django_db
def test_publish_rejects_unknown_or_deprecated_privilege() -> None:
    tenant, _, _, _ = create_tree("policy-action")
    actor = create_principal(tenant, "策略管理员")
    policy = PolicyDefinition.objects.create(
        tenant=tenant,
        key="invalid-action-policy",
        name="无效动作策略",
        created_by=actor,
    )
    version = PolicyVersion.objects.create(
        policy=policy,
        version_seq=1,
        spec={
            "statements": [
                {
                    "sid": "bad-action",
                    "effect": "DENY",
                    "actions": ["does_not_exist"],
                    "conditions": {},
                }
            ]
        },
        created_by=actor,
    )

    with pytest.raises(ValidationError):
        PolicyPublicationService().publish(
            policy_version_id=version.id,
            actor_id=actor.id,
        )

    version.refresh_from_db()
    assert version.status == PolicyVersionStatus.DRAFT
    assert version.published_at is None


@pytest.mark.django_db
def test_tenant_policy_cannot_be_published_by_other_tenant() -> None:
    tenant_a, _, _, _ = create_tree("policy-actor-a")
    tenant_b, _, _, _ = create_tree("policy-actor-b")
    actor_a = create_principal(tenant_a, "A 管理员")
    actor_b = create_principal(tenant_b, "B 管理员")
    policy = PolicyDefinition.objects.create(
        tenant=tenant_a,
        key="tenant-bound-policy",
        name="租户策略",
        created_by=actor_a,
    )
    version = PolicyVersion.objects.create(
        policy=policy,
        version_seq=1,
        spec=VALID_SPEC,
        created_by=actor_a,
    )

    with pytest.raises(PolicyPublicationError):
        PolicyPublicationService().publish(
            policy_version_id=version.id,
            actor_id=actor_b.id,
        )


@pytest.mark.django_db
def test_policy_attachment_subject_shapes_are_database_enforced() -> None:
    tenant, _, _, _ = create_tree("policy-subject")
    actor = create_principal(tenant, "管理员")
    user = create_principal(tenant, "用户")
    group = Group.objects.create(
        tenant=tenant,
        name="安全组",
        slug="security-team",
        created_by=actor,
    )
    role = RoleDefinition.objects.create(
        tenant=tenant,
        key="policy-subject-role",
        name="策略主体角色",
        created_by=actor,
    )
    _, version = create_published_policy(
        tenant=tenant,
        actor=actor,
        key="subject-shape-policy",
    )

    PolicyAttachment.objects.create(
        tenant=tenant,
        policy_version=version,
        subject_type=PolicySubjectType.ALL,
        target_type=PolicyTargetType.TENANT,
        attached_by=actor,
    )
    PolicyAttachment.objects.create(
        tenant=tenant,
        policy_version=version,
        subject_type=PolicySubjectType.PRINCIPAL,
        principal=user,
        target_type=PolicyTargetType.TENANT,
        attached_by=actor,
    )
    PolicyAttachment.objects.create(
        tenant=tenant,
        policy_version=version,
        subject_type=PolicySubjectType.GROUP,
        group=group,
        target_type=PolicyTargetType.TENANT,
        attached_by=actor,
    )
    PolicyAttachment.objects.create(
        tenant=tenant,
        policy_version=version,
        subject_type=PolicySubjectType.ROLE,
        role=role,
        target_type=PolicyTargetType.TENANT,
        attached_by=actor,
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        PolicyAttachment.objects.create(
            tenant=tenant,
            policy_version=version,
            subject_type=PolicySubjectType.PRINCIPAL,
            principal=user,
            group=group,
            target_type=PolicyTargetType.TENANT,
            attached_by=actor,
        )


@pytest.mark.django_db
def test_policy_attachment_target_shapes_are_database_enforced() -> None:
    tenant, workspace, project, environment = create_tree("policy-target")
    actor = create_principal(tenant, "管理员")
    _, version = create_published_policy(
        tenant=tenant,
        actor=actor,
        key="target-shape-policy",
    )

    PolicyAttachment.objects.create(
        tenant=tenant,
        policy_version=version,
        target_type=PolicyTargetType.WORKSPACE,
        workspace=workspace,
        attached_by=actor,
    )
    PolicyAttachment.objects.create(
        tenant=tenant,
        policy_version=version,
        target_type=PolicyTargetType.PROJECT,
        project=project,
        attached_by=actor,
    )
    PolicyAttachment.objects.create(
        tenant=tenant,
        policy_version=version,
        target_type=PolicyTargetType.ENVIRONMENT,
        environment=environment,
        attached_by=actor,
    )
    PolicyAttachment.objects.create(
        tenant=tenant,
        policy_version=version,
        target_type=PolicyTargetType.RESOURCE,
        resource_kind="asset",
        resource_id=uuid4(),
        attached_by=actor,
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        PolicyAttachment.objects.create(
            tenant=tenant,
            policy_version=version,
            target_type=PolicyTargetType.RESOURCE,
            resource_kind="asset",
            resource_id=None,
            attached_by=actor,
        )


@pytest.mark.django_db
def test_policy_attachment_cross_tenant_subject_scope_and_policy_fail_clean() -> None:
    tenant_a, _, _, _ = create_tree("policy-cross-a")
    tenant_b, workspace_b, _, _ = create_tree("policy-cross-b")
    actor_a = create_principal(tenant_a, "A 管理员")
    user_b = create_principal(tenant_b, "B 用户")
    _, version_a = create_published_policy(
        tenant=tenant_a,
        actor=actor_a,
        key="tenant-a-policy",
    )

    attachment = PolicyAttachment(
        tenant=tenant_a,
        policy_version=version_a,
        subject_type=PolicySubjectType.PRINCIPAL,
        principal=user_b,
        target_type=PolicyTargetType.WORKSPACE,
        workspace=workspace_b,
        attached_by=actor_a,
    )

    with pytest.raises(ValidationError) as exc_info:
        attachment.full_clean()

    assert "principal" in exc_info.value.message_dict
    assert "workspace" in exc_info.value.message_dict

    actor_b = create_principal(tenant_b, "B 管理员")
    cross_policy_attachment = PolicyAttachment(
        tenant=tenant_b,
        policy_version=version_a,
        target_type=PolicyTargetType.TENANT,
        attached_by=actor_b,
    )
    with pytest.raises(ValidationError) as policy_error:
        cross_policy_attachment.full_clean()
    assert "policy_version" in policy_error.value.message_dict


@pytest.mark.django_db
def test_platform_policy_can_attach_to_tenant() -> None:
    tenant, _, _, _ = create_tree("platform-policy-target")
    platform_actor = create_principal(None, "平台治理主体")
    tenant_actor = create_principal(tenant, "租户管理员")
    _, version = create_published_policy(
        tenant=None,
        actor=platform_actor,
        key="platform-security-baseline",
    )
    attachment = PolicyAttachment(
        tenant=tenant,
        policy_version=version,
        target_type=PolicyTargetType.TENANT,
        attached_by=tenant_actor,
    )

    attachment.full_clean()


@pytest.mark.django_db
def test_draft_policy_cannot_be_attached_and_valid_window_is_enforced() -> None:
    tenant, _, _, _ = create_tree("draft-attachment")
    actor = create_principal(tenant, "管理员")
    policy = PolicyDefinition.objects.create(
        tenant=tenant,
        key="draft-policy",
        name="草稿策略",
        created_by=actor,
    )
    version = PolicyVersion.objects.create(
        policy=policy,
        version_seq=1,
        spec=VALID_SPEC,
        created_by=actor,
    )
    attachment = PolicyAttachment(
        tenant=tenant,
        policy_version=version,
        target_type=PolicyTargetType.TENANT,
        attached_by=actor,
    )

    with pytest.raises(ValidationError) as exc_info:
        attachment.full_clean()
    assert "policy_version" in exc_info.value.message_dict

    _, published = create_published_policy(
        tenant=tenant,
        actor=actor,
        key="window-policy",
    )
    now = timezone.now()
    with pytest.raises(IntegrityError), transaction.atomic():
        PolicyAttachment.objects.create(
            tenant=tenant,
            policy_version=published,
            target_type=PolicyTargetType.TENANT,
            valid_from=now,
            valid_until=now - timedelta(seconds=1),
            attached_by=actor,
        )

"""Governance application services。

Phase B2.1 先实现 PolicyVersion 的受控草稿更新与发布事务。业务代码不得通过本服务绕过
Policy/Principal tenant 边界，也不得原地修改已发布策略版本。
"""

from __future__ import annotations

import hashlib
import json
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from spatial_fabric.governance.models import (
    PolicyVersion,
    PolicyVersionStatus,
    validate_published_policy_spec,
)
from spatial_fabric.iam.models import Principal


class PolicyPublicationError(ValueError):
    """PolicyVersion 发布/修改违反治理契约。"""


def canonical_policy_hash(spec: object) -> str:
    """对策略 JSON 产生稳定 sha256 内容指纹。"""

    payload = json.dumps(
        spec,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class PolicyPublicationService:
    """PolicyVersion lifecycle 的唯一领域服务入口。"""

    @transaction.atomic
    def update_draft_spec(
        self,
        *,
        policy_version_id: UUID,
        actor_id: UUID,
        spec: object,
    ) -> PolicyVersion:
        """只允许修改 DRAFT；已发布/退役版本必须新建版本。"""

        version = (
            PolicyVersion.objects.select_for_update()
            .select_related("policy")
            .get(pk=policy_version_id)
        )
        actor = Principal.objects.get(pk=actor_id)
        self._assert_actor_can_manage(version=version, actor=actor)

        if version.status != PolicyVersionStatus.DRAFT:
            raise PolicyPublicationError("已发布或退役 PolicyVersion 不可原地修改，必须创建新版本。")

        version.spec = spec
        version.full_clean()
        version.save(update_fields=["spec", "updated_at"])
        return version

    @transaction.atomic
    def publish(self, *, policy_version_id: UUID, actor_id: UUID) -> PolicyVersion:
        """原子发布 DRAFT PolicyVersion，并固定其内容指纹和发布证据。"""

        version = (
            PolicyVersion.objects.select_for_update()
            .select_related("policy")
            .get(pk=policy_version_id)
        )
        actor = Principal.objects.get(pk=actor_id)
        self._assert_actor_can_manage(version=version, actor=actor)

        if version.status != PolicyVersionStatus.DRAFT:
            raise PolicyPublicationError("只有 DRAFT PolicyVersion 可以发布。")

        validate_published_policy_spec(version.spec)
        version.content_hash = canonical_policy_hash(version.spec)
        version.status = PolicyVersionStatus.PUBLISHED
        version.published_by = actor
        version.published_at = timezone.now()
        version.full_clean()
        version.save(
            update_fields=[
                "content_hash",
                "status",
                "published_by",
                "published_at",
                "updated_at",
            ]
        )
        return version

    def _assert_actor_can_manage(self, *, version: PolicyVersion, actor: Principal) -> None:
        """校验发布/编辑主体与 PolicyDefinition 的 Tenant 边界。"""

        policy_tenant_id = version.policy.tenant_id
        if policy_tenant_id is None:
            if actor.tenant_id is not None:
                raise PolicyPublicationError("平台 PolicyVersion 只能由平台主体管理。")
            return

        if actor.tenant_id not in (None, policy_tenant_id):
            raise PolicyPublicationError("PolicyVersion 只能由所属租户或平台主体管理。")

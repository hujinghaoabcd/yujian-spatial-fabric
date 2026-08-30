"""Phase B2.2 Resource Sharing application services 与候选授权解析器。

本模块把写操作收口到事务服务，并提供 ShareGrantResolver。Resolver 只返回显式资源分享产生的
ALLOW 候选及 evidence，不执行 Policy / Entitlement / Quota / Approval 的最终组合决策。
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from spatial_fabric.iam.models import (
    Group,
    GroupMembership,
    GroupStatus,
    MembershipStatus,
    Principal,
    Privilege,
    PrivilegeStatus,
)
from spatial_fabric.sharing.models import (
    AccessRequest,
    AccessRequestPrivilege,
    AccessRequestStatus,
    ShareGrant,
    ShareGrantPrivilege,
    ShareGranteeType,
    ShareGrantStatus,
)


class ShareGrantError(ValueError):
    """ShareGrant 创建、撤销或解析违反 B2.2 契约。"""


class AccessRequestError(ValueError):
    """AccessRequest 状态转换或处理违反 B2.2 契约。"""


class ShareGrantSourceType(StrEnum):
    """一条 resolved ShareGrant 的主体来源。"""

    DIRECT = "DIRECT"
    GROUP = "GROUP"


@dataclass(frozen=True, slots=True)
class ResourceRef:
    """跨模块资源的 provider-neutral 稳定引用。"""

    tenant_id: UUID
    resource_kind: str
    resource_id: UUID


@dataclass(frozen=True, slots=True)
class ResolvedShareGrant:
    """一条 ShareGrant → Privilege 的可解释候选授权证据。"""

    grant_id: UUID
    privilege_id: UUID
    privilege_key: str
    source_type: ShareGrantSourceType
    group_id: UUID | None


@dataclass(frozen=True, slots=True)
class ShareGrantResolution:
    """一次单资源显式分享解析结果；重复 privilege evidence 不丢失。"""

    principal_id: UUID
    resource_ref: ResourceRef
    resolved_at: datetime
    grants: tuple[ResolvedShareGrant, ...]

    @property
    def effective_privilege_keys(self) -> frozenset[str]:
        """返回去重后的候选 Privilege key，供最终 AuthorizationService 组合使用。"""

        return frozenset(grant.privilege_key for grant in self.grants)


class _PrivilegeLoader:
    """共享写服务的 Privilege Contract 校验器。"""

    @staticmethod
    def load_active(privilege_keys: Collection[str]) -> tuple[Privilege, ...]:
        """按稳定 key 加载全部 ACTIVE Privilege；未知/弃用动作 fail closed。"""

        normalized_keys = tuple(sorted({key for key in privilege_keys if key}))
        if not normalized_keys:
            raise ShareGrantError("Privilege 集不能为空。")

        privileges = tuple(
            Privilege.objects.filter(
                key__in=normalized_keys,
                status=PrivilegeStatus.ACTIVE,
            ).order_by("key")
        )
        found_keys = {privilege.key for privilege in privileges}
        missing = sorted(set(normalized_keys) - found_keys)
        if missing:
            raise ShareGrantError(f"存在未知或已弃用 Privilege：{missing}。")
        return privileges


class ShareGrantService:
    """ShareGrant aggregate 的原子写入口。"""

    @transaction.atomic
    def create_grant(
        self,
        *,
        tenant_id: UUID,
        resource_kind: str,
        resource_id: UUID,
        privilege_keys: Collection[str],
        granted_by_id: UUID,
        principal_id: UUID | None = None,
        group_id: UUID | None = None,
        valid_from: datetime | None = None,
        valid_until: datetime | None = None,
        conditions: dict[str, object] | None = None,
    ) -> ShareGrant:
        """原子创建 ShareGrant + ShareGrantPrivilege。

        被分享主体必须恰好是 Principal/Group 之一；任何 Privilege 不存在/已弃用都会在创建父记录
        之前失败，因此不会留下“没有动作的空 Grant”。
        """

        if (principal_id is None) == (group_id is None):
            raise ShareGrantError("ShareGrant 必须且只能指定 Principal 或 Group 之一。")

        actor = Principal.objects.get(pk=granted_by_id)
        if actor.tenant_id not in (None, tenant_id):
            raise ShareGrantError("授权主体必须属于资源 Tenant 或平台。")

        principal: Principal | None = None
        group: Group | None = None
        if principal_id is not None:
            principal = Principal.objects.get(pk=principal_id)
            if principal.tenant_id != tenant_id:
                raise ShareGrantError("被分享 Principal 必须属于资源 Tenant。")
            grantee_type = ShareGranteeType.PRINCIPAL
        else:
            group = Group.objects.get(pk=group_id)
            if group.tenant_id != tenant_id:
                raise ShareGrantError("被分享 Group 必须属于资源 Tenant。")
            if group.status != GroupStatus.ACTIVE:
                raise ShareGrantError("只有 ACTIVE Group 可以接收新的 ShareGrant。")
            grantee_type = ShareGranteeType.GROUP

        privileges = _PrivilegeLoader.load_active(privilege_keys)
        grant = ShareGrant(
            tenant_id=tenant_id,
            resource_kind=resource_kind,
            resource_id=resource_id,
            grantee_type=grantee_type,
            principal=principal,
            group=group,
            valid_from=valid_from,
            valid_until=valid_until,
            conditions=conditions or {},
            granted_by=actor,
        )
        grant.full_clean()
        grant.save()

        ShareGrantPrivilege.objects.bulk_create(
            [
                ShareGrantPrivilege(
                    tenant_id=tenant_id,
                    grant=grant,
                    privilege=privilege,
                )
                for privilege in privileges
            ]
        )
        return grant

    @transaction.atomic
    def revoke_grant(self, *, grant_id: UUID, actor_id: UUID) -> ShareGrant:
        """原子撤销 ShareGrant；重复撤销保持幂等且不篡改第一次撤销证据。"""

        grant = ShareGrant.objects.select_for_update().get(pk=grant_id)
        actor = Principal.objects.get(pk=actor_id)
        if actor.tenant_id not in (None, grant.tenant_id):
            raise ShareGrantError("撤销主体必须属于 ShareGrant Tenant 或平台。")

        if grant.status == ShareGrantStatus.REVOKED:
            return grant

        grant.status = ShareGrantStatus.REVOKED
        grant.revoked_by = actor
        grant.revoked_at = timezone.now()
        grant.full_clean()
        grant.save(update_fields=["status", "revoked_by", "revoked_at", "updated_at"])
        return grant


class AccessRequestService:
    """AccessRequest aggregate 的原子提交与普通资源分享处理入口。"""

    def __init__(self, *, share_grant_service: ShareGrantService | None = None) -> None:
        self._share_grant_service = share_grant_service or ShareGrantService()

    @transaction.atomic
    def submit_request(
        self,
        *,
        tenant_id: UUID,
        requester_id: UUID,
        resource_kind: str,
        resource_id: UUID,
        privilege_keys: Collection[str],
        justification: str = "",
        requested_valid_until: datetime | None = None,
    ) -> AccessRequest:
        """原子创建 AccessRequest + requested Privilege links。"""

        requester = Principal.objects.get(pk=requester_id)
        if requester.tenant_id != tenant_id:
            raise AccessRequestError("requester 必须属于资源 Tenant。")
        if requested_valid_until is not None and requested_valid_until <= timezone.now():
            raise AccessRequestError("requested_valid_until 必须晚于当前时间。")

        try:
            privileges = _PrivilegeLoader.load_active(privilege_keys)
        except ShareGrantError as exc:
            raise AccessRequestError(str(exc)) from exc

        access_request = AccessRequest(
            tenant_id=tenant_id,
            requester=requester,
            resource_kind=resource_kind,
            resource_id=resource_id,
            justification=justification,
            requested_valid_until=requested_valid_until,
        )
        access_request.full_clean()
        access_request.save()

        AccessRequestPrivilege.objects.bulk_create(
            [
                AccessRequestPrivilege(
                    tenant_id=tenant_id,
                    access_request=access_request,
                    privilege=privilege,
                )
                for privilege in privileges
            ]
        )
        return access_request

    @transaction.atomic
    def fulfill_request(self, *, access_request_id: UUID, actor_id: UUID) -> AccessRequest:
        """用正式 ShareGrant 原子满足普通 AccessRequest。

        这不是 B2.4 Approval 引擎。调用本方法前，上层未来必须先完成 Policy/风险判断。若同一
        requester + ResourceRef 已存在 ACTIVE ShareGrant，本阶段只允许在该 Grant 已覆盖全部请求动作
        时复用；扩权需走显式 Grant amendment/替换能力，避免静默扩大既有授权。
        """

        access_request = (
            AccessRequest.objects.select_for_update()
            .select_related("requester")
            .prefetch_related("privilege_links__privilege")
            .get(pk=access_request_id)
        )
        actor = Principal.objects.get(pk=actor_id)
        self._assert_decision_actor(access_request=access_request, actor=actor)
        if access_request.status != AccessRequestStatus.PENDING:
            raise AccessRequestError("只有 PENDING AccessRequest 可以被满足。")

        requested_keys = tuple(
            sorted(link.privilege.key for link in access_request.privilege_links.all())
        )
        if not requested_keys:
            raise AccessRequestError("AccessRequest 没有 requested Privilege，拒绝 fulfillment。")

        existing = (
            ShareGrant.objects.select_for_update()
            .filter(
                tenant_id=access_request.tenant_id,
                resource_kind=access_request.resource_kind,
                resource_id=access_request.resource_id,
                grantee_type=ShareGranteeType.PRINCIPAL,
                principal_id=access_request.requester_id,
                status=ShareGrantStatus.ACTIVE,
            )
            .first()
        )

        if existing is not None:
            existing_keys = set(
                ShareGrantPrivilege.objects.filter(
                    grant=existing,
                    privilege__status=PrivilegeStatus.ACTIVE,
                ).values_list("privilege__key", flat=True)
            )
            if not set(requested_keys).issubset(existing_keys):
                raise AccessRequestError(
                    "已有 ACTIVE ShareGrant 但未覆盖全部请求动作；B2.2 禁止静默扩权，请显式替换授权。"
                )
            grant = existing
        else:
            grant = self._share_grant_service.create_grant(
                tenant_id=access_request.tenant_id,
                resource_kind=access_request.resource_kind,
                resource_id=access_request.resource_id,
                privilege_keys=requested_keys,
                granted_by_id=actor.id,
                principal_id=access_request.requester_id,
                valid_until=access_request.requested_valid_until,
            )

        access_request.status = AccessRequestStatus.FULFILLED
        access_request.fulfilled_by_grant = grant
        access_request.decided_by = actor
        access_request.decided_at = timezone.now()
        access_request.full_clean()
        access_request.save(
            update_fields=[
                "status",
                "fulfilled_by_grant",
                "decided_by",
                "decided_at",
                "updated_at",
            ]
        )
        return access_request

    @transaction.atomic
    def reject_request(self, *, access_request_id: UUID, actor_id: UUID) -> AccessRequest:
        """拒绝 PENDING AccessRequest，不创建 ShareGrant。"""

        access_request = AccessRequest.objects.select_for_update().get(pk=access_request_id)
        actor = Principal.objects.get(pk=actor_id)
        self._assert_decision_actor(access_request=access_request, actor=actor)
        if access_request.status != AccessRequestStatus.PENDING:
            raise AccessRequestError("只有 PENDING AccessRequest 可以被拒绝。")

        access_request.status = AccessRequestStatus.REJECTED
        access_request.decided_by = actor
        access_request.decided_at = timezone.now()
        access_request.full_clean()
        access_request.save(
            update_fields=["status", "decided_by", "decided_at", "updated_at"]
        )
        return access_request

    @transaction.atomic
    def cancel_request(self, *, access_request_id: UUID, requester_id: UUID) -> AccessRequest:
        """请求者本人撤回 PENDING AccessRequest。"""

        access_request = AccessRequest.objects.select_for_update().get(pk=access_request_id)
        requester = Principal.objects.get(pk=requester_id)
        if access_request.requester_id != requester.id:
            raise AccessRequestError("只有 AccessRequest.requester 本人可以撤回请求。")
        if access_request.status != AccessRequestStatus.PENDING:
            raise AccessRequestError("只有 PENDING AccessRequest 可以撤回。")

        access_request.status = AccessRequestStatus.CANCELLED
        access_request.decided_by = requester
        access_request.decided_at = timezone.now()
        access_request.full_clean()
        access_request.save(
            update_fields=["status", "decided_by", "decided_at", "updated_at"]
        )
        return access_request

    @staticmethod
    def _assert_decision_actor(*, access_request: AccessRequest, actor: Principal) -> None:
        """普通资源请求处理主体只能来自同 Tenant 或平台。"""

        if actor.tenant_id not in (None, access_request.tenant_id):
            raise AccessRequestError("AccessRequest 处理主体必须属于资源 Tenant 或平台。")


class ShareGrantResolver:
    """解析 Direct + Group ShareGrant 的单资源候选授权。"""

    def resolve(
        self,
        *,
        principal_id: UUID,
        resource_ref: ResourceRef,
        at: datetime | None = None,
    ) -> ShareGrantResolution:
        """解析目标 Principal 在一个 ResourceRef 上全部有效 ShareGrant evidence。

        B2.2 对 ``ShareGrant.conditions`` 采用 fail-closed：只有空条件 Grant 会进入候选结果。
        """

        moment = at or timezone.now()
        principal = self._validate_principal(
            principal_id=principal_id,
            tenant_id=resource_ref.tenant_id,
        )
        group_ids = self._effective_group_ids(
            principal_id=principal.id,
            tenant_id=resource_ref.tenant_id,
            at=moment,
        )
        grants = self._effective_grants(
            principal_id=principal.id,
            group_ids=group_ids,
            resource_ref=resource_ref,
            at=moment,
        )
        resolved = self._expand_privileges(grants)
        return ShareGrantResolution(
            principal_id=principal.id,
            resource_ref=resource_ref,
            resolved_at=moment,
            grants=resolved,
        )

    @staticmethod
    def _validate_principal(*, principal_id: UUID, tenant_id: UUID) -> Principal:
        """ShareGrant 第一版不允许 Principal 跨 Tenant 解析。"""

        try:
            principal = Principal.objects.only("id", "tenant_id").get(pk=principal_id)
        except Principal.DoesNotExist as exc:
            raise ShareGrantError("principal_id 不存在。") from exc
        if principal.tenant_id != tenant_id:
            raise ShareGrantError("Principal 与 ResourceRef 不属于同一 Tenant。")
        return principal

    @staticmethod
    def _effective_group_ids(
        *,
        principal_id: UUID,
        tenant_id: UUID,
        at: datetime,
    ) -> tuple[UUID, ...]:
        """返回当前有效且 Group 自身 ACTIVE 的成员组。"""

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

    @staticmethod
    def _effective_grants(
        *,
        principal_id: UUID,
        group_ids: tuple[UUID, ...],
        resource_ref: ResourceRef,
        at: datetime,
    ) -> tuple[ShareGrant, ...]:
        """一次查询取回目标 ResourceRef 上当前有效的 Direct/Group ShareGrant。"""

        subject_filter = Q(
            grantee_type=ShareGranteeType.PRINCIPAL,
            principal_id=principal_id,
        )
        if group_ids:
            subject_filter |= Q(
                grantee_type=ShareGranteeType.GROUP,
                group_id__in=group_ids,
                group__status=GroupStatus.ACTIVE,
            )

        window = (Q(valid_from__isnull=True) | Q(valid_from__lte=at)) & (
            Q(valid_until__isnull=True) | Q(valid_until__gt=at)
        )
        return tuple(
            ShareGrant.objects.filter(
                tenant_id=resource_ref.tenant_id,
                resource_kind=resource_ref.resource_kind,
                resource_id=resource_ref.resource_id,
                status=ShareGrantStatus.ACTIVE,
                conditions={},
            )
            .filter(subject_filter, window)
            .order_by("id")
        )

    @staticmethod
    def _expand_privileges(grants: tuple[ShareGrant, ...]) -> tuple[ResolvedShareGrant, ...]:
        """批量展开 ShareGrantPrivilege，并过滤已弃用 Privilege。"""

        if not grants:
            return ()

        grant_ids = frozenset(grant.id for grant in grants)
        links_by_grant: dict[UUID, list[ShareGrantPrivilege]] = {}
        links = ShareGrantPrivilege.objects.filter(
            grant_id__in=grant_ids,
            privilege__status=PrivilegeStatus.ACTIVE,
        ).select_related("privilege")
        for link in links:
            links_by_grant.setdefault(link.grant_id, []).append(link)

        resolved: list[ResolvedShareGrant] = []
        for grant in grants:
            source_type = (
                ShareGrantSourceType.DIRECT
                if grant.principal_id is not None
                else ShareGrantSourceType.GROUP
            )
            for link in links_by_grant.get(grant.id, []):
                resolved.append(
                    ResolvedShareGrant(
                        grant_id=grant.id,
                        privilege_id=link.privilege_id,
                        privilege_key=link.privilege.key,
                        source_type=source_type,
                        group_id=grant.group_id,
                    )
                )
        return tuple(resolved)

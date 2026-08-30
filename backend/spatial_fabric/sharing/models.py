"""Phase B2.2 Resource Sharing 领域模型。

本模块只表达“单个 ResourceRef 的显式分享”和“访问请求”。它不替代：

- IAM RoleAssignment 的 Tenant/Workspace/Project/Environment 层级授权；
- Governance Policy 的 DENY / REQUIRE_APPROVAL / 条件策略；
- 最终 AuthorizationService。

跨模块资源继续使用 ``(tenant_id, resource_kind, resource_id)`` 值语义引用，禁止在这里直接
依赖 Asset / Map / Workflow / Model 等具体资源模块，避免 modular-monolith 形成迁移依赖环。
"""

from __future__ import annotations

from datetime import datetime

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

from spatial_fabric.common.models import ConcurrentModel, TimeStampedModel, UUID7Model
from spatial_fabric.iam.models import Group, Principal, Privilege


resource_kind_validator = RegexValidator(
    regex=r"^[a-z][a-z0-9_.:-]{0,159}$",
    message="resource_kind 必须以小写字母开头，且只能包含小写字母、数字、._:-。",
)


class ShareGranteeType(models.TextChoices):
    """ShareGrant 第一版允许的被分享主体形状。"""

    PRINCIPAL = "PRINCIPAL", "主体"
    GROUP = "GROUP", "权限组"


class ShareGrantStatus(models.TextChoices):
    """ShareGrant 生命周期。

    时间到期由 valid_until 推导，不通过后台任务强制把状态改成 EXPIRED；这样授权解析在任何时刻
    都能根据事实时间窗口确定结果，不依赖异步任务是否及时执行。
    """

    ACTIVE = "ACTIVE", "有效"
    REVOKED = "REVOKED", "已撤销"


class AccessRequestStatus(models.TextChoices):
    """资源访问请求生命周期；Approval/JIT 属于 B2.4，不在这里复刻。"""

    PENDING = "PENDING", "待处理"
    FULFILLED = "FULFILLED", "已满足"
    REJECTED = "REJECTED", "已拒绝"
    CANCELLED = "CANCELLED", "已撤回"
    EXPIRED = "EXPIRED", "已过期"


class ShareGrant(UUID7Model, TimeStampedModel, ConcurrentModel):
    """单个 ResourceRef 上向 Principal/Group 显式授予若干 Privilege 的长期记录。

    ShareGrant 只产生 AuthorizationService 的一个 ALLOW 候选。即使该记录有效，未来 Policy
    evaluator 的 explicit DENY / REQUIRE_APPROVAL 仍然可以阻止动作，因此不得把本模型直接当作
    ``can() == True``。

    同一主体允许在同一 ResourceRef 上存在多条独立 ShareGrant。这样每条授权可以拥有不同的
    时间窗口、来源和撤销证据；Resolver 保留全部 evidence，只对最终 privilege key 做集合去重。
    """

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="share_grants",
        verbose_name="资源所属租户",
    )
    resource_kind = models.CharField(
        "资源类型键",
        max_length=160,
        validators=[resource_kind_validator],
        help_text="跨模块稳定 namespaced key；不是具体 Provider/GIS 服务类型。",
    )
    resource_id = models.UUIDField("资源 ID")
    grantee_type = models.CharField(
        "被分享主体类型",
        max_length=16,
        choices=ShareGranteeType.choices,
    )
    principal = models.ForeignKey(
        Principal,
        on_delete=models.PROTECT,
        related_name="direct_share_grants",
        null=True,
        blank=True,
        verbose_name="被分享主体",
    )
    group = models.ForeignKey(
        Group,
        on_delete=models.PROTECT,
        related_name="share_grants",
        null=True,
        blank=True,
        verbose_name="被分享权限组",
    )
    status = models.CharField(
        "状态",
        max_length=16,
        choices=ShareGrantStatus.choices,
        default=ShareGrantStatus.ACTIVE,
    )
    valid_from = models.DateTimeField("生效时间", null=True, blank=True)
    valid_until = models.DateTimeField("失效时间", null=True, blank=True)
    conditions = models.JSONField(
        "附加条件",
        default=dict,
        blank=True,
        help_text=(
            "B2.2 只保留 schema-validated 的低复杂度扩展位；ShareGrantResolver 对非空条件默认 "
            "fail closed，复杂条件由 Policy evaluator 处理。"
        ),
    )
    granted_by = models.ForeignKey(
        Principal,
        on_delete=models.PROTECT,
        related_name="created_share_grants",
        verbose_name="授权主体",
    )
    revoked_by = models.ForeignKey(
        Principal,
        on_delete=models.PROTECT,
        related_name="revoked_share_grants",
        null=True,
        blank=True,
        verbose_name="撤销主体",
    )
    revoked_at = models.DateTimeField("撤销时间", null=True, blank=True)

    class Meta:
        db_table = "sf_share_grant"
        verbose_name = "资源分享授权"
        verbose_name_plural = "资源分享授权"
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(
                        grantee_type=ShareGranteeType.PRINCIPAL,
                        principal__isnull=False,
                        group__isnull=True,
                    )
                    | models.Q(
                        grantee_type=ShareGranteeType.GROUP,
                        principal__isnull=True,
                        group__isnull=False,
                    )
                ),
                name="sf_share_grantee_shape_ck",
            ),
            models.CheckConstraint(
                condition=~models.Q(resource_kind=""),
                name="sf_share_resource_kind_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(valid_until__isnull=True)
                    | models.Q(valid_from__isnull=True)
                    | models.Q(valid_until__gt=models.F("valid_from"))
                ),
                name="sf_share_valid_window_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status=ShareGrantStatus.ACTIVE,
                        revoked_by__isnull=True,
                        revoked_at__isnull=True,
                    )
                    | models.Q(
                        status=ShareGrantStatus.REVOKED,
                        revoked_by__isnull=False,
                        revoked_at__isnull=False,
                    )
                ),
                name="sf_share_revoke_shape_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "resource_kind", "resource_id", "status"],
                name="sf_share_resource_status_idx",
            ),
            models.Index(
                fields=["tenant", "principal", "status"],
                name="sf_share_princ_status_idx",
            ),
            models.Index(
                fields=["tenant", "group", "status"],
                name="sf_share_group_status_idx",
            ),
        ]

    def clean(self) -> None:
        """校验普通 FK/CheckConstraint 无法表达的 Tenant 与状态不变量。"""

        super().clean()
        errors: dict[str, str] = {}
        principal = self.principal if self.principal_id else None
        group = self.group if self.group_id else None
        revoked_by = self.revoked_by if self.revoked_by_id else None

        if principal is not None and principal.tenant_id != self.tenant_id:
            errors["principal"] = "ShareGrant Principal 必须属于资源所在 Tenant。"
        if group is not None and group.tenant_id != self.tenant_id:
            errors["group"] = "ShareGrant Group 必须属于资源所在 Tenant。"
        if self.granted_by_id and self.granted_by.tenant_id not in (None, self.tenant_id):
            errors["granted_by"] = "ShareGrant 授权主体必须属于同一 Tenant 或平台。"
        if revoked_by is not None and revoked_by.tenant_id not in (None, self.tenant_id):
            errors["revoked_by"] = "ShareGrant 撤销主体必须属于同一 Tenant 或平台。"
        if self.valid_from and self.valid_until and self.valid_until <= self.valid_from:
            errors["valid_until"] = "ShareGrant 失效时间必须晚于生效时间。"
        if not isinstance(self.conditions, dict):
            errors["conditions"] = "ShareGrant.conditions 必须是 JSON object。"

        if self.status == ShareGrantStatus.ACTIVE:
            if revoked_by is not None or self.revoked_at is not None:
                errors["status"] = "ACTIVE ShareGrant 不能携带撤销证据。"
        elif self.status == ShareGrantStatus.REVOKED:
            if revoked_by is None or self.revoked_at is None:
                errors["status"] = "REVOKED ShareGrant 必须记录 revoked_by 与 revoked_at。"

        if errors:
            raise ValidationError(errors)

    def is_currently_effective(self, *, at: datetime | None = None) -> bool:
        """判断本授权事实在指定时刻是否可作为候选 Grant。"""

        moment = at or timezone.now()
        if self.status != ShareGrantStatus.ACTIVE:
            return False
        if self.valid_from and moment < self.valid_from:
            return False
        if self.valid_until and moment >= self.valid_until:
            return False
        # B2.2 尚未定义 ShareGrant.conditions evaluator，因此非空条件必须 fail closed。
        return self.conditions == {}

    def __str__(self) -> str:
        return f"{self.resource_kind}:{self.resource_id} → {self.grantee_type}"


class ShareGrantPrivilege(UUID7Model, TimeStampedModel):
    """ShareGrant 与平台 Privilege 词汇的正式关系。

    不使用 JSON 字符串数组，是为了让权限动作拥有 FK 完整性、弃用状态和稳定审计身份。
    """

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="share_grant_privileges",
        verbose_name="所属租户",
    )
    grant = models.ForeignKey(
        ShareGrant,
        on_delete=models.PROTECT,
        related_name="privilege_links",
        verbose_name="资源分享授权",
    )
    privilege = models.ForeignKey(
        Privilege,
        on_delete=models.PROTECT,
        related_name="share_grant_links",
        verbose_name="权限动作",
    )

    class Meta:
        db_table = "sf_share_grant_privilege"
        verbose_name = "资源分享权限"
        verbose_name_plural = "资源分享权限"
        constraints = [
            models.UniqueConstraint(
                fields=["grant", "privilege"],
                name="sf_sharepriv_grant_priv_uniq",
            )
        ]
        indexes = [
            models.Index(fields=["tenant", "grant"], name="sf_sharepriv_grant_idx"),
            models.Index(fields=["privilege"], name="sf_sharepriv_priv_idx"),
        ]

    def clean(self) -> None:
        """显式 tenant_id 必须与父 ShareGrant 一致，便于未来 RLS 与安全索引。"""

        super().clean()
        if self.grant_id and self.grant.tenant_id != self.tenant_id:
            raise ValidationError({"tenant": "ShareGrantPrivilege.tenant 必须与 ShareGrant 一致。"})


class AccessRequest(UUID7Model, TimeStampedModel, ConcurrentModel):
    """Principal 对单个 ResourceRef 提出的访问请求意图。

    AccessRequest 不是 Approval。普通资源所有者可以通过 ShareGrant 满足请求；如果 Policy 判定动作需要
    REQUIRE_APPROVAL，则未来 B2.4 Approval/JIT 流程必须在 fulfillment 之前完成。
    """

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="access_requests",
        verbose_name="资源所属租户",
    )
    requester = models.ForeignKey(
        Principal,
        on_delete=models.PROTECT,
        related_name="access_requests",
        verbose_name="请求主体",
    )
    resource_kind = models.CharField(
        "资源类型键",
        max_length=160,
        validators=[resource_kind_validator],
    )
    resource_id = models.UUIDField("资源 ID")
    justification = models.TextField("申请理由", blank=True)
    status = models.CharField(
        "状态",
        max_length=16,
        choices=AccessRequestStatus.choices,
        default=AccessRequestStatus.PENDING,
    )
    requested_valid_until = models.DateTimeField(
        "期望授权失效时间",
        null=True,
        blank=True,
        help_text="为空表示未指定；真正允许的最长时长仍由 Policy/Quota/治理规则限制。",
    )
    fulfilled_by_grant = models.ForeignKey(
        ShareGrant,
        on_delete=models.PROTECT,
        related_name="fulfilled_access_requests",
        null=True,
        blank=True,
        verbose_name="满足请求的 ShareGrant",
    )
    decided_by = models.ForeignKey(
        Principal,
        on_delete=models.PROTECT,
        related_name="decided_access_requests",
        null=True,
        blank=True,
        verbose_name="处理主体",
    )
    decided_at = models.DateTimeField("处理时间", null=True, blank=True)

    class Meta:
        db_table = "sf_access_request"
        verbose_name = "资源访问请求"
        verbose_name_plural = "资源访问请求"
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(resource_kind=""),
                name="sf_accessreq_resource_kind_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status=AccessRequestStatus.PENDING,
                        fulfilled_by_grant__isnull=True,
                        decided_by__isnull=True,
                        decided_at__isnull=True,
                    )
                    | models.Q(
                        status=AccessRequestStatus.FULFILLED,
                        fulfilled_by_grant__isnull=False,
                        decided_by__isnull=False,
                        decided_at__isnull=False,
                    )
                    | models.Q(
                        status__in=[
                            AccessRequestStatus.REJECTED,
                            AccessRequestStatus.CANCELLED,
                        ],
                        fulfilled_by_grant__isnull=True,
                        decided_by__isnull=False,
                        decided_at__isnull=False,
                    )
                    | models.Q(
                        status=AccessRequestStatus.EXPIRED,
                        fulfilled_by_grant__isnull=True,
                        decided_at__isnull=False,
                    )
                ),
                name="sf_accessreq_status_shape_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "requester", "status"],
                name="sf_accessreq_requester_idx",
            ),
            models.Index(
                fields=["tenant", "resource_kind", "resource_id", "status"],
                name="sf_accessreq_resource_idx",
            ),
        ]

    def clean(self) -> None:
        """校验请求者、决策主体及 fulfillment Grant 的 Tenant/Resource 一致性。"""

        super().clean()
        errors: dict[str, str] = {}
        fulfilled_grant = self.fulfilled_by_grant if self.fulfilled_by_grant_id else None
        decided_by = self.decided_by if self.decided_by_id else None

        if self.requester_id and self.requester.tenant_id != self.tenant_id:
            errors["requester"] = "AccessRequest.requester 必须属于资源所在 Tenant。"
        if decided_by is not None and decided_by.tenant_id not in (None, self.tenant_id):
            errors["decided_by"] = "AccessRequest 处理主体必须属于同一 Tenant 或平台。"

        if fulfilled_grant is not None:
            if fulfilled_grant.tenant_id != self.tenant_id:
                errors["fulfilled_by_grant"] = "fulfillment ShareGrant 必须属于同一 Tenant。"
            elif (
                fulfilled_grant.resource_kind != self.resource_kind
                or fulfilled_grant.resource_id != self.resource_id
            ):
                errors["fulfilled_by_grant"] = "fulfillment ShareGrant 必须指向同一个 ResourceRef。"
            elif (
                fulfilled_grant.grantee_type != ShareGranteeType.PRINCIPAL
                or fulfilled_grant.principal_id != self.requester_id
            ):
                errors["fulfilled_by_grant"] = "AccessRequest 只能由授予 requester 本人的 ShareGrant 满足。"

        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return f"{self.requester_id} → {self.resource_kind}:{self.resource_id}"


class AccessRequestPrivilege(UUID7Model, TimeStampedModel):
    """AccessRequest 聚合内部的 requested Privilege 关系对象。"""

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="access_request_privileges",
        verbose_name="所属租户",
    )
    access_request = models.ForeignKey(
        AccessRequest,
        on_delete=models.PROTECT,
        related_name="privilege_links",
        verbose_name="资源访问请求",
    )
    privilege = models.ForeignKey(
        Privilege,
        on_delete=models.PROTECT,
        related_name="access_request_links",
        verbose_name="请求权限动作",
    )

    class Meta:
        db_table = "sf_access_request_privilege"
        verbose_name = "访问请求权限"
        verbose_name_plural = "访问请求权限"
        constraints = [
            models.UniqueConstraint(
                fields=["access_request", "privilege"],
                name="sf_accessreqpriv_req_priv_uniq",
            )
        ]
        indexes = [
            models.Index(
                fields=["tenant", "access_request"],
                name="sf_accessreqpriv_req_idx",
            ),
            models.Index(fields=["privilege"], name="sf_accessreqpriv_priv_idx"),
        ]

    def clean(self) -> None:
        """显式 tenant_id 与父 AccessRequest 必须一致。"""

        super().clean()
        if self.access_request_id and self.access_request.tenant_id != self.tenant_id:
            raise ValidationError({"tenant": "AccessRequestPrivilege.tenant 必须与 AccessRequest 一致。"})

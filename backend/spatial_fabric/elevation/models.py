"""Phase B2.4 Elevated Access 领域模型。

本模块只表达：

- PermissionBoundary：Principal 在管理 Scope 上的最大权限边界；
- ApprovalRequest / ApprovalDecision：高风险动作与临时提升的审批证据；
- TemporaryAccessGrant：JIT / Break-glass 的短时 candidate grant；
- DelegationGrant：受 delegator 当前 authority 约束的临时委托 candidate grant。

它们都不是最终 AuthorizationDecision，也绝不通过偷偷创建普通 RoleAssignment 来实现。
最终授权仍必须组合 RBAC / Policy / Share / Entitlement / Quota / Boundary / Approval / Risk。
"""

from __future__ import annotations

from datetime import datetime

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

from spatial_fabric.common.models import ConcurrentModel, TimeStampedModel, UUID7Model
from spatial_fabric.iam.models import Principal, Privilege, PrivilegeStatus

resource_kind_validator = RegexValidator(
    regex=r"^[a-z][a-z0-9_.:-]{0,159}$",
    message="resource_kind 必须以小写字母开头，且只能包含小写字母、数字、._:-。",
)


class ElevatedScopeType(models.TextChoices):
    """可承载 Boundary/JIT/Delegation 的 Tenant 管理 Scope。"""

    TENANT = "TENANT", "租户"
    WORKSPACE = "WORKSPACE", "工作空间"
    PROJECT = "PROJECT", "项目"
    ENVIRONMENT = "ENVIRONMENT", "环境"


class ApprovalTargetType(models.TextChoices):
    """Approval 可绑定管理 Scope，也可绑定跨模块 ResourceRef。"""

    TENANT = "TENANT", "租户"
    WORKSPACE = "WORKSPACE", "工作空间"
    PROJECT = "PROJECT", "项目"
    ENVIRONMENT = "ENVIRONMENT", "环境"
    RESOURCE = "RESOURCE", "资源"


class EvidenceStatus(models.TextChoices):
    """可撤销授权证据的最小生命周期。

    时间到期由 ``valid_until`` 推导，不依赖后台任务把状态异步改成 EXPIRED。
    """

    ACTIVE = "ACTIVE", "有效"
    REVOKED = "REVOKED", "已撤销"


class ApprovalPurpose(models.TextChoices):
    HIGH_RISK_ACTION = "HIGH_RISK_ACTION", "高风险动作"
    JIT_ELEVATION = "JIT_ELEVATION", "即时临时提升"
    BREAK_GLASS_REVIEW = "BREAK_GLASS_REVIEW", "紧急访问复核"
    DELEGATION = "DELEGATION", "委托审批"


class ApprovalRequestStatus(models.TextChoices):
    PENDING = "PENDING", "待审批"
    APPROVED = "APPROVED", "已批准"
    REJECTED = "REJECTED", "已拒绝"
    CANCELLED = "CANCELLED", "已撤回"


class ApprovalDecisionValue(models.TextChoices):
    APPROVE = "APPROVE", "批准"
    REJECT = "REJECT", "拒绝"


class TemporaryAccessMode(models.TextChoices):
    JIT = "JIT", "即时临时提升"
    BREAK_GLASS = "BREAK_GLASS", "紧急访问"


def _scope_shape_condition(*, prefix: str = "") -> models.Q:
    """生成显式 Scope shape CHECK；prefix 预留给未来组合字段使用。"""

    workspace = f"{prefix}workspace__isnull"
    project = f"{prefix}project__isnull"
    environment = f"{prefix}environment__isnull"
    scope_type = f"{prefix}scope_type"
    return (
        models.Q(
            **{
                scope_type: ElevatedScopeType.TENANT,
                workspace: True,
                project: True,
                environment: True,
            }
        )
        | models.Q(
            **{
                scope_type: ElevatedScopeType.WORKSPACE,
                workspace: False,
                project: True,
                environment: True,
            }
        )
        | models.Q(
            **{
                scope_type: ElevatedScopeType.PROJECT,
                workspace: True,
                project: False,
                environment: True,
            }
        )
        | models.Q(
            **{
                scope_type: ElevatedScopeType.ENVIRONMENT,
                workspace: True,
                project: True,
                environment: False,
            }
        )
    )


def _validate_scope_tenant(
    *,
    tenant_id: object,
    scope_type: str,
    workspace: object | None,
    project: object | None,
    environment: object | None,
    errors: dict[str, str],
) -> None:
    """校验 Scope 形状与 Tenant 一致性。

    参数保持为 object 是因为本 helper 只被模型 ``clean`` 调用；真正属性访问通过显式
    ``hasattr`` 后进行，避免让领域模型依赖 tenancy 类型导入形成运行时循环。
    """

    if scope_type == ElevatedScopeType.TENANT:
        if workspace is not None or project is not None or environment is not None:
            errors["scope_type"] = "TENANT scope 不能携带子级 Scope。"
        return
    if scope_type == ElevatedScopeType.WORKSPACE:
        if workspace is None or project is not None or environment is not None:
            errors["workspace"] = "WORKSPACE scope 必须且只能指定 Workspace。"
        elif getattr(workspace, "tenant_id", None) != tenant_id:
            errors["workspace"] = "Workspace 必须属于同一 Tenant。"
        return
    if scope_type == ElevatedScopeType.PROJECT:
        if workspace is not None or project is None or environment is not None:
            errors["project"] = "PROJECT scope 必须且只能指定 Project。"
        elif getattr(project, "tenant_id", None) != tenant_id:
            errors["project"] = "Project 必须属于同一 Tenant。"
        return
    if scope_type == ElevatedScopeType.ENVIRONMENT:
        if workspace is not None or project is not None or environment is None:
            errors["environment"] = "ENVIRONMENT scope 必须且只能指定 Environment。"
        elif getattr(environment, "tenant_id", None) != tenant_id:
            errors["environment"] = "Environment 必须属于同一 Tenant。"
        return
    errors["scope_type"] = "未知 scope_type。"


def _validate_actor_tenant(
    *, actor: Principal | None, tenant_id: object, field_name: str, errors: dict[str, str]
) -> None:
    """治理 actor 可以是同 Tenant Principal 或平台 Principal。"""

    if actor is not None and actor.tenant_id not in (None, tenant_id):
        errors[field_name] = "治理操作主体必须属于同一 Tenant 或平台。"


class PermissionBoundary(UUID7Model, TimeStampedModel, ConcurrentModel):
    """Principal 在某 Scope 上的最大 Privilege 集合限制。

    Boundary 只能做集合裁剪；即使 Boundary 中包含某 Privilege，也绝不能因此产生新的 grant。
    多条同时适用 Boundary 的 allowed set 由 resolver 取交集。
    """

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="permission_boundaries",
        verbose_name="所属租户",
    )
    principal = models.ForeignKey(
        Principal,
        on_delete=models.PROTECT,
        related_name="permission_boundaries",
        verbose_name="受限主体",
    )
    scope_type = models.CharField("作用域类型", max_length=16, choices=ElevatedScopeType.choices)
    workspace = models.ForeignKey(
        "tenancy.Workspace",
        on_delete=models.PROTECT,
        related_name="permission_boundaries",
        null=True,
        blank=True,
        verbose_name="工作空间",
    )
    project = models.ForeignKey(
        "tenancy.Project",
        on_delete=models.PROTECT,
        related_name="permission_boundaries",
        null=True,
        blank=True,
        verbose_name="项目",
    )
    environment = models.ForeignKey(
        "tenancy.Environment",
        on_delete=models.PROTECT,
        related_name="permission_boundaries",
        null=True,
        blank=True,
        verbose_name="环境",
    )
    status = models.CharField(
        "状态", max_length=16, choices=EvidenceStatus.choices, default=EvidenceStatus.ACTIVE
    )
    valid_from = models.DateTimeField("生效时间", null=True, blank=True)
    valid_until = models.DateTimeField("失效时间", null=True, blank=True)
    reason = models.TextField("设置原因")
    created_by = models.ForeignKey(
        Principal,
        on_delete=models.PROTECT,
        related_name="created_permission_boundaries",
        verbose_name="创建主体",
    )
    revoked_by = models.ForeignKey(
        Principal,
        on_delete=models.PROTECT,
        related_name="revoked_permission_boundaries",
        null=True,
        blank=True,
        verbose_name="撤销主体",
    )
    revoked_at = models.DateTimeField("撤销时间", null=True, blank=True)

    class Meta:
        db_table = "sf_permission_boundary"
        verbose_name = "权限边界"
        verbose_name_plural = "权限边界"
        constraints = [
            models.CheckConstraint(condition=_scope_shape_condition(), name="sf_pbound_scope_ck"),
            models.CheckConstraint(
                condition=(
                    models.Q(valid_until__isnull=True)
                    | models.Q(valid_from__isnull=True)
                    | models.Q(valid_until__gt=models.F("valid_from"))
                ),
                name="sf_pbound_window_ck",
            ),
            models.CheckConstraint(condition=~models.Q(reason=""), name="sf_pbound_reason_ck"),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status=EvidenceStatus.ACTIVE,
                        revoked_by__isnull=True,
                        revoked_at__isnull=True,
                    )
                    | models.Q(
                        status=EvidenceStatus.REVOKED,
                        revoked_by__isnull=False,
                        revoked_at__isnull=False,
                    )
                ),
                name="sf_pbound_revoke_ck",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "principal", "status"], name="sf_pbound_princ_idx"),
            models.Index(fields=["tenant", "scope_type", "status"], name="sf_pbound_scope_idx"),
        ]

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        principal = self.principal if self.principal_id else None
        workspace = self.workspace if self.workspace_id else None
        project = self.project if self.project_id else None
        environment = self.environment if self.environment_id else None
        creator = self.created_by if self.created_by_id else None
        revoker = self.revoked_by if self.revoked_by_id else None

        if principal is not None and principal.tenant_id != self.tenant_id:
            errors["principal"] = "PermissionBoundary Principal 必须属于同一 Tenant。"
        _validate_scope_tenant(
            tenant_id=self.tenant_id,
            scope_type=self.scope_type,
            workspace=workspace,
            project=project,
            environment=environment,
            errors=errors,
        )
        _validate_actor_tenant(
            actor=creator, tenant_id=self.tenant_id, field_name="created_by", errors=errors
        )
        _validate_actor_tenant(
            actor=revoker, tenant_id=self.tenant_id, field_name="revoked_by", errors=errors
        )
        if not self.reason.strip():
            errors["reason"] = "PermissionBoundary 必须记录设置原因。"
        if self.valid_from and self.valid_until and self.valid_until <= self.valid_from:
            errors["valid_until"] = "PermissionBoundary 失效时间必须晚于生效时间。"
        if errors:
            raise ValidationError(errors)

    def is_currently_effective(self, *, at: datetime | None = None) -> bool:
        moment = at or timezone.now()
        if self.status != EvidenceStatus.ACTIVE:
            return False
        if self.valid_from and moment < self.valid_from:
            return False
        return not (self.valid_until and moment >= self.valid_until)


class PermissionBoundaryPrivilege(UUID7Model, TimeStampedModel):
    """Boundary 允许通过的 Privilege；它不是 grant。"""

    boundary = models.ForeignKey(
        PermissionBoundary,
        on_delete=models.PROTECT,
        related_name="privilege_links",
        verbose_name="权限边界",
    )
    privilege = models.ForeignKey(
        Privilege,
        on_delete=models.PROTECT,
        related_name="permission_boundary_links",
        verbose_name="允许权限",
    )

    class Meta:
        db_table = "sf_permission_boundary_privilege"
        verbose_name = "权限边界动作"
        verbose_name_plural = "权限边界动作"
        constraints = [
            models.UniqueConstraint(
                fields=["boundary", "privilege"], name="sf_pbound_priv_uniq"
            )
        ]
        indexes = [models.Index(fields=["privilege"], name="sf_pbound_priv_idx")]

    def clean(self) -> None:
        super().clean()
        if self.privilege_id and self.privilege.status != PrivilegeStatus.ACTIVE:
            raise ValidationError({"privilege": "PermissionBoundary 只能引用 ACTIVE Privilege。"})


class ApprovalRequest(UUID7Model, TimeStampedModel, ConcurrentModel):
    """高风险动作、JIT、Break-glass 复核或 Delegation 的审批请求。"""

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="approval_requests",
        verbose_name="所属租户",
    )
    purpose = models.CharField("审批目的", max_length=24, choices=ApprovalPurpose.choices)
    requester = models.ForeignKey(
        Principal,
        on_delete=models.PROTECT,
        related_name="requested_approvals",
        verbose_name="申请主体",
    )
    beneficiary = models.ForeignKey(
        Principal,
        on_delete=models.PROTECT,
        related_name="beneficiary_approvals",
        verbose_name="受益主体",
    )
    target_type = models.CharField(
        "目标类型", max_length=16, choices=ApprovalTargetType.choices
    )
    workspace = models.ForeignKey(
        "tenancy.Workspace",
        on_delete=models.PROTECT,
        related_name="approval_requests",
        null=True,
        blank=True,
        verbose_name="工作空间",
    )
    project = models.ForeignKey(
        "tenancy.Project",
        on_delete=models.PROTECT,
        related_name="approval_requests",
        null=True,
        blank=True,
        verbose_name="项目",
    )
    environment = models.ForeignKey(
        "tenancy.Environment",
        on_delete=models.PROTECT,
        related_name="approval_requests",
        null=True,
        blank=True,
        verbose_name="环境",
    )
    resource_kind = models.CharField(
        "资源类型键",
        max_length=160,
        validators=[resource_kind_validator],
        blank=True,
        default="",
    )
    resource_id = models.UUIDField("资源 ID", null=True, blank=True)
    reason = models.TextField("申请原因")
    status = models.CharField(
        "状态",
        max_length=16,
        choices=ApprovalRequestStatus.choices,
        default=ApprovalRequestStatus.PENDING,
    )
    requested_at = models.DateTimeField("申请时间", default=timezone.now)
    expires_at = models.DateTimeField("审批证据失效时间")
    requested_valid_until = models.DateTimeField(
        "期望临时权限失效时间", null=True, blank=True
    )
    cancelled_by = models.ForeignKey(
        Principal,
        on_delete=models.PROTECT,
        related_name="cancelled_approval_requests",
        null=True,
        blank=True,
        verbose_name="撤回主体",
    )
    cancelled_at = models.DateTimeField("撤回时间", null=True, blank=True)

    class Meta:
        db_table = "sf_approval_request"
        verbose_name = "审批请求"
        verbose_name_plural = "审批请求"
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(
                        target_type=ApprovalTargetType.TENANT,
                        workspace__isnull=True,
                        project__isnull=True,
                        environment__isnull=True,
                        resource_kind="",
                        resource_id__isnull=True,
                    )
                    | models.Q(
                        target_type=ApprovalTargetType.WORKSPACE,
                        workspace__isnull=False,
                        project__isnull=True,
                        environment__isnull=True,
                        resource_kind="",
                        resource_id__isnull=True,
                    )
                    | models.Q(
                        target_type=ApprovalTargetType.PROJECT,
                        workspace__isnull=True,
                        project__isnull=False,
                        environment__isnull=True,
                        resource_kind="",
                        resource_id__isnull=True,
                    )
                    | models.Q(
                        target_type=ApprovalTargetType.ENVIRONMENT,
                        workspace__isnull=True,
                        project__isnull=True,
                        environment__isnull=False,
                        resource_kind="",
                        resource_id__isnull=True,
                    )
                    | (
                        models.Q(
                            target_type=ApprovalTargetType.RESOURCE,
                            workspace__isnull=True,
                            project__isnull=True,
                            environment__isnull=True,
                            resource_id__isnull=False,
                        )
                        & ~models.Q(resource_kind="")
                    )
                ),
                name="sf_apreq_target_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(expires_at__gt=models.F("requested_at")),
                name="sf_apreq_expiry_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(requested_valid_until__isnull=True)
                    | models.Q(requested_valid_until__gt=models.F("requested_at"))
                ),
                name="sf_apreq_valid_ck",
            ),
            models.CheckConstraint(condition=~models.Q(reason=""), name="sf_apreq_reason_ck"),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status=ApprovalRequestStatus.CANCELLED,
                        cancelled_by__isnull=False,
                        cancelled_at__isnull=False,
                    )
                    | (
                        ~models.Q(status=ApprovalRequestStatus.CANCELLED)
                        & models.Q(cancelled_by__isnull=True, cancelled_at__isnull=True)
                    )
                ),
                name="sf_apreq_cancel_ck",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "beneficiary", "status"], name="sf_apreq_benef_idx"),
            models.Index(fields=["tenant", "requester", "status"], name="sf_apreq_req_idx"),
            models.Index(fields=["tenant", "purpose", "status"], name="sf_apreq_purp_idx"),
            models.Index(fields=["expires_at"], name="sf_apreq_exp_idx"),
        ]

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        requester = self.requester if self.requester_id else None
        beneficiary = self.beneficiary if self.beneficiary_id else None
        canceller = self.cancelled_by if self.cancelled_by_id else None
        workspace = self.workspace if self.workspace_id else None
        project = self.project if self.project_id else None
        environment = self.environment if self.environment_id else None

        if requester is not None and requester.tenant_id != self.tenant_id:
            errors["requester"] = "Approval requester 必须属于同一 Tenant。"
        if beneficiary is not None and beneficiary.tenant_id != self.tenant_id:
            errors["beneficiary"] = "Approval beneficiary 必须属于同一 Tenant。"
        _validate_actor_tenant(
            actor=canceller, tenant_id=self.tenant_id, field_name="cancelled_by", errors=errors
        )
        self._validate_target(
            workspace=workspace,
            project=project,
            environment=environment,
            errors=errors,
        )
        if not self.reason.strip():
            errors["reason"] = "ApprovalRequest 必须记录申请原因。"
        if self.expires_at and self.requested_at and self.expires_at <= self.requested_at:
            errors["expires_at"] = "ApprovalRequest.expires_at 必须晚于 requested_at。"
        if (
            self.requested_valid_until
            and self.requested_at
            and self.requested_valid_until <= self.requested_at
        ):
            errors["requested_valid_until"] = "requested_valid_until 必须晚于 requested_at。"
        if self.purpose == ApprovalPurpose.JIT_ELEVATION and self.requested_valid_until is None:
            errors["requested_valid_until"] = "JIT_ELEVATION 必须声明临时权限失效时间。"
        if errors:
            raise ValidationError(errors)

    def _validate_target(
        self,
        *,
        workspace: object | None,
        project: object | None,
        environment: object | None,
        errors: dict[str, str],
    ) -> None:
        if self.target_type == ApprovalTargetType.RESOURCE:
            if workspace is not None or project is not None or environment is not None:
                errors["target_type"] = "RESOURCE target 不能携带管理 Scope FK。"
            if not self.resource_kind or self.resource_id is None:
                errors["resource_kind"] = "RESOURCE target 必须完整保存 resource_kind + resource_id。"
            return
        if self.resource_kind or self.resource_id is not None:
            errors["resource_kind"] = "非 RESOURCE target 不能携带 ResourceRef。"
        _validate_scope_tenant(
            tenant_id=self.tenant_id,
            scope_type=self.target_type,
            workspace=workspace,
            project=project,
            environment=environment,
            errors=errors,
        )

    def is_currently_approved(self, *, at: datetime | None = None) -> bool:
        moment = at or timezone.now()
        return self.status == ApprovalRequestStatus.APPROVED and moment < self.expires_at


class ApprovalRequestPrivilege(UUID7Model, TimeStampedModel):
    """ApprovalRequest 请求的稳定 Privilege 集。"""

    approval_request = models.ForeignKey(
        ApprovalRequest,
        on_delete=models.PROTECT,
        related_name="privilege_links",
        verbose_name="审批请求",
    )
    privilege = models.ForeignKey(
        Privilege,
        on_delete=models.PROTECT,
        related_name="approval_request_links",
        verbose_name="请求权限",
    )

    class Meta:
        db_table = "sf_approval_request_privilege"
        verbose_name = "审批请求权限"
        verbose_name_plural = "审批请求权限"
        constraints = [
            models.UniqueConstraint(
                fields=["approval_request", "privilege"], name="sf_apreq_priv_uniq"
            )
        ]
        indexes = [models.Index(fields=["privilege"], name="sf_apreq_priv_idx")]

    def clean(self) -> None:
        super().clean()
        if self.privilege_id and self.privilege.status != PrivilegeStatus.ACTIVE:
            raise ValidationError({"privilege": "ApprovalRequest 只能引用 ACTIVE Privilege。"})


class ApprovalDecision(UUID7Model, TimeStampedModel):
    """ApprovalRequest 的第一版单一 final decision evidence。"""

    approval_request = models.OneToOneField(
        ApprovalRequest,
        on_delete=models.PROTECT,
        related_name="decision",
        verbose_name="审批请求",
    )
    decision = models.CharField("审批结果", max_length=16, choices=ApprovalDecisionValue.choices)
    approver = models.ForeignKey(
        Principal,
        on_delete=models.PROTECT,
        related_name="approval_decisions",
        verbose_name="审批主体",
    )
    comment = models.TextField("审批意见", blank=True)
    decided_at = models.DateTimeField("审批时间", default=timezone.now)

    class Meta:
        db_table = "sf_approval_decision"
        verbose_name = "审批决定"
        verbose_name_plural = "审批决定"
        indexes = [models.Index(fields=["approver", "decided_at"], name="sf_apdec_actor_idx")]

    def clean(self) -> None:
        super().clean()
        if not self.approval_request_id or not self.approver_id:
            return
        request = self.approval_request
        if self.approver.tenant_id not in (None, request.tenant_id):
            raise ValidationError({"approver": "Approver 必须属于同一 Tenant 或平台。"})
        if self.approver_id == request.requester_id:
            raise ValidationError({"approver": "Requester 不能自审批。"})


class TemporaryAccessGrant(UUID7Model, TimeStampedModel, ConcurrentModel):
    """JIT / Break-glass 的短时 candidate grant，不写普通 RoleAssignment。"""

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="temporary_access_grants",
        verbose_name="所属租户",
    )
    beneficiary = models.ForeignKey(
        Principal,
        on_delete=models.PROTECT,
        related_name="temporary_access_grants",
        verbose_name="受益主体",
    )
    mode = models.CharField("提升模式", max_length=16, choices=TemporaryAccessMode.choices)
    scope_type = models.CharField("作用域类型", max_length=16, choices=ElevatedScopeType.choices)
    workspace = models.ForeignKey(
        "tenancy.Workspace",
        on_delete=models.PROTECT,
        related_name="temporary_access_grants",
        null=True,
        blank=True,
        verbose_name="工作空间",
    )
    project = models.ForeignKey(
        "tenancy.Project",
        on_delete=models.PROTECT,
        related_name="temporary_access_grants",
        null=True,
        blank=True,
        verbose_name="项目",
    )
    environment = models.ForeignKey(
        "tenancy.Environment",
        on_delete=models.PROTECT,
        related_name="temporary_access_grants",
        null=True,
        blank=True,
        verbose_name="环境",
    )
    source_approval_request = models.ForeignKey(
        ApprovalRequest,
        on_delete=models.PROTECT,
        related_name="temporary_access_grants",
        null=True,
        blank=True,
        verbose_name="来源审批",
    )
    status = models.CharField(
        "状态", max_length=16, choices=EvidenceStatus.choices, default=EvidenceStatus.ACTIVE
    )
    valid_from = models.DateTimeField("生效时间", default=timezone.now)
    valid_until = models.DateTimeField("失效时间")
    reason = models.TextField("提升原因")
    emergency_reason = models.TextField("紧急访问原因", blank=True, default="")
    notification_required = models.BooleanField("必须触发安全通知", default=False)
    activated_by = models.ForeignKey(
        Principal,
        on_delete=models.PROTECT,
        related_name="activated_temporary_access_grants",
        verbose_name="激活主体",
    )
    revoked_by = models.ForeignKey(
        Principal,
        on_delete=models.PROTECT,
        related_name="revoked_temporary_access_grants",
        null=True,
        blank=True,
        verbose_name="撤销主体",
    )
    revoked_at = models.DateTimeField("撤销时间", null=True, blank=True)

    class Meta:
        db_table = "sf_temporary_access_grant"
        verbose_name = "临时提升授权"
        verbose_name_plural = "临时提升授权"
        constraints = [
            models.CheckConstraint(condition=_scope_shape_condition(), name="sf_tmpgrant_scope_ck"),
            models.CheckConstraint(
                condition=models.Q(valid_until__gt=models.F("valid_from")),
                name="sf_tmpgrant_window_ck",
            ),
            models.CheckConstraint(condition=~models.Q(reason=""), name="sf_tmpgrant_reason_ck"),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        mode=TemporaryAccessMode.JIT,
                        source_approval_request__isnull=False,
                        emergency_reason="",
                    )
                    | (
                        models.Q(
                            mode=TemporaryAccessMode.BREAK_GLASS,
                            notification_required=True,
                        )
                        & ~models.Q(emergency_reason="")
                    )
                ),
                name="sf_tmpgrant_mode_ck",
            ),
            models.UniqueConstraint(
                fields=["source_approval_request"],
                condition=models.Q(source_approval_request__isnull=False),
                name="sf_tmpgrant_apreq_uniq",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status=EvidenceStatus.ACTIVE,
                        revoked_by__isnull=True,
                        revoked_at__isnull=True,
                    )
                    | models.Q(
                        status=EvidenceStatus.REVOKED,
                        revoked_by__isnull=False,
                        revoked_at__isnull=False,
                    )
                ),
                name="sf_tmpgrant_revoke_ck",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "beneficiary", "status"], name="sf_tmpgrant_ben_idx"),
            models.Index(fields=["valid_until"], name="sf_tmpgrant_exp_idx"),
            models.Index(fields=["tenant", "mode", "status"], name="sf_tmpgrant_mode_idx"),
        ]

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        beneficiary = self.beneficiary if self.beneficiary_id else None
        workspace = self.workspace if self.workspace_id else None
        project = self.project if self.project_id else None
        environment = self.environment if self.environment_id else None
        activator = self.activated_by if self.activated_by_id else None
        revoker = self.revoked_by if self.revoked_by_id else None
        approval = self.source_approval_request if self.source_approval_request_id else None

        if beneficiary is not None and beneficiary.tenant_id != self.tenant_id:
            errors["beneficiary"] = "TemporaryAccess beneficiary 必须属于同一 Tenant。"
        _validate_scope_tenant(
            tenant_id=self.tenant_id,
            scope_type=self.scope_type,
            workspace=workspace,
            project=project,
            environment=environment,
            errors=errors,
        )
        _validate_actor_tenant(
            actor=activator, tenant_id=self.tenant_id, field_name="activated_by", errors=errors
        )
        _validate_actor_tenant(
            actor=revoker, tenant_id=self.tenant_id, field_name="revoked_by", errors=errors
        )
        if approval is not None and approval.tenant_id != self.tenant_id:
            errors["source_approval_request"] = "来源 ApprovalRequest 必须属于同一 Tenant。"
        if self.mode == TemporaryAccessMode.JIT:
            if approval is None:
                errors["source_approval_request"] = "JIT 必须来源于已批准 ApprovalRequest。"
            elif approval.purpose != ApprovalPurpose.JIT_ELEVATION:
                errors["source_approval_request"] = "JIT 来源 ApprovalRequest purpose 必须为 JIT_ELEVATION。"
            if self.emergency_reason:
                errors["emergency_reason"] = "JIT 不应携带 emergency_reason。"
        elif self.mode == TemporaryAccessMode.BREAK_GLASS:
            if not self.emergency_reason.strip():
                errors["emergency_reason"] = "Break-glass 必须记录强理由。"
            if not self.notification_required:
                errors["notification_required"] = "Break-glass 必须触发安全通知要求。"
        else:
            errors["mode"] = "未知 TemporaryAccess mode。"
        if not self.reason.strip():
            errors["reason"] = "TemporaryAccessGrant 必须记录原因。"
        if self.valid_from and self.valid_until and self.valid_until <= self.valid_from:
            errors["valid_until"] = "TemporaryAccessGrant 失效时间必须晚于生效时间。"
        if errors:
            raise ValidationError(errors)

    def is_currently_effective(self, *, at: datetime | None = None) -> bool:
        moment = at or timezone.now()
        return (
            self.status == EvidenceStatus.ACTIVE
            and moment >= self.valid_from
            and moment < self.valid_until
        )


class TemporaryAccessGrantPrivilege(UUID7Model, TimeStampedModel):
    """JIT / Break-glass grant 的 Privilege evidence。"""

    grant = models.ForeignKey(
        TemporaryAccessGrant,
        on_delete=models.PROTECT,
        related_name="privilege_links",
        verbose_name="临时提升授权",
    )
    privilege = models.ForeignKey(
        Privilege,
        on_delete=models.PROTECT,
        related_name="temporary_access_links",
        verbose_name="临时权限",
    )

    class Meta:
        db_table = "sf_temporary_access_privilege"
        verbose_name = "临时提升权限"
        verbose_name_plural = "临时提升权限"
        constraints = [
            models.UniqueConstraint(fields=["grant", "privilege"], name="sf_tmpgrant_priv_uniq")
        ]
        indexes = [models.Index(fields=["privilege"], name="sf_tmpgrant_priv_idx")]

    def clean(self) -> None:
        super().clean()
        if self.privilege_id and self.privilege.status != PrivilegeStatus.ACTIVE:
            raise ValidationError({"privilege": "TemporaryAccessGrant 只能引用 ACTIVE Privilege。"})


class DelegationGrant(UUID7Model, TimeStampedModel, ConcurrentModel):
    """Delegator 向 Delegatee 临时委托部分当前有效权限的 candidate evidence。"""

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="delegation_grants",
        verbose_name="所属租户",
    )
    delegator = models.ForeignKey(
        Principal,
        on_delete=models.PROTECT,
        related_name="outgoing_delegations",
        verbose_name="委托主体",
    )
    delegatee = models.ForeignKey(
        Principal,
        on_delete=models.PROTECT,
        related_name="incoming_delegations",
        verbose_name="受托主体",
    )
    scope_type = models.CharField("作用域类型", max_length=16, choices=ElevatedScopeType.choices)
    workspace = models.ForeignKey(
        "tenancy.Workspace",
        on_delete=models.PROTECT,
        related_name="delegation_grants",
        null=True,
        blank=True,
        verbose_name="工作空间",
    )
    project = models.ForeignKey(
        "tenancy.Project",
        on_delete=models.PROTECT,
        related_name="delegation_grants",
        null=True,
        blank=True,
        verbose_name="项目",
    )
    environment = models.ForeignKey(
        "tenancy.Environment",
        on_delete=models.PROTECT,
        related_name="delegation_grants",
        null=True,
        blank=True,
        verbose_name="环境",
    )
    status = models.CharField(
        "状态", max_length=16, choices=EvidenceStatus.choices, default=EvidenceStatus.ACTIVE
    )
    valid_from = models.DateTimeField("生效时间", default=timezone.now)
    valid_until = models.DateTimeField("失效时间")
    reason = models.TextField("委托原因")
    authority_snapshot = models.JSONField(
        "创建时 authority evidence",
        default=dict,
        blank=True,
        help_text="仅用于解释创建时校验，不成为永久权限来源；resolver 必须重新验证 delegator authority。",
    )
    authority_checked_at = models.DateTimeField("创建时 authority 校验时间")
    created_by = models.ForeignKey(
        Principal,
        on_delete=models.PROTECT,
        related_name="created_delegation_grants",
        verbose_name="创建主体",
    )
    revoked_by = models.ForeignKey(
        Principal,
        on_delete=models.PROTECT,
        related_name="revoked_delegation_grants",
        null=True,
        blank=True,
        verbose_name="撤销主体",
    )
    revoked_at = models.DateTimeField("撤销时间", null=True, blank=True)

    class Meta:
        db_table = "sf_delegation_grant"
        verbose_name = "权限委托"
        verbose_name_plural = "权限委托"
        constraints = [
            models.CheckConstraint(condition=_scope_shape_condition(), name="sf_deleg_scope_ck"),
            models.CheckConstraint(
                condition=models.Q(valid_until__gt=models.F("valid_from")),
                name="sf_deleg_window_ck",
            ),
            models.CheckConstraint(condition=~models.Q(reason=""), name="sf_deleg_reason_ck"),
            models.CheckConstraint(
                condition=~models.Q(delegator=models.F("delegatee")),
                name="sf_deleg_distinct_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status=EvidenceStatus.ACTIVE,
                        revoked_by__isnull=True,
                        revoked_at__isnull=True,
                    )
                    | models.Q(
                        status=EvidenceStatus.REVOKED,
                        revoked_by__isnull=False,
                        revoked_at__isnull=False,
                    )
                ),
                name="sf_deleg_revoke_ck",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "delegator", "status"], name="sf_deleg_from_idx"),
            models.Index(fields=["tenant", "delegatee", "status"], name="sf_deleg_to_idx"),
            models.Index(fields=["valid_until"], name="sf_deleg_exp_idx"),
        ]

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        delegator = self.delegator if self.delegator_id else None
        delegatee = self.delegatee if self.delegatee_id else None
        workspace = self.workspace if self.workspace_id else None
        project = self.project if self.project_id else None
        environment = self.environment if self.environment_id else None
        creator = self.created_by if self.created_by_id else None
        revoker = self.revoked_by if self.revoked_by_id else None

        if delegator is not None and delegator.tenant_id != self.tenant_id:
            errors["delegator"] = "Delegator 必须属于同一 Tenant。"
        if delegatee is not None and delegatee.tenant_id != self.tenant_id:
            errors["delegatee"] = "Delegatee 必须属于同一 Tenant。"
        if self.delegator_id and self.delegator_id == self.delegatee_id:
            errors["delegatee"] = "Delegator 与 Delegatee 必须不同。"
        _validate_scope_tenant(
            tenant_id=self.tenant_id,
            scope_type=self.scope_type,
            workspace=workspace,
            project=project,
            environment=environment,
            errors=errors,
        )
        _validate_actor_tenant(
            actor=creator, tenant_id=self.tenant_id, field_name="created_by", errors=errors
        )
        _validate_actor_tenant(
            actor=revoker, tenant_id=self.tenant_id, field_name="revoked_by", errors=errors
        )
        if not self.reason.strip():
            errors["reason"] = "DelegationGrant 必须记录委托原因。"
        if self.valid_until and self.valid_from and self.valid_until <= self.valid_from:
            errors["valid_until"] = "DelegationGrant 失效时间必须晚于生效时间。"
        if not isinstance(self.authority_snapshot, dict):
            errors["authority_snapshot"] = "authority_snapshot 必须是 JSON object。"
        if errors:
            raise ValidationError(errors)

    def is_currently_effective(self, *, at: datetime | None = None) -> bool:
        moment = at or timezone.now()
        return (
            self.status == EvidenceStatus.ACTIVE
            and moment >= self.valid_from
            and moment < self.valid_until
        )


class DelegationGrantPrivilege(UUID7Model, TimeStampedModel):
    """DelegationGrant 请求并经 authority checker 验证过的 Privilege evidence。"""

    delegation = models.ForeignKey(
        DelegationGrant,
        on_delete=models.PROTECT,
        related_name="privilege_links",
        verbose_name="委托授权",
    )
    privilege = models.ForeignKey(
        Privilege,
        on_delete=models.PROTECT,
        related_name="delegation_links",
        verbose_name="委托权限",
    )

    class Meta:
        db_table = "sf_delegation_grant_privilege"
        verbose_name = "委托权限"
        verbose_name_plural = "委托权限"
        constraints = [
            models.UniqueConstraint(
                fields=["delegation", "privilege"], name="sf_deleg_priv_uniq"
            )
        ]
        indexes = [models.Index(fields=["privilege"], name="sf_deleg_priv_idx")]

    def clean(self) -> None:
        super().clean()
        if self.privilege_id and self.privilege.status != PrivilegeStatus.ACTIVE:
            raise ValidationError({"privilege": "DelegationGrant 只能引用 ACTIVE Privilege。"})

"""Phase B2.3 Commercial Controls 领域模型。

本模块严格区分：

    EntitlementGrant ≠ Quota ≠ Budget ≠ UsageRecord

- EntitlementGrant 表达产品/能力的商业资格；
- Quota 表达技术 metric 的数量上限；
- Budget 表达货币/成本治理边界；
- UsageReservation/UsageCounter 负责并发安全的 HARD quota 决策；
- UsageRecord 保存已经发生的使用事实。

商业控制仍只是最终 AuthorizationService 的输入，不得把“有许可/有额度”直接解释为某个资源动作
已经被授权。Role/Policy/ShareGrant 的权限语义继续由各自 bounded context 负责。
"""

from __future__ import annotations

from datetime import datetime

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

from spatial_fabric.common.models import ConcurrentModel, TimeStampedModel, UUID7Model
from spatial_fabric.iam.models import Principal

commercial_key_validator = RegexValidator(
    regex=r"^[a-z][a-z0-9_.:-]{0,159}$",
    message="商业控制 key 必须以小写字母开头，且只能包含小写字母、数字、._:-。",
)
unit_key_validator = RegexValidator(
    regex=r"^[a-z][a-z0-9_.:-]{0,31}$",
    message="unit 必须以小写字母开头，且只能包含小写字母、数字、._:-。",
)
currency_code_validator = RegexValidator(
    regex=r"^[A-Z]{3}$",
    message="currency_code 必须是三个大写 ASCII 字母，例如 CNY、USD。",
)
idempotency_key_validator = RegexValidator(
    regex=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}$",
    message="idempotency_key 只能包含字母、数字、._:/-，且必须以字母或数字开头。",
)


class CommercialSubjectType(models.TextChoices):
    """B2.3 第一版商业控制主体；Group 语义不在这里复制 IAM。"""

    TENANT = "TENANT", "租户"
    PRINCIPAL = "PRINCIPAL", "主体"


class CommercialScopeType(models.TextChoices):
    """商业控制沿用 Fabric 管理层级，不创建平行 scope 树。"""

    TENANT = "TENANT", "租户"
    WORKSPACE = "WORKSPACE", "工作空间"
    PROJECT = "PROJECT", "项目"
    ENVIRONMENT = "ENVIRONMENT", "环境"


class CommercialGrantStatus(models.TextChoices):
    """授权/配置记录的显式生命周期；时间到期继续由 valid window 推导。"""

    ACTIVE = "ACTIVE", "有效"
    REVOKED = "REVOKED", "已撤销"


class EnforcementMode(models.TextChoices):
    """Quota/Budget 的执行强度。"""

    OBSERVE = "OBSERVE", "仅观测"
    SOFT = "SOFT", "软限制"
    HARD = "HARD", "硬限制"


class QuotaMeasurementType(models.TextChoices):
    """技术 usage 的计量语义。"""

    GAUGE = "GAUGE", "当前占用"
    CONCURRENCY = "CONCURRENCY", "并发占用"
    CONSUMPTION = "CONSUMPTION", "累计消费"


class QuotaWindowType(models.TextChoices):
    """第一版只实现能由单 counter 精确表达的窗口。"""

    NONE = "NONE", "无时间窗口"
    CALENDAR_DAY = "CALENDAR_DAY", "自然日"
    CALENDAR_MONTH = "CALENDAR_MONTH", "自然月"


class BudgetWindowType(models.TextChoices):
    """预算周期与技术 quota window 分开定义。"""

    CALENDAR_MONTH = "CALENDAR_MONTH", "自然月"
    CALENDAR_YEAR = "CALENDAR_YEAR", "自然年"
    FIXED_TERM = "FIXED_TERM", "固定合同期"


class UsageReservationStatus(models.TextChoices):
    """一次逻辑 usage reservation 的生命周期。"""

    RESERVED = "RESERVED", "已预留"
    COMMITTED = "COMMITTED", "已提交"
    RELEASED = "RELEASED", "已释放"
    EXPIRED = "EXPIRED", "已过期"


class UsageEventType(models.TextChoices):
    """第一版 UsageRecord 事件；调整/冲正以后单独定义，不删除历史。"""

    CONSUME = "CONSUME", "消费"
    RELEASE = "RELEASE", "释放"


class EntitlementGrant(UUID7Model, TimeStampedModel, ConcurrentModel):
    """Tenant/Principal 在某管理 Scope 下拥有某产品/能力资格的独立证据。

    同一 entitlement 可以存在多条独立 evidence；不使用“最多一条 ACTIVE”的 partial unique，
    避免 valid_until 已过期但状态尚为 ACTIVE 时阻塞重新授权的时态死锁。
    """

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="entitlement_grants",
        verbose_name="所属租户",
    )
    entitlement_key = models.CharField(
        "许可键", max_length=160, validators=[commercial_key_validator]
    )
    subject_type = models.CharField(
        "主体类型", max_length=16, choices=CommercialSubjectType.choices
    )
    principal = models.ForeignKey(
        Principal,
        on_delete=models.PROTECT,
        related_name="entitlement_grants",
        null=True,
        blank=True,
        verbose_name="主体",
    )
    scope_type = models.CharField("作用域类型", max_length=16, choices=CommercialScopeType.choices)
    workspace = models.ForeignKey(
        "tenancy.Workspace",
        on_delete=models.PROTECT,
        related_name="entitlement_grants",
        null=True,
        blank=True,
        verbose_name="工作空间",
    )
    project = models.ForeignKey(
        "tenancy.Project",
        on_delete=models.PROTECT,
        related_name="entitlement_grants",
        null=True,
        blank=True,
        verbose_name="项目",
    )
    environment = models.ForeignKey(
        "tenancy.Environment",
        on_delete=models.PROTECT,
        related_name="entitlement_grants",
        null=True,
        blank=True,
        verbose_name="环境",
    )
    status = models.CharField(
        "状态",
        max_length=16,
        choices=CommercialGrantStatus.choices,
        default=CommercialGrantStatus.ACTIVE,
    )
    valid_from = models.DateTimeField("生效时间", null=True, blank=True)
    valid_until = models.DateTimeField("失效时间", null=True, blank=True)
    granted_by = models.ForeignKey(
        Principal,
        on_delete=models.PROTECT,
        related_name="created_entitlement_grants",
        verbose_name="授权主体",
    )
    revoked_by = models.ForeignKey(
        Principal,
        on_delete=models.PROTECT,
        related_name="revoked_entitlement_grants",
        null=True,
        blank=True,
        verbose_name="撤销主体",
    )
    revoked_at = models.DateTimeField("撤销时间", null=True, blank=True)

    class Meta:
        db_table = "sf_entitlement_grant"
        verbose_name = "商业许可授权"
        verbose_name_plural = "商业许可授权"
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(subject_type=CommercialSubjectType.TENANT, principal__isnull=True)
                    | models.Q(
                        subject_type=CommercialSubjectType.PRINCIPAL,
                        principal__isnull=False,
                    )
                ),
                name="sf_ent_subject_shape_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        scope_type=CommercialScopeType.TENANT,
                        workspace__isnull=True,
                        project__isnull=True,
                        environment__isnull=True,
                    )
                    | models.Q(
                        scope_type=CommercialScopeType.WORKSPACE,
                        workspace__isnull=False,
                        project__isnull=True,
                        environment__isnull=True,
                    )
                    | models.Q(
                        scope_type=CommercialScopeType.PROJECT,
                        workspace__isnull=True,
                        project__isnull=False,
                        environment__isnull=True,
                    )
                    | models.Q(
                        scope_type=CommercialScopeType.ENVIRONMENT,
                        workspace__isnull=True,
                        project__isnull=True,
                        environment__isnull=False,
                    )
                ),
                name="sf_ent_scope_shape_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(valid_until__isnull=True)
                    | models.Q(valid_from__isnull=True)
                    | models.Q(valid_until__gt=models.F("valid_from"))
                ),
                name="sf_ent_valid_window_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status=CommercialGrantStatus.ACTIVE,
                        revoked_by__isnull=True,
                        revoked_at__isnull=True,
                    )
                    | models.Q(
                        status=CommercialGrantStatus.REVOKED,
                        revoked_by__isnull=False,
                        revoked_at__isnull=False,
                    )
                ),
                name="sf_ent_revoke_shape_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "entitlement_key", "status"], name="sf_ent_key_status_idx"
            ),
            models.Index(
                fields=["tenant", "principal", "status"], name="sf_ent_princ_status_idx"
            ),
        ]

    def clean(self) -> None:
        """校验 subject、scope、actor 与 Tenant 的跨表一致性。"""

        super().clean()
        errors: dict[str, str] = {}
        principal = self.principal if self.principal_id else None
        workspace = self.workspace if self.workspace_id else None
        project = self.project if self.project_id else None
        environment = self.environment if self.environment_id else None
        revoked_by = self.revoked_by if self.revoked_by_id else None

        if self.subject_type == CommercialSubjectType.TENANT:
            if principal is not None:
                errors["principal"] = "TENANT Entitlement 不能指定 Principal。"
        elif self.subject_type == CommercialSubjectType.PRINCIPAL:
            if principal is None:
                errors["principal"] = "PRINCIPAL Entitlement 必须指定 Principal。"
            elif principal.tenant_id != self.tenant_id:
                errors["principal"] = "Entitlement Principal 必须属于同一 Tenant。"
        else:
            errors["subject_type"] = "未知 Entitlement subject_type。"

        self._validate_scope(errors, workspace=workspace, project=project, environment=environment)
        if self.granted_by_id and self.granted_by.tenant_id not in (None, self.tenant_id):
            errors["granted_by"] = "授权主体必须属于同一 Tenant 或平台。"
        if revoked_by is not None and revoked_by.tenant_id not in (None, self.tenant_id):
            errors["revoked_by"] = "撤销主体必须属于同一 Tenant 或平台。"
        if self.valid_from and self.valid_until and self.valid_until <= self.valid_from:
            errors["valid_until"] = "失效时间必须晚于生效时间。"

        if errors:
            raise ValidationError(errors)

    def _validate_scope(
        self,
        errors: dict[str, str],
        *,
        workspace: object | None,
        project: object | None,
        environment: object | None,
    ) -> None:
        """Scope shape 由数据库兜底；这里补充跨表 tenant 比较与可读错误。"""

        if self.scope_type == CommercialScopeType.TENANT:
            if self.workspace_id or self.project_id or self.environment_id:
                errors["scope_type"] = "TENANT scope 不能携带子级 Scope。"
        elif self.scope_type == CommercialScopeType.WORKSPACE:
            if workspace is None or self.project_id or self.environment_id:
                errors["workspace"] = "WORKSPACE scope 必须且只能指定 Workspace。"
            elif self.workspace.tenant_id != self.tenant_id:
                errors["workspace"] = "Workspace 必须属于同一 Tenant。"
        elif self.scope_type == CommercialScopeType.PROJECT:
            if self.workspace_id or project is None or self.environment_id:
                errors["project"] = "PROJECT scope 必须且只能指定 Project。"
            elif self.project.tenant_id != self.tenant_id:
                errors["project"] = "Project 必须属于同一 Tenant。"
        elif self.scope_type == CommercialScopeType.ENVIRONMENT:
            if self.workspace_id or self.project_id or environment is None:
                errors["environment"] = "ENVIRONMENT scope 必须且只能指定 Environment。"
            elif self.environment.tenant_id != self.tenant_id:
                errors["environment"] = "Environment 必须属于同一 Tenant。"
        else:
            errors["scope_type"] = "未知商业作用域类型。"

    def is_currently_effective(self, *, at: datetime | None = None) -> bool:
        """判断许可 evidence 在指定时刻是否有效。"""

        moment = at or timezone.now()
        if self.status != CommercialGrantStatus.ACTIVE:
            return False
        if self.valid_from and moment < self.valid_from:
            return False
        return not (self.valid_until and moment >= self.valid_until)


class Quota(UUID7Model, TimeStampedModel, ConcurrentModel):
    """某 subject/scope 上针对稳定 metric 的技术用量上限配置。

    Quota 不保存当前 Prometheus 指标值；HARD enforcement 使用 UsageCounter + Reservation 事务协议。
    同一 metric 可以同时存在 Tenant/Project 等多层 quota，所有 applicable HARD quota 都必须通过。
    """

    tenant = models.ForeignKey(
        "tenancy.Tenant", on_delete=models.PROTECT, related_name="quotas", verbose_name="所属租户"
    )
    metric_key = models.CharField("指标键", max_length=160, validators=[commercial_key_validator])
    unit = models.CharField("单位", max_length=32, validators=[unit_key_validator])
    subject_type = models.CharField(
        "主体类型", max_length=16, choices=CommercialSubjectType.choices
    )
    principal = models.ForeignKey(
        Principal,
        on_delete=models.PROTECT,
        related_name="quotas",
        null=True,
        blank=True,
        verbose_name="主体",
    )
    scope_type = models.CharField("作用域类型", max_length=16, choices=CommercialScopeType.choices)
    workspace = models.ForeignKey(
        "tenancy.Workspace",
        on_delete=models.PROTECT,
        related_name="quotas",
        null=True,
        blank=True,
        verbose_name="工作空间",
    )
    project = models.ForeignKey(
        "tenancy.Project",
        on_delete=models.PROTECT,
        related_name="quotas",
        null=True,
        blank=True,
        verbose_name="项目",
    )
    environment = models.ForeignKey(
        "tenancy.Environment",
        on_delete=models.PROTECT,
        related_name="quotas",
        null=True,
        blank=True,
        verbose_name="环境",
    )
    measurement_type = models.CharField(
        "计量类型", max_length=16, choices=QuotaMeasurementType.choices
    )
    limit_value = models.PositiveBigIntegerField("限制值")
    window_type = models.CharField("窗口类型", max_length=24, choices=QuotaWindowType.choices)
    enforcement_mode = models.CharField(
        "执行模式", max_length=16, choices=EnforcementMode.choices, default=EnforcementMode.HARD
    )
    status = models.CharField(
        "状态",
        max_length=16,
        choices=CommercialGrantStatus.choices,
        default=CommercialGrantStatus.ACTIVE,
    )
    valid_from = models.DateTimeField("生效时间", null=True, blank=True)
    valid_until = models.DateTimeField("失效时间", null=True, blank=True)
    created_by = models.ForeignKey(
        Principal,
        on_delete=models.PROTECT,
        related_name="created_quotas",
        verbose_name="创建主体",
    )
    revoked_by = models.ForeignKey(
        Principal,
        on_delete=models.PROTECT,
        related_name="revoked_quotas",
        null=True,
        blank=True,
        verbose_name="撤销主体",
    )
    revoked_at = models.DateTimeField("撤销时间", null=True, blank=True)

    class Meta:
        db_table = "sf_quota"
        verbose_name = "技术配额"
        verbose_name_plural = "技术配额"
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(subject_type=CommercialSubjectType.TENANT, principal__isnull=True)
                    | models.Q(
                        subject_type=CommercialSubjectType.PRINCIPAL,
                        principal__isnull=False,
                    )
                ),
                name="sf_quota_subject_shape_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        scope_type=CommercialScopeType.TENANT,
                        workspace__isnull=True,
                        project__isnull=True,
                        environment__isnull=True,
                    )
                    | models.Q(
                        scope_type=CommercialScopeType.WORKSPACE,
                        workspace__isnull=False,
                        project__isnull=True,
                        environment__isnull=True,
                    )
                    | models.Q(
                        scope_type=CommercialScopeType.PROJECT,
                        workspace__isnull=True,
                        project__isnull=False,
                        environment__isnull=True,
                    )
                    | models.Q(
                        scope_type=CommercialScopeType.ENVIRONMENT,
                        workspace__isnull=True,
                        project__isnull=True,
                        environment__isnull=False,
                    )
                ),
                name="sf_quota_scope_shape_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        measurement_type__in=[
                            QuotaMeasurementType.GAUGE,
                            QuotaMeasurementType.CONCURRENCY,
                        ],
                        window_type=QuotaWindowType.NONE,
                    )
                    | models.Q(
                        measurement_type=QuotaMeasurementType.CONSUMPTION,
                        window_type__in=[
                            QuotaWindowType.CALENDAR_DAY,
                            QuotaWindowType.CALENDAR_MONTH,
                        ],
                    )
                ),
                name="sf_quota_measure_window_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(valid_until__isnull=True)
                    | models.Q(valid_from__isnull=True)
                    | models.Q(valid_until__gt=models.F("valid_from"))
                ),
                name="sf_quota_valid_window_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status=CommercialGrantStatus.ACTIVE,
                        revoked_by__isnull=True,
                        revoked_at__isnull=True,
                    )
                    | models.Q(
                        status=CommercialGrantStatus.REVOKED,
                        revoked_by__isnull=False,
                        revoked_at__isnull=False,
                    )
                ),
                name="sf_quota_revoke_shape_ck",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "metric_key", "status"], name="sf_quota_metric_idx"),
            models.Index(
                fields=["tenant", "principal", "status"], name="sf_quota_princ_idx"
            ),
            models.Index(fields=["project", "metric_key", "status"], name="sf_quota_proj_idx"),
        ]

    def clean(self) -> None:
        """校验 Quota 的 Tenant、主体、Scope 与计量语义。"""

        super().clean()
        errors: dict[str, str] = {}
        principal = self.principal if self.principal_id else None
        workspace = self.workspace if self.workspace_id else None
        project = self.project if self.project_id else None
        environment = self.environment if self.environment_id else None
        revoked_by = self.revoked_by if self.revoked_by_id else None

        if self.subject_type == CommercialSubjectType.TENANT:
            if principal is not None:
                errors["principal"] = "TENANT Quota 不能指定 Principal。"
        elif self.subject_type == CommercialSubjectType.PRINCIPAL:
            if principal is None:
                errors["principal"] = "PRINCIPAL Quota 必须指定 Principal。"
            elif principal.tenant_id != self.tenant_id:
                errors["principal"] = "Quota Principal 必须属于同一 Tenant。"
        else:
            errors["subject_type"] = "未知 Quota subject_type。"

        self._validate_scope(errors, workspace=workspace, project=project, environment=environment)
        if self.measurement_type in {
            QuotaMeasurementType.GAUGE,
            QuotaMeasurementType.CONCURRENCY,
        }:
            if self.window_type != QuotaWindowType.NONE:
                errors["window_type"] = "GAUGE/CONCURRENCY Quota 必须使用 NONE window。"
        elif self.measurement_type == QuotaMeasurementType.CONSUMPTION:
            if self.window_type not in {
                QuotaWindowType.CALENDAR_DAY,
                QuotaWindowType.CALENDAR_MONTH,
            }:
                errors["window_type"] = "CONSUMPTION Quota 必须使用自然日或自然月窗口。"
        else:
            errors["measurement_type"] = "未知 Quota measurement_type。"

        if self.created_by_id and self.created_by.tenant_id not in (None, self.tenant_id):
            errors["created_by"] = "Quota 创建主体必须属于同一 Tenant 或平台。"
        if revoked_by is not None and revoked_by.tenant_id not in (None, self.tenant_id):
            errors["revoked_by"] = "Quota 撤销主体必须属于同一 Tenant 或平台。"
        if self.valid_from and self.valid_until and self.valid_until <= self.valid_from:
            errors["valid_until"] = "Quota 失效时间必须晚于生效时间。"

        if errors:
            raise ValidationError(errors)

    def _validate_scope(
        self,
        errors: dict[str, str],
        *,
        workspace: object | None,
        project: object | None,
        environment: object | None,
    ) -> None:
        if self.scope_type == CommercialScopeType.TENANT:
            if self.workspace_id or self.project_id or self.environment_id:
                errors["scope_type"] = "TENANT Quota 不能携带子级 Scope。"
        elif self.scope_type == CommercialScopeType.WORKSPACE:
            if workspace is None or self.project_id or self.environment_id:
                errors["workspace"] = "WORKSPACE Quota 必须且只能指定 Workspace。"
            elif self.workspace.tenant_id != self.tenant_id:
                errors["workspace"] = "Quota Workspace 必须属于同一 Tenant。"
        elif self.scope_type == CommercialScopeType.PROJECT:
            if self.workspace_id or project is None or self.environment_id:
                errors["project"] = "PROJECT Quota 必须且只能指定 Project。"
            elif self.project.tenant_id != self.tenant_id:
                errors["project"] = "Quota Project 必须属于同一 Tenant。"
        elif self.scope_type == CommercialScopeType.ENVIRONMENT:
            if self.workspace_id or self.project_id or environment is None:
                errors["environment"] = "ENVIRONMENT Quota 必须且只能指定 Environment。"
            elif self.environment.tenant_id != self.tenant_id:
                errors["environment"] = "Quota Environment 必须属于同一 Tenant。"
        else:
            errors["scope_type"] = "未知 Quota scope_type。"

    def is_currently_effective(self, *, at: datetime | None = None) -> bool:
        moment = at or timezone.now()
        if self.status != CommercialGrantStatus.ACTIVE:
            return False
        if self.valid_from and moment < self.valid_from:
            return False
        return not (self.valid_until and moment >= self.valid_until)


class Budget(UUID7Model, TimeStampedModel, ConcurrentModel):
    """管理 Scope 上的货币/成本预算策略。

    Budget 不与 Quota 合并，也不在没有 normalized cost ledger 时伪造 HARD 成本扣减。第一版先保存
    稳定 budget policy/evidence，后续成本归因层建立后再接入真正 spend reservation。
    """

    tenant = models.ForeignKey(
        "tenancy.Tenant", on_delete=models.PROTECT, related_name="budgets", verbose_name="所属租户"
    )
    budget_key = models.CharField("预算键", max_length=160, validators=[commercial_key_validator])
    name = models.CharField("预算名称", max_length=200)
    scope_type = models.CharField("作用域类型", max_length=16, choices=CommercialScopeType.choices)
    workspace = models.ForeignKey(
        "tenancy.Workspace",
        on_delete=models.PROTECT,
        related_name="budgets",
        null=True,
        blank=True,
        verbose_name="工作空间",
    )
    project = models.ForeignKey(
        "tenancy.Project",
        on_delete=models.PROTECT,
        related_name="budgets",
        null=True,
        blank=True,
        verbose_name="项目",
    )
    environment = models.ForeignKey(
        "tenancy.Environment",
        on_delete=models.PROTECT,
        related_name="budgets",
        null=True,
        blank=True,
        verbose_name="环境",
    )
    currency_code = models.CharField(
        "币种", max_length=3, validators=[currency_code_validator], help_text="ISO 4217 三字母代码。"
    )
    amount_limit = models.DecimalField("预算上限", max_digits=20, decimal_places=4)
    window_type = models.CharField("预算周期", max_length=24, choices=BudgetWindowType.choices)
    enforcement_mode = models.CharField(
        "执行模式", max_length=16, choices=EnforcementMode.choices, default=EnforcementMode.SOFT
    )
    status = models.CharField(
        "状态",
        max_length=16,
        choices=CommercialGrantStatus.choices,
        default=CommercialGrantStatus.ACTIVE,
    )
    valid_from = models.DateTimeField("生效时间", null=True, blank=True)
    valid_until = models.DateTimeField("失效时间", null=True, blank=True)
    created_by = models.ForeignKey(
        Principal,
        on_delete=models.PROTECT,
        related_name="created_budgets",
        verbose_name="创建主体",
    )
    revoked_by = models.ForeignKey(
        Principal,
        on_delete=models.PROTECT,
        related_name="revoked_budgets",
        null=True,
        blank=True,
        verbose_name="撤销主体",
    )
    revoked_at = models.DateTimeField("撤销时间", null=True, blank=True)

    class Meta:
        db_table = "sf_budget"
        verbose_name = "成本预算"
        verbose_name_plural = "成本预算"
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(
                        scope_type=CommercialScopeType.TENANT,
                        workspace__isnull=True,
                        project__isnull=True,
                        environment__isnull=True,
                    )
                    | models.Q(
                        scope_type=CommercialScopeType.WORKSPACE,
                        workspace__isnull=False,
                        project__isnull=True,
                        environment__isnull=True,
                    )
                    | models.Q(
                        scope_type=CommercialScopeType.PROJECT,
                        workspace__isnull=True,
                        project__isnull=False,
                        environment__isnull=True,
                    )
                    | models.Q(
                        scope_type=CommercialScopeType.ENVIRONMENT,
                        workspace__isnull=True,
                        project__isnull=True,
                        environment__isnull=False,
                    )
                ),
                name="sf_budget_scope_shape_ck",
            ),
            models.CheckConstraint(condition=models.Q(amount_limit__gt=0), name="sf_budget_amount_ck"),
            models.CheckConstraint(
                condition=(
                    models.Q(valid_until__isnull=True)
                    | models.Q(valid_from__isnull=True)
                    | models.Q(valid_until__gt=models.F("valid_from"))
                ),
                name="sf_budget_valid_window_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        window_type=BudgetWindowType.FIXED_TERM,
                        valid_from__isnull=False,
                        valid_until__isnull=False,
                    )
                    | models.Q(
                        window_type__in=[
                            BudgetWindowType.CALENDAR_MONTH,
                            BudgetWindowType.CALENDAR_YEAR,
                        ]
                    )
                ),
                name="sf_budget_term_shape_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status=CommercialGrantStatus.ACTIVE,
                        revoked_by__isnull=True,
                        revoked_at__isnull=True,
                    )
                    | models.Q(
                        status=CommercialGrantStatus.REVOKED,
                        revoked_by__isnull=False,
                        revoked_at__isnull=False,
                    )
                ),
                name="sf_budget_revoke_shape_ck",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "budget_key", "status"], name="sf_budget_key_idx"),
            models.Index(fields=["project", "status"], name="sf_budget_proj_idx"),
        ]

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        workspace = self.workspace if self.workspace_id else None
        project = self.project if self.project_id else None
        environment = self.environment if self.environment_id else None
        revoked_by = self.revoked_by if self.revoked_by_id else None

        if self.scope_type == CommercialScopeType.TENANT:
            if self.workspace_id or self.project_id or self.environment_id:
                errors["scope_type"] = "TENANT Budget 不能携带子级 Scope。"
        elif self.scope_type == CommercialScopeType.WORKSPACE:
            if workspace is None or self.project_id or self.environment_id:
                errors["workspace"] = "WORKSPACE Budget 必须且只能指定 Workspace。"
            elif self.workspace.tenant_id != self.tenant_id:
                errors["workspace"] = "Budget Workspace 必须属于同一 Tenant。"
        elif self.scope_type == CommercialScopeType.PROJECT:
            if self.workspace_id or project is None or self.environment_id:
                errors["project"] = "PROJECT Budget 必须且只能指定 Project。"
            elif self.project.tenant_id != self.tenant_id:
                errors["project"] = "Budget Project 必须属于同一 Tenant。"
        elif self.scope_type == CommercialScopeType.ENVIRONMENT:
            if self.workspace_id or self.project_id or environment is None:
                errors["environment"] = "ENVIRONMENT Budget 必须且只能指定 Environment。"
            elif self.environment.tenant_id != self.tenant_id:
                errors["environment"] = "Budget Environment 必须属于同一 Tenant。"
        else:
            errors["scope_type"] = "未知 Budget scope_type。"

        if self.amount_limit <= 0:
            errors["amount_limit"] = "Budget amount_limit 必须大于 0。"
        if len(self.currency_code) != 3 or not self.currency_code.isascii() or not self.currency_code.isupper():
            errors["currency_code"] = "Budget currency_code 必须是三个大写 ASCII 字母。"
        if self.window_type == BudgetWindowType.FIXED_TERM:
            if self.valid_from is None or self.valid_until is None:
                errors["window_type"] = "FIXED_TERM Budget 必须指定完整 valid_from/valid_until。"
        if self.created_by_id and self.created_by.tenant_id not in (None, self.tenant_id):
            errors["created_by"] = "Budget 创建主体必须属于同一 Tenant 或平台。"
        if revoked_by is not None and revoked_by.tenant_id not in (None, self.tenant_id):
            errors["revoked_by"] = "Budget 撤销主体必须属于同一 Tenant 或平台。"
        if self.valid_from and self.valid_until and self.valid_until <= self.valid_from:
            errors["valid_until"] = "Budget 失效时间必须晚于生效时间。"

        if errors:
            raise ValidationError(errors)

    def is_currently_effective(self, *, at: datetime | None = None) -> bool:
        moment = at or timezone.now()
        if self.status != CommercialGrantStatus.ACTIVE:
            return False
        if self.valid_from and moment < self.valid_from:
            return False
        return not (self.valid_until and moment >= self.valid_until)


class UsageCounter(UUID7Model, TimeStampedModel, ConcurrentModel):
    """某一 Quota/窗口的强一致物化计数器。

    ``consumed_value`` 是已经 commit 的量，``reserved_value`` 是尚未 commit 的占位量。HARD quota
    决策必须在 row lock 下读取并修改这两个字段，不能在 controller 中先 SUM 再 INSERT。
    """

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="usage_counters",
        verbose_name="所属租户",
    )
    quota = models.ForeignKey(
        Quota, on_delete=models.PROTECT, related_name="counters", verbose_name="配额"
    )
    window_start = models.DateTimeField("窗口开始", null=True, blank=True)
    window_end = models.DateTimeField("窗口结束", null=True, blank=True)
    consumed_value = models.PositiveBigIntegerField("已消费", default=0)
    reserved_value = models.PositiveBigIntegerField("已预留", default=0)

    class Meta:
        db_table = "sf_usage_counter"
        verbose_name = "使用计数器"
        verbose_name_plural = "使用计数器"
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(window_start__isnull=True, window_end__isnull=True)
                    | models.Q(
                        window_start__isnull=False,
                        window_end__isnull=False,
                        window_end__gt=models.F("window_start"),
                    )
                ),
                name="sf_ucounter_window_shape_ck",
            ),
            models.UniqueConstraint(
                fields=["quota", "window_start", "window_end"],
                name="sf_ucounter_identity_uniq",
                nulls_distinct=False,
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "quota"], name="sf_ucounter_quota_idx"),
            models.Index(fields=["window_end"], name="sf_ucounter_window_idx"),
        ]

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        if self.quota_id and self.quota.tenant_id != self.tenant_id:
            errors["tenant"] = "UsageCounter.tenant 必须与 Quota.tenant 一致。"
        if self.quota_id:
            if self.quota.window_type == QuotaWindowType.NONE:
                if self.window_start is not None or self.window_end is not None:
                    errors["window_start"] = "NONE Quota 的 Counter 不应携带窗口时间。"
            elif self.window_start is None or self.window_end is None:
                errors["window_start"] = "有时间窗口的 Quota Counter 必须保存窗口起止时间。"
        if self.window_start and self.window_end and self.window_end <= self.window_start:
            errors["window_end"] = "UsageCounter.window_end 必须晚于 window_start。"
        if errors:
            raise ValidationError(errors)


class UsageReservation(UUID7Model, TimeStampedModel, ConcurrentModel):
    """一次逻辑业务操作的 reservation/idempotency identity。

    一次操作可能同时命中多条 Tenant/Project Quota，因此 Quota evidence 放在子表
    UsageReservationQuota，而不是复制多个互不关联的顶层 reservation。
    """

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="usage_reservations",
        verbose_name="所属租户",
    )
    principal = models.ForeignKey(
        Principal,
        on_delete=models.PROTECT,
        related_name="usage_reservations",
        verbose_name="消费主体",
    )
    metric_key = models.CharField("指标键", max_length=160, validators=[commercial_key_validator])
    measurement_type = models.CharField(
        "计量类型", max_length=16, choices=QuotaMeasurementType.choices
    )
    unit = models.CharField("单位", max_length=32, validators=[unit_key_validator])
    scope_type = models.CharField("操作作用域", max_length=16, choices=CommercialScopeType.choices)
    workspace = models.ForeignKey(
        "tenancy.Workspace",
        on_delete=models.PROTECT,
        related_name="usage_reservations",
        null=True,
        blank=True,
        verbose_name="工作空间",
    )
    project = models.ForeignKey(
        "tenancy.Project",
        on_delete=models.PROTECT,
        related_name="usage_reservations",
        null=True,
        blank=True,
        verbose_name="项目",
    )
    environment = models.ForeignKey(
        "tenancy.Environment",
        on_delete=models.PROTECT,
        related_name="usage_reservations",
        null=True,
        blank=True,
        verbose_name="环境",
    )
    amount = models.PositiveBigIntegerField("请求量")
    idempotency_key = models.CharField(
        "幂等键", max_length=160, validators=[idempotency_key_validator]
    )
    status = models.CharField(
        "状态",
        max_length=16,
        choices=UsageReservationStatus.choices,
        default=UsageReservationStatus.RESERVED,
    )
    reserved_at = models.DateTimeField("预留时间", default=timezone.now)
    expires_at = models.DateTimeField("预留失效时间")
    committed_at = models.DateTimeField("提交时间", null=True, blank=True)
    closed_at = models.DateTimeField("关闭时间", null=True, blank=True)

    class Meta:
        db_table = "sf_usage_reservation"
        verbose_name = "使用预留"
        verbose_name_plural = "使用预留"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "principal", "idempotency_key"],
                name="sf_ures_idempotency_uniq",
            ),
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="sf_ures_amount_ck"),
            models.CheckConstraint(
                condition=models.Q(expires_at__gt=models.F("reserved_at")),
                name="sf_ures_expiry_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        scope_type=CommercialScopeType.TENANT,
                        workspace__isnull=True,
                        project__isnull=True,
                        environment__isnull=True,
                    )
                    | models.Q(
                        scope_type=CommercialScopeType.WORKSPACE,
                        workspace__isnull=False,
                        project__isnull=True,
                        environment__isnull=True,
                    )
                    | models.Q(
                        scope_type=CommercialScopeType.PROJECT,
                        workspace__isnull=True,
                        project__isnull=False,
                        environment__isnull=True,
                    )
                    | models.Q(
                        scope_type=CommercialScopeType.ENVIRONMENT,
                        workspace__isnull=True,
                        project__isnull=True,
                        environment__isnull=False,
                    )
                ),
                name="sf_ures_scope_shape_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status=UsageReservationStatus.RESERVED,
                        committed_at__isnull=True,
                        closed_at__isnull=True,
                    )
                    | models.Q(
                        status=UsageReservationStatus.COMMITTED,
                        committed_at__isnull=False,
                        closed_at__isnull=True,
                    )
                    | models.Q(
                        status=UsageReservationStatus.RELEASED,
                        closed_at__isnull=False,
                    )
                    | models.Q(
                        status=UsageReservationStatus.EXPIRED,
                        committed_at__isnull=True,
                        closed_at__isnull=False,
                    )
                ),
                name="sf_ures_status_shape_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "metric_key", "status", "expires_at"],
                name="sf_ures_metric_exp_idx",
            ),
            models.Index(fields=["tenant", "principal", "status"], name="sf_ures_princ_idx"),
        ]

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        if self.principal_id and self.principal.tenant_id != self.tenant_id:
            errors["principal"] = "UsageReservation Principal 必须属于同一 Tenant。"
        self._validate_scope(errors)
        if self.amount <= 0:
            errors["amount"] = "UsageReservation.amount 必须大于 0。"
        if self.expires_at and self.reserved_at and self.expires_at <= self.reserved_at:
            errors["expires_at"] = "expires_at 必须晚于 reserved_at。"
        if errors:
            raise ValidationError(errors)

    def _validate_scope(self, errors: dict[str, str]) -> None:
        if self.scope_type == CommercialScopeType.TENANT:
            if self.workspace_id or self.project_id or self.environment_id:
                errors["scope_type"] = "TENANT usage scope 不能携带子级 Scope。"
        elif self.scope_type == CommercialScopeType.WORKSPACE:
            if not self.workspace_id or self.project_id or self.environment_id:
                errors["workspace"] = "WORKSPACE usage scope 必须且只能指定 Workspace。"
            elif self.workspace.tenant_id != self.tenant_id:
                errors["workspace"] = "Usage Workspace 必须属于同一 Tenant。"
        elif self.scope_type == CommercialScopeType.PROJECT:
            if self.workspace_id or not self.project_id or self.environment_id:
                errors["project"] = "PROJECT usage scope 必须且只能指定 Project。"
            elif self.project.tenant_id != self.tenant_id:
                errors["project"] = "Usage Project 必须属于同一 Tenant。"
        elif self.scope_type == CommercialScopeType.ENVIRONMENT:
            if self.workspace_id or self.project_id or not self.environment_id:
                errors["environment"] = "ENVIRONMENT usage scope 必须且只能指定 Environment。"
            elif self.environment.tenant_id != self.tenant_id:
                errors["environment"] = "Usage Environment 必须属于同一 Tenant。"
        else:
            errors["scope_type"] = "未知 Usage scope_type。"


class UsageReservationQuota(UUID7Model, TimeStampedModel):
    """一次 UsageReservation 命中的每条 Quota 决策证据。

    snapshot 字段保证以后 Quota 配置被撤销/修改后，仍能解释当时为什么允许或拒绝附近的操作。
    """

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="usage_reservation_quota_links",
        verbose_name="所属租户",
    )
    reservation = models.ForeignKey(
        UsageReservation,
        on_delete=models.PROTECT,
        related_name="quota_links",
        verbose_name="使用预留",
    )
    quota = models.ForeignKey(
        Quota,
        on_delete=models.PROTECT,
        related_name="reservation_links",
        verbose_name="命中的配额",
    )
    counter = models.ForeignKey(
        UsageCounter,
        on_delete=models.PROTECT,
        related_name="reservation_links",
        verbose_name="计数器",
    )
    amount = models.PositiveBigIntegerField("预留量")
    limit_snapshot = models.PositiveBigIntegerField("限制值快照")
    enforcement_mode_snapshot = models.CharField(
        "执行模式快照", max_length=16, choices=EnforcementMode.choices
    )
    projected_value_snapshot = models.PositiveBigIntegerField("决策时 projected 值")
    exceeded_snapshot = models.BooleanField("决策时是否超限", default=False)

    class Meta:
        db_table = "sf_usage_reservation_quota"
        verbose_name = "使用预留配额证据"
        verbose_name_plural = "使用预留配额证据"
        constraints = [
            models.UniqueConstraint(
                fields=["reservation", "quota"], name="sf_uresquota_res_quota_uniq"
            ),
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="sf_uresquota_amount_ck"),
        ]
        indexes = [
            models.Index(fields=["tenant", "quota"], name="sf_uresquota_quota_idx"),
            models.Index(fields=["counter"], name="sf_uresquota_counter_idx"),
        ]

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        if self.reservation_id and self.reservation.tenant_id != self.tenant_id:
            errors["tenant"] = "ReservationQuota.tenant 必须与 Reservation 一致。"
        if self.quota_id and self.quota.tenant_id != self.tenant_id:
            errors["quota"] = "ReservationQuota.Quo​​ta 必须属于同一 Tenant。"
        if self.counter_id and self.quota_id and self.counter.quota_id != self.quota_id:
            errors["counter"] = "ReservationQuota.Counter 必须属于同一 Quota。"
        if self.reservation_id and self.quota_id:
            if self.reservation.metric_key != self.quota.metric_key:
                errors["quota"] = "Reservation 与 Quota metric_key 必须一致。"
            if self.reservation.measurement_type != self.quota.measurement_type:
                errors["quota"] = "Reservation 与 Quota measurement_type 必须一致。"
            if self.reservation.unit != self.quota.unit:
                errors["quota"] = "Reservation 与 Quota unit 必须一致。"
        if self.reservation_id and self.amount != self.reservation.amount:
            errors["amount"] = "ReservationQuota.amount 必须等于父 Reservation.amount。"
        if errors:
            raise ValidationError(errors)


class UsageRecord(UUID7Model, TimeStampedModel):
    """已发生使用事实的不可删除式治理账本记录。

    第一版不实现通用 adjustment；错误修正以后通过新的冲正/调整事件表达，不原地删除 CONSUME。
    """

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="usage_records",
        verbose_name="所属租户",
    )
    reservation = models.ForeignKey(
        UsageReservation,
        on_delete=models.PROTECT,
        related_name="usage_records",
        verbose_name="来源预留",
    )
    principal = models.ForeignKey(
        Principal, on_delete=models.PROTECT, related_name="usage_records", verbose_name="消费主体"
    )
    metric_key = models.CharField("指标键", max_length=160, validators=[commercial_key_validator])
    measurement_type = models.CharField(
        "计量类型", max_length=16, choices=QuotaMeasurementType.choices
    )
    unit = models.CharField("单位", max_length=32, validators=[unit_key_validator])
    scope_type = models.CharField("操作作用域", max_length=16, choices=CommercialScopeType.choices)
    workspace = models.ForeignKey(
        "tenancy.Workspace",
        on_delete=models.PROTECT,
        related_name="usage_records",
        null=True,
        blank=True,
        verbose_name="工作空间",
    )
    project = models.ForeignKey(
        "tenancy.Project",
        on_delete=models.PROTECT,
        related_name="usage_records",
        null=True,
        blank=True,
        verbose_name="项目",
    )
    environment = models.ForeignKey(
        "tenancy.Environment",
        on_delete=models.PROTECT,
        related_name="usage_records",
        null=True,
        blank=True,
        verbose_name="环境",
    )
    event_type = models.CharField("事件类型", max_length=16, choices=UsageEventType.choices)
    amount = models.PositiveBigIntegerField("数量")
    recorded_at = models.DateTimeField("记账时间", default=timezone.now)

    class Meta:
        db_table = "sf_usage_record"
        verbose_name = "使用事实"
        verbose_name_plural = "使用事实"
        constraints = [
            models.UniqueConstraint(
                fields=["reservation", "event_type"], name="sf_urecord_res_event_uniq"
            ),
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="sf_urecord_amount_ck"),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        scope_type=CommercialScopeType.TENANT,
                        workspace__isnull=True,
                        project__isnull=True,
                        environment__isnull=True,
                    )
                    | models.Q(
                        scope_type=CommercialScopeType.WORKSPACE,
                        workspace__isnull=False,
                        project__isnull=True,
                        environment__isnull=True,
                    )
                    | models.Q(
                        scope_type=CommercialScopeType.PROJECT,
                        workspace__isnull=True,
                        project__isnull=False,
                        environment__isnull=True,
                    )
                    | models.Q(
                        scope_type=CommercialScopeType.ENVIRONMENT,
                        workspace__isnull=True,
                        project__isnull=True,
                        environment__isnull=False,
                    )
                ),
                name="sf_urecord_scope_shape_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "metric_key", "recorded_at"], name="sf_urecord_metric_idx"
            ),
            models.Index(
                fields=["tenant", "principal", "recorded_at"], name="sf_urecord_princ_idx"
            ),
        ]

    def clean(self) -> None:
        """UsageRecord 必须是 Reservation 的不可歧义事实快照。"""

        super().clean()
        errors: dict[str, str] = {}
        if self.reservation_id:
            reservation = self.reservation
            if self.tenant_id != reservation.tenant_id:
                errors["tenant"] = "UsageRecord.tenant 必须与 Reservation 一致。"
            if self.principal_id != reservation.principal_id:
                errors["principal"] = "UsageRecord.principal 必须与 Reservation 一致。"
            if self.metric_key != reservation.metric_key:
                errors["metric_key"] = "UsageRecord.metric_key 必须与 Reservation 一致。"
            if self.measurement_type != reservation.measurement_type:
                errors["measurement_type"] = "UsageRecord.measurement_type 必须与 Reservation 一致。"
            if self.unit != reservation.unit:
                errors["unit"] = "UsageRecord.unit 必须与 Reservation 一致。"
            if self.scope_type != reservation.scope_type:
                errors["scope_type"] = "UsageRecord.scope_type 必须与 Reservation 一致。"
            if self.workspace_id != reservation.workspace_id:
                errors["workspace"] = "UsageRecord.workspace 必须与 Reservation 一致。"
            if self.project_id != reservation.project_id:
                errors["project"] = "UsageRecord.project 必须与 Reservation 一致。"
            if self.environment_id != reservation.environment_id:
                errors["environment"] = "UsageRecord.environment 必须与 Reservation 一致。"
            if self.amount != reservation.amount:
                errors["amount"] = "UsageRecord.amount 必须与 Reservation.amount 一致。"
        if errors:
            raise ValidationError(errors)

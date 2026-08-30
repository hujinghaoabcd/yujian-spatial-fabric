"""Phase B2.3 Commercial Controls application services。

最重要的安全边界是 HARD quota 的 reservation protocol：不能在 controller 中执行
``SUM -> CHECK -> INSERT``。本服务在数据库事务内锁定全部 applicable quota/counter，并把一次逻辑
操作固定为单个 UsageReservation + 多条 UsageReservationQuota evidence。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from spatial_fabric.commercial.models import (
    Budget,
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
    UsageReservationQuota,
    UsageReservationStatus,
)
from spatial_fabric.iam.models import Principal, PrincipalStatus
from spatial_fabric.tenancy.models import Environment, Project, Tenant, Workspace

_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_.:-]{0,159}$")
_UNIT_PATTERN = re.compile(r"^[a-z][a-z0-9_.:-]{0,31}$")
_IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}$")


class CommercialControlError(ValueError):
    """Commercial Controls 契约或状态机被违反。"""


class EntitlementError(CommercialControlError):
    """Entitlement 查询/配置上下文无效。"""


class QuotaControlError(CommercialControlError):
    """Quota/Usage reservation 契约被违反。"""


class QuotaAccountingInvariantError(QuotaControlError):
    """Counter 与 Reservation evidence 已出现不可接受的不一致，必须 fail closed。"""


class QuotaExceededError(QuotaControlError):
    """至少一条 applicable HARD quota 被 projected usage 超过。"""

    def __init__(self, evidence: tuple[QuotaDecisionEvidence, ...]) -> None:
        self.evidence = evidence
        quota_ids = ", ".join(str(item.quota_id) for item in evidence if item.exceeded)
        super().__init__(f"HARD quota exceeded: {quota_ids}")


@dataclass(frozen=True, slots=True)
class CommercialScopeRef:
    """调用方提供的单一管理 Scope 引用。"""

    tenant_id: UUID
    scope_type: CommercialScopeType
    scope_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class _ScopeContext:
    """解析后的 Scope 及其祖先，用于继承匹配。"""

    tenant_id: UUID
    exact_type: CommercialScopeType
    exact_id: UUID | None
    workspace_id: UUID | None
    project_id: UUID | None
    environment_id: UUID | None


@dataclass(frozen=True, slots=True)
class EntitlementResolution:
    """Entitlement evaluator 的可解释结果。"""

    principal_id: UUID
    entitlement_key: str
    scope_ref: CommercialScopeRef
    grant_ids: tuple[UUID, ...]

    @property
    def entitled(self) -> bool:
        return bool(self.grant_ids)


@dataclass(frozen=True, slots=True)
class QuotaResolution:
    """指定 metric 在当前主体/Scope 下全部 applicable quota。"""

    principal_id: UUID
    metric_key: str
    scope_ref: CommercialScopeRef
    quota_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class BudgetResolution:
    """指定 Scope 下可解释的 Budget evidence。"""

    scope_ref: CommercialScopeRef
    budget_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class QuotaDecisionEvidence:
    """一次 reservation 对单条 Quota 的 projected decision 快照。"""

    quota_id: UUID
    counter_id: UUID
    enforcement_mode: str
    limit_value: int
    consumed_value: int
    reserved_value: int
    requested_amount: int
    projected_value: int
    exceeded: bool


@dataclass(frozen=True, slots=True)
class UsageReservationDecision:
    """一次幂等 reservation 的结果与全部 quota evidence。"""

    reservation_id: UUID
    status: str
    evidence: tuple[QuotaDecisionEvidence, ...]

    @property
    def exceeded_quota_ids(self) -> tuple[UUID, ...]:
        return tuple(item.quota_id for item in self.evidence if item.exceeded)


class _ScopeLoader:
    """把单一 ScopeRef 解析为带祖先的可信 ScopeContext。"""

    @staticmethod
    def load(scope_ref: CommercialScopeRef) -> _ScopeContext:
        if scope_ref.scope_type == CommercialScopeType.TENANT:
            if scope_ref.scope_id is not None:
                raise CommercialControlError("TENANT scope_ref 不应携带 scope_id。")
            if not Tenant.objects.filter(pk=scope_ref.tenant_id).exists():
                raise CommercialControlError("Tenant 不存在。")
            return _ScopeContext(
                tenant_id=scope_ref.tenant_id,
                exact_type=CommercialScopeType.TENANT,
                exact_id=None,
                workspace_id=None,
                project_id=None,
                environment_id=None,
            )

        if scope_ref.scope_id is None:
            raise CommercialControlError("非 TENANT scope_ref 必须提供 scope_id。")

        if scope_ref.scope_type == CommercialScopeType.WORKSPACE:
            try:
                workspace = Workspace.objects.get(pk=scope_ref.scope_id)
            except Workspace.DoesNotExist as exc:
                raise CommercialControlError("Workspace 不存在。") from exc
            if workspace.tenant_id != scope_ref.tenant_id:
                raise CommercialControlError("Workspace 不属于 scope_ref.tenant。")
            return _ScopeContext(
                tenant_id=scope_ref.tenant_id,
                exact_type=CommercialScopeType.WORKSPACE,
                exact_id=workspace.id,
                workspace_id=workspace.id,
                project_id=None,
                environment_id=None,
            )

        if scope_ref.scope_type == CommercialScopeType.PROJECT:
            try:
                project = Project.objects.select_related("workspace").get(pk=scope_ref.scope_id)
            except Project.DoesNotExist as exc:
                raise CommercialControlError("Project 不存在。") from exc
            if project.tenant_id != scope_ref.tenant_id:
                raise CommercialControlError("Project 不属于 scope_ref.tenant。")
            return _ScopeContext(
                tenant_id=scope_ref.tenant_id,
                exact_type=CommercialScopeType.PROJECT,
                exact_id=project.id,
                workspace_id=project.workspace_id,
                project_id=project.id,
                environment_id=None,
            )

        if scope_ref.scope_type == CommercialScopeType.ENVIRONMENT:
            try:
                environment = Environment.objects.select_related("project__workspace").get(
                    pk=scope_ref.scope_id
                )
            except Environment.DoesNotExist as exc:
                raise CommercialControlError("Environment 不存在。") from exc
            if environment.tenant_id != scope_ref.tenant_id:
                raise CommercialControlError("Environment 不属于 scope_ref.tenant。")
            return _ScopeContext(
                tenant_id=scope_ref.tenant_id,
                exact_type=CommercialScopeType.ENVIRONMENT,
                exact_id=environment.id,
                workspace_id=environment.project.workspace_id,
                project_id=environment.project_id,
                environment_id=environment.id,
            )

        raise CommercialControlError("未知 Commercial scope_type。")


class _CommercialQuery:
    """共享的 subject/scope/validity 查询构造器。"""

    @staticmethod
    def active_principal(*, principal_id: UUID, tenant_id: UUID) -> Principal:
        try:
            principal = Principal.objects.get(pk=principal_id)
        except Principal.DoesNotExist as exc:
            raise CommercialControlError("Principal 不存在。") from exc
        if principal.tenant_id != tenant_id:
            raise CommercialControlError("Principal 必须属于当前 Tenant。")
        if principal.status != PrincipalStatus.ACTIVE:
            raise CommercialControlError("非 ACTIVE Principal 不能消费商业许可/配额。")
        return principal

    @staticmethod
    def scope_q(context: _ScopeContext) -> Q:
        result = Q(scope_type=CommercialScopeType.TENANT)
        if context.workspace_id is not None:
            result |= Q(
                scope_type=CommercialScopeType.WORKSPACE,
                workspace_id=context.workspace_id,
            )
        if context.project_id is not None:
            result |= Q(scope_type=CommercialScopeType.PROJECT, project_id=context.project_id)
        if context.environment_id is not None:
            result |= Q(
                scope_type=CommercialScopeType.ENVIRONMENT,
                environment_id=context.environment_id,
            )
        return result

    @staticmethod
    def subject_q(principal_id: UUID) -> Q:
        return Q(subject_type=CommercialSubjectType.TENANT, principal__isnull=True) | Q(
            subject_type=CommercialSubjectType.PRINCIPAL,
            principal_id=principal_id,
        )

    @staticmethod
    def valid_at_q(moment: datetime) -> Q:
        return (Q(valid_from__isnull=True) | Q(valid_from__lte=moment)) & (
            Q(valid_until__isnull=True) | Q(valid_until__gt=moment)
        )


class EntitlementEvaluator:
    """解析 Tenant/Principal 在层级 Scope 下的有效 Entitlement evidence。"""

    def resolve(
        self,
        *,
        principal_id: UUID,
        entitlement_key: str,
        scope_ref: CommercialScopeRef,
        at: datetime | None = None,
    ) -> EntitlementResolution:
        if not _KEY_PATTERN.fullmatch(entitlement_key):
            raise EntitlementError("entitlement_key 格式无效。")
        moment = at or timezone.now()
        context = _ScopeLoader.load(scope_ref)
        _CommercialQuery.active_principal(
            principal_id=principal_id,
            tenant_id=scope_ref.tenant_id,
        )
        grants = EntitlementGrant.objects.filter(
            tenant_id=scope_ref.tenant_id,
            entitlement_key=entitlement_key,
            status=CommercialGrantStatus.ACTIVE,
        ).filter(
            _CommercialQuery.subject_q(principal_id),
            _CommercialQuery.scope_q(context),
            _CommercialQuery.valid_at_q(moment),
        )
        return EntitlementResolution(
            principal_id=principal_id,
            entitlement_key=entitlement_key,
            scope_ref=scope_ref,
            grant_ids=tuple(grants.order_by("id").values_list("id", flat=True)),
        )


class QuotaEvaluator:
    """返回全部 applicable Quota；不使用“最具体一条覆盖父级”的危险简化。"""

    def resolve(
        self,
        *,
        principal_id: UUID,
        metric_key: str,
        scope_ref: CommercialScopeRef,
        at: datetime | None = None,
    ) -> QuotaResolution:
        if not _KEY_PATTERN.fullmatch(metric_key):
            raise QuotaControlError("metric_key 格式无效。")
        moment = at or timezone.now()
        context = _ScopeLoader.load(scope_ref)
        _CommercialQuery.active_principal(
            principal_id=principal_id,
            tenant_id=scope_ref.tenant_id,
        )
        queryset = self.applicable_queryset(
            principal_id=principal_id,
            metric_key=metric_key,
            context=context,
            moment=moment,
        )
        return QuotaResolution(
            principal_id=principal_id,
            metric_key=metric_key,
            scope_ref=scope_ref,
            quota_ids=tuple(queryset.order_by("id").values_list("id", flat=True)),
        )

    @staticmethod
    def applicable_queryset(
        *,
        principal_id: UUID,
        metric_key: str,
        context: _ScopeContext,
        moment: datetime,
    ) -> QuerySet[Quota]:
        return Quota.objects.filter(
            tenant_id=context.tenant_id,
            metric_key=metric_key,
            status=CommercialGrantStatus.ACTIVE,
        ).filter(
            _CommercialQuery.subject_q(principal_id),
            _CommercialQuery.scope_q(context),
            _CommercialQuery.valid_at_q(moment),
        )


class BudgetEvaluator:
    """返回 Scope 继承链上的 Budget policy evidence；第一版不伪造成本扣减。"""

    def resolve(
        self,
        *,
        scope_ref: CommercialScopeRef,
        currency_code: str | None = None,
        at: datetime | None = None,
    ) -> BudgetResolution:
        moment = at or timezone.now()
        context = _ScopeLoader.load(scope_ref)
        queryset = Budget.objects.filter(
            tenant_id=scope_ref.tenant_id,
            status=CommercialGrantStatus.ACTIVE,
        ).filter(
            _CommercialQuery.scope_q(context),
            _CommercialQuery.valid_at_q(moment),
        )
        if currency_code is not None:
            if len(currency_code) != 3 or not currency_code.isascii() or not currency_code.isupper():
                raise CommercialControlError("currency_code 必须是三个大写 ASCII 字母。")
            queryset = queryset.filter(currency_code=currency_code)
        return BudgetResolution(
            scope_ref=scope_ref,
            budget_ids=tuple(queryset.order_by("id").values_list("id", flat=True)),
        )


class UsageReservationService:
    """并发安全的 Quota reservation / commit / release 事务服务。"""

    def reserve_for_context(
        self,
        *,
        principal_id: UUID,
        metric_key: str,
        measurement_type: QuotaMeasurementType,
        unit: str,
        scope_ref: CommercialScopeRef,
        amount: int,
        idempotency_key: str,
        ttl: timedelta = timedelta(minutes=15),
        at: datetime | None = None,
    ) -> UsageReservationDecision:
        """对全部 applicable quota 原子预留一次操作。

        任一 HARD quota 超限会回滚整个事务；SOFT/OBSERVE 仍保存 exceeded snapshot。幂等重试返回
        原 reservation，不重复修改 counter，也不因后来新增 quota 偷偷改变原 evidence 集。
        """

        self._validate_request(
            metric_key=metric_key,
            measurement_type=measurement_type,
            unit=unit,
            amount=amount,
            idempotency_key=idempotency_key,
            ttl=ttl,
        )
        moment = at or timezone.now()
        try:
            context = _ScopeLoader.load(scope_ref)
            principal = _CommercialQuery.active_principal(
                principal_id=principal_id,
                tenant_id=scope_ref.tenant_id,
            )
        except CommercialControlError as exc:
            # ScopeLoader 被 Entitlement/Budget/Quota 共享；Quota 写服务必须把共享基础异常
            # 收口成稳定 QuotaControlError，避免调用方依赖内部 loader 的异常层级。
            if isinstance(exc, QuotaControlError):
                raise
            raise QuotaControlError(str(exc)) from exc
        scope_defaults = self._exact_scope_defaults(context)

        with transaction.atomic():
            reservation, created = UsageReservation.objects.get_or_create(
                tenant_id=scope_ref.tenant_id,
                principal=principal,
                idempotency_key=idempotency_key,
                defaults={
                    "metric_key": metric_key,
                    "measurement_type": measurement_type,
                    "unit": unit,
                    "scope_type": context.exact_type,
                    "workspace_id": scope_defaults["workspace_id"],
                    "project_id": scope_defaults["project_id"],
                    "environment_id": scope_defaults["environment_id"],
                    "amount": amount,
                    "reserved_at": moment,
                    "expires_at": moment + ttl,
                },
            )
            reservation = UsageReservation.objects.select_for_update().get(pk=reservation.pk)
            if not created:
                self._validate_idempotent_fingerprint(
                    reservation,
                    metric_key=metric_key,
                    measurement_type=measurement_type,
                    unit=unit,
                    context=context,
                    amount=amount,
                )

            # 过期占位必须在新 projected decision 前释放，否则会造成“幽灵 reserved”拒绝正常请求。
            self._expire_stale_reservations(
                tenant_id=scope_ref.tenant_id,
                metric_key=metric_key,
                moment=moment,
            )
            reservation.refresh_from_db()
            if not created or reservation.status != UsageReservationStatus.RESERVED:
                return self._decision_for(reservation)

            quotas = list(
                QuotaEvaluator.applicable_queryset(
                    principal_id=principal_id,
                    metric_key=metric_key,
                    context=context,
                    moment=moment,
                )
                .select_for_update()
                .order_by("id")
            )
            for quota in quotas:
                if quota.measurement_type != measurement_type or quota.unit != unit:
                    raise QuotaAccountingInvariantError(
                        "同一 metric 的 applicable Quota 存在 measurement/unit 冲突。"
                    )

            counter_pairs: list[tuple[Quota, UsageCounter, QuotaDecisionEvidence]] = []
            hard_exceeded: list[QuotaDecisionEvidence] = []
            for quota in quotas:
                counter = self._get_locked_counter(quota=quota, moment=moment)
                projected = counter.consumed_value + counter.reserved_value + amount
                exceeded = projected > quota.limit_value
                evidence = QuotaDecisionEvidence(
                    quota_id=quota.id,
                    counter_id=counter.id,
                    enforcement_mode=quota.enforcement_mode,
                    limit_value=quota.limit_value,
                    consumed_value=counter.consumed_value,
                    reserved_value=counter.reserved_value,
                    requested_amount=amount,
                    projected_value=projected,
                    exceeded=exceeded,
                )
                counter_pairs.append((quota, counter, evidence))
                if exceeded and quota.enforcement_mode == EnforcementMode.HARD:
                    hard_exceeded.append(evidence)

            if hard_exceeded:
                raise QuotaExceededError(tuple(hard_exceeded))

            for quota, counter, evidence in counter_pairs:
                counter.reserved_value += amount
                counter.lock_version += 1
                counter.save(update_fields=["reserved_value", "lock_version", "updated_at"])
                UsageReservationQuota.objects.create(
                    tenant_id=scope_ref.tenant_id,
                    reservation=reservation,
                    quota=quota,
                    counter=counter,
                    amount=amount,
                    limit_snapshot=quota.limit_value,
                    enforcement_mode_snapshot=quota.enforcement_mode,
                    consumed_value_snapshot=evidence.consumed_value,
                    reserved_value_snapshot=evidence.reserved_value,
                    projected_value_snapshot=evidence.projected_value,
                    exceeded_snapshot=evidence.exceeded,
                )

            return UsageReservationDecision(
                reservation_id=reservation.id,
                status=reservation.status,
                evidence=tuple(item[2] for item in counter_pairs),
            )

    def commit(
        self,
        *,
        reservation_id: UUID,
        at: datetime | None = None,
    ) -> UsageReservation:
        """将 RESERVED 原子转换为 COMMITTED，并写入唯一 CONSUME UsageRecord。"""

        moment = at or timezone.now()
        with transaction.atomic():
            reservation = self._lock_reservation(reservation_id)
            if reservation.status == UsageReservationStatus.COMMITTED:
                return reservation
            if reservation.status in {
                UsageReservationStatus.RELEASED,
                UsageReservationStatus.EXPIRED,
            }:
                return reservation
            if moment >= reservation.expires_at:
                self._expire_locked_reservation(reservation=reservation, moment=moment)
                return reservation

            lines, counters = self._lock_lines_and_counters(reservation)
            for line in lines:
                counter = counters[line.counter_id]
                if counter.reserved_value < line.amount:
                    raise QuotaAccountingInvariantError(
                        "Counter.reserved_value 小于待 commit reservation amount。"
                    )
            for line in lines:
                counter = counters[line.counter_id]
                counter.reserved_value -= line.amount
                counter.consumed_value += line.amount
                counter.lock_version += 1
                counter.save(
                    update_fields=[
                        "reserved_value",
                        "consumed_value",
                        "lock_version",
                        "updated_at",
                    ]
                )

            reservation.status = UsageReservationStatus.COMMITTED
            reservation.committed_at = moment
            reservation.lock_version += 1
            reservation.save(
                update_fields=["status", "committed_at", "lock_version", "updated_at"]
            )
            self._create_usage_record(
                reservation=reservation,
                event_type=UsageEventType.CONSUME,
                moment=moment,
            )
            return reservation

    def release(
        self,
        *,
        reservation_id: UUID,
        at: datetime | None = None,
    ) -> UsageReservation:
        """取消 RESERVED，或释放已 COMMITTED 的 GAUGE/CONCURRENCY 当前占用。

        CONSUMPTION 一旦 commit 不允许 release；未来纠错必须使用独立 adjustment/credit 事件，而不是
        破坏已发生消费事实。
        """

        moment = at or timezone.now()
        with transaction.atomic():
            reservation = self._lock_reservation(reservation_id)
            if reservation.status in {
                UsageReservationStatus.RELEASED,
                UsageReservationStatus.EXPIRED,
            }:
                return reservation
            if (
                reservation.status == UsageReservationStatus.COMMITTED
                and reservation.measurement_type == QuotaMeasurementType.CONSUMPTION
            ):
                raise QuotaControlError("已 COMMITTED 的 CONSUMPTION usage 不能 release。")
            if reservation.status == UsageReservationStatus.RESERVED and moment >= reservation.expires_at:
                self._expire_locked_reservation(reservation=reservation, moment=moment)
                return reservation

            lines, counters = self._lock_lines_and_counters(reservation)
            if reservation.status == UsageReservationStatus.RESERVED:
                for line in lines:
                    if counters[line.counter_id].reserved_value < line.amount:
                        raise QuotaAccountingInvariantError("释放前 reserved counter 已小于 evidence amount。")
                for line in lines:
                    counter = counters[line.counter_id]
                    counter.reserved_value -= line.amount
                    counter.lock_version += 1
                    counter.save(update_fields=["reserved_value", "lock_version", "updated_at"])
            elif reservation.status == UsageReservationStatus.COMMITTED:
                for line in lines:
                    if counters[line.counter_id].consumed_value < line.amount:
                        raise QuotaAccountingInvariantError("释放前 consumed counter 已小于 evidence amount。")
                for line in lines:
                    counter = counters[line.counter_id]
                    counter.consumed_value -= line.amount
                    counter.lock_version += 1
                    counter.save(update_fields=["consumed_value", "lock_version", "updated_at"])
                self._create_usage_record(
                    reservation=reservation,
                    event_type=UsageEventType.RELEASE,
                    moment=moment,
                )
            else:
                raise QuotaAccountingInvariantError("未知 UsageReservation 状态。")

            reservation.status = UsageReservationStatus.RELEASED
            reservation.closed_at = moment
            reservation.lock_version += 1
            reservation.save(update_fields=["status", "closed_at", "lock_version", "updated_at"])
            return reservation

    @staticmethod
    def _validate_request(
        *,
        metric_key: str,
        measurement_type: QuotaMeasurementType,
        unit: str,
        amount: int,
        idempotency_key: str,
        ttl: timedelta,
    ) -> None:
        if not _KEY_PATTERN.fullmatch(metric_key):
            raise QuotaControlError("metric_key 格式无效。")
        if measurement_type not in QuotaMeasurementType.values:
            raise QuotaControlError("measurement_type 无效。")
        if not _UNIT_PATTERN.fullmatch(unit):
            raise QuotaControlError("unit 格式无效。")
        if amount <= 0:
            raise QuotaControlError("amount 必须大于 0。")
        if not _IDEMPOTENCY_PATTERN.fullmatch(idempotency_key):
            raise QuotaControlError("idempotency_key 格式无效。")
        if ttl <= timedelta(0):
            raise QuotaControlError("reservation TTL 必须大于 0。")

    @staticmethod
    def _exact_scope_defaults(context: _ScopeContext) -> dict[str, UUID | None]:
        return {
            "workspace_id": context.exact_id
            if context.exact_type == CommercialScopeType.WORKSPACE
            else None,
            "project_id": context.exact_id
            if context.exact_type == CommercialScopeType.PROJECT
            else None,
            "environment_id": context.exact_id
            if context.exact_type == CommercialScopeType.ENVIRONMENT
            else None,
        }

    @staticmethod
    def _validate_idempotent_fingerprint(
        reservation: UsageReservation,
        *,
        metric_key: str,
        measurement_type: QuotaMeasurementType,
        unit: str,
        context: _ScopeContext,
        amount: int,
    ) -> None:
        expected = UsageReservationService._exact_scope_defaults(context)
        if (
            reservation.metric_key != metric_key
            or reservation.measurement_type != measurement_type
            or reservation.unit != unit
            or reservation.scope_type != context.exact_type
            or reservation.workspace_id != expected["workspace_id"]
            or reservation.project_id != expected["project_id"]
            or reservation.environment_id != expected["environment_id"]
            or reservation.amount != amount
        ):
            raise QuotaControlError("同一 idempotency_key 被用于不同 usage request fingerprint。")

    def _get_locked_counter(self, *, quota: Quota, moment: datetime) -> UsageCounter:
        window_start, window_end = self._quota_window(quota=quota, moment=moment)
        counter, _ = UsageCounter.objects.get_or_create(
            tenant_id=quota.tenant_id,
            quota=quota,
            window_start=window_start,
            window_end=window_end,
        )
        return UsageCounter.objects.select_for_update().get(pk=counter.pk)

    @staticmethod
    def _quota_window(*, quota: Quota, moment: datetime) -> tuple[datetime | None, datetime | None]:
        if quota.window_type == QuotaWindowType.NONE:
            return None, None
        try:
            zone = ZoneInfo(quota.tenant.default_timezone)
        except ZoneInfoNotFoundError as exc:
            raise QuotaAccountingInvariantError("Tenant.default_timezone 无法解析。") from exc

        local = moment.astimezone(zone)
        start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
        if quota.window_type == QuotaWindowType.CALENDAR_DAY:
            end_local = start_local + timedelta(days=1)
        elif quota.window_type == QuotaWindowType.CALENDAR_MONTH:
            start_local = start_local.replace(day=1)
            if start_local.month == 12:
                end_local = start_local.replace(year=start_local.year + 1, month=1)
            else:
                end_local = start_local.replace(month=start_local.month + 1)
        else:
            raise QuotaAccountingInvariantError("未知 Quota window_type。")
        return start_local.astimezone(UTC), end_local.astimezone(UTC)

    def _expire_stale_reservations(
        self,
        *,
        tenant_id: UUID,
        metric_key: str,
        moment: datetime,
    ) -> None:
        stale = list(
            UsageReservation.objects.select_for_update()
            .filter(
                tenant_id=tenant_id,
                metric_key=metric_key,
                status=UsageReservationStatus.RESERVED,
                expires_at__lte=moment,
            )
            .order_by("id")
        )
        for reservation in stale:
            self._expire_locked_reservation(reservation=reservation, moment=moment)

    def _expire_locked_reservation(
        self,
        *,
        reservation: UsageReservation,
        moment: datetime,
    ) -> None:
        if reservation.status != UsageReservationStatus.RESERVED:
            return
        lines, counters = self._lock_lines_and_counters(reservation)
        for line in lines:
            if counters[line.counter_id].reserved_value < line.amount:
                raise QuotaAccountingInvariantError("过期回收时 reserved counter 已小于 evidence amount。")
        for line in lines:
            counter = counters[line.counter_id]
            counter.reserved_value -= line.amount
            counter.lock_version += 1
            counter.save(update_fields=["reserved_value", "lock_version", "updated_at"])
        reservation.status = UsageReservationStatus.EXPIRED
        reservation.closed_at = moment
        reservation.lock_version += 1
        reservation.save(update_fields=["status", "closed_at", "lock_version", "updated_at"])

    @staticmethod
    def _lock_reservation(reservation_id: UUID) -> UsageReservation:
        try:
            return UsageReservation.objects.select_for_update().get(pk=reservation_id)
        except UsageReservation.DoesNotExist as exc:
            raise QuotaControlError("UsageReservation 不存在。") from exc

    @staticmethod
    def _lock_lines_and_counters(
        reservation: UsageReservation,
    ) -> tuple[list[UsageReservationQuota], dict[UUID, UsageCounter]]:
        lines = list(
            UsageReservationQuota.objects.filter(reservation=reservation).order_by("counter_id", "id")
        )
        counter_ids = sorted({line.counter_id for line in lines}, key=str)
        counters = {
            counter.id: counter
            for counter in UsageCounter.objects.select_for_update()
            .filter(id__in=counter_ids)
            .order_by("id")
        }
        if len(counters) != len(counter_ids):
            raise QuotaAccountingInvariantError("Reservation evidence 引用的 Counter 不完整。")
        return lines, counters

    @staticmethod
    def _create_usage_record(
        *,
        reservation: UsageReservation,
        event_type: UsageEventType,
        moment: datetime,
    ) -> UsageRecord:
        record, _ = UsageRecord.objects.get_or_create(
            reservation=reservation,
            event_type=event_type,
            defaults={
                "tenant_id": reservation.tenant_id,
                "principal_id": reservation.principal_id,
                "metric_key": reservation.metric_key,
                "measurement_type": reservation.measurement_type,
                "unit": reservation.unit,
                "scope_type": reservation.scope_type,
                "workspace_id": reservation.workspace_id,
                "project_id": reservation.project_id,
                "environment_id": reservation.environment_id,
                "amount": reservation.amount,
                "recorded_at": moment,
            },
        )
        return record

    @staticmethod
    def _decision_for(reservation: UsageReservation) -> UsageReservationDecision:
        links = UsageReservationQuota.objects.filter(reservation=reservation).order_by("quota_id")
        evidence = tuple(
            QuotaDecisionEvidence(
                quota_id=link.quota_id,
                counter_id=link.counter_id,
                enforcement_mode=link.enforcement_mode_snapshot,
                limit_value=link.limit_snapshot,
                consumed_value=link.consumed_value_snapshot,
                reserved_value=link.reserved_value_snapshot,
                requested_amount=link.amount,
                projected_value=link.projected_value_snapshot,
                exceeded=link.exceeded_snapshot,
            )
            for link in links
        )
        return UsageReservationDecision(
            reservation_id=reservation.id,
            status=reservation.status,
            evidence=evidence,
        )

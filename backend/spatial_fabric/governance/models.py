"""Spatial Fabric Governance 领域模型。

Phase B2.1 只实现 Policy Core：

    PolicyDefinition → PolicyVersion → PolicyAttachment

Role/RoleAssignment 仍属于 IAM；资源具体类型仍属于各自领域模块。Governance 通过稳定 ResourceRef
值语义绑定跨模块资源，禁止为了方便创建万能 Resource 表或反向依赖所有资源模块。
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models

from spatial_fabric.common.models import ConcurrentModel, TimeStampedModel, UUID7Model
from spatial_fabric.iam.models import Group, Principal, Privilege, PrivilegeStatus, RoleDefinition


class PolicyDefinitionStatus(models.TextChoices):
    """PolicyDefinition 生命周期。"""

    ACTIVE = "ACTIVE", "启用"
    DEPRECATED = "DEPRECATED", "已弃用"


class PolicyVersionStatus(models.TextChoices):
    """PolicyVersion 生命周期。"""

    DRAFT = "DRAFT", "草稿"
    PUBLISHED = "PUBLISHED", "已发布"
    RETIRED = "RETIRED", "已退役"


class PolicyAttachmentStatus(models.TextChoices):
    """PolicyAttachment 生命周期。"""

    ACTIVE = "ACTIVE", "启用"
    REVOKED = "REVOKED", "已撤销"


class PolicySubjectType(models.TextChoices):
    """PolicyAttachment 的主体选择器形状。"""

    ALL = "ALL", "租户内全部主体"
    PRINCIPAL = "PRINCIPAL", "指定主体"
    GROUP = "GROUP", "指定组"
    ROLE = "ROLE", "指定角色"


class PolicyTargetType(models.TextChoices):
    """PolicyAttachment 的目标选择器形状。"""

    TENANT = "TENANT", "租户"
    WORKSPACE = "WORKSPACE", "工作空间"
    PROJECT = "PROJECT", "项目"
    ENVIRONMENT = "ENVIRONMENT", "环境"
    RESOURCE = "RESOURCE", "具体资源"


POLICY_EFFECTS = frozenset({"ALLOW", "DENY", "REQUIRE_APPROVAL"})


def validate_published_policy_spec(spec: object) -> None:
    """校验准备发布的第一版内部 Policy spec。

    这里只定义 Fabric 自己能可靠解释的最小结构。复杂 ABAC 以后通过独立 evaluator / OPA adapter
    扩展；未知 effect、action 或 condition 不能静默退化成 ALLOW。
    """

    if not isinstance(spec, dict):
        raise ValidationError({"spec": "Policy spec 必须是 JSON object。"})

    statements = spec.get("statements")
    if not isinstance(statements, list) or not statements:
        raise ValidationError({"spec": "已发布 Policy 必须包含非空 statements 数组。"})

    statement_ids: set[str] = set()
    action_keys: set[str] = set()
    errors: list[str] = []

    for index, statement in enumerate(statements):
        if not isinstance(statement, dict):
            errors.append(f"statements[{index}] 必须是 object。")
            continue

        sid = statement.get("sid")
        if not isinstance(sid, str) or not sid.strip():
            errors.append(f"statements[{index}].sid 必须是非空字符串。")
        elif sid in statement_ids:
            errors.append(f"statement sid 重复：{sid}。")
        else:
            statement_ids.add(sid)

        effect = statement.get("effect")
        if effect not in POLICY_EFFECTS:
            errors.append(
                f"statements[{index}].effect 必须是 ALLOW / DENY / REQUIRE_APPROVAL。"
            )

        actions = statement.get("actions")
        if (
            not isinstance(actions, list)
            or not actions
            or any(not isinstance(action, str) or not action for action in actions)
        ):
            errors.append(f"statements[{index}].actions 必须是非空 Privilege key 数组。")
        else:
            action_keys.update(actions)

        conditions = statement.get("conditions", {})
        if not isinstance(conditions, dict):
            errors.append(f"statements[{index}].conditions 必须是 object。")

        extra_keys = set(statement) - {"sid", "effect", "actions", "conditions"}
        if extra_keys:
            errors.append(
                f"statements[{index}] 包含 B2.1 尚未定义的字段：{sorted(extra_keys)}。"
            )

    if action_keys:
        active_action_keys = set(
            Privilege.objects.filter(
                key__in=action_keys,
                status=PrivilegeStatus.ACTIVE,
            ).values_list("key", flat=True)
        )
        unknown_actions = sorted(action_keys - active_action_keys)
        if unknown_actions:
            errors.append(f"存在未知或已弃用 Privilege：{unknown_actions}。")

    if errors:
        raise ValidationError({"spec": errors})


class PolicyDefinition(UUID7Model, TimeStampedModel, ConcurrentModel):
    """策略的长期身份；规则正文必须放在不可变 PolicyVersion。"""

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="policy_definitions",
        null=True,
        blank=True,
        verbose_name="所属租户",
        help_text="NULL 表示平台策略；非 NULL 表示 Tenant 自定义策略。",
    )
    key = models.CharField("策略键", max_length=160)
    name = models.CharField("名称", max_length=200)
    description = models.TextField("说明", blank=True)
    status = models.CharField(
        "状态",
        max_length=20,
        choices=PolicyDefinitionStatus.choices,
        default=PolicyDefinitionStatus.ACTIVE,
    )
    is_system = models.BooleanField("系统管理", default=False)
    created_by = models.ForeignKey(
        Principal,
        on_delete=models.PROTECT,
        related_name="created_policy_definitions",
        null=True,
        blank=True,
        verbose_name="创建主体",
    )

    class Meta:
        db_table = "sf_policy_definition"
        verbose_name = "策略定义"
        verbose_name_plural = "策略定义"
        constraints = [
            models.UniqueConstraint(
                fields=["key"],
                condition=models.Q(tenant__isnull=True),
                name="sf_policy_platform_key_uniq",
            ),
            models.UniqueConstraint(
                fields=["tenant", "key"],
                condition=models.Q(tenant__isnull=False),
                name="sf_policy_tenant_key_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "status"], name="sf_policy_tenant_status_idx"),
        ]

    def clean(self) -> None:
        """平台/Tenant Policy 的创建主体必须处于允许的租户边界。"""

        super().clean()
        creator = self.created_by if self.created_by_id else None
        if creator is None:
            return
        if self.tenant_id is None and creator.tenant_id is not None:
            raise ValidationError(
                {"created_by": "平台 Policy 只能由平台主体创建，或由系统 migration 创建。"}
            )
        if self.tenant_id is not None and creator.tenant_id not in (None, self.tenant_id):
            raise ValidationError({"created_by": "Tenant Policy 创建主体必须属于同一租户或平台。"})

    def __str__(self) -> str:
        return self.key


class PolicyVersion(UUID7Model, TimeStampedModel):
    """PolicyDefinition 的具体版本。

    DRAFT 可以持续编辑；一旦 PUBLISHED，业务代码必须通过 PolicyPublicationService 管理生命周期，
    不得原地覆盖 spec。第一阶段与 AssetVersion 相同：先由领域服务和测试强制不可变契约，必要时
    后续再增加数据库 trigger。
    """

    policy = models.ForeignKey(
        PolicyDefinition,
        on_delete=models.PROTECT,
        related_name="versions",
        verbose_name="策略定义",
    )
    version_seq = models.PositiveBigIntegerField("版本序号")
    schema_version = models.CharField("策略 Schema 版本", max_length=40, default="1")
    spec = models.JSONField("策略规范", default=dict)
    content_hash = models.CharField("内容指纹", max_length=128, blank=True)
    status = models.CharField(
        "状态",
        max_length=20,
        choices=PolicyVersionStatus.choices,
        default=PolicyVersionStatus.DRAFT,
    )
    created_by = models.ForeignKey(
        Principal,
        on_delete=models.PROTECT,
        related_name="created_policy_versions",
        verbose_name="创建主体",
    )
    published_by = models.ForeignKey(
        Principal,
        on_delete=models.PROTECT,
        related_name="published_policy_versions",
        null=True,
        blank=True,
        verbose_name="发布主体",
    )
    published_at = models.DateTimeField("发布时间", null=True, blank=True)

    class Meta:
        db_table = "sf_policy_version"
        verbose_name = "策略版本"
        verbose_name_plural = "策略版本"
        constraints = [
            models.UniqueConstraint(
                fields=["policy", "version_seq"],
                name="sf_policyver_seq_uniq",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status=PolicyVersionStatus.DRAFT,
                        published_by__isnull=True,
                        published_at__isnull=True,
                    )
                    | models.Q(
                        status__in=[
                            PolicyVersionStatus.PUBLISHED,
                            PolicyVersionStatus.RETIRED,
                        ],
                        published_by__isnull=False,
                        published_at__isnull=False,
                    )
                ),
                name="sf_policyver_publish_shape_ck",
            ),
        ]
        indexes = [
            models.Index(fields=["policy", "status"], name="sf_policyver_policy_status_idx"),
            models.Index(fields=["status", "published_at"], name="sf_policyver_publish_idx"),
        ]

    def clean(self) -> None:
        """校验 Actor tenant 与发布态 Policy spec。"""

        super().clean()
        errors: dict[str, object] = {}
        policy_tenant_id = self.policy.tenant_id

        creator = self.created_by
        if policy_tenant_id is None and creator.tenant_id is not None:
            errors["created_by"] = "平台 PolicyVersion 必须由平台主体创建。"
        elif policy_tenant_id is not None and creator.tenant_id not in (None, policy_tenant_id):
            errors["created_by"] = "PolicyVersion 创建主体必须属于策略所在租户或平台。"

        publisher = self.published_by if self.published_by_id else None
        if publisher is not None:
            if policy_tenant_id is None and publisher.tenant_id is not None:
                errors["published_by"] = "平台 PolicyVersion 必须由平台主体发布。"
            elif policy_tenant_id is not None and publisher.tenant_id not in (
                None,
                policy_tenant_id,
            ):
                errors["published_by"] = "PolicyVersion 发布主体必须属于策略所在租户或平台。"

        if self.status in (PolicyVersionStatus.PUBLISHED, PolicyVersionStatus.RETIRED):
            if not self.content_hash:
                errors["content_hash"] = "已发布/退役 PolicyVersion 必须保存内容指纹。"
            try:
                validate_published_policy_spec(self.spec)
            except ValidationError as exc:
                errors["spec"] = exc.message_dict.get("spec", exc.messages)

        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return f"{self.policy.key} v{self.version_seq}"


class PolicyAttachment(UUID7Model, TimeStampedModel, ConcurrentModel):
    """把一个具体 PolicyVersion 绑定到主体选择器与管理 Scope/ResourceRef。"""

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="policy_attachments",
        verbose_name="所属租户",
    )
    policy_version = models.ForeignKey(
        PolicyVersion,
        on_delete=models.PROTECT,
        related_name="attachments",
        verbose_name="策略版本",
    )
    subject_type = models.CharField(
        "主体类型",
        max_length=16,
        choices=PolicySubjectType.choices,
        default=PolicySubjectType.ALL,
    )
    principal = models.ForeignKey(
        Principal,
        on_delete=models.PROTECT,
        related_name="policy_attachments",
        null=True,
        blank=True,
        verbose_name="指定主体",
    )
    group = models.ForeignKey(
        Group,
        on_delete=models.PROTECT,
        related_name="policy_attachments",
        null=True,
        blank=True,
        verbose_name="指定组",
    )
    role = models.ForeignKey(
        RoleDefinition,
        on_delete=models.PROTECT,
        related_name="policy_attachments",
        null=True,
        blank=True,
        verbose_name="指定角色",
    )
    target_type = models.CharField("目标类型", max_length=16, choices=PolicyTargetType.choices)
    workspace = models.ForeignKey(
        "tenancy.Workspace",
        on_delete=models.PROTECT,
        related_name="policy_attachments",
        null=True,
        blank=True,
        verbose_name="工作空间目标",
    )
    project = models.ForeignKey(
        "tenancy.Project",
        on_delete=models.PROTECT,
        related_name="policy_attachments",
        null=True,
        blank=True,
        verbose_name="项目目标",
    )
    environment = models.ForeignKey(
        "tenancy.Environment",
        on_delete=models.PROTECT,
        related_name="policy_attachments",
        null=True,
        blank=True,
        verbose_name="环境目标",
    )
    resource_kind = models.CharField(
        "资源类型键",
        max_length=160,
        blank=True,
        default="",
        help_text="跨模块稳定 namespaced key；存在性由 ResourceResolver 校验，不是万能资源表。",
    )
    resource_id = models.UUIDField("资源 ID", null=True, blank=True)
    priority = models.IntegerField(
        "优先级",
        default=100,
        help_text="数值仅用于同层稳定排序；DENY/REQUIRE_APPROVAL/ALLOW 的安全优先级由 evaluator 定义。",
    )
    status = models.CharField(
        "状态",
        max_length=16,
        choices=PolicyAttachmentStatus.choices,
        default=PolicyAttachmentStatus.ACTIVE,
    )
    valid_from = models.DateTimeField("生效时间", null=True, blank=True)
    valid_until = models.DateTimeField("失效时间", null=True, blank=True)
    attached_by = models.ForeignKey(
        Principal,
        on_delete=models.PROTECT,
        related_name="created_policy_attachments",
        verbose_name="绑定主体",
    )

    class Meta:
        db_table = "sf_policy_attachment"
        verbose_name = "策略绑定"
        verbose_name_plural = "策略绑定"
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(
                        subject_type=PolicySubjectType.ALL,
                        principal__isnull=True,
                        group__isnull=True,
                        role__isnull=True,
                    )
                    | models.Q(
                        subject_type=PolicySubjectType.PRINCIPAL,
                        principal__isnull=False,
                        group__isnull=True,
                        role__isnull=True,
                    )
                    | models.Q(
                        subject_type=PolicySubjectType.GROUP,
                        principal__isnull=True,
                        group__isnull=False,
                        role__isnull=True,
                    )
                    | models.Q(
                        subject_type=PolicySubjectType.ROLE,
                        principal__isnull=True,
                        group__isnull=True,
                        role__isnull=False,
                    )
                ),
                name="sf_policyattach_subject_shape_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        target_type=PolicyTargetType.TENANT,
                        workspace__isnull=True,
                        project__isnull=True,
                        environment__isnull=True,
                        resource_kind="",
                        resource_id__isnull=True,
                    )
                    | models.Q(
                        target_type=PolicyTargetType.WORKSPACE,
                        workspace__isnull=False,
                        project__isnull=True,
                        environment__isnull=True,
                        resource_kind="",
                        resource_id__isnull=True,
                    )
                    | models.Q(
                        target_type=PolicyTargetType.PROJECT,
                        workspace__isnull=True,
                        project__isnull=False,
                        environment__isnull=True,
                        resource_kind="",
                        resource_id__isnull=True,
                    )
                    | models.Q(
                        target_type=PolicyTargetType.ENVIRONMENT,
                        workspace__isnull=True,
                        project__isnull=True,
                        environment__isnull=False,
                        resource_kind="",
                        resource_id__isnull=True,
                    )
                    | (
                        models.Q(
                            target_type=PolicyTargetType.RESOURCE,
                            workspace__isnull=True,
                            project__isnull=True,
                            environment__isnull=True,
                            resource_id__isnull=False,
                        )
                        & ~models.Q(resource_kind="")
                    )
                ),
                name="sf_policyattach_target_shape_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(valid_until__isnull=True)
                    | models.Q(valid_from__isnull=True)
                    | models.Q(valid_until__gt=models.F("valid_from"))
                ),
                name="sf_policyattach_valid_window_ck",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "status"], name="sf_pattach_tenant_status_idx"),
            models.Index(fields=["principal", "status"], name="sf_pattach_princ_status_idx"),
            models.Index(fields=["group", "status"], name="sf_pattach_group_status_idx"),
            models.Index(fields=["role", "status"], name="sf_pattach_role_status_idx"),
            models.Index(fields=["workspace", "status"], name="sf_pattach_ws_status_idx"),
            models.Index(fields=["project", "status"], name="sf_pattach_proj_status_idx"),
            models.Index(fields=["environment", "status"], name="sf_pattach_env_status_idx"),
            models.Index(
                fields=["tenant", "resource_kind", "resource_id", "status"],
                name="sf_pattach_resource_idx",
            ),
        ]

    def clean(self) -> None:
        """校验 DB 普通 FK/shape CHECK 无法表达的 Tenant 与 lifecycle 不变量。"""

        super().clean()
        errors: dict[str, str] = {}

        principal = self.principal if self.principal_id else None
        group = self.group if self.group_id else None
        role = self.role if self.role_id else None
        workspace = self.workspace if self.workspace_id else None
        project = self.project if self.project_id else None
        environment = self.environment if self.environment_id else None

        if self.policy_version_id:
            policy_tenant_id = self.policy_version.policy.tenant_id
            if policy_tenant_id not in (None, self.tenant_id):
                errors["policy_version"] = "Tenant PolicyVersion 不能跨租户绑定。"
            if self.policy_version.status == PolicyVersionStatus.DRAFT:
                errors["policy_version"] = "DRAFT PolicyVersion 不能进入有效 PolicyAttachment。"

        if principal is not None and principal.tenant_id != self.tenant_id:
            errors["principal"] = "PolicyAttachment Principal 必须属于同一租户。"
        if group is not None and group.tenant_id != self.tenant_id:
            errors["group"] = "PolicyAttachment Group 必须属于同一租户。"
        if role is not None and role.tenant_id not in (None, self.tenant_id):
            errors["role"] = "Tenant Role 只能在其所属租户内作为 Policy subject。"

        if workspace is not None and workspace.tenant_id != self.tenant_id:
            errors["workspace"] = "Policy target Workspace 必须属于同一租户。"
        if project is not None and project.tenant_id != self.tenant_id:
            errors["project"] = "Policy target Project 必须属于同一租户。"
        if environment is not None and environment.tenant_id != self.tenant_id:
            errors["environment"] = "Policy target Environment 必须属于同一租户。"

        if self.attached_by_id and self.attached_by.tenant_id not in (None, self.tenant_id):
            errors["attached_by"] = "Policy 绑定主体必须属于同一租户或平台。"

        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return f"{self.policy_version} → {self.target_type}"

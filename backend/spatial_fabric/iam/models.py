"""Spatial Fabric 身份、主体与第一阶段授权核心模型。

本模块故意把认证、主体、角色和治理概念拆开：

    Account ≠ Principal ≠ Group ≠ Role ≠ RoleAssignment

- Account：Django 登录账户，只解决“如何登录”；
- Principal：Fabric 统一权限主体，人、服务账户、Agent、外部应用都可以成为 Principal；
- Group：Tenant 内的权限主体集合，不等于 OrgUnit/Department；
- Privilege：稳定动作词汇，例如 execute/download/share；
- RoleDefinition：一组 Privilege 的可复用定义；
- RoleAssignment：Principal/Group 在 Tenant/Workspace/Project/Environment 管理范围内拥有某 Role。

Phase B1 只建立 RBAC + Scope Inheritance 的稳定输入，不在这里实现完整 ABAC/ReBAC/Entitlement/Quota。
显式 DENY、资源级 ShareGrant、Entitlement、Quota、JIT/PIM 等属于 Phase B2。
"""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

from spatial_fabric.common.ids import uuid7
from spatial_fabric.common.models import ConcurrentModel, TimeStampedModel, UUID7Model


class AccountManager(BaseUserManager["Account"]):
    use_in_migrations = True

    def create_user(self, email: str, password: str | None = None, **extra_fields: object) -> Account:
        if not email:
            raise ValueError("必须提供邮箱地址。")
        email = self.normalize_email(email)
        account = self.model(email=email, **extra_fields)
        account.set_password(password)
        account.save(using=self._db)
        return account

    def create_superuser(
        self, email: str, password: str | None = None, **extra_fields: object
    ) -> Account:
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("超级管理员必须设置 is_staff=True。")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("超级管理员必须设置 is_superuser=True。")
        return self.create_user(email=email, password=password, **extra_fields)


class Account(AbstractUser):
    """Django 登录账户；不要向这里堆 Tenant、Role、Quota、Agent 等领域字段。

    ``is_staff`` / ``is_superuser`` 只控制 Django Admin 技术管理能力，不能自动转换为 Fabric
    的 tenant/project/resource 权限。业务 API 最终仍必须经过 Fabric AuthorizationService。
    """

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    # Email-only AbstractUser 是 Django 支持的模式；django-stubs 仍保留父类 username: str。
    username = None  # type: ignore[assignment]
    email = models.EmailField("邮箱", unique=True)
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: ClassVar[list[str]] = []
    # AbstractUser 将 objects 声明为 UserManager；本项目有意使用 email-only BaseUserManager。
    objects: ClassVar[AccountManager] = AccountManager()  # type: ignore[assignment]

    def __str__(self) -> str:
        return self.email


class PrincipalType(models.TextChoices):
    HUMAN_USER = "HUMAN_USER", "人员"
    SERVICE_ACCOUNT = "SERVICE_ACCOUNT", "服务账户"
    AGENT = "AGENT", "智能体"
    EXTERNAL_APPLICATION = "EXTERNAL_APPLICATION", "外部应用"
    FEDERATED = "FEDERATED", "联合身份"


class PrincipalStatus(models.TextChoices):
    INVITED = "INVITED", "待激活"
    ACTIVE = "ACTIVE", "正常"
    SUSPENDED = "SUSPENDED", "已暂停"
    LOCKED = "LOCKED", "已锁定"
    DEPROVISIONED = "DEPROVISIONED", "已注销"


class Principal(UUID7Model, TimeStampedModel, ConcurrentModel):
    """Fabric 所有授权判断的统一主体。tenant 允许为空以容纳平台级系统主体。"""

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="principals",
        null=True,
        blank=True,
        verbose_name="所属租户",
    )
    account = models.OneToOneField(
        Account,
        on_delete=models.PROTECT,
        related_name="principal",
        null=True,
        blank=True,
        verbose_name="登录账户",
    )
    principal_type = models.CharField("主体类型", max_length=32, choices=PrincipalType.choices)
    status = models.CharField(
        "状态", max_length=24, choices=PrincipalStatus.choices, default=PrincipalStatus.ACTIVE
    )
    display_name = models.CharField("显示名称", max_length=200)
    description = models.TextField("说明", blank=True)

    class Meta:
        db_table = "sf_principal"
        verbose_name = "权限主体"
        verbose_name_plural = "权限主体"
        indexes = [
            models.Index(fields=["tenant", "status"], name="sf_princ_tenant_status_idx"),
            models.Index(fields=["tenant", "principal_type"], name="sf_princ_tenant_type_idx"),
        ]

    def __str__(self) -> str:
        return self.display_name


# Privilege key 是平台级稳定 Contract。允许 future package 使用 namespaced key，例如
# ``geophysics.model.validate``；核心权限继续保留 discover/read/execute 等简洁键。
privilege_key_validator = RegexValidator(
    regex=r"^[a-z][a-z0-9_.:-]{0,79}$",
    message="Privilege key 必须以小写字母开头，且只能包含小写字母、数字、._:-。",
)


class PrivilegeCategory(models.TextChoices):
    READ = "READ", "读取"
    WRITE = "WRITE", "写入"
    EXECUTE = "EXECUTE", "执行"
    GOVERNANCE = "GOVERNANCE", "治理"
    ADMIN = "ADMIN", "管理"
    SECRET = "SECRET", "密钥"


class PrivilegeRiskLevel(models.TextChoices):
    LOW = "LOW", "低"
    MEDIUM = "MEDIUM", "中"
    HIGH = "HIGH", "高"
    CRITICAL = "CRITICAL", "关键"


class PrivilegeStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "正常"
    DEPRECATED = "DEPRECATED", "已弃用"


class Privilege(UUID7Model, TimeStampedModel):
    """Fabric 可授权动作词汇。

    Privilege 是系统参考数据，而不是用户角色。把它独立建表可以让 RolePrivilege 使用正式 FK，并让
    风险等级、弃用、插件注册和审计拥有稳定身份。普通 Tenant 用户不应直接修改 system-managed
    Privilege。
    """

    key = models.CharField("权限键", max_length=80, unique=True, validators=[privilege_key_validator])
    name = models.CharField("权限名称", max_length=120)
    category = models.CharField("类别", max_length=20, choices=PrivilegeCategory.choices)
    risk_level = models.CharField(
        "风险等级",
        max_length=16,
        choices=PrivilegeRiskLevel.choices,
        default=PrivilegeRiskLevel.LOW,
    )
    description = models.TextField("说明", blank=True)
    status = models.CharField(
        "状态", max_length=16, choices=PrivilegeStatus.choices, default=PrivilegeStatus.ACTIVE
    )
    system_managed = models.BooleanField(
        "系统管理",
        default=True,
        help_text="系统管理的 Privilege 只能通过受控 migration/package registration 变更。",
    )

    class Meta:
        db_table = "sf_privilege"
        verbose_name = "权限动作"
        verbose_name_plural = "权限动作"
        indexes = [models.Index(fields=["status", "category"], name="sf_priv_status_cat_idx")]

    def __str__(self) -> str:
        return self.key


class GroupType(models.TextChoices):
    SECURITY = "SECURITY", "安全组"
    COLLABORATION = "COLLABORATION", "协作组"


class GroupStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "正常"
    SUSPENDED = "SUSPENDED", "已暂停"
    ARCHIVED = "ARCHIVED", "已归档"


class Group(UUID7Model, TimeStampedModel, ConcurrentModel):
    """Tenant 内可接收 RoleAssignment 的 Principal 集合。

    Group 是权限/协作概念，**不是组织部门**。OrgUnit/Department 的生命周期、组织汇报线与 Group
    的权限集合语义不同，禁止为了少一张表把两者合并。
    """

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="principal_groups",
        verbose_name="所属租户",
    )
    name = models.CharField("组名称", max_length=200)
    slug = models.SlugField("组标识", max_length=100)
    group_type = models.CharField(
        "组类型", max_length=24, choices=GroupType.choices, default=GroupType.SECURITY
    )
    status = models.CharField(
        "状态", max_length=16, choices=GroupStatus.choices, default=GroupStatus.ACTIVE
    )
    description = models.TextField("说明", blank=True)
    created_by = models.ForeignKey(
        Principal,
        on_delete=models.PROTECT,
        related_name="created_principal_groups",
        verbose_name="创建主体",
    )

    class Meta:
        db_table = "sf_group"
        verbose_name = "权限组"
        verbose_name_plural = "权限组"
        constraints = [
            models.UniqueConstraint(fields=["tenant", "slug"], name="sf_group_tenant_slug_uniq")
        ]
        indexes = [
            models.Index(fields=["tenant", "status"], name="sf_group_tenant_status_idx"),
            models.Index(fields=["tenant", "group_type"], name="sf_group_tenant_type_idx"),
        ]

    def clean(self) -> None:
        """防止 Tenant Group 被其他 Tenant 的主体创建。"""

        super().clean()
        if self.created_by_id and self.created_by.tenant_id not in (None, self.tenant_id):
            raise ValidationError({"created_by": "Group 创建主体必须是平台主体或属于同一租户。"})

    def __str__(self) -> str:
        return self.name


class MembershipStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "正常"
    SUSPENDED = "SUSPENDED", "已暂停"


class GroupMembership(UUID7Model, TimeStampedModel, ConcurrentModel):
    """Principal 在 Group 中的成员关系。

    一个 Group Role 不会复制成每个成员的 Direct RoleAssignment；授权计算时动态合并有效 Group
    membership。这样 Group 权限修改保持 O(1) 关系语义，而不是复制成大量难以审计的授权记录。
    """

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="group_memberships",
        verbose_name="所属租户",
    )
    group = models.ForeignKey(
        Group,
        on_delete=models.PROTECT,
        related_name="memberships",
        verbose_name="权限组",
    )
    principal = models.ForeignKey(
        Principal,
        on_delete=models.PROTECT,
        related_name="group_memberships",
        verbose_name="成员主体",
    )
    status = models.CharField(
        "状态", max_length=16, choices=MembershipStatus.choices, default=MembershipStatus.ACTIVE
    )
    valid_from = models.DateTimeField("生效时间", null=True, blank=True)
    valid_until = models.DateTimeField("失效时间", null=True, blank=True)
    added_by = models.ForeignKey(
        Principal,
        on_delete=models.PROTECT,
        related_name="added_group_memberships",
        verbose_name="添加主体",
    )

    class Meta:
        db_table = "sf_group_membership"
        verbose_name = "组成员关系"
        verbose_name_plural = "组成员关系"
        constraints = [
            models.UniqueConstraint(
                fields=["group", "principal"], name="sf_groupmem_group_princ_uniq"
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(valid_until__isnull=True)
                    | models.Q(valid_from__isnull=True)
                    | models.Q(valid_until__gt=models.F("valid_from"))
                ),
                name="sf_groupmem_valid_window_ck",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "principal", "status"], name="sf_gmem_princ_status_idx"),
            models.Index(fields=["tenant", "group", "status"], name="sf_gmem_group_status_idx"),
        ]

    def clean(self) -> None:
        """GroupMembership 第一版严格禁止跨租户成员关系。"""

        super().clean()
        errors: dict[str, str] = {}
        if self.group_id and self.group.tenant_id != self.tenant_id:
            errors["group"] = "GroupMembership.group 必须属于同一租户。"
        if self.principal_id and self.principal.tenant_id != self.tenant_id:
            errors["principal"] = "Group 成员 Principal 必须属于同一租户；平台主体不得隐式加入租户组。"
        if self.added_by_id and self.added_by.tenant_id not in (None, self.tenant_id):
            errors["added_by"] = "添加成员的主体必须是平台主体或属于同一租户。"
        if self.valid_from and self.valid_until and self.valid_until <= self.valid_from:
            errors["valid_until"] = "失效时间必须晚于生效时间。"
        if errors:
            raise ValidationError(errors)

    def is_currently_effective(self, *, at: datetime | None = None) -> bool:
        """判断成员关系在某一时刻是否有效；授权查询仍应在数据库层过滤有效窗口。"""

        moment = at or timezone.now()
        if self.status != MembershipStatus.ACTIVE:
            return False
        if self.valid_from and moment < self.valid_from:
            return False
        return not (self.valid_until and moment >= self.valid_until)


class RoleStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "正常"
    DEPRECATED = "DEPRECATED", "已弃用"


class RoleScopeType(models.TextChoices):
    TENANT = "TENANT", "租户"
    WORKSPACE = "WORKSPACE", "工作空间"
    PROJECT = "PROJECT", "项目"
    ENVIRONMENT = "ENVIRONMENT", "环境"


class RoleDefinition(UUID7Model, TimeStampedModel, ConcurrentModel):
    """可复用的 Privilege 集合定义。

    ``tenant=NULL`` 表示平台提供的角色模板/系统角色；非空则表示 Tenant 自定义 Role。Role 本身
    不绑定具体用户和资源，真正的“谁在什么范围拥有该角色”由 RoleAssignment 表达。
    """

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="role_definitions",
        null=True,
        blank=True,
        verbose_name="所属租户",
    )
    key = models.CharField("角色键", max_length=80, validators=[privilege_key_validator])
    name = models.CharField("角色名称", max_length=160)
    description = models.TextField("说明", blank=True)
    status = models.CharField(
        "状态", max_length=16, choices=RoleStatus.choices, default=RoleStatus.ACTIVE
    )
    is_system = models.BooleanField("系统角色", default=False)
    is_assignable = models.BooleanField("允许分配", default=True)
    allowed_scope_types = models.JSONField(
        "允许的作用域类型",
        default=list,
        blank=True,
        help_text="空列表表示不额外限制；非空时只能包含 TENANT/WORKSPACE/PROJECT/ENVIRONMENT。",
    )
    created_by = models.ForeignKey(
        Principal,
        on_delete=models.PROTECT,
        related_name="created_role_definitions",
        null=True,
        blank=True,
        verbose_name="创建主体",
    )

    class Meta:
        db_table = "sf_role_definition"
        verbose_name = "角色定义"
        verbose_name_plural = "角色定义"
        constraints = [
            models.UniqueConstraint(
                fields=["key"],
                condition=models.Q(tenant__isnull=True),
                name="sf_role_platform_key_uniq",
            ),
            models.UniqueConstraint(
                fields=["tenant", "key"],
                condition=models.Q(tenant__isnull=False),
                name="sf_role_tenant_key_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "status"], name="sf_role_tenant_status_idx")
        ]

    def clean(self) -> None:
        """校验角色归属与可分配作用域，不把 JSON 当成无约束垃圾桶。"""

        super().clean()
        errors: dict[str, str] = {}
        creator = self.created_by if self.created_by_id else None
        if creator is not None:
            if self.tenant_id is None and creator.tenant_id is not None:
                errors["created_by"] = "平台级 Role 只能由平台主体创建，或留空交由系统 migration 创建。"
            elif self.tenant_id and creator.tenant_id not in (None, self.tenant_id):
                errors["created_by"] = "Tenant Role 创建主体必须是平台主体或属于同一租户。"

        if not isinstance(self.allowed_scope_types, list):
            errors["allowed_scope_types"] = "allowed_scope_types 必须是字符串列表。"
        else:
            allowed_values = set(RoleScopeType.values)
            invalid = [item for item in self.allowed_scope_types if item not in allowed_values]
            if invalid:
                errors["allowed_scope_types"] = f"存在不支持的作用域类型：{invalid}。"

        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return self.key


class RolePrivilege(UUID7Model, TimeStampedModel):
    """Role 到 Privilege 的 GRANT 关系。

    B1 Role 只表达允许能力。显式 DENY 属于 B2 PolicyDefinition，避免 Role 同时承担规则引擎职责。
    """

    role = models.ForeignKey(
        RoleDefinition,
        on_delete=models.PROTECT,
        related_name="role_privileges",
        verbose_name="角色",
    )
    privilege = models.ForeignKey(
        Privilege,
        on_delete=models.PROTECT,
        related_name="role_privileges",
        verbose_name="权限动作",
    )

    class Meta:
        db_table = "sf_role_privilege"
        verbose_name = "角色权限"
        verbose_name_plural = "角色权限"
        constraints = [
            models.UniqueConstraint(fields=["role", "privilege"], name="sf_rolepriv_pair_uniq")
        ]
        indexes = [models.Index(fields=["role"], name="sf_rolepriv_role_idx")]

    def __str__(self) -> str:
        return f"{self.role.key}:{self.privilege.key}"


class RoleAssignmentStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "正常"
    REVOKED = "REVOKED", "已撤销"


class RoleAssignment(UUID7Model, TimeStampedModel, ConcurrentModel):
    """Principal/Group 在一个管理层级 Scope 上拥有某个 Role 的授权事实。

    RoleAssignment 只负责 Tenant/Workspace/Project/Environment 的层级 RBAC。单个 Dataset/Map/
    ModelPack 等资源的精确分享后续由 ShareGrant 负责，因此 IAM 不需要反向 FK Asset，也避免
    ``iam ↔ assets`` 迁移依赖环。
    """

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="role_assignments",
        verbose_name="所属租户",
    )
    principal = models.ForeignKey(
        Principal,
        on_delete=models.PROTECT,
        related_name="direct_role_assignments",
        null=True,
        blank=True,
        verbose_name="被授权主体",
    )
    group = models.ForeignKey(
        Group,
        on_delete=models.PROTECT,
        related_name="role_assignments",
        null=True,
        blank=True,
        verbose_name="被授权组",
    )
    role = models.ForeignKey(
        RoleDefinition,
        on_delete=models.PROTECT,
        related_name="assignments",
        verbose_name="角色",
    )
    scope_type = models.CharField("作用域类型", max_length=16, choices=RoleScopeType.choices)
    workspace = models.ForeignKey(
        "tenancy.Workspace",
        on_delete=models.PROTECT,
        related_name="role_assignments",
        null=True,
        blank=True,
        verbose_name="工作空间作用域",
    )
    project = models.ForeignKey(
        "tenancy.Project",
        on_delete=models.PROTECT,
        related_name="role_assignments",
        null=True,
        blank=True,
        verbose_name="项目作用域",
    )
    environment = models.ForeignKey(
        "tenancy.Environment",
        on_delete=models.PROTECT,
        related_name="role_assignments",
        null=True,
        blank=True,
        verbose_name="环境作用域",
    )
    status = models.CharField(
        "状态",
        max_length=16,
        choices=RoleAssignmentStatus.choices,
        default=RoleAssignmentStatus.ACTIVE,
    )
    valid_from = models.DateTimeField("生效时间", null=True, blank=True)
    valid_until = models.DateTimeField("失效时间", null=True, blank=True)
    conditions = models.JSONField(
        "附加条件",
        default=dict,
        blank=True,
        help_text="B1 仅允许 schema-validated 的低复杂度条件；完整 ABAC 留给 B2 Policy。",
    )
    granted_by = models.ForeignKey(
        Principal,
        on_delete=models.PROTECT,
        related_name="granted_role_assignments",
        verbose_name="授权主体",
    )

    class Meta:
        db_table = "sf_role_assignment"
        verbose_name = "角色分配"
        verbose_name_plural = "角色分配"
        constraints = [
            # 被授权主体必须恰好是 Principal 或 Group 之一。
            models.CheckConstraint(
                condition=(
                    models.Q(principal__isnull=False, group__isnull=True)
                    | models.Q(principal__isnull=True, group__isnull=False)
                ),
                name="sf_roleassign_subject_xor_ck",
            ),
            # 每种 scope_type 只允许对应的一类 scope FK 存在，防止同一行同时表达多个层级。
            models.CheckConstraint(
                condition=(
                    models.Q(
                        scope_type=RoleScopeType.TENANT,
                        workspace__isnull=True,
                        project__isnull=True,
                        environment__isnull=True,
                    )
                    | models.Q(
                        scope_type=RoleScopeType.WORKSPACE,
                        workspace__isnull=False,
                        project__isnull=True,
                        environment__isnull=True,
                    )
                    | models.Q(
                        scope_type=RoleScopeType.PROJECT,
                        workspace__isnull=True,
                        project__isnull=False,
                        environment__isnull=True,
                    )
                    | models.Q(
                        scope_type=RoleScopeType.ENVIRONMENT,
                        workspace__isnull=True,
                        project__isnull=True,
                        environment__isnull=False,
                    )
                ),
                name="sf_roleassign_scope_shape_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(valid_until__isnull=True)
                    | models.Q(valid_from__isnull=True)
                    | models.Q(valid_until__gt=models.F("valid_from"))
                ),
                name="sf_roleassign_valid_window_ck",
            ),
            # PostgreSQL 17 支持 NULLS NOT DISTINCT；让同一 subject/role/scope 不因 nullable FK
            # 而出现重复授权行。撤销/重新授予应复用这个 Aggregate 并留下 Audit，而不是复制记录。
            models.UniqueConstraint(
                fields=[
                    "tenant",
                    "principal",
                    "group",
                    "role",
                    "scope_type",
                    "workspace",
                    "project",
                    "environment",
                ],
                name="sf_roleassign_identity_uniq",
                nulls_distinct=False,
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "principal", "status"], name="sf_rassign_princ_status_idx"
            ),
            models.Index(fields=["tenant", "group", "status"], name="sf_rassign_group_status_idx"),
            models.Index(fields=["workspace", "status"], name="sf_rassign_ws_status_idx"),
            models.Index(fields=["project", "status"], name="sf_rassign_proj_status_idx"),
            models.Index(fields=["environment", "status"], name="sf_rassign_env_status_idx"),
        ]

    def clean(self) -> None:
        """校验数据库普通 FK 无法表达的 Tenant/Role/Scope 领域不变量。"""

        super().clean()
        errors: dict[str, str] = {}
        principal = self.principal if self.principal_id else None
        group = self.group if self.group_id else None
        role = self.role if self.role_id else None
        workspace = self.workspace if self.workspace_id else None
        project = self.project if self.project_id else None
        environment = self.environment if self.environment_id else None
        granted_by = self.granted_by if self.granted_by_id else None

        # subject XOR 同时在数据库 CHECK 和应用层做，给 API/Service 返回更可读的错误。
        if bool(self.principal_id) == bool(self.group_id):
            errors["principal"] = "RoleAssignment 必须且只能指定 principal 或 group 之一。"

        if principal is not None and principal.tenant_id != self.tenant_id:
            errors["principal"] = "被授权 Principal 必须属于同一租户。"
        if group is not None and group.tenant_id != self.tenant_id:
            errors["group"] = "被授权 Group 必须属于同一租户。"

        # 平台 Role（tenant=NULL）可被租户使用；Tenant 自定义 Role 只能在自己的 Tenant 内使用。
        if role is not None and role.tenant_id not in (None, self.tenant_id):
            errors["role"] = "Tenant 自定义 Role 只能分配在其所属租户内。"
        if role is not None and not role.is_assignable:
            errors["role"] = "该 Role 已标记为不可分配。"
        if role is not None and role.allowed_scope_types:
            if self.scope_type not in role.allowed_scope_types:
                errors["scope_type"] = "当前 Role 不允许分配到该作用域类型。"

        # 对 Scope 做应用层一致性校验；数据库 CHECK 只负责形状，无法跨表比较 tenant_id。
        if self.scope_type == RoleScopeType.TENANT:
            if self.workspace_id or self.project_id or self.environment_id:
                errors["scope_type"] = "TENANT scope 不能同时指定 Workspace/Project/Environment。"
        elif self.scope_type == RoleScopeType.WORKSPACE:
            if workspace is None or self.project_id or self.environment_id:
                errors["workspace"] = "WORKSPACE scope 必须且只能指定 Workspace。"
            elif workspace.tenant_id != self.tenant_id:
                errors["workspace"] = "Workspace 必须属于 RoleAssignment.tenant。"
        elif self.scope_type == RoleScopeType.PROJECT:
            if self.workspace_id or project is None or self.environment_id:
                errors["project"] = "PROJECT scope 必须且只能指定 Project。"
            elif project.tenant_id != self.tenant_id:
                errors["project"] = "Project 必须属于 RoleAssignment.tenant。"
        elif self.scope_type == RoleScopeType.ENVIRONMENT:
            if self.workspace_id or self.project_id or environment is None:
                errors["environment"] = "ENVIRONMENT scope 必须且只能指定 Environment。"
            elif environment.tenant_id != self.tenant_id:
                errors["environment"] = "Environment 必须属于 RoleAssignment.tenant。"
        else:
            errors["scope_type"] = "未知的 RoleAssignment scope_type。"

        if granted_by is not None and granted_by.tenant_id not in (None, self.tenant_id):
            errors["granted_by"] = "授权主体必须是平台主体或属于同一租户。"
        if self.valid_from and self.valid_until and self.valid_until <= self.valid_from:
            errors["valid_until"] = "失效时间必须晚于生效时间。"
        if not isinstance(self.conditions, dict):
            errors["conditions"] = "RoleAssignment.conditions 必须是对象结构。"

        if errors:
            raise ValidationError(errors)

    def is_currently_effective(self, *, at: datetime | None = None) -> bool:
        """判断 RoleAssignment 在某时刻是否有效，不包含 B2 Policy/Entitlement/Quota 计算。"""

        moment = at or timezone.now()
        if self.status != RoleAssignmentStatus.ACTIVE:
            return False
        if self.valid_from and moment < self.valid_from:
            return False
        return not (self.valid_until and moment >= self.valid_until)

    def __str__(self) -> str:
        subject = self.principal or self.group
        return f"{subject} → {self.role.key} @ {self.scope_type}"

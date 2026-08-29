"""统一资产注册中心（Asset Registry）的第一批核心模型。

本模块落实 Spatial Fabric 最重要的四层边界：

    Asset ≠ AssetVersion ≠ Artifact ≠ Distribution

- Asset：长期逻辑身份，例如“南京道路网络”；
- AssetVersion：某个不可变版本的语义/配置快照；
- Artifact：真实字节制品，例如 COG、GeoParquet、模型镜像描述文件；
- Distribution：某个版本的可消费形态，例如 PostGIS、COG、PMTiles、WMS。

四者属于同一领域，但故意设计为独立 Aggregate Root，避免形成一个高争用的巨大事务聚合。
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from spatial_fabric.common.models import ConcurrentModel, TimeStampedModel, UUID7Model


class AssetStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "正常"
    DEPRECATED = "DEPRECATED", "已弃用"
    ARCHIVED = "ARCHIVED", "已归档"
    TRASHED = "TRASHED", "回收站"
    BLOCKED = "BLOCKED", "已阻止"


class ResourceClassification(models.TextChoices):
    PUBLIC = "PUBLIC", "公开"
    INTERNAL = "INTERNAL", "内部"
    CONFIDENTIAL = "CONFIDENTIAL", "机密"
    RESTRICTED = "RESTRICTED", "严格受限"


class AssetVersionStatus(models.TextChoices):
    DRAFT = "DRAFT", "草稿"
    VALIDATING = "VALIDATING", "验证中"
    READY = "READY", "就绪"
    PUBLISHED = "PUBLISHED", "已发布"
    DEPRECATED = "DEPRECATED", "已弃用"
    ARCHIVED = "ARCHIVED", "已归档"
    QUARANTINED = "QUARANTINED", "隔离"
    REJECTED = "REJECTED", "拒绝发布"


class ArtifactIntegrityStatus(models.TextChoices):
    PENDING = "PENDING", "待验证"
    VERIFYING = "VERIFYING", "验证中"
    AVAILABLE = "AVAILABLE", "可用"
    QUARANTINED = "QUARANTINED", "隔离"
    CORRUPTED = "CORRUPTED", "损坏"
    DELETING = "DELETING", "删除中"
    DELETED = "DELETED", "已删除"


class DistributionStatus(models.TextChoices):
    PENDING = "PENDING", "待生成"
    AVAILABLE = "AVAILABLE", "可用"
    DEGRADED = "DEGRADED", "降级"
    UNAVAILABLE = "UNAVAILABLE", "不可用"
    RETIRED = "RETIRED", "已退役"


class DistributionType(models.TextChoices):
    FILE = "FILE", "文件"
    DATABASE = "DATABASE", "数据库"
    OBJECT = "OBJECT", "对象存储"
    VECTOR = "VECTOR", "矢量"
    RASTER = "RASTER", "栅格"
    MULTIDIMENSIONAL = "MULTIDIMENSIONAL", "多维科学数据"
    TILE_ARCHIVE = "TILE_ARCHIVE", "瓦片归档"
    WEB_SERVICE = "WEB_SERVICE", "Web 服务"
    STREAM = "STREAM", "实时流"
    MODEL_ENDPOINT = "MODEL_ENDPOINT", "模型端点"
    THREE_D = "THREE_D", "三维"
    POINT_CLOUD = "POINT_CLOUD", "点云"
    CATALOG = "CATALOG", "目录"


class DistributionAccessMode(models.TextChoices):
    DIRECT = "DIRECT", "直接访问"
    SIGNED = "SIGNED", "签名访问"
    PROXIED = "PROXIED", "平台代理"
    INTERNAL = "INTERNAL", "仅内部"


class DistributionMutability(models.TextChoices):
    IMMUTABLE_CONTENT = "IMMUTABLE_CONTENT", "不可变内容"
    MUTABLE_ENDPOINT = "MUTABLE_ENDPOINT", "可变端点"
    EPHEMERAL = "EPHEMERAL", "临时"
    LIVE = "LIVE", "实时"


class Asset(UUID7Model, TimeStampedModel, ConcurrentModel):
    """可版本化长期资产的逻辑身份。"""

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="assets",
        verbose_name="所属租户",
    )
    workspace = models.ForeignKey(
        "tenancy.Workspace",
        on_delete=models.PROTECT,
        related_name="assets",
        null=True,
        blank=True,
        verbose_name="所属工作空间",
    )
    project = models.ForeignKey(
        "tenancy.Project",
        on_delete=models.PROTECT,
        related_name="assets",
        null=True,
        blank=True,
        verbose_name="所属项目",
    )
    asset_type = models.CharField(
        "资产类型",
        max_length=80,
        help_text="使用稳定、可扩展的 namespaced key，例如 dataset、map、geophysics.modelpack。",
    )
    traits = models.JSONField(
        "能力特征",
        default=list,
        blank=True,
        help_text="仅保存诸如 spatial/executable/temporal 等 trait key；不能代替关系表。",
    )
    name = models.CharField("资产名称", max_length=240)
    slug = models.SlugField("资产标识", max_length=100)
    description = models.TextField("说明", blank=True)
    status = models.CharField(
        "生命周期状态", max_length=20, choices=AssetStatus.choices, default=AssetStatus.ACTIVE
    )
    classification = models.CharField(
        "数据/资源分级",
        max_length=20,
        choices=ResourceClassification.choices,
        default=ResourceClassification.INTERNAL,
    )
    owner_principal = models.ForeignKey(
        "iam.Principal",
        on_delete=models.PROTECT,
        related_name="owned_assets",
        verbose_name="资产责任主体",
    )
    steward_principal = models.ForeignKey(
        "iam.Principal",
        on_delete=models.PROTECT,
        related_name="stewarded_assets",
        null=True,
        blank=True,
        verbose_name="资产治理责任人",
    )
    created_by = models.ForeignKey(
        "iam.Principal",
        on_delete=models.PROTECT,
        related_name="created_assets",
        verbose_name="创建主体",
    )

    class Meta:
        db_table = "sf_asset"
        verbose_name = "资产"
        verbose_name_plural = "资产"
        constraints = [
            # Project 级资产：同一 Project、同一类型下 slug 唯一。
            models.UniqueConstraint(
                fields=["tenant", "project", "asset_type", "slug"],
                condition=Q(project__isnull=False),
                name="sf_asset_project_slug_uniq",
            ),
            # Workspace 级资产：没有 Project，但有 Workspace。
            models.UniqueConstraint(
                fields=["tenant", "workspace", "asset_type", "slug"],
                condition=Q(project__isnull=True, workspace__isnull=False),
                name="sf_asset_workspace_slug_uniq",
            ),
            # Tenant 级资产：既没有 Project，也没有 Workspace。
            models.UniqueConstraint(
                fields=["tenant", "asset_type", "slug"],
                condition=Q(project__isnull=True, workspace__isnull=True),
                name="sf_asset_tenant_slug_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "asset_type", "status"], name="sf_asset_type_status_idx"),
            models.Index(fields=["tenant", "project", "status"], name="sf_asset_proj_status_idx"),
            models.Index(fields=["tenant", "workspace", "status"], name="sf_asset_ws_status_idx"),
        ]

    def clean(self) -> None:
        """验证 Tenant/Workspace/Project/Principal 的作用域一致性。"""

        super().clean()
        errors: dict[str, str] = {}

        if self.workspace_id and self.workspace.tenant_id != self.tenant_id:
            errors["workspace"] = "Asset.workspace 必须属于 Asset.tenant。"
        if self.project_id:
            if self.project.tenant_id != self.tenant_id:
                errors["project"] = "Asset.project 必须属于 Asset.tenant。"
            if self.workspace_id and self.project.workspace_id != self.workspace_id:
                errors["project"] = "Asset.project 必须属于指定的 Asset.workspace。"
        for field_name, principal in (
            ("owner_principal", self.owner_principal),
            ("steward_principal", self.steward_principal),
            ("created_by", self.created_by),
        ):
            if principal is not None and principal.tenant_id not in (None, self.tenant_id):
                errors[field_name] = "资产责任主体必须是平台主体或属于同一租户。"

        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return self.name


class AssetVersion(UUID7Model, TimeStampedModel):
    """Asset 的具体不可变版本。

    设计规则：一旦进入 PUBLISHED，定义内容不得原地修改。
    当前模型只提供状态和字段；真正的发布/不可变校验将在 AssetPublicationService 和测试中完成，
    后续如有必要再增加数据库级触发器。禁止使用 QuerySet.update() 绕过领域服务改已发布内容。
    """

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="asset_versions",
        verbose_name="所属租户",
        help_text="显式冗余 tenant_id，便于 RLS、索引与跨租户防护；必须与 Asset.tenant 一致。",
    )
    asset = models.ForeignKey(
        Asset,
        on_delete=models.PROTECT,
        related_name="versions",
        verbose_name="所属资产",
    )
    version_seq = models.PositiveBigIntegerField("内部版本序号")
    version_label = models.CharField("版本标签", max_length=80, blank=True)
    schema_ref = models.CharField(
        "Schema 引用", max_length=240, blank=True, help_text="例如内部 JSON Schema 的稳定 URI/key。"
    )
    schema_version = models.CharField("Schema 版本", max_length=80, blank=True)
    spec = models.JSONField("版本规范", default=dict)
    metadata = models.JSONField("版本元数据", default=dict, blank=True)
    content_hash = models.CharField(
        "内容指纹",
        max_length=128,
        blank=True,
        help_text="发布时对规范化 spec、关键依赖和 Artifact digest 计算；算法由发布服务统一决定。",
    )
    status = models.CharField(
        "版本状态",
        max_length=20,
        choices=AssetVersionStatus.choices,
        default=AssetVersionStatus.DRAFT,
    )
    created_by = models.ForeignKey(
        "iam.Principal",
        on_delete=models.PROTECT,
        related_name="created_asset_versions",
        verbose_name="创建主体",
    )
    published_at = models.DateTimeField("发布时间", null=True, blank=True)
    deprecated_at = models.DateTimeField("弃用时间", null=True, blank=True)
    replacement_version = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="replaced_versions",
        null=True,
        blank=True,
        verbose_name="替代版本",
    )

    class Meta:
        db_table = "sf_asset_version"
        verbose_name = "资产版本"
        verbose_name_plural = "资产版本"
        constraints = [
            models.UniqueConstraint(
                fields=["asset", "version_seq"], name="sf_assetver_seq_uniq"
            )
        ]
        indexes = [
            models.Index(fields=["tenant", "status"], name="sf_assetver_tenant_status_idx"),
            models.Index(fields=["asset", "status"], name="sf_assetver_asset_status_idx"),
            models.Index(fields=["status", "published_at"], name="sf_assetver_pub_idx"),
        ]

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        if self.asset_id and self.tenant_id and self.asset.tenant_id != self.tenant_id:
            errors["tenant"] = "AssetVersion.tenant 必须与 Asset.tenant 保持一致。"
        if self.created_by_id and self.asset_id:
            if self.created_by.tenant_id not in (None, self.asset.tenant_id):
                errors["created_by"] = "版本创建主体必须是平台主体或属于 Asset 所在租户。"
        if self.replacement_version_id:
            if self.replacement_version.asset_id != self.asset_id:
                errors["replacement_version"] = "替代版本必须属于同一个 Asset。"
            if self.replacement_version_id == self.id:
                errors["replacement_version"] = "AssetVersion 不能把自己设置为替代版本。"
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return f"{self.asset.name} v{self.version_seq}"


class AssetAlias(UUID7Model, TimeStampedModel, ConcurrentModel):
    """逻辑别名到具体 AssetVersion 的可变指针，例如 stable/candidate/production。"""

    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="aliases")
    alias = models.SlugField("别名", max_length=64)
    target_version = models.ForeignKey(
        AssetVersion,
        on_delete=models.PROTECT,
        related_name="aliases",
        verbose_name="目标版本",
    )
    updated_by = models.ForeignKey(
        "iam.Principal",
        on_delete=models.PROTECT,
        related_name="updated_asset_aliases",
        verbose_name="最后更新主体",
    )

    class Meta:
        db_table = "sf_asset_alias"
        verbose_name = "资产版本别名"
        verbose_name_plural = "资产版本别名"
        constraints = [
            models.UniqueConstraint(fields=["asset", "alias"], name="sf_asset_alias_uniq")
        ]

    def clean(self) -> None:
        super().clean()
        if self.asset_id and self.target_version_id and self.target_version.asset_id != self.asset_id:
            raise ValidationError({"target_version": "Alias 只能指向同一 Asset 的版本。"})

    def __str__(self) -> str:
        return f"{self.asset.slug}:{self.alias}"


class Artifact(UUID7Model, TimeStampedModel):
    """不可原地覆盖的物理/远程制品。

    ``storage_pool_ref`` 是 provider-neutral 的稳定引用，当前 Phase A 暂不建立 StoragePool FK；
    后续 Operations/Storage ERD 完成后再决定是否升级为外键。它绝不能替换成 minio_bucket 等字段。
    """

    tenant = models.ForeignKey(
        "tenancy.Tenant", on_delete=models.PROTECT, related_name="artifacts", verbose_name="所属租户"
    )
    digest_algorithm = models.CharField("摘要算法", max_length=24, default="sha256")
    digest = models.CharField("内容摘要", max_length=256)
    media_type = models.CharField("媒体类型", max_length=160, blank=True)
    size = models.PositiveBigIntegerField("字节数")
    filename = models.CharField("原始/建议文件名", max_length=512, blank=True)
    storage_pool_ref = models.UUIDField(
        "存储池引用",
        help_text="逻辑 StoragePool UUID；不是 MinIO/S3 厂商 ID。Phase A 先保留稳定引用。",
    )
    storage_object_ref = models.CharField(
        "对象引用",
        max_length=1024,
        help_text="ObjectStore 内部不透明对象 key；API 不应直接向普通用户暴露真实存储路径。",
    )
    integrity_status = models.CharField(
        "完整性状态",
        max_length=20,
        choices=ArtifactIntegrityStatus.choices,
        default=ArtifactIntegrityStatus.PENDING,
    )
    retention_class = models.CharField("保留策略类别", max_length=80, blank=True)
    legal_hold = models.BooleanField(
        "法律/合规保留", default=False, help_text="为 True 时禁止普通 GC/物理删除。"
    )

    class Meta:
        db_table = "sf_artifact"
        verbose_name = "制品"
        verbose_name_plural = "制品"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "storage_pool_ref", "storage_object_ref"],
                name="sf_artifact_storage_ref_uniq",
            )
        ]
        indexes = [
            models.Index(
                fields=["tenant", "digest_algorithm", "digest"], name="sf_artifact_digest_idx"
            ),
            models.Index(fields=["tenant", "integrity_status"], name="sf_artifact_integrity_idx"),
        ]

    def __str__(self) -> str:
        return self.filename or self.digest


class Distribution(UUID7Model, TimeStampedModel, ConcurrentModel):
    """AssetVersion 的一种可消费访问/表达方式。

    例如同一个南京道路版本可以同时拥有 PostGIS、GeoParquet、PMTiles 等 Distribution。
    因此 Distribution 不是“文件扩展名”，也不等同于 ServiceInstance endpoint。
    """

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="distributions",
        verbose_name="所属租户",
        help_text="显式冗余 tenant_id，便于 RLS、索引和安全查询。",
    )
    asset_version = models.ForeignKey(
        AssetVersion,
        on_delete=models.PROTECT,
        related_name="distributions",
        verbose_name="所属资产版本",
    )
    distribution_type = models.CharField("分发类型", max_length=24, choices=DistributionType.choices)
    format = models.CharField(
        "格式",
        max_length=80,
        blank=True,
        help_text="例如 postgis、geoparquet、cog、zarr、pmtiles、wms、ogc_features。",
    )
    protocol = models.CharField("协议", max_length=80, blank=True)
    status = models.CharField(
        "状态", max_length=20, choices=DistributionStatus.choices, default=DistributionStatus.PENDING
    )
    access_mode = models.CharField(
        "访问模式", max_length=16, choices=DistributionAccessMode.choices, default=DistributionAccessMode.PROXIED
    )
    mutability = models.CharField(
        "可变性", max_length=24, choices=DistributionMutability.choices, default=DistributionMutability.IMMUTABLE_CONTENT
    )
    artifact = models.ForeignKey(
        Artifact, on_delete=models.PROTECT, related_name="distributions", null=True, blank=True, verbose_name="制品"
    )
    source_distribution = models.ForeignKey(
        "self", on_delete=models.PROTECT, related_name="derived_distributions", null=True, blank=True, verbose_name="来源分发"
    )
    # 这里只保存 Fabric ServiceDeployment 的稳定 UUID，不保存 GeoServer/Martin/TiTiler 的内部 ID。
    # services 模块 ERD 完成后再升级为真正 FK，避免 Phase A 制造跨模块循环迁移。
    service_deployment_ref = models.UUIDField("服务部署引用", null=True, blank=True)
    external_location_ref = models.TextField(
        "外部位置引用", blank=True, help_text="外部资源的受控引用；不得在这里存明文 Secret。"
    )
    spatial_metadata = models.JSONField("空间技术元数据", default=dict, blank=True)
    temporal_metadata = models.JSONField("时间技术元数据", default=dict, blank=True)
    technical_metadata = models.JSONField(
        "扩展技术元数据", default=dict, blank=True,
        help_text="必须是 provider-neutral 或 namespaced 扩展；核心关系不得藏在 JSONB。",
    )

    class Meta:
        db_table = "sf_distribution"
        verbose_name = "分发形态"
        verbose_name_plural = "分发形态"
        indexes = [
            models.Index(fields=["tenant", "status"], name="sf_dist_tenant_status_idx"),
            models.Index(fields=["asset_version", "status"], name="sf_dist_assetver_status_idx"),
            models.Index(fields=["distribution_type", "format", "status"], name="sf_dist_type_fmt_idx"),
        ]

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        expected_tenant_id = self.asset_version.asset.tenant_id if self.asset_version_id else None
        if expected_tenant_id and self.tenant_id != expected_tenant_id:
            errors["tenant"] = "Distribution.tenant 必须与 AssetVersion 所属租户一致。"
        if self.artifact_id and expected_tenant_id and self.artifact.tenant_id != expected_tenant_id:
            errors["artifact"] = "Distribution.artifact 必须与 AssetVersion 属于同一租户。"
        if self.source_distribution_id:
            source_tenant_id = self.source_distribution.asset_version.asset.tenant_id
            if expected_tenant_id and source_tenant_id != expected_tenant_id:
                errors["source_distribution"] = "来源 Distribution 不得跨租户引用。"
            if self.source_distribution_id == self.id:
                errors["source_distribution"] = "Distribution 不能引用自己作为来源。"
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        suffix = self.format or self.distribution_type
        return f"{self.asset_version} [{suffix}]"

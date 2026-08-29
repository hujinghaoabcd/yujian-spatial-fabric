"""注册 Spatial Fabric 架构冻结的核心 Privilege 词汇。

这是平台级参考数据，不创建任何 Role，也不会给任何 Principal 自动授权。
Role / RoleAssignment 仍必须由正式治理流程建立。
"""

from django.db import migrations


# migration 必须自包含，不能 import 当前运行时 iam.models 中的 TextChoices；否则未来修改枚举后，
# 历史 migration 的含义也会被悄悄改变。
CORE_PRIVILEGES = (
    ("discover", "发现资源", "READ", "LOW", "允许知道资源存在并出现在可发现目录中。"),
    ("view_metadata", "查看元数据", "READ", "LOW", "允许查看资源说明、范围、版本等元数据。"),
    ("read", "读取资源", "READ", "LOW", "允许读取资源内容；不自动包含下载、导出或执行。"),
    ("query", "查询资源", "READ", "LOW", "允许对资源执行受控查询。"),
    ("tile_read", "读取瓦片", "READ", "LOW", "允许读取地图/场景瓦片，不自动授予底层数据下载。"),
    ("feature_read", "读取要素", "READ", "LOW", "允许读取矢量要素级内容。"),
    ("download", "下载资源", "READ", "MEDIUM", "允许下载原始或指定 Distribution。"),
    ("export", "导出资源", "READ", "MEDIUM", "允许将资源转换或导出到外部格式/位置。"),
    ("create", "创建资源", "WRITE", "MEDIUM", "允许在授权作用域创建新资源。"),
    ("edit", "编辑资源", "WRITE", "MEDIUM", "允许修改可变对象或创建新草稿版本。"),
    ("delete", "删除资源", "WRITE", "HIGH", "允许发起回收、删除或保留策略下的销毁操作。"),
    ("publish", "发布资源", "GOVERNANCE", "HIGH", "允许发布版本、服务或内容进入正式可用状态。"),
    ("share", "分享资源", "GOVERNANCE", "HIGH", "允许向其他主体授予资源访问；实际授予由 ShareGrant 承担。"),
    ("execute", "执行能力", "EXECUTE", "HIGH", "允许运行模型、算法、Workflow 或 Agent Skill，不自动包含下载。"),
    ("use_secret", "使用密钥", "SECRET", "CRITICAL", "允许通过 SecretRef 使用受保护凭据；不暴露明文 Secret。"),
    ("approve", "审批操作", "GOVERNANCE", "HIGH", "允许对受控访问、发布或高风险动作作出审批决定。"),
    ("manage", "管理范围", "ADMIN", "HIGH", "允许管理授权范围内的配置与治理对象。"),
    ("admin", "平台/租户管理", "ADMIN", "CRITICAL", "最高级管理动作词汇；仍受作用域、Policy 与审计约束。"),
)


def seed_core_privileges(apps, schema_editor):  # noqa: ANN001
    """幂等注册核心 Privilege；重复 migrate 不会复制记录。"""

    privilege_model = apps.get_model("iam", "Privilege")
    for key, name, category, risk_level, description in CORE_PRIVILEGES:
        privilege_model.objects.update_or_create(
            key=key,
            defaults={
                "name": name,
                "category": category,
                "risk_level": risk_level,
                "description": description,
                "status": "ACTIVE",
                "system_managed": True,
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ("iam", "0002_group_groupmembership_privilege_roledefinition_and_more"),
    ]

    operations = [
        # Core Privilege 是长期稳定的系统参考词汇。reverse 采用 noop，避免仅回退这一条数据
        # migration 时误删已经被 Tenant Role 引用的权限动作；若继续回退 0002，对应表本身会被移除。
        migrations.RunPython(seed_core_privileges, migrations.RunPython.noop),
    ]

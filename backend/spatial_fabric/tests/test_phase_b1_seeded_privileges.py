"""验证 Phase B1 核心 Privilege 数据 migration。

这些权限动作是架构 Contract，不应依赖管理员在某个环境里手工创建。
"""

import pytest

from spatial_fabric.iam.models import Privilege, PrivilegeCategory, PrivilegeRiskLevel


CORE_KEYS = {
    "discover",
    "view_metadata",
    "read",
    "query",
    "tile_read",
    "feature_read",
    "download",
    "export",
    "create",
    "edit",
    "delete",
    "publish",
    "share",
    "execute",
    "use_secret",
    "approve",
    "manage",
    "admin",
}


@pytest.mark.django_db
def test_core_privileges_are_seeded_by_migration() -> None:
    """全新数据库执行正式 migrations 后必须拥有完整核心权限词汇。"""

    actual = set(Privilege.objects.filter(key__in=CORE_KEYS).values_list("key", flat=True))
    assert actual == CORE_KEYS


@pytest.mark.django_db
def test_execute_and_download_keep_distinct_semantics() -> None:
    """核心参考数据层也必须保持 execute ≠ download。"""

    execute = Privilege.objects.get(key="execute")
    download = Privilege.objects.get(key="download")

    assert execute.category == PrivilegeCategory.EXECUTE
    assert execute.risk_level == PrivilegeRiskLevel.HIGH
    assert download.category == PrivilegeCategory.READ
    assert download.risk_level == PrivilegeRiskLevel.MEDIUM
    assert execute.id != download.id

"""Spatial Fabric 全局通用 Django 抽象模型。

这里只放真正跨领域且稳定的技术型基类，不得为了少写几行代码把业务字段继续堆进这里。
"""

from django.db import models

from spatial_fabric.common.ids import uuid7


class UUID7Model(models.Model):
    """为领域记录提供稳定公开 UUIDv7 主键。"""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)

    class Meta:
        abstract = True


class TimeStampedModel(models.Model):
    """技术创建/更新时间；不能替代 valid_time、published_at 等业务时间语义。"""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class ConcurrentModel(models.Model):
    """为可变草稿/配置聚合预留乐观锁版本号。

    真正更新时应由 Application Service 原子比较并递增；已发布不可变版本不依赖此字段实现不可变性。
    """

    lock_version = models.PositiveBigIntegerField(default=0)

    class Meta:
        abstract = True

"""启用 Spatial Fabric 数据库必须具备的 PostGIS 扩展。

PostGIS 是 Spatial Fabric 的数据库能力基线，而不是某个云厂商 Provider。
这里使用幂等 SQL，既兼容本地 postgis/postgis 镜像，也兼容 Render/Neon 等
允许安装 PostGIS 的托管 PostgreSQL。反向迁移故意不执行 DROP EXTENSION：
一旦后续存在 geometry/geography 等空间对象，删除扩展会破坏数据和依赖关系。
"""

from django.db import migrations


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.RunSQL(
            sql="CREATE EXTENSION IF NOT EXISTS postgis;",
            reverse_sql=migrations.RunSQL.noop,
        )
    ]

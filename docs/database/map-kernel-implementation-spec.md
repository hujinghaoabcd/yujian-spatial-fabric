# Spatial Map Kernel｜地图内核实现规格

> 状态：**Implementation Spec（非 FINAL 架构宪章）**  
> 分支：`feat/map-kernel-design`  
> 上位约束：`00_PROJECT_HANDOFF.md` 中冻结的 `Asset ≠ AssetVersion ≠ Artifact ≠ Distribution` 与 `Dataset ≠ Layer ≠ Style ≠ Map ≠ Scene`。  
> 本文只把已冻结边界映射成可落地的 Django/PostgreSQL/PostGIS 结构；若未来同步进仓库的三份 FINAL 全文与本文冲突，以 FINAL 为准。

## 1. 目标

地图能力属于 Spatial Fabric 基座，但地图不是整个基座。

地图内核必须同时满足：

1. GeoNode 类平台成熟的 Dataset / Layer / Map 资源管理体验；
2. Spatial Fabric 已冻结的统一 Asset Kernel；
3. 2D Map 与 3D Scene 分离；
4. Dataset 与 Layer 分离；
5. Style 可独立版本化、复用和由 GeoAgent 操作；
6. 同一个数据版本可以有 PostGIS、COG、PMTiles、MVT、WMS、3D Tiles 等多种消费形态；
7. Provider endpoint 可以重建或迁移，而不改变资产语义身份；
8. 计算结果能够自动进入地图链路，即“计算即地图”。

## 2. 不新增 `Representation` 根对象

Phase A 已经存在：

```text
Asset
  ↓
AssetVersion
  ├── Artifact
  └── Distribution
```

其中 `Distribution` 的既有定义就是：

> AssetVersion 的一种可消费访问/表达方式。

它已经可以表达：

```text
PostGIS
GeoParquet
COG
PMTiles
MVT
WMS / WMTS / WFS
3D Tiles
Stream
Catalog
Model Endpoint
```

因此地图阶段**禁止**再建立与其同义的 `Representation` / `SpatialRepresentation` Aggregate Root。

正确扩展方式是：

```text
Distribution
    │
    └── SpatialDistributionProfile   ← Typed Facet
```

这样：

- Asset Kernel 继续保持通用；
- 地图模块获得强类型、可查询的空间字段；
- 不把 TiTiler / Martin / GeoServer / Cesium ion 等 Provider 写进 Core Model；
- endpoint 重建时不需要修改 Dataset/Layer/Map 的语义身份。

## 3. 核心建模原则：Logical Asset + Version Typed Facet

Dataset、Layer、Style、Map、Scene 都是**可复用、可授权、可分享、可审计、可版本化**资源，因此它们都使用已有 `Asset` / `AssetVersion` 作为统一身份和生命周期。

但禁止用 Django multi-table inheritance 把 Asset 继承成五棵独立根表。

采用：

```text
Asset(asset_type="dataset")
        ↓
AssetVersion
        ↓ 1:1
DatasetVersionProfile
```

其余同理：

```text
Asset(asset_type="layer")  → AssetVersion → LayerVersionProfile
Asset(asset_type="style")  → AssetVersion → StyleVersionProfile
Asset(asset_type="map")    → AssetVersion → MapVersionProfile
Asset(asset_type="scene")  → AssetVersion → SceneVersionProfile
```

理由：

1. Tenant / Workspace / Project / owner / classification / alias / publication 生命周期全部复用 Asset Kernel；
2. 空间领域字段仍然有数据库强类型和索引，不把所有内容塞入 `AssetVersion.spec`；
3. 新资产类型可以继续通过 Typed Facet 扩展，不修改 Asset 核心表；
4. 避免 Dataset/Map/Model/Workflow 各自重复实现 ownership、version、alias、permission。

`AssetVersion.spec` 仍保留用于低频、可扩展、schema 驱动配置；高频查询、关系完整性、空间索引所需字段进入 Typed Facet。

## 4. DatasetVersionProfile

建议表：`sf_dataset_version_profile`

一条记录必须且只能对应一个 `asset_type=dataset` 的 AssetVersion。

第一版字段：

```text
id                      UUIDv7
asset_version_id        FK AssetVersion UNIQUE
kind                    VECTOR | RASTER | MULTIDIMENSIONAL |
                        TABLE | TRAJECTORY | POINT_CLOUD | THREE_D | STREAM
geometry_type           POINT | MULTIPOINT | LINESTRING | MULTILINESTRING |
                        POLYGON | MULTIPOLYGON | GEOMETRYCOLLECTION | MIXED | NONE
native_crs              string，例如 EPSG:4326
spatial_extent          Geometry/Polygon nullable
spatial_extent_crs      string
start_time              timestamptz nullable
end_time                timestamptz nullable
temporal_resolution     string nullable
spatial_resolution      string/json nullable
feature_count           bigint nullable
row_count               bigint nullable
schema_summary          jsonb
variables               jsonb
bands                    jsonb
units                    jsonb
extra_metadata          jsonb
```

### 4.1 为什么 extent 要进入 PostGIS 字段

`bbox` 不能只存在 JSON 中。至少需要一个可 GiST/SP-GiST 索引的空间范围字段，用于：

- 地图初始视图；
- 空间目录检索；
- “查找南京范围内的所有数据”；
- Dataset 自动匹配 Map/Scene；
- GeoAgent spatial discovery。

第一版可统一使用 `PolygonField(srid=4326)` 保存规范化 WGS84 extent；原生 CRS 与原生 bbox 可继续写 metadata。

### 4.2 不在 DatasetProfile 中保存 Provider 字段

禁止：

```text
geoserver_workspace
minio_bucket
titiler_url
martin_table
neon_project_id
```

这些属于 Distribution / Provider / ServiceDeployment 层。

## 5. SpatialDistributionProfile

建议表：`sf_spatial_distribution_profile`

```text
id                      UUIDv7
distribution_id         FK Distribution UNIQUE
spatial_kind            VECTOR | RASTER | MULTIDIMENSIONAL |
                        TILE | THREE_D | POINT_CLOUD | STREAM | QUERY
format                   GEOJSON | GPKG | GEOPARQUET | COG | NETCDF | ZARR |
                        PMTILES | MVT | PNG_TILE | WEBP_TILE | THREE_D_TILES |
                        COPC | OTHER
protocol                 FILE | S3 | HTTP | XYZ | TMS | MVT | WMS | WFS |
                        WMTS | OGC_API_FEATURES | STAC | QUERY_API | STREAM
crs                      string nullable
bounds                   PolygonField(4326) nullable
min_zoom                 smallint nullable
max_zoom                 smallint nullable
tile_matrix_set          string nullable
variable                 string nullable
band                     string nullable
content_encoding         string nullable
capabilities             jsonb
render_hints             jsonb
```

### 5.1 Distribution 与 ServiceInstance 的边界

```text
Distribution = “这个 AssetVersion 有一种 MVT / COG / WMS 消费形态”
ServiceInstance = “当前由哪个运行实例真正提供该能力”
```

因此未来允许：

```text
LayerVersion
   ↓ semantic source
DatasetVersion
   ↓ resolver
Distribution(MVT)
   ↓ runtime resolution
ServiceInstance(Martin instance A)
```

Martin A 换成 Martin B，不应生成新的 DatasetVersion 或 LayerVersion；只有消费形态语义本身改变时才产生新的 Distribution。

## 6. LayerVersionProfile

建议表：`sf_layer_version_profile`

Layer 不是 Dataset 的别名。

```text
Dataset = 数据是什么
Layer   = 数据如何作为地图图层被消费和表达
```

一个 DatasetVersion 可以产生多个 LayerVersion，例如道路数据：

```text
road_dataset_v3
 ├── highway_layer_v1
 ├── arterial_layer_v2
 ├── traffic_flow_layer_v5
 └── congestion_layer_v4
```

建议字段：

```text
id                      UUIDv7
asset_version_id        FK AssetVersion UNIQUE
layer_kind              VECTOR | RASTER | TERRAIN | HEATMAP |
                        TRAJECTORY | POINT_CLOUD | THREE_D | LIVE
source_dataset_version  FK AssetVersion
source_selector         jsonb
source_policy           jsonb
query_spec              jsonb
interaction_spec        jsonb
legend_spec             jsonb
```

### 6.1 Layer 持久化绑定 DatasetVersion，而不是固定 endpoint

Published LayerVersion 的主要数据源必须解析为**具体 Dataset AssetVersion**，而不是：

```text
https://server-123/tiles/{z}/{x}/{y}.pbf
```

原因：endpoint 是运行/Provider 状态，可以迁移；DatasetVersion 是可追溯的语义输入。

`source_policy` 用来表达 Layer 需要的消费能力，例如：

```json
{
  "preferred_protocols": ["MVT", "PMTILES"],
  "fallback_protocols": ["WFS"],
  "require_tiling": true
}
```

运行时 Resolver 再从 DatasetVersion 的可用 Distribution 中选择合适形态。

### 6.2 Job 输出如何进入 Layer

持久 Map 不直接长期引用匿名临时文件。

统一链路：

```text
Job output
   ↓
Artifact
   ↓
auto register/promote
   ↓
Result Dataset AssetVersion
   ↓
Distribution
   ↓
LayerVersion
   ↓
Map
```

如果只是运行中的临时预览，可以允许前端使用 ephemeral Distribution；一旦保存 Map，则必须先把结果提升为稳定 AssetVersion。

这保证 provenance、权限和重跑都不会因为临时 URL 消失而断裂。

## 7. StyleVersionProfile

建议表：`sf_style_version_profile`

Style 是独立 Asset，而不是 Layer/Map JSON 中的一段不可复用颜色配置。

字段：

```text
id                      UUIDv7
asset_version_id        FK AssetVersion UNIQUE
style_kind              VECTOR | RASTER | SYMBOL | HEATMAP |
                        CLASSIFIED | CONTINUOUS | THREE_D | COMPOSITE
semantic_spec           jsonb
renderer_family         MAPLIBRE | SLD | CESIUM | GENERIC
renderer_spec           jsonb
legend_spec             jsonb
```

### 7.1 Semantic Style 与 Renderer Spec 分层

GeoAgent 不应该被迫直接生成大量 MapLibre paint JSON。

例如内部语义：

```json
{
  "variable": "no2",
  "classification": "continuous",
  "units": "μg/m³",
  "breaks": [20, 40, 60, 80],
  "palette_intent": "sequential_high_is_alert"
}
```

Renderer adapter 可将其编译成：

```text
MapLibre style fragment
SLD
Cesium material
server-side ColorMap
```

因此：

```text
Semantic Style = 业务/科学表达意图
Renderer Spec  = 某渲染器的具体实现
```

## 8. LayerStyleBinding

不要把 Style FK 直接塞进 LayerVersionProfile，原因是：

- 一个 Layer 可以有多个样式；
- Map 中可以临时覆盖默认样式；
- default / alternate / print / dark 等关系有语义。

建议表：`sf_layer_style_binding`

```text
id                      UUIDv7
layer_version_id        FK AssetVersion
style_version_id        FK AssetVersion
role                    DEFAULT | ALTERNATE | FALLBACK
is_default              bool
priority                int
```

约束：

- 两端必须同 tenant 或引用平台级共享资产；
- `layer_version_id` 必须属于 layer Asset；
- `style_version_id` 必须属于 style Asset；
- 每个 LayerVersion 最多一个 DEFAULT。

这些跨 asset_type 约束第一版由领域服务 + 测试保证；必要时再升级数据库 trigger。

## 9. MapVersionProfile

建议表：`sf_map_version_profile`

Map 是**保存的二维空间组合**，不是截图。

字段：

```text
id                      UUIDv7
asset_version_id        FK AssetVersion UNIQUE
projection              string, default EPSG:3857
camera_spec             jsonb
basemap_spec            jsonb
interaction_spec        jsonb
ui_spec                  jsonb
extent                   PolygonField(4326) nullable
```

典型 camera：

```json
{
  "center": [118.78, 32.04],
  "zoom": 10,
  "bearing": 0,
  "pitch": 0
}
```

## 10. MapLayerBinding

Map 和 Layer 是 many-to-many，但中间关系本身有大量语义，必须建正式实体。

建议表：`sf_map_layer_binding`

```text
id                      UUIDv7
map_version_id          FK AssetVersion
layer_version_id        FK AssetVersion
order_index             int
visible                 bool
opacity                 decimal
min_zoom                decimal nullable
max_zoom                decimal nullable
filter_spec             jsonb
style_override_version  FK AssetVersion nullable
interaction_override    jsonb
temporal_selector       jsonb
render_override         jsonb
```

约束：

```text
UNIQUE(map_version_id, layer_version_id, order_index?)
UNIQUE(map_version_id, order_index)
0 <= opacity <= 1
min_zoom <= max_zoom
```

是否允许同一个 LayerVersion 在同一 Map 中出现多次：**允许**。

例：同一道路 Layer 可分别以“底部灰色道路”和“顶部拥堵着色”出现，因此不能简单 `UNIQUE(map_version, layer_version)`。

推荐增加稳定 `binding_key`：

```text
binding_key = roads-base
binding_key = roads-congestion
```

最终唯一约束：

```text
UNIQUE(map_version_id, binding_key)
UNIQUE(map_version_id, order_index)
```

## 11. SceneVersionProfile 与 SceneLayerBinding

Scene 与 Map 必须分开，不做一个万能 `Map3D`。

Scene 面向：

- 3D Tiles；
- 建筑；
- Terrain / DEM；
- 点云；
- 地下空间；
- 污染羽流；
- 三维气象场；
- 数字孪生/仿真结果。

建议：

```text
sf_scene_version_profile
sf_scene_layer_binding
```

SceneVersionProfile：

```text
asset_version_id
camera_spec
lighting_spec
terrain_spec
time_spec
scene_environment_spec
extent
```

SceneLayerBinding：

```text
scene_version_id
layer_version_id
binding_key
order_index
visible
opacity
style_override_version
transform_spec
clipping_spec
temporal_selector
interaction_override
```

第一阶段可只建模型与 contract，不要求立即接 Cesium。

## 12. 关系总图

```text
                           Asset
                             │
                        AssetVersion
                             │
       ┌───────────────┬─────┴─────┬───────────────┐
       │               │           │               │
 DatasetVersion   LayerVersion  StyleVersion   MapVersion
    Profile          Profile       Profile       Profile
       │               │             │             │
       │               ├──── LayerStyleBinding ────┤
       │               │                           │
       └── source ──────┘                    MapLayerBinding
       │                                           │
       │                                           │
       ├── Artifact                                │
       │                                           │
       └── Distribution ── SpatialDistributionProfile

AssetVersion ── SceneVersionProfile
                       │
                 SceneLayerBinding
                       │
                  LayerVersion
```

更准确的语义链：

```text
Dataset Asset
  ↓ exact version
DatasetVersionProfile
  ↓ available forms
Distribution + SpatialDistributionProfile

DatasetVersion
  ↓ semantic source
Layer AssetVersion
  ↓ style bindings
Style AssetVersion

LayerVersion
  ↓ map composition binding
Map AssetVersion

LayerVersion
  ↓ scene composition binding
Scene AssetVersion
```

## 13. Asset type keys

第一版使用稳定 namespaced key：

```text
dataset
layer
style
map
scene
```

未来专业类型仍通过 trait/profile/package 扩展：

```text
dataset + traits=[spatial, temporal]
dataset + traits=[raster, remote_sensing]
dataset + traits=[multidimensional, meteorology]
layer + traits=[live]
scene + traits=[simulation]
```

不要快速膨胀为：

```text
aermod_raster_dataset
wrf_dataset
no2_layer
traffic_layer
```

这些属于 metadata、schema、package 或行业层，不属于核心 asset_type。

## 14. 版本与可变性

### 14.1 必须产生新版本

下列变化改变语义，应创建新 AssetVersion：

- Dataset 数据内容发生变化；
- Layer source selector/query 发生语义变化；
- Style 分类规则/色带语义发生变化；
- Map 保存的 layer composition 发生变化并执行正式保存/发布；
- Scene composition 发生变化并正式保存/发布。

### 14.2 不应产生新版本

下列运行变化通常不改变资产语义：

- Martin 实例从 A 换成 B；
- TiTiler pod 重启；
- signed URL 刷新；
- cache key 改变；
- endpoint host 迁移但 Distribution 语义不变。

这些属于 Provider/ServiceInstance/Operational state。

## 15. “计算即地图”的正式链路

以 AERMOD 输出为例，但流程对其他模型通用：

```text
Job / Run
  ↓
output Artifact: concentration.tif
  ↓ provenance
register Dataset AssetVersion
  ↓ inspect
DatasetVersionProfile
  ↓ build serving form
Distribution: COG
  ↓
SpatialDistributionProfile
  ↓
create Layer AssetVersion
  ↓
create/apply Style AssetVersion
  ↓
MapLayerBinding
  ↓
Map AssetVersion
```

用户最终操作应是：

```text
运行模型 → 查看结果
```

而不是：

```text
下载 TIFF → 重新上传 GIS → 配服务 → 手工配色 → 再打开地图
```

## 16. GeoAgent 工具边界

未来 GeoAgent 只能调用正式 Fabric API，例如：

```text
find_dataset
resolve_asset_alias
create_layer
apply_style
set_layer_filter
add_layer_to_map
remove_layer_from_map
set_map_camera
save_map_version
```

Agent 不得绕过 AssetVersion/Permission/Provenance，直接把临时 URL 塞入持久 Map JSON。

示例：

```text
“只显示 NO₂ > 80 μg/m³ 的区域”
```

优先变成：

```text
MapLayerBinding.filter_spec
```

而不是重新运行 AERMOD。

## 17. 第一批数据库约束

至少需要：

1. 每个 Typed Profile 对 `asset_version_id` UNIQUE；
2. Map/Scene binding 的 `binding_key` 在所属版本内唯一；
3. Map/Scene binding 的 `order_index` 在所属版本内唯一；
4. opacity 范围 `[0,1]`；
5. min_zoom <= max_zoom；
6. Dataset spatial extent 使用有效 Polygon/MultiPolygon；
7. Layer source 与 Layer 本身 tenant 一致，或 source 为允许引用的平台级共享资产；
8. LayerStyleBinding 的两端版本类型正确；
9. MapLayerBinding / SceneLayerBinding 的两端版本类型正确；
10. Published Map/Layer/Style/Scene version 不可原地修改。

跨 `Asset.asset_type` 的类型约束无法用普通 FK 表达时，第一阶段采用：

```text
Domain Service + model clean() + tests
```

不要为了数据库“一次性完美”而引入难维护的 polymorphic generic FK。

## 18. 第一批索引

建议：

```text
DatasetVersionProfile.spatial_extent           GiST
MapVersionProfile.extent                       GiST
SceneVersionProfile.extent                     GiST
SpatialDistributionProfile.bounds              GiST
DatasetVersionProfile(start_time, end_time)
LayerVersionProfile(source_dataset_version)
MapLayerBinding(map_version, order_index)
SceneLayerBinding(scene_version, order_index)
LayerStyleBinding(layer_version, role)
```

后续真实查询基准出现前，不预建大量 JSONB GIN 索引。

## 19. Django app 边界

建议新增独立 app：

```text
spatial_fabric.spatial
```

负责：

```text
DatasetVersionProfile
SpatialDistributionProfile
LayerVersionProfile
StyleVersionProfile
LayerStyleBinding
MapVersionProfile
MapLayerBinding
SceneVersionProfile
SceneLayerBinding
```

`assets` app 继续保持纯 Asset Kernel，不把地图字段反向塞进去。

未来 provider adapters 分开：

```text
providers/martin
providers/titiler
providers/geoserver
providers/object_storage
```

前端 MapLibre/deck.gl/Cesium 也不进入 Django Domain Model。

## 20. 建议 migration 顺序

地图内核第一批 migration 应避免把所有对象一次塞入巨型 0001。

建议：

```text
spatial/0001_dataset_distribution_profiles
        ↓
spatial/0002_layer_style_profiles
        ↓
spatial/0003_layer_style_bindings
        ↓
spatial/0004_map_profiles_bindings
        ↓
spatial/0005_scene_profiles_bindings
```

每一步都必须：

```text
makemigrations --check
migrate on empty PostGIS
pytest with real migrations
```

如果 Django 自动生成的依赖图与上述编号不同，以**无环、可从空库重建**为最高原则，不为了编号美观手改出循环。

## 21. V0.1 垂直验证切片

地图内核不是以“把九张表建完”为完成标准，而是以下链路真的跑通：

```text
register Dataset
↓
inspect spatial metadata
↓
register Distribution
↓
create Layer
↓
apply Style
↓
save Map
↓
fetch Map definition API
↓
前端 MapLibre 可以解析并显示
```

随后再接计算：

```text
register Model
↓
create Job
↓
Runner executes
↓
output Artifact
↓
auto DatasetVersion + Distribution
↓
auto Layer
↓
add to Map
↓
trace provenance
```

第二条链路跑通时，才算真正实现 Spatial Fabric 的“计算即地图”。

## 22. 当前不做

此规格阶段不引入：

- GeoServer/Martin/TiTiler 的具体 provider model；
- Cesium 实际前端；
- OGC API 全量实现；
- STAC Catalog 服务；
- Tile cache 调度；
- Job/Run 的正式表；
- GeoAgent；
- 行业专属 Layer 类型；
- 复杂数据库 trigger。

这些都必须在核心空间对象边界稳定后按独立 Phase/ADR 接入。

## 23. 与 Phase A 的关系

本文不修改 Phase A 的 Asset Kernel，而是在其上扩展 Typed Facet：

```text
Phase A
Asset / AssetVersion / Artifact / Distribution

          ↓ additive extension

Spatial Map Kernel
Dataset / Layer / Style / Map / Scene typed profiles + bindings
```

因此 PR #1 可以独立完成 Review/合并；地图内核应作为后续独立 PR 实现，避免 Phase A 基线无限扩张。

同时，既定开发顺序仍以项目状态文件为准：Phase B IAM & Governance 若优先级更高，地图内核规格可以保持冻结而不抢占 Phase B 实现顺序。

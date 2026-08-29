# Spatial Map Kernel｜地图内核实现规格

> 状态：**Implementation Spec（非 FINAL 架构宪章）**  
> 分支：`feat/map-kernel-design`  
> 上位约束：`00_PROJECT_HANDOFF.md` 中冻结的 `Asset ≠ AssetVersion ≠ Artifact ≠ Distribution` 与 `Dataset ≠ Layer ≠ Style ≠ Map ≠ Scene`。  
> 本文只把已冻结边界映射成可落地的 Django/PostgreSQL/PostGIS 结构；未来若同步进仓库的三份 FINAL 全文与本文冲突，以 FINAL 为准。

## 1. 目标

地图能力属于 Spatial Fabric 基座，但地图不是整个基座。

地图内核必须同时满足：

1. 复用 GeoNode 类平台成熟的 Dataset / Layer / Map 资源管理经验；
2. 不破坏 Spatial Fabric 已冻结的统一 Asset Kernel；
3. Dataset 与 Layer 分离；
4. 2D Map 与 3D Scene 分离；
5. Style 可独立版本化、复用，并能被 GeoAgent 以语义方式操作；
6. 同一个数据版本可以拥有 PostGIS、COG、GeoParquet、PMTiles、MVT、WMS、3D Tiles、Stream 等多种消费形态；
7. Provider endpoint 可以重建或迁移，而不改变资产语义身份；
8. 计算结果能够自动进入地图链路，即“计算即地图”；
9. 道路矢量、科学栅格、NetCDF/Zarr、多源专题图、实时流和 3D Tiles 必须落在同一套模型中，而不是为每类数据重新造一套表。

---

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
PostGIS / Database
GeoParquet / File
COG / Object
PMTiles / Tile Archive
MVT / Web Service
WMS / WFS / WMTS
3D Tiles
Stream
Catalog
Model Endpoint
```

因此地图阶段**禁止**再建立与其同义的 `Representation` / `SpatialRepresentation` Aggregate Root。

正确扩展方式：

```text
Distribution
    │
    └── SpatialDistributionProfile   ← Typed Facet
```

这样：

- Asset Kernel 继续保持通用；
- 地图模块获得强类型、可查询的空间字段；
- 不把 TiTiler / Martin / GeoServer / Cesium ion 等 Provider 写进 Core Model；
- endpoint 重建时不需要修改 Dataset / Layer / Map 的语义身份。

---

## 3. 核心建模原则：Logical Asset + Version Typed Facet

Dataset、Layer、Style、Map、Scene 都是**可复用、可授权、可分享、可审计、可版本化**资源，因此统一复用已有 `Asset` / `AssetVersion`。

禁止用 Django multi-table inheritance 把 Asset 继承成五棵独立根表。

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
2. 空间领域字段仍有数据库强类型和索引，不把所有内容塞入 `AssetVersion.spec`；
3. 新资产类型可继续通过 Typed Facet 扩展，不修改 Asset 核心表；
4. 避免 Dataset / Map / Model / Workflow 各自重复实现 ownership、version、alias、permission。

`AssetVersion.spec` 继续承载低频、可扩展、schema 驱动配置；高频查询、关系完整性、空间索引所需字段进入 Typed Facet / Binding 表。

---

## 4. DatasetVersionProfile

建议表：`sf_dataset_version_profile`

一条记录必须且只能对应一个 `asset_type=dataset` 的 AssetVersion。

```text
id                      UUIDv7
asset_version_id        FK AssetVersion UNIQUE
kind                    VECTOR | RASTER | MULTIDIMENSIONAL |
                        TABLE | TRAJECTORY | POINT_CLOUD | THREE_D | STREAM
geometry_type           POINT | MULTIPOINT | LINESTRING | MULTILINESTRING |
                        POLYGON | MULTIPOLYGON | GEOMETRYCOLLECTION | MIXED | NONE
native_crs              string，例如 EPSG:4326
spatial_extent          MultiPolygonField(4326) nullable
start_time              timestamptz nullable
end_time                timestamptz nullable
temporal_resolution     string nullable
spatial_resolution      jsonb
feature_count           bigint nullable
row_count               bigint nullable
schema_summary          jsonb
variables               jsonb
bands                    jsonb
units                    jsonb
extra_metadata          jsonb
```

### 4.1 为什么 extent 必须是 PostGIS 字段

`bbox` 不能只存在 JSON 中。至少要有可 GiST 索引的规范化 WGS84 footprint，用于：

- 地图初始视图；
- 空间目录检索；
- “查找南京范围内的所有数据”；
- Dataset 自动匹配 Map / Scene；
- GeoAgent spatial discovery。

采用 `MultiPolygonField(srid=4326)` 而不是单一 Polygon，可以表达跨 180° 经线时拆分后的多个范围片段。原生 CRS / 原生 bbox 继续保存在 metadata 中。

### 4.2 实时 Dataset 的版本含义

`DatasetVersion` 对实时流表示**流定义 / schema / 语义版本**，不是每一条实时消息都生成新版本。

```text
traffic-stream DatasetVersion v3
    ↓
Distribution(mutability=LIVE)
    ↓
连续事件
```

只有 schema、来源语义、关键单位等定义变化时才产生新的 DatasetVersion。

### 4.3 禁止 Provider 字段进入 DatasetProfile

禁止：

```text
geoserver_workspace
minio_bucket
titiler_url
martin_table
neon_project_id
```

这些属于 Distribution / Provider / ServiceDeployment / ServiceInstance 层。

---

## 5. SpatialDistributionProfile

建议表：`sf_spatial_distribution_profile`

它是 `Distribution` 的空间 Typed Facet，不是新的 Aggregate Root。

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
bounds                   MultiPolygonField(4326) nullable
min_zoom                 smallint nullable
max_zoom                 smallint nullable
tile_matrix_set          string nullable
content_encoding         string nullable
capabilities             jsonb
render_hints             jsonb
```

变量、band、时间片等**消费选择**不应强制固定在 Distribution 级别；同一 NetCDF/Zarr Distribution 可以由不同 LayerSourceBinding 选择不同变量。

### 5.1 Distribution 与 ServiceInstance

```text
Distribution = “这个 AssetVersion 有一种 MVT / COG / WMS 消费形态”
ServiceInstance = “当前由哪个运行实例真正提供该能力”
```

未来允许：

```text
LayerVersion
   ↓ semantic source
DatasetVersion
   ↓ resolver
Distribution(MVT)
   ↓ runtime resolution
ServiceInstance(Martin instance A)
```

Martin A 换成 Martin B，不应生成新的 DatasetVersion 或 LayerVersion；只有消费形态语义本身变化时才产生新的 Distribution。

---

## 6. LayerVersionProfile

建议表：`sf_layer_version_profile`

Layer 不是 Dataset 的别名：

```text
Dataset = 数据是什么
Layer   = 数据如何作为地图/场景图层被消费和表达
```

同一个 DatasetVersion 可生成多个 LayerVersion：

```text
road_dataset_v3
 ├── highway_layer_v1
 ├── arterial_layer_v2
 ├── traffic_flow_layer_v5
 └── congestion_layer_v4
```

LayerVersionProfile 本身只保存 Layer 级语义：

```text
id                      UUIDv7
asset_version_id        FK AssetVersion UNIQUE
layer_kind              VECTOR | RASTER | TERRAIN | HEATMAP |
                        TRAJECTORY | VECTOR_FIELD | POINT_CLOUD |
                        THREE_D | LIVE | COMPOSITE
query_spec              jsonb
interaction_spec        jsonb
legend_spec             jsonb
render_semantics        jsonb
```

**不再**在 LayerVersionProfile 中放单个 `source_dataset_version`。

原因：一个 LayerVersion 可能合法依赖多个 DatasetVersion。

典型例子：

```text
道路 geometry DatasetVersion
        +
实时交通属性 DatasetVersion
        ↓
拥堵专题 LayerVersion
```

或：

```text
WRF DatasetVersion
 selector=[U10, V10]
        ↓
风矢量 LayerVersion
```

---

## 7. LayerSourceBinding

建议表：`sf_layer_source_binding`

这是 Layer 与 DatasetVersion 之间的正式多源关系。

```text
id                      UUIDv7
layer_version_id        FK AssetVersion
source_dataset_version  FK AssetVersion
binding_key             slug/string
role                    PRIMARY | JOIN | MASK | REFERENCE | AUXILIARY
selector_spec           jsonb
join_spec               jsonb
source_policy           jsonb
priority                int
```

约束：

```text
UNIQUE(layer_version_id, binding_key)
```

`selector_spec` 示例：

```json
{
  "variables": ["U10", "V10"],
  "time": "2026-08-30T08:00:00Z",
  "level": "surface"
}
```

`join_spec` 示例：

```json
{
  "left_key": "road_id",
  "right_key": "road_id",
  "join_type": "left"
}
```

`source_policy` 只声明需要的消费能力，不绑定具体 endpoint：

```json
{
  "preferred_protocols": ["MVT", "PMTILES"],
  "fallback_protocols": ["WFS"],
  "require_tiling": true
}
```

运行时 Resolver 再从对应 DatasetVersion 的 Distribution 中选择实际消费形态。

### 7.1 为什么不能直接持久化 endpoint

Published LayerVersion 的 source 必须最终解析为具体 Dataset AssetVersion，而不是：

```text
https://server-123/tiles/{z}/{x}/{y}.pbf
```

endpoint 是运行状态，可以迁移；DatasetVersion 才是可追溯的语义输入。

### 7.2 Job 输出如何进入 Layer

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
LayerSourceBinding
   ↓
LayerVersion
   ↓
Map / Scene
```

运行中的即时预览可以临时使用 ephemeral Distribution；一旦保存为持久 Map/Scene，则必须先把结果提升为稳定 AssetVersion。

---

## 8. StyleVersionProfile

建议表：`sf_style_version_profile`

Style 是独立 Asset，而不是 Layer/Map JSON 中一段不可复用颜色配置。

```text
id                      UUIDv7
asset_version_id        FK AssetVersion UNIQUE
style_kind              VECTOR | RASTER | SYMBOL | HEATMAP |
                        CLASSIFIED | CONTINUOUS | VECTOR_FIELD |
                        THREE_D | COMPOSITE
semantic_spec           jsonb
legend_spec             jsonb
```

### 8.1 Semantic Style 是主语义

GeoAgent 不应该被迫直接生成大量 MapLibre paint JSON。

```json
{
  "variable": "no2",
  "classification": "continuous",
  "units": "μg/m³",
  "breaks": [20, 40, 60, 80],
  "palette_intent": "sequential_high_is_alert"
}
```

这一份语义可以同时编译到多个渲染器，因此**禁止**在 StyleVersionProfile 上放单一 `renderer_family` 字段。

---

## 9. StyleRendererVariant

建议表：`sf_style_renderer_variant`

它是 StyleVersion 的依赖实体，不是新的 Asset。

```text
id                      UUIDv7
style_version_id        FK AssetVersion
renderer_family         MAPLIBRE | SLD | CESIUM | SERVER_COLORMAP | GENERIC
variant_key             string
renderer_spec           jsonb
compiled_artifact       FK Artifact nullable
priority                int
```

约束：

```text
UNIQUE(style_version_id, renderer_family, variant_key)
```

这样同一个 StyleVersion 可以拥有：

```text
semantic style
 ├── MapLibre variant
 ├── SLD variant
 ├── Cesium variant
 └── server ColorMap variant
```

Renderer-specific 内容只是语义样式的编译结果或实现，不反过来成为 Style 的业务身份。

---

## 10. LayerStyleBinding

建议表：`sf_layer_style_binding`

不要把 Style FK 直接塞进 LayerVersionProfile：

- 一个 Layer 可以有多个样式；
- Map/Scene 中可以覆盖默认样式；
- default / alternate / dark / print 等关系本身有语义。

```text
id                      UUIDv7
layer_version_id        FK AssetVersion
style_version_id        FK AssetVersion
role                    DEFAULT | ALTERNATE | FALLBACK
binding_key             string
priority                int
```

约束：

- 两端必须同 tenant，或引用允许共享的平台级资产；
- layer_version 必须属于 layer Asset；
- style_version 必须属于 style Asset；
- 每个 LayerVersion 最多一个 `role=DEFAULT`；
- `UNIQUE(layer_version_id, binding_key)`。

跨 `Asset.asset_type` 约束第一版由 Domain Service + model validation + tests 保证；必要时再升级数据库 trigger。

---

## 11. MapVersionProfile

建议表：`sf_map_version_profile`

Map 是**保存的二维空间组合**，不是截图。

```text
id                      UUIDv7
asset_version_id        FK AssetVersion UNIQUE
projection              string, default EPSG:3857
camera_spec             jsonb
interaction_spec        jsonb
ui_spec                  jsonb
extent                   MultiPolygonField(4326) nullable
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

### 11.1 Basemap 也是 Layer

MapVersionProfile **不保存 `basemap_url` 或 `basemap_spec` 外部 Provider 配置**。

底图应建模成正常 Layer Asset：

```text
OSM / 企业底图 / 卫星影像 Dataset
   ↓
Distribution
   ↓
Basemap LayerVersion
   ↓
MapLayerBinding(role=BASEMAP)
```

系统默认底图可以是平台级共享 Asset。

这样底图仍然具备：

- 权限；
- 版本；
- provenance / 来源说明；
- Provider 可替换；
- 离线/内网替换能力。

---

## 12. MapLayerBinding

Map 和 Layer 是 many-to-many，但中间关系自身有大量语义，必须建正式实体。

```text
id                      UUIDv7
map_version_id          FK AssetVersion
layer_version_id        FK AssetVersion
binding_key             string
role                    BASEMAP | CONTENT | OVERLAY | ANNOTATION
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
UNIQUE(map_version_id, binding_key)
UNIQUE(map_version_id, order_index)
0 <= opacity <= 1
min_zoom <= max_zoom
```

允许同一个 LayerVersion 在同一 Map 中出现多次。

例如：

```text
roads-base        → 同一个 roads LayerVersion，灰色底层
roads-congestion  → 同一个 roads LayerVersion，顶部拥堵着色
```

两次出现通过不同 `binding_key` 和 override 区分。

---

## 13. SceneVersionProfile

Scene 与 Map 必须分开，不做一个万能 `Map3D`。

Scene 面向：

- 3D Tiles；
- 建筑；
- Terrain / DEM；
- 点云；
- 地下空间；
- 污染羽流；
- 三维气象场；
- 数字孪生 / 仿真结果。

建议表：`sf_scene_version_profile`

```text
id                      UUIDv7
asset_version_id        FK AssetVersion UNIQUE
camera_spec             jsonb
lighting_spec           jsonb
time_spec               jsonb
scene_environment_spec  jsonb
extent                   MultiPolygonField(4326) nullable
```

### 13.1 Terrain 也通过 Layer 进入 Scene

SceneVersionProfile 不保存 `terrain_url`。

```text
DEM / terrain Dataset
  ↓
Terrain Distribution
  ↓
Terrain LayerVersion
  ↓
SceneLayerBinding(role=TERRAIN)
```

这样 Terrain 和普通 3D 内容共用一致的 Asset / Distribution / Permission / Provenance 机制。

---

## 14. SceneLayerBinding

建议表：`sf_scene_layer_binding`

```text
id                      UUIDv7
scene_version_id        FK AssetVersion
layer_version_id        FK AssetVersion
binding_key             string
role                    TERRAIN | BASEMAP | CONTENT | EFFECT | ANNOTATION
order_index             int
visible                 bool
opacity                 decimal
style_override_version  FK AssetVersion nullable
transform_spec          jsonb
clipping_spec           jsonb
temporal_selector       jsonb
interaction_override    jsonb
```

约束：

```text
UNIQUE(scene_version_id, binding_key)
UNIQUE(scene_version_id, order_index)
0 <= opacity <= 1
```

第一阶段可先完成领域模型与 contract，不要求立即接 Cesium。

---

## 15. 关系总图

```text
                              Asset
                                │
                           AssetVersion
                                │
          ┌────────────┬────────┼──────────┬──────────┐
          │            │        │          │          │
       Dataset       Layer    Style       Map       Scene
       Profile      Profile   Profile    Profile     Profile
          │            │        │          │          │
          │            │        ├─ StyleRendererVariant
          │            │        │
          │      LayerStyleBinding
          │            │
          └─ LayerSourceBinding
                       │
                       ├────────── MapLayerBinding ───── Map
                       │
                       └────────── SceneLayerBinding ─── Scene

Dataset AssetVersion
    ├── Artifact
    └── Distribution ── SpatialDistributionProfile
```

关键语义链：

```text
DatasetVersion
  ↓ available forms
Distribution

DatasetVersion(s)
  ↓ LayerSourceBinding
LayerVersion
  ↓ LayerStyleBinding
StyleVersion

LayerVersion
  ↓ MapLayerBinding
MapVersion

LayerVersion
  ↓ SceneLayerBinding
SceneVersion
```

---

## 16. Asset type keys

第一版稳定 key：

```text
dataset
layer
style
map
scene
```

专业差异通过 trait / profile / schema / package 扩展：

```text
dataset + traits=[spatial, temporal]
dataset + traits=[raster, remote_sensing]
dataset + traits=[multidimensional, meteorology]
layer + traits=[live]
layer + traits=[vector_field]
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

---

## 17. 版本与可变性

### 17.1 必须产生新 AssetVersion

- Dataset 数据快照内容改变；
- 实时 Dataset 的 schema / 单位 / 来源语义改变；
- Layer source bindings / selector / query 发生语义变化；
- Style semantic rules 发生变化；
- Map 的正式 layer composition 改变并保存/发布；
- Scene 的正式 composition 改变并保存/发布。

### 17.2 通常不产生新 AssetVersion

- Martin 实例 A 换成 B；
- TiTiler pod 重启；
- signed URL 刷新；
- cache key 改变；
- endpoint host 迁移但 Distribution 语义不变；
- renderer variant 重新编译但语义输出等价且仍属于同一未发布版本。

Published 版本的依赖实体同样视为不可原地改写；若编译产物构成 content hash，应通过发布服务管理。

---

## 18. “计算即地图”的正式链路

以 AERMOD 输出为例，但链路对其他模型通用：

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
  ↓ LayerSourceBinding
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

---

## 19. GeoAgent 工具边界

未来 GeoAgent 只能调用正式 Fabric API，例如：

```text
find_dataset
resolve_asset_alias
create_layer
bind_layer_source
apply_style
set_layer_filter
add_layer_to_map
remove_layer_from_map
set_map_camera
save_map_version
```

Agent 不得绕过 AssetVersion / Permission / Provenance，直接把临时 URL 塞入持久 Map JSON。

例如：

```text
“只显示 NO₂ > 80 μg/m³ 的区域”
```

优先变成：

```text
MapLayerBinding.filter_spec
```

而不是重新运行 AERMOD。

---

## 20. 反向压力测试

设计在写 migration 前必须至少通过以下五类场景。

### 20.1 道路矢量

```text
DatasetVersion(kind=VECTOR)
  ├── Distribution(PostGIS)
  ├── Distribution(GeoParquet)
  └── Distribution(MVT/PMTiles)
        ↓
多个 LayerVersion
        ↓
多个 StyleVersion
        ↓
Map
```

结论：一个 Dataset 多消费形态、多 Layer、多 Style，无模型冲突。

### 20.2 AERMOD 浓度 COG

```text
Artifact(original TIFF)
  ↓ derived
DatasetVersion(kind=RASTER)
  ↓
Distribution(COG)
  ↓
LayerVersion(kind=RASTER)
  ↓
continuous concentration Style
  ↓
Map
```

结论：Artifact、DatasetVersion、Distribution、Layer 不混淆；可完整追踪计算来源。

### 20.3 WRF NetCDF / Zarr 风场

```text
DatasetVersion(kind=MULTIDIMENSIONAL)
  ↓
Distribution(NetCDF/Zarr)
  ↓
LayerSourceBinding.selector_spec = [U10, V10, time]
  ↓
LayerVersion(kind=VECTOR_FIELD)
```

结论：变量/时间选择属于 Layer source binding，而不是复制 Dataset 或 Distribution。

### 20.4 道路 + 实时交通多源专题图

```text
roads DatasetVersion
       ┐
       ├─ LayerSourceBinding(PRIMARY/JOIN) → congestion LayerVersion
       │
traffic-live DatasetVersion
       ┘
```

结论：LayerSourceBinding 必须是 one Layer → many DatasetVersions；单 FK 模型不成立。

### 20.5 3D Tiles + Terrain

```text
building DatasetVersion → 3D Tiles Distribution → building Layer
terrain DatasetVersion  → terrain Distribution  → terrain Layer
                                          ↓
                                  SceneLayerBinding
                          role=CONTENT / TERRAIN
                                          ↓
                                         Scene
```

结论：Scene 不持久化 provider URL；Terrain 与 3D 内容都保持正式 Layer 语义。

### 20.6 外部共享底图

```text
platform-level Dataset Asset
  ↓ external/live Distribution
  ↓ Basemap LayerVersion
  ↓ MapLayerBinding(role=BASEMAP)
```

结论：底图不是 Map JSON 里的特殊字符串；仍受统一权限、版本和来源管理。

---

## 21. 第一批数据库约束

至少需要：

1. 每个 Typed Profile 对 `asset_version_id` UNIQUE；
2. LayerSourceBinding 的 `binding_key` 在 LayerVersion 内唯一；
3. LayerStyleBinding 的 `binding_key` 在 LayerVersion 内唯一；
4. StyleRendererVariant 的 `(style_version, renderer_family, variant_key)` 唯一；
5. Map/Scene binding 的 `binding_key` 在所属版本内唯一；
6. Map/Scene binding 的 `order_index` 在所属版本内唯一；
7. opacity 范围 `[0,1]`；
8. min_zoom <= max_zoom；
9. Layer source 与 Layer tenant 一致，或 source 为允许引用的平台级共享资产；
10. LayerStyleBinding 的两端版本类型正确；
11. MapLayerBinding / SceneLayerBinding 的两端版本类型正确；
12. Style override 必须指向 style AssetVersion；
13. Published Dataset/Layer/Style/Map/Scene version 及其组成关系不可原地修改。

跨 `Asset.asset_type` 的类型约束无法用普通 FK 表达时，第一阶段采用：

```text
Domain Service
+ model clean()/validation
+ tests
```

不要为了数据库“一次性完美”引入 GenericForeignKey 或难维护的 polymorphic FK。

---

## 22. 第一批索引

```text
DatasetVersionProfile.spatial_extent                 GiST
MapVersionProfile.extent                             GiST
SceneVersionProfile.extent                           GiST
SpatialDistributionProfile.bounds                    GiST
DatasetVersionProfile(start_time, end_time)
LayerSourceBinding(layer_version_id, role)
LayerSourceBinding(source_dataset_version_id)
LayerStyleBinding(layer_version_id, role)
StyleRendererVariant(style_version_id, renderer_family)
MapLayerBinding(map_version_id, order_index)
SceneLayerBinding(scene_version_id, order_index)
```

在出现真实查询基准前，不预建大量 JSONB GIN 索引。

---

## 23. Django app 边界

建议新增独立 app：

```text
spatial_fabric.spatial
```

负责：

```text
DatasetVersionProfile
SpatialDistributionProfile
LayerVersionProfile
LayerSourceBinding
StyleVersionProfile
StyleRendererVariant
LayerStyleBinding
MapVersionProfile
MapLayerBinding
SceneVersionProfile
SceneLayerBinding
```

`assets` app 继续保持纯 Asset Kernel，不把地图字段反向塞进去。

Provider adapters 后续分开：

```text
providers/martin
providers/titiler
providers/geoserver
providers/object_storage
```

MapLibre / deck.gl / Cesium 属于前端 renderer，不进入 Django Domain Model。

---

## 24. 建议 migration 顺序

地图内核第一批 migration 不一次塞进巨型 0001：

```text
spatial/0001_dataset_distribution_profiles
        ↓
spatial/0002_layer_style_profiles
        ↓
spatial/0003_source_style_renderer_bindings
        ↓
spatial/0004_map_profiles_bindings
        ↓
spatial/0005_scene_profiles_bindings
```

每一步必须真实验证：

```text
makemigrations --check
migrate on empty PostGIS
pytest with real migrations
```

如果 Django 自动生成依赖图与上述编号不同，以**无环、可从空库重建**为最高原则，不为了编号美观手改出循环。

---

## 25. V0.1 垂直验证切片

地图内核不是以“把表建完”为完成标准，而是以下链路真的跑通：

```text
register Dataset
↓
inspect spatial metadata
↓
register Distribution
↓
create Layer + LayerSourceBinding
↓
apply Style
↓
save Map + MapLayerBinding
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

---

## 26. 当前不做

此规格阶段不引入：

- GeoServer / Martin / TiTiler 的具体 provider model；
- Cesium 实际前端；
- OGC API 全量实现；
- STAC Catalog 服务；
- Tile cache 调度；
- Job / Run 的正式表；
- GeoAgent；
- 行业专属 Layer 类型；
- 复杂数据库 trigger。

这些都必须在核心空间对象边界稳定后按独立 Phase / ADR 接入。

---

## 27. 与 Phase A 的关系

本文不修改 Phase A 的 Asset Kernel，而是在其上扩展 Typed Facet：

```text
Phase A
Asset / AssetVersion / Artifact / Distribution

          ↓ additive extension

Spatial Map Kernel
Dataset / Layer / Style / Map / Scene typed profiles
+ LayerSourceBinding
+ Style renderer variants
+ Map/Scene composition bindings
```

因此 PR #1 可以独立完成 Review/合并；地图内核作为后续独立 PR 实现，避免 Phase A 基线无限扩张。

既定开发顺序仍以项目状态文件为准：如果 Phase B IAM & Governance 优先，则地图内核规格保持冻结而不抢占 Phase B 实现顺序。

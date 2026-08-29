# Spatial Fabric 前端产品面设计

> 状态：架构基线。本文定义产品面边界，不规定最终视觉设计。

## 1. 结论

建议建设 **3 个逻辑前端面，但早期只维护 1 个前端 monorepo**：

```text
Portal
GeoStudio
Fabric Console
```

它们不是三套“中台”。Spatial Fabric 是共同平台/Control Plane，三个前端只是面向不同角色的产品体验层。

## 2. Portal｜门户

主要用户：客户业务人员、合作伙伴、外部用户、公开用户。

主要能力：

- 统一搜索 / Resource Catalog；
- Dataset、Map、Scene、Model、Workflow、Application 发现；
- 应用中心；
- 收藏、最近使用；
- Shared With Me；
- AccessRequest；
- 个人 Task/Approval；
- Developer Center；
- 可配置专题站点和行业门户。

Portal 不承担复杂专业建模，也不直接管理底层 Provider。

## 3. GeoStudio｜专业工作台

主要用户：GIS/遥感/环境/模型/数据分析人员、实施工程师、高级客户。

主要能力：

```text
Data Browser
Map / Scene Composer
Spatial Analysis
Model Runner
Workflow Designer
Scenario Lab
Job / Result Explorer
Provenance Viewer
GeoAgent Workspace
Report Builder
```

GeoStudio 是专业生产力工具，不是后台管理系统。

## 4. Fabric Console｜管理控制台

主要用户：平台管理员、租户管理员、安全管理员、运维、实施人员。

主要能力：

```text
Tenant / Org / Principal
Role / Policy / Entitlement / Quota
Workspace / Project / Environment
Asset Governance
ServiceDeployment / ServiceInstance
ComputePool / StoragePool
Job / Run Operations
Usage / Cost
Audit / Security
Backup / Restore
Package / Feature Flag
System Settings
```

Console 默认安全等级高于 Portal，生产环境应支持独立访问域、MFA/SSO、网络限制与管理审计。

## 5. 推荐前端仓库

```text
yujian-spatial-web/
├── apps/
│   ├── portal/
│   ├── studio/
│   └── console/
└── packages/
    ├── design-system/
    ├── api-sdk/
    ├── auth/
    ├── map-kit/
    ├── resource-picker/
    ├── workflow-ui/
    └── shared-types/
```

三个 App 可分别部署：

```text
portal.example.com
studio.example.com
console.example.com
```

但共享：

- Design Token / Design System；
- API SDK；
- Auth Context；
- MapLibre/deck.gl Map Kit；
- Resource Picker；
- Error/Telemetry 基础设施。

## 6. 权限原则

前端隐藏按钮不是授权。

所有动作最终必须由 Fabric 后端判断：

```text
Authentication
∩ Entitlement
∩ Authorization
∩ Quota
∩ Policy
```

三个前端都不得直接把 GeoServer/Martin/TiTiler/Kubernetes 管理接口暴露给普通用户。

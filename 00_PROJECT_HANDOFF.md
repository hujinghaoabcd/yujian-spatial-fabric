# 00 — Spatial Fabric 项目交接入口

> **任何新对话、新开发会话或新开发者接手时，第一份必须阅读的文件。**  
> 项目状态必须保存在 GitHub，而不是依赖聊天记忆。

## 1. 项目标识

- Repository：`hujinghaoabcd/yujian-spatial-fabric`
- Python namespace：`spatial_fabric`
- 当前阶段：**Phase A — Tenancy + Principal + Asset Kernel**
- 架构：**Django Modular Monolith Control Plane + Independent Workers/Providers**
- Domain ID：UUIDv7
- API prefix：`/api/v1/`

## 2. 接手阅读顺序

1. 本文件；
2. `ARCHITECTURE_INDEX.md`；
3. `docs/architecture/` 中的架构基线；
4. `docs/domain-model/` 中的领域模型；
5. `docs/database/phase-a-erd.md`；
6. `docs/adr/`；
7. `docs/project/CURRENT_STATUS.md`。

若下层代码和上位规范冲突，**禁止静默重构上位架构**。优先使用 Adapter / Provider / Projection / Typed Facet / Package；核心 Contract 确需改变时必须写 ADR。

## 3. 冻结边界

```text
Asset ≠ AssetVersion ≠ Artifact ≠ Distribution
Task ≠ Workflow ≠ Job ≠ Run ≠ Result
Capability ≠ Runtime ≠ Runner
Dataset ≠ Layer ≠ Style ≠ Map ≠ Scene
Tenant ≠ OrgUnit ≠ Workspace ≠ Project ≠ Environment
Principal ≠ Role ≠ Entitlement ≠ Quota
AssetVersion ≠ ServiceDeployment ≠ ServiceInstance
Policy ≠ Provenance ≠ Evaluation ≠ Audit ≠ Observability
```

Published Version 不可原地修改；执行前 Alias 必须解析成具体 Version；Provider-specific 概念不得泄漏到 Core Model。

## 4. 当前已写入远程分支

当前开发分支：`feat/phase-a-foundation`

已包含：

- Python 3.12 / Django 5.2 LTS / DRF / PostGIS 工程基线；
- split settings、health、OpenAPI、JSON logging、request ID；
- UUIDv7；
- `iam.Account` 自定义 Django 用户模型；
- `Principal` 领域主体；
- `Tenant / Workspace / Project / Environment`；
- `Asset / AssetVersion / AssetAlias / Artifact / Distribution`；
- Phase A 跨租户与资产引用不变量测试；
- GitHub Actions migration preview CI。

## 5. 目前明确尚未完成

- 正式 Django migrations；
- `migrate` 验证；
- PostgreSQL/PostGIS 约束运行验证；
- Phase B Role/Policy/Entitlement/Quota；
- Job/Run/Result；
- ServiceDeployment/ServiceInstance；
- Temporal、Object Storage、Martin、TiTiler、GeoServer 等 Provider；
- Portal / GeoStudio / Fabric Console 前端。

当前生成环境无法访问 Python 包仓库，所以**禁止把未运行的 Django checks/migrations/pytest 描述为“已通过”**。GitHub CI 将承担第一轮真实依赖验证。

## 6. 当前数据库任务

请先阅读：

```text
docs/database/phase-a-erd.md
docs/database/phase-a-migration-plan.md
```

首批 migration 必须避免自定义 `AUTH_USER_MODEL` 的依赖环，推荐顺序：

```text
iam/0001_initial       = Account only
tenancy/0001_initial   = Tenant / Workspace / Project / Environment
iam/0002_principal     = Principal
assets/0001_initial    = Asset Kernel
```

## 7. Account 与 Principal

- `Account`：登录认证账户；
- `Principal`：授权主体。

一个 Principal 可以代表 Human、ServiceAccount、Agent、ExternalApplication。Agent 不应伪装成 Django User。未来 Keycloak/OIDC/SAML 通过 IdentityLink 关联，不使用外部 IdP ID 替代 Fabric UUID。

## 8. 前端边界

三个逻辑产品面：

```text
Portal          = 资源/应用/客户伙伴入口
GeoStudio       = 地图/数据/模型/Workflow/Scenario/Agent 专业工作台
Fabric Console  = 租户/权限/安全/服务/计算/用量/运维管理控制台
```

它们不是三套“中台”。建议以后建立 `yujian-spatial-web` monorepo，共享 design-system、map-kit、auth、api-sdk。

## 9. 中文注释规范

核心领域代码必须优先使用详细中文注释/docstring 解释：

- 为什么存在这个对象；
- 它和相邻对象为什么必须分开；
- 哪些修改会破坏长期架构；
- 哪些字段只是临时稳定引用；
- 哪些约束不能只依赖 ORM。

公开标准、协议名、类名、字段 key 保留英文，避免生硬翻译影响互操作。

## 10. 每次开发结束必须更新

1. `docs/project/CURRENT_STATUS.md`；
2. 本文件中的阶段/下一任务（如有变化）；
3. structural decision 对应 ADR；
4. migration 和测试状态；
5. Known Risk。

新对话可直接使用：

```text
继续 yujian-spatial-fabric。先读取 00_PROJECT_HANDOFF.md，再按其中优先级读取文档；不要重设计已冻结架构，从 CURRENT_STATUS.md 的下一任务继续。
```

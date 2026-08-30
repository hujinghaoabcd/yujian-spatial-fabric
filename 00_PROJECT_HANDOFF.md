# 00 — Spatial Fabric 项目交接入口

> **任何新对话、新开发会话或新开发者接手时，第一份必须阅读的文件。**  
> 项目状态必须保存在 GitHub，而不是依赖聊天记忆。

## 1. 项目标识

- Repository：`hujinghaoabcd/yujian-spatial-fabric`
- Python namespace：`spatial_fabric`
- 架构：**Django Modular Monolith Control Plane + Independent Workers / Runners / Providers**
- Domain ID：UUIDv7
- API prefix：`/api/v1/`
- 当前开发分支：`feat/phase-b2-governance-controls`
- 当前 stacked PR：**#5 — Phase B2 Governance Controls（Draft）**
- PR base：`feat/phase-b-iam-governance`
- 当前阶段：**B2.2 Resource Sharing 已完成，下一任务 B2.3 Commercial Controls**

## 2. 接手阅读顺序

1. 本文件；
2. `ARCHITECTURE_INDEX.md`；
3. `docs/project/CURRENT_STATUS.md`；
4. `docs/database/phase-b-iam-governance-spec.md`；
5. `docs/database/phase-b1-role-grant-resolver-spec.md`；
6. `docs/database/phase-b2-governance-controls-spec.md`；
7. `docs/database/phase-b2-resource-sharing-spec.md`；
8. `docs/database/phase-b2-resource-sharing-amendment-001.md`；
9. 相关 ADR / architecture / database 文档。

若下层代码和上位规范冲突，**禁止静默重构上位架构**。优先使用 Adapter / Provider / Projection / Typed Facet / Package；核心 Contract 确需改变时必须写 ADR 或正式 Amendment。

> 注意：三份大型 FINAL 上位规范目前仍未全文同步进仓库。`ARCHITECTURE_INDEX.md` 已标记该缺口。不得把当前摘要或 Implementation Spec 误称为 FINAL 全文。

## 3. 冻结架构边界

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

继续保持：

```text
Account ≠ Principal
Role ≠ Policy
RoleAssignment ≠ PolicyAttachment
ShareGrant ≠ RoleAssignment
Entitlement ≠ Quota ≠ Budget
```

Published Version 不可原地修改；Provider-specific 概念不得泄漏到 Core Model。

## 4. 产品/平台总方向

```text
Spatial Fabric
   ↓
GeoSense / GeoCore / GeoPhysics / GeoMind / GeoSim
   ↓
GeoAgent
   ↓
N industry applications
```

Spatial Fabric 是统一资产、空间数据、运行计算、任务编排、治理、安全、追溯与互操作基座，不是单一 GIS Server、地图门户、数据中台、模型平台或 Agent 平台。

GeoAgent 未来执行必须走：

```text
GeoAgent → Task / Workflow → Fabric Job → Runner
```

禁止任意绕过 Fabric Job/Runner 直接执行外部 Python 或模型。

## 5. 当前已经稳定的 Foundation / IAM

### Phase A

已包含：

- Django 5.2 LTS / DRF / PostgreSQL 17 / PostGIS 3.5；
- split settings、health、OpenAPI/Swagger、JSON logging、request ID；
- UUIDv7；
- `iam.Account`；
- `Principal`；
- `Tenant / Workspace / Project / Environment`；
- `Asset / AssetVersion / AssetAlias / Artifact / Distribution`；
- provider-neutral database / storage / service reference 方向；
- 生产 Docker + Preview smoke chain。

### Phase B1

已经实现并稳定：

- `Privilege`；
- `RoleDefinition / RolePrivilege`；
- `Group / GroupMembership`；
- `RoleAssignment`；
- Core Privilege seed migration；
- `RoleGrantResolver`。

B1 只回答：

> Principal 在给定管理 Scope 下有哪些候选 RBAC GRANT？

它不承担 Policy、Share、Entitlement、Quota、Approval 等治理语义。

## 6. B2.1 Policy Core — 已完成

bounded context：

```text
spatial_fabric.governance
```

已经实现：

- `PolicyDefinition`；
- `PolicyVersion`；
- `PolicyAttachment`；
- `PolicyPublicationService`；
- Published PolicyVersion immutable contract；
- `ALLOW / DENY / REQUIRE_APPROVAL` effect contract；
- `governance/0001_initial.py`。

策略安全优先级方向冻结为：

```text
explicit DENY > REQUIRE_APPROVAL > ALLOW
```

Policy 仍只是最终 AuthorizationService 的输入，不是最终决策本身。

## 7. B2.2 Resource Sharing — 已完成

bounded context：

```text
spatial_fabric.sharing
```

已经实现：

- `ShareGrant`；
- `ShareGrantPrivilege`；
- `AccessRequest`；
- `AccessRequestPrivilege`；
- `ShareGrantService`；
- `AccessRequestService`；
- `ShareGrantResolver`；
- `sharing/0001_initial.py`。

跨模块资源继续使用值语义：

```text
ResourceRef
- tenant_id
- resource_kind
- resource_id
```

`ResourceRef` 不是万能 Resource table，也不能替代普通同模块 FK。

### B2.2 关键不变量

- grantee 必须 Principal XOR Group；
- 被分享主体必须属于资源 Tenant；
- 第一版禁止直接跨 Tenant ShareGrant；
- 外部协作者未来应先成为资源 Tenant 内的 guest/federated Principal；
- deprecated / unknown Privilege fail closed；
- 非空 `conditions` 当前 fail closed，复杂条件交给 Policy evaluator；
- revoke 必须保存撤销主体与时间证据；
- 新 Grant 的 `valid_until` 必须晚于创建时刻；
- AccessRequest fulfillment 生成独立 ShareGrant evidence，不偷偷修改既有授权。

### Contract Amendment 001

原先“同 ResourceRef + 同主体最多一条 ACTIVE ShareGrant”与按 `valid_until` 推导过期的设计会产生时态死锁。

现已正式修正为：

```text
同一 ResourceRef
+ 同一 Principal / Group
+ 可存在多条独立 ShareGrant evidence
```

Resolver：

- 对每条 evidence 独立检查 ACTIVE / 时间窗口 / conditions；
- 保留全部 evidence；
- `effective_privilege_keys` 只对 privilege key 去重。

撤销一条 Grant 不会误伤另一条来源。

ShareGrant 仍只是：

```text
resource-level explicit ALLOW candidate
```

最终组合仍满足：

```text
explicit DENY > REQUIRE_APPROVAL > ShareGrant / RBAC ALLOW
```

## 8. AuthorizationService 最终组合方向

冻结方向：

```text
AuthorizationService
  = RoleGrantResolver
  + PolicyEvaluator
  + ShareGrantResolver
  + EntitlementEvaluator
  + QuotaEvaluator
  + Approval / Delegation / Risk controls
```

任何一个子模块都不能提前自称最终 AuthorizationDecision。

Agent 最终有效权限仍应满足：

```text
Agent permission
∩ delegated user permission
∩ tool permission
∩ project policy
∩ action risk policy
```

## 9. 当前正式 migrations

当前主要迁移链已经固化：

```text
common/0001_enable_postgis
        ↓
tenancy/0001_initial
        ↓
Django contenttypes/auth
        ↓
iam/0001_initial
        ↓
assets/0001_initial
        ↓
assets/0002_initial
        ↓
iam/0002_...
        ↓
iam/0003_seed_core_privileges
        ├───────────────┐
        ↓               ↓
governance/0001     sharing/0001
```

`sharing/0001_initial.py` 由 Django 5.2.17 在真实 GitHub Runner/PostGIS 环境生成，不是手写猜测 migration。

一次性 B2.2 migration workflow 已删除，禁止把临时 workflow 留作正式构建路径。

## 10. B2.2 最终 CI 状态

永久 CI 对提交：

```text
f69dea44bde3d103626fe49fd971bd63cb8a9eef
```

完整通过：

- Django `manage.py check`；
- model/migration sync；
- 空 PostgreSQL/PostGIS `migrate --noinput`；
- 全部领域测试（59 tests）；
- coverage threshold（最近验证 84.90%）；
- Provider leakage check；
- Ruff；
- strict mypy；
- pip-audit；
- production Docker build；
- production container + `scripts/start-preview.sh`；
- `/health/ready`；
- `/api/schema/`；
- `/api/docs/`。

因此 B2.2 已达到收口条件。

## 11. 下一开发阶段：B2.3 Commercial Controls

下一步只进入：

```text
EntitlementGrant
Quota
Budget
Usage hard-limit integration
```

开始编码前先冻结：

```text
EntitlementGrant ≠ Quota ≠ Budget ≠ UsageRecord
```

建议语义方向：

- `EntitlementGrant`：某 Tenant/Principal/Scope 是否拥有某产品、能力或商业功能的“资格”；
- `Quota`：某 metric 在某作用域和时间窗口内的可消费技术上限；
- `Budget`：成本/货币/计算信用额度治理，不和技术 quota 合并；
- `UsageRecord`：已发生使用事实，不等于 Quota policy；
- hard limit：必须通过统一 evaluator / reservation contract 执行，不能散落在 API controller。

B2.3 必须先完成 dedicated spec + invariants，再生成正式 migration；完整永久 CI 绿后才可进入 B2.4。

## 12. 后续 B2.4

尚未开始：

```text
PermissionBoundary
Approval integration
JIT / Temporary Elevation
Break-glass
Delegation
```

禁止提前混入 B2.3。

## 13. 当前 Known Risk

### 13.1 `uv.lock` 尚未固化

正式发布/生产冻结前必须：

1. 生成并提交 `uv.lock`；
2. CI 使用 lockfile；
3. Docker 使用 frozen install。

### 13.2 FINAL 上位规范尚未全文同步

三份大型 FINAL 架构/技术/领域规范仍未全文同步。当前 implementation docs 只能作为实现依据和阶段性契约，不得冒充完整 FINAL。

### 13.3 外部 Preview 尚未实际部署

仓库内生产 Docker 与 Preview smoke 已通过，但 Render 账号侧还未真正 Apply Blueprint，因此没有真实 `*.onrender.com` URL。禁止虚构外部部署成功。

## 14. 当前禁止

- 不重新设计已经冻结的总体架构；
- 不把 Entitlement / Quota / Budget 塞回 Role；
- 不把 ShareGrant 当最终 AuthorizationDecision；
- 不创建万能 `resource(id, type, json)`；
- 不让 governance/sharing 直接 FK 每一种 Asset/Job/Result/Map 类型；
- 不把 Provider ID 写进 Core Model；
- 不拆微服务；
- 不开始 GeoAgent；
- 不加入 EnvOS/ParkOS 等行业专属表；
- 不因 Render/部署便利改变核心领域边界。

## 15. 前端边界

三个逻辑产品面保持：

```text
Portal          = 资源/应用/客户伙伴入口
GeoStudio       = 地图/数据/模型/Workflow/Scenario/Agent 专业工作台
Fabric Console  = 租户/权限/安全/服务/计算/用量/运维管理控制台
```

以后建议使用 `yujian-spatial-web` monorepo；当前后端开发不创建三套独立前端工程。

## 16. 中文注释与工程纪律

核心领域代码优先使用详细中文注释/docstring解释：

- 对象为什么存在；
- 与相邻对象为什么不能合并；
- 哪些约束不能只靠 ORM/controller；
- 哪些引用是 provider-neutral 稳定值；
- 哪些行为必须 fail closed。

每个阶段结束必须：

1. 正式 migration；
2. 空库 migrate；
3. invariants tests；
4. Provider leak；
5. Ruff；
6. strict mypy；
7. pip-audit；
8. production Docker build；
9. Preview smoke；
10. 更新 `CURRENT_STATUS.md` 与本文件。

## 17. 新对话继续方式

直接输入：

```text
继续 yujian-spatial-fabric。先读取 00_PROJECT_HANDOFF.md 和 docs/project/CURRENT_STATUS.md；不要重新设计已冻结架构，从 B2.3 Commercial Controls 继续。
```

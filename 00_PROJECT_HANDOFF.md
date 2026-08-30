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
- 当前阶段：**B2.4 Elevated Access 已完成，下一任务为最终 AuthorizationService 组合决策器**

## 2. 接手阅读顺序

1. 本文件；
2. `ARCHITECTURE_INDEX.md`；
3. `docs/project/CURRENT_STATUS.md`；
4. `docs/database/phase-b-iam-governance-spec.md`；
5. `docs/database/phase-b1-role-grant-resolver-spec.md`；
6. `docs/database/phase-b2-governance-controls-spec.md`；
7. `docs/database/phase-b2-resource-sharing-spec.md`；
8. `docs/database/phase-b2-resource-sharing-amendment-001.md`；
9. `docs/database/phase-b2-commercial-controls-spec.md`；
10. `docs/database/phase-b2-elevated-access-spec.md`；
11. 相关 ADR / architecture / database 文档。

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
EntitlementGrant ≠ Quota ≠ Budget ≠ UsageRecord
PermissionBoundary ≠ Role ≠ RoleAssignment
Approval ≠ ShareGrant ≠ RoleAssignment
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

同一 ResourceRef + 同一 Principal / Group 允许存在多条独立 ShareGrant evidence；Resolver 独立检查每条 evidence 并只对最终 privilege key 去重。撤销一条 Grant 不会误伤另一条来源。

ShareGrant 仍只是：

```text
resource-level explicit ALLOW candidate
```

## 8. B2.3 Commercial Controls — 已完成

bounded context：

```text
spatial_fabric.commercial
```

### 8.1 已实现对象

- `EntitlementGrant`
- `Quota`
- `Budget`
- `UsageReservation`
- `UsageReservationQuota`
- `UsageCounter`
- `UsageRecord`

严格保持：

```text
EntitlementGrant ≠ Quota ≠ Budget ≠ UsageRecord
```

语义：

- Entitlement = 商业产品/能力资格；
- Quota = 技术 metric + limit + window + enforcement policy；
- Budget = 货币/成本治理 policy evidence；
- UsageRecord = 已发生使用事实；
- UsageReservation / UsageCounter = 并发安全执行机制，不是新的授权根对象。

### 8.2 已实现 evaluator / service

- `EntitlementEvaluator`
- `QuotaEvaluator`
- `BudgetEvaluator`
- `UsageReservationService.reserve_for_context()`
- `UsageReservationService.commit()`
- `UsageReservationService.release()`

### 8.3 Quota reservation 关键契约

一次业务操作只有一个顶层：

```text
UsageReservation
```

若同时命中 Tenant / Workspace / Project / Environment 等多条适用 Quota，则使用：

```text
UsageReservation
  └─ UsageReservationQuota * N
```

保存每条独立 quota evidence。

这样同一 idempotency key 可以原子检查/预留全部适用 HARD quota，而不是“先 SUM 再 INSERT”造成竞态。

关键不变量：

- reservation fingerprint 幂等；
- retry 不重复增加 `reserved_value`；
- 多 quota 一次事务处理；
- counter / reservation 使用 row lock；
- 任一 HARD quota 超限则整个 reservation 回滚；
- SOFT / OBSERVE 可继续，但保存 `exceeded_snapshot`；
- stale reservation 到期释放，避免 ghost reserved capacity；
- commit 把 reserved 原子转入 consumed；
- GAUGE / CONCURRENCY 可 release committed capacity；
- CONSUMPTION 一旦 commit 不允许通过 release 擦除事实，未来纠错需独立 adjustment/credit 事件；
- Quota 写服务把共享 Scope Loader 异常收口为稳定 `QuotaControlError`。

### 8.4 精确 decision evidence

`UsageReservationQuota` 固化决策时：

```text
limit_snapshot
 enforcement_mode_snapshot
consumed_value_snapshot
reserved_value_snapshot
projected_value_snapshot
exceeded_snapshot
```

因此幂等重试可原样重放原始 quota decision，而不是事后根据 projected 值近似倒推。

### 8.5 Budget 第一版边界

当前 `BudgetEvaluator` 只返回 Scope 继承链上的成本/货币治理 policy evidence。

**没有 normalized cost ledger 前，不伪造实时预算扣减。**

后续如需成本 reservation / settlement，必须先引入独立、可审计的 cost/charge ledger contract，而不是复用技术 Quota counter。

### 8.6 正式 migration

B2.3 正式 migration：

```text
commercial/0001_initial.py
```

由 Django 5.2.17 在真实 PostgreSQL 17 / PostGIS 3.5 Runner 中生成并验证。

首次固化提交：

```text
4d6d5d0a3aa6c19f2bf123764755397e6b179984
```

message：

```text
db: add verified B2.3 commercial controls schema
```

一次性 B2.3 migration/finalize workflows 已全部删除。

## 9. AuthorizationService 最终组合方向

冻结方向：

```text
AuthorizationService
  = RoleGrantResolver
  + PolicyEvaluator
  + ShareGrantResolver
  + EntitlementEvaluator
  + QuotaEvaluator
  + PermissionBoundaryResolver
  + ApprovalResolver
  + TemporaryAccessResolver
  + DelegationResolver
  + Risk controls
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

## 10. 当前正式 migrations

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
        ↓
governance/0001 + sharing/0001 + commercial/0001 + elevation/0001
```

B2.1—B2.4 的一次性 migration/finalize workflows 均已删除，禁止把临时 workflow 留作正式构建路径。

## 11. 最近永久 CI 状态

cleanup 后真实分支 HEAD：

```text
fe110df9b07ad9d8ae3dabcf6590a14e78efe23d
```

永久 CI **#109** 已完整 SUCCESS：

- Django `manage.py check`；
- model/migration sync；
- 空 PostgreSQL/PostGIS `migrate --noinput`；
- 全部领域测试（91 tests）；
- coverage threshold（最近验证 77.90%，阈值 70%）；
- Provider leakage check；
- Ruff；
- strict mypy；
- pip-audit；
- production Docker image build；
- production container + `scripts/start-preview.sh`；
- `/health/ready`；
- `/api/schema/`；
- `/api/docs/`。

因此 B2.4 已达到正式收口条件。

## 12. B2.4 Elevated Access — 已完成

B2.4 独立 bounded context：

```text
spatial_fabric.elevation
```

正式 migration：

```text
elevation/0001_initial.py
```

由 Django 5.2.17 在真实 PostgreSQL 17 / PostGIS 3.5 Runner 中生成并验证，首次固化提交：

```text
ab6737011d1dc9f89a0e3cf239ce1cf8f383ba01
```

实现对象：

```text
PermissionBoundary
PermissionBoundaryPrivilege
ApprovalRequest
ApprovalRequestPrivilege
ApprovalDecision
TemporaryAccessGrant
TemporaryAccessGrantPrivilege
DelegationGrant
DelegationGrantPrivilege
```

实现服务与 resolver：

```text
PermissionBoundaryResolver
ApprovalService / ApprovalResolver
TemporaryAccessService / TemporaryAccessResolver
DelegationService / DelegationResolver
```

已冻结并由测试覆盖：

- PermissionBoundary 只裁剪 candidate privilege，永远不能产生新 grant；
- Approval 是独立审批状态和 evidence，不创建普通 RoleAssignment/ShareGrant；
- Approval authority checker 缺失、异常、拒绝时 fail closed，并保存 `authority_snapshot`；
- JIT 使用独立 `TemporaryAccessGrant`，由唯一 source ApprovalRequest 保证幂等；
- Break-glass 强制理由、通知要求、60 分钟 TTL 上限、authority snapshot 与 idempotency fingerprint；
- Delegation 不得超过 delegator 当前有效权限，创建时保存 snapshot，解析时重新验证；
- deprecated/unknown Privilege、跨 Tenant、无效 Scope 和 checker 异常均 fail closed；
- 所有 resolver 只返回 candidate/evidence，仍不是最终 AuthorizationDecision。

### 下一开发阶段

唯一开发主线进入最终 `AuthorizationService` 组合决策器。开始实现前必须先冻结：

1. Role / Policy / Share / Commercial / Elevation / Risk 的组合输入；
2. `explicit DENY > REQUIRE_APPROVAL > candidate ALLOW` 的精确优先级；
3. Entitlement、Quota/Budget、PermissionBoundary 与临时/委托证据的 fail-closed 语义；
4. 可解释 AuthorizationDecision evidence；
5. 不把各 bounded context 合并成 God Service。

最终组合器尚未实现，禁止提前标记完成。

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

### 13.4 Budget cost ledger 尚未设计

B2.3 有意只实现 Budget policy/evidence；没有 normalized cost ledger 前，不做虚假余额扣减或成本结算。

## 14. 当前禁止

- 不重新设计已经冻结的总体架构；
- 不把 Entitlement / Quota / Budget 塞回 Role；
- 不把 ShareGrant 当最终 AuthorizationDecision；
- 不把 PermissionBoundary 设计成 grant source；
- 不把 Break-glass 设计成永久超级管理员角色；
- 不允许 Delegation 超过 delegator 自身有效权限；
- 不创建万能 `resource(id, type, json)`；
- 不让 governance/sharing/commercial 直接 FK 每一种 Asset/Job/Result/Map 类型；
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
继续 yujian-spatial-fabric。先读取 00_PROJECT_HANDOFF.md 和 docs/project/CURRENT_STATUS.md；不要重新设计已冻结架构，从最终 AuthorizationService 组合决策器继续。
```

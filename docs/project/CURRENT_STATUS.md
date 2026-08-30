# Current Project Status｜当前项目状态

**Last updated:** 2026-08-30  
**Active phase:** Phase B2 — Governance Controls  
**Current milestone:** **B2.2 Resource Sharing 已完成；下一阶段 B2.3 Commercial Controls**  
**Remote branch:** `feat/phase-b2-governance-controls`  
**Pull request:** #5 — Phase B2 Governance Controls（Draft，stacked on B1）

## 已完成

### Phase A — Foundation

- [x] 企业级 Django/PostGIS 工程骨架
- [x] split settings / API / health / OpenAPI / request ID / JSON logging
- [x] UUIDv7 基础实现
- [x] 自定义 `iam.Account`
- [x] `Principal`，保持 `Account ≠ Principal`
- [x] `Tenant / Workspace / Project / Environment`
- [x] `Asset / AssetVersion / AssetAlias / Artifact / Distribution`
- [x] provider-neutral PostGIS baseline migration
- [x] Phase A 正式 migrations
- [x] Render Preview 配置与生产 Docker smoke chain

### Phase B1 — IAM & Role Grant

- [x] `Privilege`
- [x] `RoleDefinition / RolePrivilege`
- [x] `Group / GroupMembership`
- [x] `RoleAssignment`
- [x] Core Privilege seed migration
- [x] `RoleGrantResolver`
- [x] Scope inheritance / tenant isolation / fail-closed invariants

### Phase B2.1 — Policy Core

- [x] 独立 `spatial_fabric.governance` bounded context
- [x] `PolicyDefinition`
- [x] `PolicyVersion`
- [x] `PolicyAttachment`
- [x] `PolicyPublicationService`
- [x] Published PolicyVersion immutable contract
- [x] `ALLOW / DENY / REQUIRE_APPROVAL` effect contract
- [x] `governance/0001_initial.py`
- [x] B2.1 policy invariants

### Phase B2.2 — Resource Sharing

- [x] 独立 `spatial_fabric.sharing` bounded context
- [x] `ShareGrant`
- [x] `ShareGrantPrivilege`
- [x] `AccessRequest`
- [x] `AccessRequestPrivilege`
- [x] `ShareGrantService`
- [x] `AccessRequestService`
- [x] `ShareGrantResolver`
- [x] Principal / Group XOR grantee shape
- [x] ResourceRef shape + tenant isolation
- [x] Grant 时间窗口 / revoke evidence
- [x] deprecated/unknown Privilege fail closed
- [x] 非空 `conditions` 默认 fail closed，复杂条件继续交给 Policy evaluator
- [x] AccessRequest fulfillment 原子生成独立 ShareGrant evidence
- [x] `sharing/0001_initial.py` 由 Django 5.2.17 在真实 PostGIS Runner 中生成并固化
- [x] `B2.2 Contract Amendment 001`：允许同一资源/主体存在多条独立 ShareGrant evidence，避免 `valid_until` 与 partial ACTIVE unique 产生时态死锁

## B2.2 最终验证状态

永久 CI 对提交 `f69dea44bde3d103626fe49fd971bd63cb8a9eef` 已在 PostgreSQL 17 / PostGIS 3.5 环境完整通过：

- [x] Django `manage.py check`
- [x] `makemigrations --check --dry-run`
- [x] 空库 `migrate --noinput`
- [x] 全部领域测试（59 tests）
- [x] coverage threshold（最近一轮 84.90%）
- [x] Provider leakage check
- [x] Ruff
- [x] strict mypy
- [x] pip-audit
- [x] production Docker image build
- [x] production image + `scripts/start-preview.sh`
- [x] `/health/ready`
- [x] `/api/schema/`
- [x] `/api/docs/`

一次性 migration workflow 已删除；正式 schema 只保留正常 migration 与永久 CI。

当前关键 migration 图：

```text
common/0001_enable_postgis
        ↓
tenancy/0001_initial
        ↓
iam/0001_initial
        ↓
assets/0001_initial + assets/0002_initial
        ↓
iam/0002_...
        ↓
iam/0003_seed_core_privileges
        ├───────────────┐
        ↓               ↓
governance/0001     sharing/0001
```

## 当前冻结的授权组合方向

```text
AuthorizationService
  = RoleGrantResolver
  + PolicyEvaluator
  + ShareGrantResolver
  + EntitlementEvaluator
  + QuotaEvaluator
  + Approval / Delegation / Risk controls
```

B2.2 的 ShareGrant 只产生显式资源级 `ALLOW candidate`，不是最终授权结果：

```text
explicit DENY > REQUIRE_APPROVAL > ShareGrant / RBAC ALLOW
```

## 尚未完成

- [ ] **B2.3 Commercial Controls：EntitlementGrant / Quota / Budget / Usage hard-limit integration**
- [ ] B2.4 Elevated Access：PermissionBoundary / Approval / JIT / Break-glass / Delegation
- [ ] 最终 AuthorizationService 组合决策器
- [ ] Render 账号侧真实 Apply Blueprint 与外部 `*.onrender.com` Preview URL 验证
- [ ] Job / Run / Result
- [ ] ServiceDeployment / ServiceInstance
- [ ] Dataset / Layer / Style / Map / Scene 正式模型实现
- [ ] Temporal / Object Storage / Martin / TiTiler / GeoServer 等 Provider adapters
- [ ] Portal / GeoStudio / Fabric Console 前端
- [ ] 三份大型 FINAL 架构/技术/领域规范全文同步

## Known Risk / 待收敛

- `uv.lock` 尚未固化。正式发布/生产冻结前必须生成并提交 lockfile，并把 Docker/CI 切换到 frozen install。
- 三份大型 FINAL 上位规范仍未全文同步进仓库，现有摘要/Implementation Spec 不得冒充 FINAL 全文。
- Render Free Preview 只用于演示，不能保存正式客户数据，也不能承担生产 SLA。
- `ResourceRef` 当前是治理模块的跨域稳定值引用，不是万能 Resource System of Record；后续禁止为了方便把所有领域对象塞进单表。

## 下一任务

唯一开发主线进入 **B2.3 Commercial Controls**：

1. 先冻结 `EntitlementGrant ≠ Quota ≠ Budget ≠ UsageRecord` 边界；
2. 定义 entitlement 的 product/capability 权利语义与 tenant/principal/scope 绑定；
3. 定义 quota 的 metric + limit + window + enforcement mode；
4. 定义 budget 的货币/成本治理语义，不与技术 quota 合并；
5. Usage hard-limit 必须通过统一 evaluator / reservation contract 执行，不能把计数逻辑散落到业务 controller；
6. 先写 spec + invariants，再生成 migration；
7. B2.3 仍需通过完整永久 CI 后才进入 B2.4。

## 当前禁止

- 不把 Entitlement/Quota/Budget 塞进 Role；
- 不把 ShareGrant 当成最终 AuthorizationDecision；
- 不跨 Tenant 直接分享资源；
- 不创建万能 `resource(id, type, json)` 表；
- 不集成 GeoServer/Martin/TiTiler 到 Core；
- 不开始 GeoAgent；
- 不加入 EnvOS/ParkOS 行业专属表；
- 不拆微服务；
- 不新增 provider-specific 核心字段；
- 不把免费 Preview 环境当成生产环境。

**纪律：只有真实执行过的检查才能标记为通过；B2.3 未实现前不得提前标记。**

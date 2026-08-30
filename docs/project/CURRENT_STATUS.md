# Current Project Status｜当前项目状态

**Last updated:** 2026-08-30  
**Active phase:** Phase B2 — Governance Controls  
**Current milestone:** **B2.3 Commercial Controls 已完成；下一阶段 B2.4 Elevated Access**  
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

### Phase B2.3 — Commercial Controls

- [x] 独立 `spatial_fabric.commercial` bounded context
- [x] dedicated Commercial Controls implementation spec
- [x] `EntitlementGrant`
- [x] `Quota`
- [x] `Budget`
- [x] `UsageReservation`
- [x] `UsageReservationQuota`
- [x] `UsageCounter`
- [x] `UsageRecord`
- [x] `EntitlementEvaluator`
- [x] `QuotaEvaluator`
- [x] `BudgetEvaluator`
- [x] `UsageReservationService.reserve_for_context / commit / release`
- [x] 一次业务操作对应一个 `UsageReservation`；多条适用 Quota 通过 `UsageReservationQuota` 保存独立 evidence
- [x] HARD Quota 超限原子回滚；SOFT / OBSERVE 保留 exceeded evidence
- [x] reservation idempotency fingerprint，重试不重复占用计数器
- [x] `select_for_update` + transaction 的并发安全 quota reservation / commit / release
- [x] stale reservation 到期回收，避免 ghost reserved capacity
- [x] CONSUMPTION commit 后禁止通过 release 擦除已发生消费事实
- [x] `UsageReservationQuota` 保存 decision-time `consumed / reserved / projected / limit / enforcement / exceeded` 精确快照
- [x] Quota 公共写服务把共享 Scope Loader 异常收口为稳定 `QuotaControlError`
- [x] `Budget` 第一版只提供成本/货币治理 policy evidence，不伪造成本扣减 ledger
- [x] `commercial/0001_initial.py` 由 Django 5.2.17 在真实 PostgreSQL 17 / PostGIS 3.5 Runner 中生成并固化
- [x] strict mypy 使用具体 Scope 类型收窄；未使用 `Any` / `cast` / `type: ignore` 规避检查

## B2.3 最终验证状态

B2.3 正式 schema 首次由验证 Runner 固化在提交：

```text
4d6d5d0a3aa6c19f2bf123764755397e6b179984
```

提交消息：

```text
db: add verified B2.3 commercial controls schema
```

随后删除全部一次性 B2.3 migration/finalize workflow。永久 CI #93 对 cleanup 后真实分支 HEAD：

```text
e4d9fd7d2eff8baa6e18a4a85f25ab57bbb316c6
```

在 PostgreSQL 17 / PostGIS 3.5 环境完整通过：

- [x] Django `manage.py check`
- [x] `makemigrations --check --dry-run`
- [x] 空库正式 `migrate --noinput`
- [x] 全部领域测试（75 tests）
- [x] coverage threshold（最近验证 78.21%，阈值 70%）
- [x] Provider leakage check
- [x] Ruff
- [x] strict mypy
- [x] pip-audit
- [x] production Docker image build
- [x] production image + `scripts/start-preview.sh`
- [x] `/health/ready`
- [x] `/api/schema/`
- [x] `/api/docs/`

一次性 B2.3 workflows 已全部删除；正式构建路径只保留正常 migrations 与永久 CI。

当前关键 migration 图：

```text
common/0001_enable_postgis
        ↓
tenancy/0001_initial
        ↓
Django contenttypes/auth
        ↓
iam/0001_initial
        ↓
assets/0001_initial + assets/0002_initial
        ↓
iam/0002_...
        ↓
iam/0003_seed_core_privileges
        ├────────────────────┬────────────────────┐
        ↓                    ↓                    ↓
governance/0001         sharing/0001        commercial/0001
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

各子模块继续只提供候选决策/evidence，不提前自称最终 AuthorizationDecision。

安全优先级方向保持：

```text
explicit DENY > REQUIRE_APPROVAL > ShareGrant / RBAC ALLOW
```

Commercial Controls 也不等于 RBAC：

```text
Role ≠ EntitlementGrant ≠ Quota ≠ Budget ≠ UsageRecord
```

其中：

- Entitlement 回答“是否具备某商业产品/能力资格”；
- Quota 回答“某 metric 在当前 scope/window 下是否还能消费”；
- Budget 回答“有哪些货币/成本治理 policy evidence”；
- UsageRecord 保存已发生使用事实；
- reservation/counter 是并发安全执行机制，不是新的授权根对象。

## 尚未完成

- [ ] **B2.4 Elevated Access：PermissionBoundary / Approval integration / JIT / Break-glass / Delegation**
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
- Budget 尚没有 normalized cost ledger；B2.3 第一版有意不实现虚假的实时成本余额扣减。

## 下一任务

唯一开发主线进入 **B2.4 Elevated Access**。开始编码前先冻结以下边界与状态机：

1. `PermissionBoundary` 是最大权限边界，不是新的 Role，也不能凭空授予权限；
2. Approval 是高风险操作/临时提升的决策与证据，不等于 ShareGrant / RoleAssignment；
3. JIT / Temporary Elevation 必须有明确 requester、approver、scope、privilege、有效窗口、理由和 revoke/expire evidence；
4. Break-glass 必须是显式高风险流程，默认短时、强审计、可追溯，不能成为永久管理员后门；
5. Delegation 必须区分 delegator / delegatee / delegated scope / privilege，并受 delegator 自身有效权限和 permission boundary 限制；
6. Elevated Access 仍只作为最终 AuthorizationService 的输入，不能绕过 explicit DENY / policy / entitlement / quota 等治理层；
7. 先写 dedicated spec + invariants，再生成正式 migration；
8. B2.4 完成并永久 CI 全绿后，才进入最终 AuthorizationService 组合决策器。

## 当前禁止

- 不把 Entitlement/Quota/Budget 塞进 Role；
- 不把 ShareGrant 当成最终 AuthorizationDecision；
- 不把 PermissionBoundary 设计成 grant source；
- 不把 Break-glass 设计成永久超级管理员角色；
- 不允许 Delegation 超过 delegator 的有效权限边界；
- 不跨 Tenant 直接分享资源；
- 不创建万能 `resource(id, type, json)` 表；
- 不集成 GeoServer/Martin/TiTiler 到 Core；
- 不开始 GeoAgent；
- 不加入 EnvOS/ParkOS 行业专属表；
- 不拆微服务；
- 不新增 provider-specific 核心字段；
- 不把免费 Preview 环境当成生产环境。

**纪律：只有真实执行过的检查才能标记为通过；B2.4 未实现前不得提前标记。**

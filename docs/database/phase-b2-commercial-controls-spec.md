# Phase B2.3｜Commercial Controls 实现规格

> 状态：Implementation Spec  
> 分支：`feat/phase-b2-governance-controls`  
> 前置：B2.1 Policy Core、B2.2 Resource Sharing 已完成并通过永久 CI  
> 原则：商业许可、技术配额、成本预算、使用事实必须保持可审计且彼此独立。

## 1. 目标

B2.3 负责回答四类不同问题：

```text
EntitlementGrant → 是否具备某产品/能力的商业使用资格？
Quota            → 某技术 metric 最多可以使用多少？
Budget           → 某作用域最多允许承担多少货币/成本？
UsageRecord      → 实际发生了什么使用事实？
```

冻结：

```text
Role ≠ EntitlementGrant ≠ Quota ≠ Budget ≠ UsageRecord
```

Role/ShareGrant 解决“权限动作”，Entitlement 解决“商业资格”，Quota 解决“技术数量上限”，Budget 解决“成本上限”。禁止为了少建表把这些语义塞进同一个 JSON 配置。

---

## 2. Django bounded context

B2.3 新建：

```text
spatial_fabric.commercial
```

它仍属于 Governance 根对象家族下的商业控制 bounded context，不创建新的平台根对象家族。

依赖方向：

```text
commercial
   ↓
iam + tenancy
```

禁止：

```text
iam → commercial
assets/execution/map → commercial
commercial → 某云厂商 billing SDK
```

---

## 3. 商业 Scope

B2.3 第一版统一使用管理层级：

```text
TENANT
WORKSPACE
PROJECT
ENVIRONMENT
```

每一行显式保存 `tenant_id`，并用 shape CHECK 保证对应 scope FK 互斥。

父级控制向下适用，例如 Tenant quota 与 Project quota 可以同时约束某个 Project 操作。**更具体的 quota 不能静默放宽父级 HARD quota**。

---

## 4. EntitlementGrant

EntitlementGrant 是独立授权证据，不是 Role，也不是 Subscription/Contract 的替身。

第一版：

```text
EntitlementGrant
- tenant_id
- entitlement_key
- subject_type        TENANT | PRINCIPAL
- principal_id?
- scope_type
- workspace_id?
- project_id?
- environment_id?
- status              ACTIVE | REVOKED
- valid_from?
- valid_until?
- granted_by
- revoked_by?
- revoked_at?
```

示例 key：

```text
geophysics.aermod
geoagent.pro
simulation.advanced
```

同一 entitlement/scope/subject 允许存在多条独立 evidence。原因与 B2.2 ShareGrant 相同：时间到期不依赖异步任务改状态，不能用永久 ACTIVE partial unique 制造时态死锁。

Evaluator 保留全部有效 evidence，只在最终 `entitled` 结果上做 OR。

---

## 5. Quota

Quota 是配置事实：

```text
Quota
- tenant_id
- metric_key
- unit
- subject_type        TENANT | PRINCIPAL
- principal_id?
- scope_type
- workspace_id?
- project_id?
- environment_id?
- measurement_type
- limit_value
- window_type
- enforcement_mode
- status
- valid_from / valid_until
- created_by
- revoked_by / revoked_at
```

### 5.1 Measurement type

第一版冻结：

```text
GAUGE        当前占用量，例如 storage_bytes
CONCURRENCY  当前并发占用，例如 concurrent_jobs
CONSUMPTION  累积消费量，例如 gpu_seconds / ai_tokens
```

### 5.2 Window type

第一版冻结：

```text
NONE
CALENDAR_DAY
CALENDAR_MONTH
```

约束：

```text
GAUGE / CONCURRENCY → NONE
CONSUMPTION         → CALENDAR_DAY | CALENDAR_MONTH
```

第一版故意不做 ROLLING window。滚动窗口不能由单个固定 counter 正确表达，若以后需要应单独设计时间桶/流式聚合，不用近似语义冒充精确限制。

日/月窗口以 `Tenant.default_timezone` 计算业务边界，再存为 UTC aware datetime。

### 5.3 Enforcement mode

```text
OBSERVE  只记录，不阻断
SOFT     超限仍允许，但返回 exceeded evidence
HARD     projected usage 超限即拒绝
```

若一个操作同时命中多个 quota：

```text
所有 applicable HARD quota 都必须通过
```

因此 Tenant HARD=100、Project HARD=10 时，Project 操作不能因为更具体配置存在而绕过 Tenant 上限。

---

## 6. Budget

Budget 与技术 Quota 分离：

```text
Budget
- tenant_id
- budget_key
- name
- scope_type
- workspace/project/environment?
- currency_code       ISO 4217 三字母代码
- amount_limit        Decimal
- window_type         CALENDAR_MONTH | CALENDAR_YEAR | FIXED_TERM
- enforcement_mode
- status
- valid_from / valid_until
- created_by
- revoked_by / revoked_at
```

第一版只建立 Budget policy/evidence 与 evaluator，不把 `gpu_seconds × 临时单价` 直接当成权威货币账本。

真正 HARD Budget enforcement 必须等 normalized cost ledger / price attribution contract 建立后接入。否则会把不稳定的供应商价格、内部成本或折扣逻辑写死进 Core。

---

## 7. UsageRecord 是事实，不是配置

Prometheus/OpenTelemetry 可以作为观测来源，但**不能成为权威 Quota 配置表**。

B2.3 需要 Fabric 自己的治理账本：

```text
UsageReservation
UsageReservationQuota
UsageCounter
UsageRecord
```

关系：

```text
一次业务操作
   ↓
UsageReservation
   ├── UsageReservationQuota → Tenant quota counter
   └── UsageReservationQuota → Project quota counter
   ↓ commit
UsageRecord(CONSUME)
   ↓ optional release for GAUGE/CONCURRENCY
UsageRecord(RELEASE)
```

`UsageRecord` 是已发生事实；`UsageCounter` 是为强一致限额决策维护的物化计数，不取代 UsageRecord 的审计意义。

---

## 8. 为什么不能 `SUM → CHECK → INSERT`

以下实现存在经典并发竞态：

```text
request A: SUM = 9
request B: SUM = 9
limit = 10
A 检查 9 + 1 <= 10
B 检查 9 + 1 <= 10
A INSERT
B INSERT
最终 = 11
```

因此 HARD quota 必须通过事务内的 reservation contract。

B2.3 第一版顺序：

```text
1. 生成/复用 UsageReservation（idempotency key）
2. 清理已过期 reservation 的占位
3. 查询全部 applicable quota
4. 按稳定顺序 SELECT ... FOR UPDATE 锁定 quota
5. 锁定对应 UsageCounter
6. 计算 projected = consumed + reserved + requested
7. 任一 HARD 超限 → 整个事务回滚
8. 全部通过 → 每个 counter.reserved += requested
9. 创建 UsageReservationQuota evidence
```

这样同一 metric 上的并发决策由数据库行锁串行化，不依赖 API controller 的“先查再写”。

---

## 9. 为什么 UsageReservation 是操作级父对象

一个请求可能同时受多个 quota 约束。如果直接“一条 quota 一条 reservation”，重试期间 quota 集合发生变化时，很难判断哪些行属于同一次业务操作。

因此：

```text
UsageReservation        = 一次逻辑操作 + idempotency identity
UsageReservationQuota   = 此操作命中的每条 Quota evidence
```

`UsageReservation` 在 `(tenant, principal, idempotency_key)` 上唯一。

同一 idempotency key 重试：

- 请求 fingerprint 相同 → 返回原 reservation/evidence，不重复占用；
- fingerprint 不同 → fail closed；
- 不因后来新增 quota 而偷偷改变已成功创建的原操作证据集。

---

## 10. Reservation 生命周期

```text
RESERVED
COMMITTED
RELEASED
EXPIRED
```

语义：

- RESERVED：只占 `reserved_value`；
- COMMITTED：reservation 转为真实 usage，`reserved → consumed`；
- RELEASED：取消未提交占位，或释放已提交的 GAUGE/CONCURRENCY 占用；
- EXPIRED：TTL 到期、尚未 commit 的占位被回收。

CONSUMPTION 一旦 COMMITTED 不允许 release；纠错以后通过独立 adjustment contract，而不是删除历史事实。

---

## 11. UsageCounter

Counter identity：

```text
(quota_id, window_start, window_end)
```

`NONE` window 使用两个 NULL，并依赖 PostgreSQL 17 `NULLS NOT DISTINCT` 语义确保每个 quota 只有一条无窗口 counter。

字段：

```text
consumed_value
reserved_value
```

所有修改必须经过事务服务和 row lock；禁止 controller 直接 `F()` 绕过 reservation 状态机。

---

## 12. UsageRecord

第一版事件：

```text
CONSUME
RELEASE
```

每个 reservation 每种事件最多一条，保证 commit/release 幂等。

UsageRecord 复制 metric、unit、measurement、scope 和 principal 快照，避免未来只靠 join 才能解释历史事实。

---

## 13. 数据库最低约束

至少包括：

1. Entitlement/Quota subject shape；
2. Entitlement/Quota/Budget/Reservation scope shape；
3. 所有 valid window `valid_until > valid_from`；
4. ACTIVE/REVOKED revoke evidence shape；
5. Quota measurement/window compatibility；
6. Budget `amount_limit > 0`；
7. FIXED_TERM Budget 必须有完整起止时间；
8. Counter window 要么双 NULL，要么双非 NULL 且 end > start；
9. Counter `(quota, window_start, window_end)` NULLS NOT DISTINCT unique；
10. Reservation `amount > 0`、`expires_at > reserved_at`；
11. Reservation status/evidence shape；
12. `(tenant, principal, idempotency_key)` unique；
13. ReservationQuota `(reservation, quota)` unique；
14. UsageRecord `(reservation, event_type)` unique。

跨表 Tenant、metric、unit、scope 一致性由 `clean()` + Application Service 双重校验；未来 RLS/trigger 可继续加固。

---

## 14. 第一批服务

```text
EntitlementEvaluator
QuotaEvaluator
BudgetEvaluator
UsageReservationService
```

Evaluator 必须返回 evidence，不只返回 bool。

`UsageReservationService` 提供：

```text
reserve_for_context(...)
commit(...)
release(...)
```

并支持注入 `at` 作为确定性测试/重放时间；生产默认 `timezone.now()`。

---

## 15. 第一批测试门槛

至少覆盖：

1. Entitlement subject/scope/Tenant 不变量；
2. Entitlement 向子 scope 继承；
3. Quota measurement/window 不合法组合拒绝；
4. Budget 金额/币种/FIXED_TERM 约束；
5. 多层 HARD quota 任一超限即整体拒绝；
6. HARD 拒绝不得留下 reservation/counter 占用；
7. SOFT 超限保留 exceeded evidence 但不阻断；
8. idempotency retry 不重复 reserve；
9. fingerprint 冲突 fail closed；
10. commit 原子执行 `reserved → consumed` 并生成单一 UsageRecord；
11. commit 重试幂等；
12. GAUGE/CONCURRENCY release 正确释放 consumed；
13. CONSUMPTION commit 后禁止 release；
14. TTL 过期 reservation 能回收 reserved capacity；
15. cross-tenant principal/scope fail closed；
16. model/migration sync、empty PostGIS migrate、Ruff、strict mypy、pip-audit、Docker/Preview smoke。

---

## 16. B2.3 明确不做

- 不实现 B2.4 Approval/JIT/Break-glass；
- 不把 Budget 伪装成 Quota；
- 不直接接 AWS/GCP/Azure/Stripe 专属账单字段；
- 不用 Prometheus 当前值当 quota policy；
- 不做 rolling window 近似；
- 不让业务 controller 自己计算 hard limit；
- 不创建万能 resource/billing 表；
- 不开始最终 AuthorizationService 总组合器。

B2.3 完整永久 CI 绿后，才允许进入 B2.4。

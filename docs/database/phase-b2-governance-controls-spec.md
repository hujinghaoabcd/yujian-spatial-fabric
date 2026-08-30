# Phase B2｜Governance Controls 实现规格

> 状态：Implementation Spec  
> 分支：`feat/phase-b2-governance-controls`  
> Base：`feat/phase-b-iam-governance`  
> 原则：B2 组合治理语义，但不重写 B1 `RoleGrantResolver`。

## 1. 目标

B1 已经稳定回答：

> 某 Principal 在某管理 Scope 下有哪些候选 RBAC GRANT？

B2 开始回答：

> 这些候选 GRANT 在资源、策略、许可、额度、风险与审批约束下，最终还能保留多少？

最终方向保持：

```text
AuthorizationService
  = RoleGrantResolver
  + PolicyEvaluator
  + ShareGrantResolver
  + EntitlementEvaluator
  + QuotaEvaluator
  + Approval / Delegation / Risk controls
```

B2 不允许把以上概念重新塞回 Role 或 RoleAssignment。

---

## 2. 分阶段

### B2.1 — Policy Core

第一批实现：

```text
PolicyDefinition
PolicyVersion
PolicyAttachment
```

### B2.2 — Resource Sharing

随后实现：

```text
ShareGrant
ShareGrantPrivilege
AccessRequest
```

### B2.3 — Commercial Controls

随后实现：

```text
EntitlementGrant
Quota
Budget
Usage hard-limit integration
```

### B2.4 — Elevated Access

随后实现：

```text
PermissionBoundary
Approval integration
JIT / Temporary Elevation
Break-glass
Delegation
```

这样每个阶段都可以单独迁移、测试、审查。

---

# 3. Django 模块边界

B2 新建：

```text
spatial_fabric.governance
```

依赖方向：

```text
governance
   ↓
iam + tenancy
```

禁止：

```text
iam → governance
assets → governance
```

资源级 Policy/Share 也不让 Governance 直接 FK 每一种资源模型，否则未来会产生：

```text
governance ↔ assets/execution/portal/...
```

迁移依赖环。

---

# 4. ResourceRef：值语义，不是万能资源表

架构已经禁止创建：

```text
resource(id, type, json)
```

作为所有领域对象的万能 System of Record。

B2 需要的只是**稳定资源引用值**：

```text
ResourceRef
- tenant_id
- resource_kind     namespaced stable key
- resource_id       UUID
```

示例：

```text
asset / <uuid>
asset_version / <uuid>
job / <uuid>
result / <uuid>
portal.site / <uuid>
```

`resource_kind` 不做数据库 Enum，因为 FabricPackage / 新模块未来需要扩展；它必须通过 Resource Registry / Resolver 在应用层验证。

关键边界：

```text
ResourceRef ≠ Resource table
ResourceRef ≠ Catalog projection
ResourceRef ≠ foreign key replacement everywhere
```

普通同模块关系仍然使用正式 FK；只有真正跨领域、跨模块的治理绑定使用 ResourceRef。

---

# 5. B2.1 PolicyDefinition

`PolicyDefinition` 是策略的长期身份，不保存可变规则正文。

建议：

```text
id                  UUIDv7
tenant_id            nullable
key
name
description
status               ACTIVE | DEPRECATED
is_system
created_by           nullable
created_at
updated_at
lock_version
```

语义：

```text
tenant IS NULL      → 平台系统策略/模板
tenant IS NOT NULL  → Tenant 策略
```

唯一约束与 RoleDefinition 相同思路：

```text
平台 key 条件唯一
Tenant 内 (tenant, key) 条件唯一
```

PolicyDefinition 只保存 identity / lifecycle，不把规则数组原地覆盖进去。

---

# 6. B2.1 PolicyVersion

策略正文必须版本化：

```text
PolicyDefinition
       ↓
PolicyVersion
```

建议字段：

```text
id
policy_id
version_seq
schema_version
spec                 JSONB
content_hash
status               DRAFT | PUBLISHED | RETIRED
created_by
published_by nullable
published_at nullable
created_at
updated_at
```

约束：

```text
UNIQUE(policy_id, version_seq)
```

发布后的 PolicyVersion 视为 immutable；修改必须创建新版本。

第一阶段与 AssetVersion 一样：领域服务 + tests 保证不可变，必要时以后增加数据库 trigger；禁止业务代码对已发布版本 `QuerySet.update()`。

## 6.1 Policy spec 最低稳定结构

第一版内部策略文档采用 schema-validated JSON：

```json
{
  "statements": [
    {
      "sid": "deny-prod-export",
      "effect": "DENY",
      "actions": ["export"],
      "conditions": {
        "environment_types": ["PRODUCTION"]
      }
    }
  ]
}
```

Effect 冻结：

```text
ALLOW
DENY
REQUIRE_APPROVAL
```

`actions` 中每个 key 在 publish 时必须解析为 ACTIVE `Privilege`；不能让拼写错误静默成为策略语义。

第一版只允许明确列入 schema 的条件操作符。未知 condition / 未知 effect / 未知 action：

```text
publish reject / evaluation fail closed
```

不在 B2 自己发明通用编程语言。

---

# 7. B2.1 PolicyAttachment

PolicyVersion 本身不决定“作用到谁、作用到哪里”。绑定由 `PolicyAttachment` 表达。

```text
PolicyAttachment
- tenant_id
- policy_version_id
- subject_type
- principal_id?
- group_id?
- role_id?
- target_type
- workspace_id?
- project_id?
- environment_id?
- resource_kind?
- resource_id?
- priority
- status
- valid_from?
- valid_until?
- attached_by
- created_at
- updated_at
```

## 7.1 Subject shape

```text
ALL
PRINCIPAL
GROUP
ROLE
```

形状：

```text
ALL       → principal/group/role 全 NULL
PRINCIPAL → principal 非 NULL，其余 NULL
GROUP     → group 非 NULL，其余 NULL
ROLE      → role 非 NULL，其余 NULL
```

所有 subject 必须与 `PolicyAttachment.tenant` 一致；平台 Role 可作为受控例外。

## 7.2 Target shape

```text
TENANT
WORKSPACE
PROJECT
ENVIRONMENT
RESOURCE
```

形状：

```text
TENANT      → scope FK / ResourceRef 均为空
WORKSPACE   → 只 workspace
PROJECT     → 只 project
ENVIRONMENT → 只 environment
RESOURCE    → 只 resource_kind + resource_id
```

Tenant 永远显式保存，便于隔离、索引、RLS 与安全检查。

RESOURCE 不直接 FK Asset/Job/Result；目标存在性与 Tenant 一致性由未来 `ResourceResolver` 在 attachment service 中验证。

---

# 8. Policy precedence

B2.1 冻结决策优先级，但第一批模型 PR 不提前实现完整 evaluator：

```text
explicit DENY
    > REQUIRE_APPROVAL
    > ALLOW / inherited RBAC grant
```

最终 `PolicyEvaluator` 必须返回 evidence，不只返回 bool：

```text
PolicyDecision
- outcome
- matched_policy_version_ids
- matched_statement_ids
- matched_attachment_ids
- reasons
```

未知/损坏/无法解析的策略不得变成 ALLOW。

---

# 9. B2.2 ShareGrant 边界（先冻结，不在 B2.1 建表）

ShareGrant 回答：

> 某个具体资源额外分享给哪个 Principal/Group 哪些 Privilege？

建议：

```text
ShareGrant
- tenant_id
- resource_kind
- resource_id
- principal_id XOR group_id
- status
- valid_from / valid_until
- conditions
- granted_by

ShareGrantPrivilege
- share_grant_id
- privilege_id
```

动作必须用 FK 到 `Privilege`，不保存 `actions JSON`。

ShareGrant：

```text
≠ RoleAssignment
≠ Ownership
≠ resource tenant transfer
```

它只增加资源级候选 grant，之后仍受 Policy / Entitlement / Quota / Approval 约束。

---

# 10. B2.3 Entitlement / Quota 边界

Entitlement 回答：

```text
是否拥有某产品/能力许可？
```

Quota 回答：

```text
最多可以使用多少？
```

例如：

```text
geophysics.aermod
geoagent.pro
concurrent_jobs
storage_bytes
gpu_seconds
ai_tokens
```

冻结：

```text
Role ≠ Entitlement ≠ Quota
```

也禁止用 Prometheus 当前 metric 作为权威 Quota 配置。

---

# 11. B2.4 Approval / JIT / Break-glass 边界

基础时间窗口：

```text
RoleAssignment.valid_from / valid_until
```

不等于企业级 JIT。

JIT 必须以后包含：

```text
request
approval
reason
bounded scope
time-to-live
activation
revocation
receipt/audit
```

Break-glass 必须：

```text
强理由
短 TTL
高强度审计
通知
不可静默持久化为普通 RoleAssignment
```

---

# 12. B2.1 数据库约束最低集合

1. PolicyDefinition 平台/Tenant key 条件唯一；
2. PolicyVersion `(policy, version_seq)` 唯一；
3. PolicyVersion 与 PolicyDefinition Tenant 语义一致；
4. PolicyAttachment subject shape CHECK；
5. PolicyAttachment target shape CHECK；
6. `valid_until > valid_from`；
7. subject / scope Tenant 一致；
8. PolicyVersion 必须属于同一 Tenant 策略，或是允许绑定的系统策略；
9. RESOURCE target 必须同时有 `resource_kind` 与 `resource_id`；
10. 非 RESOURCE target 必须没有 ResourceRef；
11. REVOKED attachment 不参与计算。

---

# 13. B2.1 索引

建议：

```text
PolicyDefinition(tenant, status)
PolicyVersion(policy, status)
PolicyAttachment(tenant, status)
PolicyAttachment(principal, status)
PolicyAttachment(group, status)
PolicyAttachment(role, status)
PolicyAttachment(workspace, status)
PolicyAttachment(project, status)
PolicyAttachment(environment, status)
PolicyAttachment(tenant, resource_kind, resource_id, status)
```

第一版不对 `PolicyVersion.spec` 创建 GIN；只有真实查询模式证明需要时再加。

---

# 14. B2.1 测试门槛

至少覆盖：

1. 平台/Tenant Policy key 唯一边界；
2. PolicyVersion 不可跨 Policy 错配 Tenant；
3. PolicyAttachment subject 四种合法 shape；
4. subject 非法组合被 DB/application 双层拒绝；
5. target 五种合法 shape；
6. target 非法组合被拒绝；
7. Workspace/Project/Environment 不能跨 Tenant；
8. Principal/Group 不能跨 Tenant；
9. Tenant Policy 不能跨 Tenant attach；
10. 平台 Policy 可受控 attach 到 Tenant；
11. 时间窗口合法性；
12. RESOURCE target 必须完整 ResourceRef；
13. 未知 resource_kind 不由 ORM 静默判定有效，留给 ResourceResolver；
14. 已发布 PolicyVersion 的修改路径由领域服务拒绝；
15. `DENY > REQUIRE_APPROVAL > ALLOW` 作为后续 evaluator contract 测试冻结。

所有新增代码继续通过：

```text
Django check
makemigrations --check
empty PostGIS migrate
pytest + coverage
provider leakage
Ruff
strict mypy
pip-audit
production Docker build
Preview smoke
```

---

# 15. 最终边界

```text
B1 RoleGrantResolver
    → 只解析 RBAC candidate grants

B2 Policy Core
    → 只解析 context/policy restrictions & requirements

B2 ShareGrant
    → 只增加 resource-specific candidate grants

B2 Entitlement
    → 只回答 product/capability eligibility

B2 Quota
    → 只回答 usage limit

B2 Approval/JIT
    → 只回答 elevated/risky action authorization state
```

任何一个模块都不能单独把自己当成最终 `AuthorizationService`。

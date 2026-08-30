# Phase B1｜RoleGrantResolver 实现规格

> 状态：Implemented / Verified  
> 作用：只解析 Phase B1 RBAC grant，不等于最终 AuthorizationService。

## 1. 为什么需要独立 Resolver

Phase B1 已经把授权事实拆成：

```text
Principal
Group / GroupMembership
RoleDefinition / RolePrivilege
RoleAssignment
```

业务模块后续不能自己到这些表里拼接查询，否则会快速出现：

- Dataset 用一套继承逻辑；
- Map 用另一套；
- Job 又忘了 Group Role；
- Agent 直接判断某个字段绕过 Tenant scope。

因此 B1 需要唯一的 RBAC 解析入口：

```text
RoleGrantResolver
```

它回答：

> 在给定 Principal、时间点和管理 Scope 下，有哪些有效 Role/Privilege GRANT？

它**不回答最终是否允许动作**。

最终授权仍是未来：

```text
AuthorizationService
  = RoleGrantResolver
  + Policy
  + Entitlement
  + Quota
  + Approval
  + Delegation
  + resource ShareGrant
```

---

## 2. 输入

正式值对象：

```text
AuthorizationScope
- tenant_id
- workspace_id?
- project_id?
- environment_id?
```

以及：

```text
principal_id
at                  datetime | None
```

Scope 必须形成合法链：

```text
Tenant
  ↓
Workspace
  ↓
Project
  ↓
Environment
```

调用方可以只传最深层 ID，由 Resolver 从数据库推导祖先；如果同时显式传入祖先 ID，则必须与真实层级一致。不能传入来自不同 Tenant 的混合 ID，任何不一致均 fail closed。

---

## 3. Role 来源

Resolver 只合并两类事实：

### 3.1 Direct Role

```text
RoleAssignment(principal = P)
```

### 3.2 Group Role

```text
P
 ↓ active GroupMembership
Group(status = ACTIVE)
 ↓ active RoleAssignment(group = G)
Role
```

GroupMembership 有效并不代表一个 SUSPENDED/ARCHIVED Group 仍可继续授权；Group 本身必须为 ACTIVE。

严禁把 Group Role 复制成 Direct RoleAssignment。

---

## 4. Scope inheritance

目标 scope 若为：

### Tenant

只匹配 Tenant assignment。

### Workspace

匹配：

```text
Tenant
Workspace
```

### Project

匹配：

```text
Tenant
Workspace(project.workspace)
Project
```

### Environment

匹配：

```text
Tenant
Workspace(environment.project.workspace)
Project(environment.project)
Environment
```

这叫**候选 GRANT 继承**，不是最终无条件 ALLOW。

未来 B2 `explicit DENY / permission boundary / classification policy` 可以继续收窄。

---

## 5. 有效性过滤与 fail-closed 条件

RoleAssignment 必须：

```text
status = ACTIVE
valid_from <= at   或 valid_from IS NULL
valid_until > at  或 valid_until IS NULL
```

GroupMembership 同样必须处于有效时间窗口，并且：

```text
membership.status = ACTIVE
group.status = ACTIVE
```

RoleDefinition 必须：

```text
status = ACTIVE
```

Privilege 必须：

```text
status = ACTIVE
```

`REVOKED`、过期、未来才生效、已弃用的关系均不能进入结果。

### 5.1 RoleAssignment.conditions

B1 暂未定义正式条件表达式/evaluator，因此安全语义冻结为：

```text
conditions == {}
    → 可进入 B1 grant 解析

conditions != {}
    → fail closed，不产生 grant
```

不能因为 JSON 结构“看起来像条件”就默认允许。未来若引入低复杂度 ABAC 条件，必须由独立条件 evaluator 和对应测试接管，再修改此契约。

---

## 6. 返回值不能只是一组字符串

正式返回证据：

```text
ResolvedRoleGrant
- role_id
- role_key
- privilege_id
- privilege_key
- source_type        DIRECT | GROUP
- assignment_id
- group_id?
- assignment_scope
- inherited_from
```

一次解析返回：

```text
RoleGrantResolution
- principal_id
- target_scope
- resolved_at
- grants[]
- effective_privilege_keys
- effective_role_ids
```

原因：授权 explain / audit / debugging 必须回答：

> 为什么 Alice 有 execute？

而不能只返回：

```text
{"execute", "read"}
```

例如：

```text
execute
  ← Project Model Operator
  ← Group: atmospheric-team
  ← RoleAssignment #...
  ← Project #...
```

同一 Privilege 可以存在多条 evidence；`effective_privilege_keys` 可以去重，但 evidence 不能丢失。这为未来 `AuthorizationDecision.matched_policies/grants` 奠定证据链。

---

## 7. 不允许的快捷方式

Resolver 内禁止：

```python
if account.is_superuser:
    return ALL_PRIVILEGES
```

Django superuser 只控制 Django Admin。

也禁止：

```python
if asset.owner == principal:
    return ALL_PRIVILEGES
```

Ownership 是否隐含哪些权限，应由后续正式 Policy 决定，不能在 B1 写死。

---

## 8. Agent

Agent 只是 `PrincipalType.AGENT`。

RoleGrantResolver 对 Human / ServiceAccount / Agent 使用同一解析算法。

但最终 Agent 权限以后仍必须：

```text
Agent RBAC grants
∩ Delegated User Permission
∩ Tool Permission
∩ Resource Policy
∩ Risk Policy
```

因此 B1 Resolver 的输出只是 Agent 最终权限的一个输入。

---

## 9. 查询策略

第一版使用 PostgreSQL/Django ORM 少量批量查询完成：

1. 校验目标 scope chain；
2. 校验 Principal 与目标 Tenant；
3. 取 Principal 的有效 GroupMembership，并验证 Group ACTIVE；
4. 一次查询 Direct + Group RoleAssignments；
5. scope ancestor 条件过滤；
6. 一次批量查询 RolePrivilege + active Privilege；
7. 返回带来源证据的 immutable result。

禁止 N+1：

```text
for role_assignment:
    query privileges
```

后续如权限查询成为热点，再引入：

```text
Authorization cache / projection
```

但缓存永远不能成为授权真相源。

---

## 10. Cache 边界

B1 第一版不缓存。

未来缓存 key 至少包含：

```text
principal_id
scope fingerprint
RBAC revision / invalidation token
```

以下事件必须导致失效：

```text
role assignment changed
role privileges changed
group membership changed
group suspended/archived
role deprecated
privilege deprecated
```

不能只按用户 ID 长期缓存权限。

---

## 11. 已验证行为

当前自动化测试覆盖：

1. Tenant Role 向 Workspace/Project/Environment 继承；
2. Workspace Role 向所属 Project/Environment 继承，但不能横跨 Workspace；
3. Project Role 向所属 Environment 继承；
4. Environment Role 不向兄弟 Environment 传播；
5. Direct + Group grants 合并；
6. 同一 Privilege 多来源时保留多条 evidence；
7. 过期 GroupMembership 不产生 grants；
8. SUSPENDED Group 不产生 grants；
9. REVOKED RoleAssignment 不产生 grants；
10. Deprecated Role/Privilege 不产生 grants；
11. Django superuser 无 Fabric Role 时结果仍为空；
12. Agent 与 Human 使用相同算法；
13. 跨租户 Scope / Principal 输入必须拒绝；
14. 非空 RoleAssignment.conditions 在无正式 evaluator 时 fail closed。

Phase A + Phase B1 当前永久 CI 已验证：

```text
Django system check                  ✅
models ↔ migrations                  ✅
empty PostgreSQL/PostGIS migrate     ✅
34 tests                             ✅
coverage gate                        ✅
provider leakage check               ✅
Ruff                                 ✅
strict mypy                          ✅
pip-audit                            ✅
production Docker build              ✅
Preview API smoke                    ✅
```

---

## 12. 最终边界

```text
RoleGrantResolver
= B1 RBAC grant resolver
≠ AuthorizationService
≠ Policy engine
≠ Entitlement engine
≠ Quota engine
≠ ShareGrant resolver
```

这样 B2 以后可以在不重写 B1 的情况下逐层组合企业治理语义。

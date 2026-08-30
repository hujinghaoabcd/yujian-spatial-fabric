# Phase B2.4｜Elevated Access 实现规格

> 状态：Implementation Spec  
> 分支：`feat/phase-b2-governance-controls`  
> 上位依据：`phase-b2-governance-controls-spec.md` + `00_PROJECT_HANDOFF.md`  
> 原则：B2.4 只建立高风险/临时访问治理输入，不提前实现最终 `AuthorizationService`。

## 1. 目标与边界

B2.4 实现：

```text
PermissionBoundary
Approval integration
JIT / Temporary Elevation
Break-glass
Delegation
```

必须保持：

```text
PermissionBoundary ≠ Role ≠ RoleAssignment
ApprovalRequest ≠ ShareGrant ≠ RoleAssignment
TemporaryAccessGrant ≠ RoleAssignment
DelegationGrant ≠ RoleAssignment
```

任何 B2.4 grant 都只是最终 AuthorizationService 的 candidate/evidence，不能直接解释为最终 `ALLOW`。

最终组合方向继续是：

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

---

## 2. Django bounded context

B2.4 使用独立模块：

```text
spatial_fabric.elevation
```

依赖方向：

```text
elevation
   ↓
iam + tenancy
```

第一版不让 IAM 反向依赖 elevation，也不直接依赖 assets/execution/map 等具体资源模块。

---

## 3. PermissionBoundary

`PermissionBoundary` 是 Principal 在某管理 Scope 上的**最大权限集合限制**，不是 grant source。

```text
PermissionBoundary
- tenant
- principal
- scope_type
- workspace / project / environment
- status
- valid_from / valid_until
- created_by
- revoked_by / revoked_at
- reason
```

```text
PermissionBoundaryPrivilege
- boundary
- privilege
```

Scope：

```text
TENANT
WORKSPACE
PROJECT
ENVIRONMENT
```

规则：

1. Boundary 的 Principal 必须属于同一 Tenant；
2. Scope FK 必须属于同一 Tenant；
3. 多条同时适用 Boundary 的 permitted privilege 取**交集**；
4. 没有适用 Boundary 时，本模块不额外裁剪 candidate set；
5. 有适用 Boundary 但 allowed set 为空时，结果为空；
6. Boundary 引用未知/DEPRECATED Privilege 必须 fail closed；
7. Boundary 只裁剪 candidate privilege，不产生新 privilege。

因此：

```text
bounded(candidate) ⊆ candidate
```

永远成立。

---

## 4. ApprovalRequest / ApprovalDecision

Approval 保存“为什么允许某个高风险动作/临时提升继续”的独立证据。

```text
ApprovalRequest
- tenant
- purpose
- requester
- beneficiary
- target_type
- workspace / project / environment
- resource_kind / resource_id
- reason
- requested_at
- expires_at
- requested_valid_until
- status
```

```text
ApprovalRequestPrivilege
- approval_request
- privilege
```

```text
ApprovalDecision
- request
- decision
- approver
- comment
- decided_at
```

Purpose：

```text
HIGH_RISK_ACTION
JIT_ELEVATION
BREAK_GLASS_REVIEW
DELEGATION
```

Target：

```text
TENANT
WORKSPACE
PROJECT
ENVIRONMENT
RESOURCE
```

RESOURCE 继续使用：

```text
(tenant_id, resource_kind, resource_id)
```

值语义，不创建万能 Resource table。

第一版一条 ApprovalRequest 只有一个 final ApprovalDecision。未来多级审批需要独立 ApprovalStage/Decision 扩展，不把多级语义伪装进当前单决策字段。

关键规则：

- requester / beneficiary 必须属于同一 Tenant；
- approver 必须属于同一 Tenant 或平台；
- requester 不能自己批准自己的请求；
- 只有 PENDING 可被 approve/reject；
- 过期请求不可批准；
- APPROVED evidence 过了 `expires_at` 后不再有效；
- requested privilege 必须是 ACTIVE `Privilege`；
- Approval 只回答审批状态，不自动创建 RoleAssignment/ShareGrant。

---

## 5. TemporaryAccessGrant

JIT 与 Break-glass 都使用独立短时 grant，不复用普通 `RoleAssignment`。

```text
TemporaryAccessGrant
- tenant
- beneficiary
- mode
- scope_type
- workspace / project / environment
- source_approval_request?
- valid_from
- valid_until
- status
- reason
- emergency_reason
- notification_required
- activated_by
- revoked_by / revoked_at
```

```text
TemporaryAccessGrantPrivilege
- grant
- privilege
```

Mode：

```text
JIT
BREAK_GLASS
```

### 5.1 JIT

JIT 必须来源于：

```text
APPROVED ApprovalRequest(purpose=JIT_ELEVATION)
```

并且：

- grant beneficiary / scope / privilege set 与批准 evidence 一致；
- privilege set 必须经过 beneficiary 当前 PermissionBoundary 裁剪后仍完整允许；
- 一个 ApprovalRequest 最多激活一个 JIT grant；
- grant 只在 `valid_from <= now < valid_until` 时产生 candidate privilege。

### 5.2 Break-glass

Break-glass 第一版允许在没有预先 Approval 的紧急场景激活，但**不能绕过最终 AuthorizationService 的 explicit DENY / Policy / Entitlement / Quota / Boundary**。

必须：

- 使用专门的 `BreakGlassAuthorityChecker`，未注入 checker 时 fail closed；
- 强制非空 emergency reason；
- v1 TTL 硬上限：60 分钟；
- `notification_required = true`；
- 仍受 beneficiary PermissionBoundary；
- 单独保存 mode=evidence，不能静默持久化为 RoleAssignment。

B2.4 只保存“必须通知”的领域要求；真正邮件/Slack/SMS/Webhook 投递由未来 Outbox/Notification Provider 负责，不在 Core Model 写 provider 字段，也不伪造“已发送”。

---

## 6. DelegationGrant

Delegation 是 Principal A 在受控范围内把自己当前有效权限的一部分临时委托给 Principal B。

```text
DelegationGrant
- tenant
- delegator
- delegatee
- scope_type
- workspace / project / environment
- status
- valid_from / valid_until
- reason
- authority_snapshot
- authority_checked_at
- created_by
- revoked_by / revoked_at
```

```text
DelegationGrantPrivilege
- delegation
- privilege
```

规则：

1. delegator ≠ delegatee；
2. 两者必须属于同一 Tenant；
3. Delegation 必须有有限 `valid_until`；
4. 创建时必须通过注入式 `DelegationAuthorityChecker` 验证：

```text
requested privileges ⊆ delegator current effective privileges
```

5. 创建时同时受 delegatee PermissionBoundary；
6. resolver 每次解析时必须**重新验证 delegator 当前 authority**，不能永久信任创建时 snapshot；
7. 如果 authority checker 不可用/异常/返回不足，fail closed；
8. Delegation 只是 candidate grant，仍受 Policy/Entitlement/Quota/Approval/Risk 等最终组合约束。

`authority_snapshot` 只保存创建时可解释 evidence，不成为永久权限来源。

---

## 7. Resolver / Service 输出

### PermissionBoundaryResolver

输入 candidate privilege keys，输出：

```text
BoundaryResolution
- constrained
- candidate_privilege_keys
- allowed_privilege_keys
- boundary_ids
```

### ApprovalResolver

输出有效 approval evidence：

```text
ApprovalResolution
- approved
- request_id
- decision_id
- privilege_keys
```

### TemporaryAccessResolver

输出短时 candidate grants：

```text
TemporaryAccessResolution
- effective_privilege_keys
- grant_ids
- modes
```

### DelegationResolver

输出经过 delegator current authority revalidation + delegatee boundary 后的 candidate grants。

所有 Resolver 返回 evidence，不只返回 bool。

---

## 8. 最低数据库不变量

1. PermissionBoundary scope shape CHECK；
2. PermissionBoundary valid window CHECK；
3. PermissionBoundary revoke shape CHECK；
4. BoundaryPrivilege `(boundary, privilege)` unique；
5. ApprovalRequest target shape CHECK；
6. ApprovalRequest `expires_at > requested_at`；
7. ApprovalRequest `requested_valid_until > requested_at`（若存在）；
8. ApprovalRequestPrivilege `(request, privilege)` unique；
9. ApprovalDecision request 一对一；
10. TemporaryAccessGrant scope shape CHECK；
11. TemporaryAccessGrant `valid_until > valid_from`；
12. JIT 必须有 source approval；
13. BREAK_GLASS 必须有 emergency reason 且 `notification_required=true`；
14. source approval 非空时最多对应一个 temporary grant；
15. TemporaryAccessGrant revoke shape CHECK；
16. DelegationGrant scope shape CHECK；
17. DelegationGrant `valid_until > valid_from`；
18. DelegationGrant revoke shape CHECK；
19. DelegationPrivilege `(delegation, privilege)` unique。

跨 Tenant 与 ACTIVE Privilege 等无法用普通 CHECK 表达的关系继续由 model validation + domain service + tests 双层保护。

---

## 9. 最低测试门槛

至少覆盖：

1. Boundary Principal/Scope 跨 Tenant 拒绝；
2. 多 Boundary 取 privilege 交集；
3. 无 Boundary 不增加限制；
4. Boundary 不能产生 candidate 之外的新权限；
5. deprecated privilege fail closed；
6. Approval target shape；
7. Approval requester/beneficiary tenant isolation；
8. self-approval 拒绝；
9. expired approval 拒绝；
10. ApprovalResolver 返回 evidence；
11. JIT 未批准不可激活；
12. JIT boundary 越界不可激活；
13. JIT activation 幂等；
14. Break-glass 无 checker fail closed；
15. Break-glass TTL > 60m 拒绝；
16. Break-glass 强理由与 notification requirement；
17. temporary grant revoke / expiry；
18. Delegation 超过 delegator authority 拒绝；
19. Delegation 超过 delegatee boundary 拒绝；
20. Delegation resolver 重新验证 delegator current authority；
21. Delegation cross-tenant 拒绝；
22. deprecated/unknown privilege fail closed。

全部新增代码继续通过：

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

## 10. 当前不做

B2.4 不做：

- 最终 AuthorizationService 组合器；
- 多级/法定人数审批工作流；
- 外部通知 Provider；
- 通用审计/Observability 平台；
- PAM vault / secret rotation；
- 跨 Tenant delegation；
- 把 Break-glass 变成永久系统管理员；
- 把 PermissionBoundary 变成 grant source；
- 把 elevated access 写进普通 RoleAssignment。

这些边界后续若改变，必须通过正式 Spec/ADR/Amendment，而不是静默扩张当前模型。

# Phase B2.2 — Resource Sharing Contract

> 状态：FROZEN FOR IMPLEMENTATION  
> 范围：ShareGrant / ShareGrantPrivilege / AccessRequest  
> 依赖：Phase B1 IAM + Phase B2.1 Policy Core

## 1. 目标

B2.2 解决“把**某一个具体资源**按明确动作分享给某个主体”的问题。

它不替代 RoleAssignment，不替代 Policy，也不创造新的万能 Resource 表。

```text
RoleAssignment
  = Tenant / Workspace / Project / Environment 层级授权

PolicyAttachment
  = DENY / REQUIRE_APPROVAL / ALLOW 等策略输入

ShareGrant
  = 单个 ResourceRef 上的显式 ALLOW 候选

AccessRequest
  = 主体对单个 ResourceRef 提出访问请求的意图记录
```

最终授权仍由未来统一 AuthorizationService 决策：

```text
Authenticated
AND Entitled
AND WithinQuota
AND PolicyAllowed
AND Authorized
```

其中 ShareGrant 只贡献 `Authorized` 的一个候选来源。

关键安全契约：

```text
explicit DENY > REQUIRE_APPROVAL > ShareGrant ALLOW / RBAC ALLOW
```

因此：

> ShareGrant ≠ final AuthorizationDecision

---

## 2. 资源引用边界

B2.2 继续复用 B2.1 已冻结的 ResourceRef 值语义：

```text
(tenant_id, resource_kind, resource_id)
```

其中：

- `tenant_id`：资源所属 Tenant；
- `resource_kind`：跨模块稳定、可 namespaced 的资源类型键；
- `resource_id`：资源在所属模块中的稳定 UUID。

例如：

```text
(asset, <uuid>)
(map, <uuid>)
(workflow, <uuid>)
(model_pack, <uuid>)
(result, <uuid>)
```

禁止为了 ShareGrant 创建：

```text
UniversalResource
```

也禁止在 Governance Core 中直接 FK：

```text
Asset / Map / Workflow / Model / Result / ...
```

资源存在性、资源 Tenant 与 classification 等信息，后续由 `ResourceResolver` / adapter 校验。

---

## 3. ShareGrant

ShareGrant 表示：

> 对一个单独 ResourceRef，向一个 Principal 或 Group 显式授予一组 Privilege。

建议字段：

```text
ShareGrant
- id UUIDv7
- tenant
- resource_kind
- resource_id
- grantee_type
- principal nullable
- group nullable
- status
- valid_from nullable
- valid_until nullable
- conditions jsonb
- granted_by
- revoked_by nullable
- revoked_at nullable
- created_at
- updated_at
- lock_version
```

### 3.1 Grantee shape

第一版只允许：

```text
PRINCIPAL XOR GROUP
```

不允许 Role 作为 ShareGrant grantee。

原因：

- Role 本身已经有 RoleAssignment 的层级语义；
- 把 Role 再作为单资源 ACL 主体会混淆 RBAC 与 resource sharing；
- 若需要“满足某角色时应用策略”，B2.1 PolicyAttachment 已支持 `ROLE` subject。

### 3.2 同租户约束

第一版禁止 ShareGrant 穿透 Tenant 边界：

```text
ShareGrant.tenant
= ResourceRef tenant
= Principal/Group tenant
```

外部协作者应先以 guest / federated / external Principal 的形式进入资源 Tenant，再接收 ShareGrant。

真正 cross-tenant federation 留给后续独立 Contract，不在 B2.2 暗中开放。

### 3.3 生命周期

第一版状态：

```text
ACTIVE
REVOKED
```

时间窗口：

```text
valid_from
valid_until
```

有效候选必须同时满足：

```text
status = ACTIVE
AND valid_from <= now   (若存在)
AND valid_until > now   (若存在)
```

撤销必须记录：

```text
revoked_by
revoked_at
```

不能靠物理删除表达撤销。

---

## 4. ShareGrantPrivilege

ShareGrant 与 Privilege 使用正式 through model：

```text
ShareGrant
  1 ─── N ShareGrantPrivilege N ─── 1 Privilege
```

不把权限动作塞成不可校验的字符串数组。

理由：

- Privilege 是 B1 已冻结的平台动作词汇；
- 能做 FK 完整性；
- 能拒绝 deprecated Privilege；
- 能保留审计与未来 risk-level 判断；
- 不会产生 `tile_read` 自动包含 `download` 之类隐式权限。

至少约束：

```text
UNIQUE(grant, privilege)
```

应用服务创建 ShareGrant 时必须保证 privilege 集非空，并且全部处于 ACTIVE。

---

## 5. AccessRequest

AccessRequest 回答：

> “某 Principal 希望访问某一个 ResourceRef，并请求哪些 Privilege？”

它不是审批流，也不是最终授权。

```text
AccessRequest ≠ Approval
AccessRequest ≠ ShareGrant
```

建议字段：

```text
AccessRequest
- id UUIDv7
- tenant
- requester Principal
- resource_kind
- resource_id
- justification
- status
- requested_valid_until nullable
- fulfilled_by_grant nullable
- decided_by nullable
- decided_at nullable
- created_at
- updated_at
- lock_version
```

状态：

```text
PENDING
FULFILLED
REJECTED
CANCELLED
EXPIRED
```

语义：

- `PENDING`：等待资源所有者/治理服务处理；
- `FULFILLED`：已经产生一个正式 ShareGrant；
- `REJECTED`：请求被拒绝，但没有生成授权；
- `CANCELLED`：请求者主动撤回；
- `EXPIRED`：请求本身过期。

真正高风险动作的审批、JIT/PIM、Break-glass 属于 B2.4。

---

## 6. AccessRequestPrivilege

AccessRequest 请求的动作同样通过正式 Privilege FK 表达：

```text
AccessRequest
  1 ─── N AccessRequestPrivilege N ─── 1 Privilege
```

它是 AccessRequest 聚合内部的关系对象，不升级为新的治理根对象。

约束：

```text
UNIQUE(access_request, privilege)
```

提交请求时：

- privilege 集必须非空；
- privilege 必须 ACTIVE；
- 整个创建过程必须原子化。

---

## 7. 服务边界

B2.2 应通过 Application Service 修改聚合，禁止控制器散落写表。

### 7.1 ShareGrantService

至少：

```text
create_grant(...)
revoke_grant(...)
```

`create_grant` 必须在同一事务内：

1. 校验 ResourceRef shape；
2. 校验 grantee tenant；
3. 校验 actor tenant；
4. 校验 privilege 集非空；
5. 校验全部 Privilege ACTIVE；
6. 创建 ShareGrant；
7. 创建 ShareGrantPrivilege；
8. 任一步失败全部回滚。

`revoke_grant` 必须原子记录：

```text
status = REVOKED
revoked_by
revoked_at
```

### 7.2 AccessRequestService

至少：

```text
submit_request(...)
fulfill_request(...)
cancel_request(...)
reject_request(...)
```

`fulfill_request` 不是 B2.4 Approval 引擎；它只表示普通资源分享请求被处理，并原子地产生一个 ShareGrant。

高风险/需审批动作未来必须先经过 Policy/Approval 决策，再允许调用 fulfillment。

---

## 8. ShareGrant Resolver

B2.2 增加一个只负责“候选显式资源授权”的 resolver：

```text
ResolveShareGrants(
  principal,
  resource_ref,
  now
) -> CandidateGrantSet
```

来源：

```text
Direct ShareGrant(principal)
+
ShareGrant(group)
where principal ∈ active group membership
```

必须 fail closed：

- grant REVOKED → 忽略；
- grant 未生效/已过期 → 忽略；
- GroupMembership inactive/expired → 忽略；
- Privilege deprecated → 忽略；
- Tenant 不匹配 → 不返回；
- resource_kind/resource_id 不完全匹配 → 不返回。

resolver 只返回候选 Grant + evidence，不做最终 Policy/Quota/Entitlement 决策。

---

## 9. 数据库约束

B2.2 至少需要：

1. ShareGrant grantee PRINCIPAL/GROUP XOR；
2. ShareGrant `resource_kind != ''` 且 `resource_id IS NOT NULL`；
3. `valid_until > valid_from`；
4. ACTIVE grant 不带 revoke metadata；
5. REVOKED grant 必须有 `revoked_by + revoked_at`；
6. 同一 ResourceRef + 同一 Principal 最多一个 ACTIVE ShareGrant；
7. 同一 ResourceRef + 同一 Group 最多一个 ACTIVE ShareGrant；
8. ShareGrantPrivilege `(grant, privilege)` UNIQUE；
9. AccessRequest ResourceRef shape；
10. AccessRequestPrivilege `(access_request, privilege)` UNIQUE；
11. FULFILLED AccessRequest 必须指向 `fulfilled_by_grant`；
12. Tenant/Principal/Group/Actor/Grant 的跨租户一致性由 `clean()` + Service 双重校验。

---

## 10. 索引

第一批建议：

```text
ShareGrant(tenant, resource_kind, resource_id, status)
ShareGrant(tenant, principal, status)
ShareGrant(tenant, group, status)
ShareGrantPrivilege(grant)
ShareGrantPrivilege(privilege)
AccessRequest(tenant, requester, status)
AccessRequest(tenant, resource_kind, resource_id, status)
AccessRequestPrivilege(access_request)
```

不为 `conditions` JSONB 提前建 GIN。

---

## 11. 与最终 AuthorizationService 的关系

最终决策输入概念：

```text
RoleGrantResolver
+
ShareGrantResolver
+
PolicyEvaluator
+
Entitlement
+
Quota/Budget
+
Action Risk / Approval
        ↓
AuthorizationService.Can(...)
```

B2.2 不提前把这些模块揉成一个 God Service。

---

## 12. B2.2 Definition of Done

必须同时满足：

- [ ] ShareGrant / ShareGrantPrivilege 模型与 DB constraints；
- [ ] AccessRequest / AccessRequestPrivilege 模型与 DB constraints；
- [ ] ShareGrantService 原子创建/撤销；
- [ ] AccessRequestService 原子提交/处理；
- [ ] ShareGrantResolver 直接/组授权 + evidence；
- [ ] cross-tenant fail closed；
- [ ] deprecated Privilege fail closed；
- [ ] Django-generated migration；
- [ ] 空 PostGIS migrate；
- [ ] pytest；
- [ ] provider leak；
- [ ] Ruff；
- [ ] strict mypy；
- [ ] pip-audit；
- [ ] production Docker build；
- [ ] Preview smoke。

# Phase B｜IAM & Governance 实现规格

> 状态：**Implementation Spec**  
> 分支：`feat/phase-b-iam-governance`  
> 上位约束：`Principal ≠ Role ≠ Entitlement ≠ Quota`，以及 `Tenant ≠ Workspace ≠ Project ≠ Environment`。  
> 本文只把已冻结的企业治理边界映射为可实现的 Django/PostgreSQL 结构，不重新设计总体架构。

---

## 1. Phase B 目标

Phase A 已经建立：

```text
Account
  ≠
Principal

Tenant → Workspace → Project → Environment

Asset → AssetVersion → Artifact / Distribution
```

Phase B 要回答：

> **谁，可以在什么范围内，对什么资源，执行什么动作？**

但企业级授权不能简化成：

```text
user.role = "admin"
```

也不能把：

```text
Role
Policy
Entitlement
Quota
Share
Approval
```

混成一个“权限表”。

因此 Phase B 保持以下边界：

```text
Authentication  = 你是谁
Principal       = 谁在执行动作
Privilege       = 可以执行什么动作
Role            = 一组 Privilege
RoleAssignment  = 谁在什么管理范围拥有某 Role
ShareGrant      = 某个具体资源被分享给谁
Policy          = 在上下文条件下允许/拒绝/要求审批
Entitlement     = 是否购买/获得某产品能力
Quota           = 最多可以使用多少
Approval        = 高风险动作是否已经获得人工授权
```

---

# 2. Phase B 分两步实施

## B1 — Authorization Core

本 PR 第一批真正实现：

```text
Privilege
Group
GroupMembership
RoleDefinition
RolePrivilege
RoleAssignment
```

目标：形成稳定的企业级 RBAC + Scope Inheritance 地基。

## B2 — Governance Controls

B1 稳定以后再实现：

```text
PolicyDefinition
PolicyAttachment
EntitlementGrant
Quota
PermissionBoundary
JIT / Temporary Elevation
Break-glass
ShareGrant
AccessRequest
Approval integration
```

这样避免一次把 IAM 做成不可审查的巨型模块。

---

# 3. 认证与授权继续分离

Phase A 已冻结：

```text
Account ≠ Principal
```

### Account

只负责 Django 登录认证。

### Principal

统一权限主体：

```text
HUMAN_USER
SERVICE_ACCOUNT
AGENT
EXTERNAL_APPLICATION
FEDERATED
```

因此未来：

```text
GeoAgent
CI service account
external application
```

都可以获得权限，但不需要伪装成 Django User。

---

# 4. Privilege

建议表：`sf_privilege`

Privilege 是稳定动作词汇，不是 Role。

```text
id                UUIDv7
key               unique string
name              中文名称
category          READ | WRITE | EXECUTE | GOVERNANCE | ADMIN | SECRET
risk_level        LOW | MEDIUM | HIGH | CRITICAL
description       text
status            ACTIVE | DEPRECATED
system_managed    bool
created_at
updated_at
```

## 4.1 Core Privilege Key

第一批固定动作来自架构 Contract：

```text
discover
view_metadata
read
query
tile_read
feature_read
download
export
create
edit
delete
publish
share
execute
use_secret
approve
manage
admin
```

其中特别强调：

```text
execute ≠ read ≠ download
```

因此完全允许：

```text
可以执行 AERMOD
但不能下载 ModelPack
```

或者：

```text
可以读取地图 tile
但不能导出底层 Dataset
```

## 4.2 为什么 Privilege 建表，而不是 Role JSON 数组

原因：

- RolePrivilege 可以使用正式 FK；
- 插件以后可以注册 namespaced privilege；
- 可以标记风险等级；
- 可以做依赖、审计、文档与 UI；
- 避免字符串拼写错误成为长期权限漏洞。

Privilege 是系统参考数据，通常不由普通 Tenant 用户任意修改。

---

# 5. Group

建议表：`sf_group`

Group 是权限主体集合，不等于组织部门。

```text
OrgUnit ≠ Group ≠ Team
```

第一版 Group 类型：

```text
SECURITY
COLLABORATION
```

核心字段：

```text
id
 tenant_id
name
slug
group_type
status
description
created_by
created_at
updated_at
lock_version
```

约束：

```text
UNIQUE(tenant_id, slug)
```

禁止删除 Tenant 时级联删除权限证据。

---

# 6. GroupMembership

建议表：`sf_group_membership`

```text
id
tenant_id
group_id
principal_id
status              ACTIVE | SUSPENDED
valid_from           nullable
valid_until          nullable
added_by
created_at
updated_at
```

约束：

```text
UNIQUE(group_id, principal_id)
valid_until > valid_from
```

### 6.1 Tenant invariant

```text
group.tenant
=
principal.tenant
=
membership.tenant
```

第一版不允许平台级 Principal 被悄悄加入普通 Tenant Group；平台级系统主体要通过单独的平台治理路径处理。

### 6.2 Agent 可以加入 Group 吗

可以。

因为 Group 成员是 Principal，而不是 Account。

但 Agent 的最终有效权限仍必须满足：

```text
Agent permission
∩ delegated user permission
∩ tool permission
∩ project policy
∩ risk policy
```

B1 Group 只解决 RBAC 关系，不意味着 Agent 可以绕过后续 B2 Policy。

---

# 7. RoleDefinition

建议表：`sf_role_definition`

```text
id
tenant_id nullable
key
name
description
status              ACTIVE | DEPRECATED
is_system
is_assignable
allowed_scope_types jsonb
created_by nullable
created_at
updated_at
lock_version
```

语义：

### tenant_id IS NULL

平台预定义 Role / Role Template。

### tenant_id NOT NULL

Tenant 自定义 Role。

唯一约束：

```text
平台 role:  key UNIQUE WHERE tenant IS NULL
租户 role:  UNIQUE(tenant, key) WHERE tenant IS NOT NULL
```

Role 不保存：

```text
users JSON
privileges JSON
assets JSON
```

Privilege 使用正式 RolePrivilege 关系。

---

# 8. RolePrivilege

建议表：`sf_role_privilege`

```text
id
role_id
privilege_id
created_at
```

约束：

```text
UNIQUE(role_id, privilege_id)
```

RolePrivilege 只表达 **GRANT**。

显式 DENY 不塞进 Role；DENY 属于 B2 `PolicyDefinition`。

原因：

```text
RBAC Role = 可理解、可复用的允许能力集合
Policy    = 上下文和例外规则
```

否则 Role 很快会变成无法解释的 allow/deny 规则引擎。

---

# 9. RoleAssignment

建议表：`sf_role_assignment`

回答：

> 某 Principal / Group 在某管理范围内拥有什么 Role？

```text
id
tenant_id
principal_id nullable
group_id nullable
role_id
scope_type          TENANT | WORKSPACE | PROJECT | ENVIRONMENT
workspace_id nullable
project_id nullable
environment_id nullable
status              ACTIVE | REVOKED
valid_from nullable
valid_until nullable
conditions jsonb
 granted_by
 created_at
 updated_at
 lock_version
```

## 9.1 Subject XOR

必须：

```text
principal XOR group
```

不能同时存在，也不能同时为空。

## 9.2 Scope invariant

### TENANT

```text
workspace = NULL
project = NULL
environment = NULL
```

### WORKSPACE

```text
workspace != NULL
project = NULL
environment = NULL
```

### PROJECT

```text
project != NULL
environment = NULL
```

### ENVIRONMENT

```text
environment != NULL
```

Tenant 永远显式保存，用于：

- tenant isolation；
- RLS；
- 索引；
- 防止意外跨租户引用。

## 9.3 为什么 RoleAssignment 第一版不直接 FK Asset

这是一个重要边界。

RoleAssignment 负责：

```text
Tenant / Workspace / Project / Environment
```

这些**管理范围的角色授权**。

单个具体资源，例如：

```text
某一个 Dataset
某一张 Map
某一个 ModelPack
某一个 Workflow
```

的精确分享应使用未来：

```text
ShareGrant
```

而不是在 RoleAssignment 中不断增加：

```text
asset_id
map_id
model_id
workflow_id
...
```

这样还有一个工程收益：

```text
iam
   ↓
assets
```

不会反过来形成：

```text
iam ↔ assets
```

迁移依赖环。

---

# 10. Scope inheritance

RoleAssignment 默认沿层级向下继承：

```text
Tenant
  ↓
Workspace
  ↓
Project
  ↓
Environment
```

例如：

```text
Alice = Workspace Viewer
```

默认可读取该 Workspace 下 Project / Environment 中允许 Viewer 的资源。

但未来 B2 Policy 可以：

```text
explicit deny
permission boundary
classification policy
```

限制继承。

因此 B1 不能把：

> “父 scope Role 永远无条件拥有子资源权限”

写成数据库硬逻辑。

实际 effective authorization 后续统一由：

```text
AuthorizationService
```

计算。

---

# 11. Group Role Inheritance

有效 B1 Role 来源：

```text
Direct RoleAssignment(principal)
+
RoleAssignment(group)
where principal ∈ active group
```

不得复制 Group Role 到每个成员的 RoleAssignment。

否则：

```text
1000 人 group
角色变更
→ 更新 1000 行
```

不仅低效，也会让审计语义错误。

---

# 12. 时间有效性

GroupMembership 和 RoleAssignment 都允许：

```text
valid_from
valid_until
```

用于：

- 临时项目成员；
- 顾问；
- 外部合作；
- 临时管理员。

但正式的 JIT / PIM 临时提权仍留给 B2。

B1 时间窗口只是基础授权有效期，不等于审批驱动的 JIT。

---

# 13. RoleAssignment conditions

第一版保留：

```text
conditions jsonb
```

但只允许 schema-validated、低复杂度约束。

例如：

```json
{
  "environment_types": ["DEV", "STAGING"]
}
```

不要在 B1 自己发明完整 policy language。

复杂：

```text
IP
network zone
risk
classification
time
approval state
```

全部交给 B2 Policy / OPA-compatible adapter。

---

# 14. B2 PolicyDefinition

B2 才实现。

建议模型：

```text
PolicyDefinition
PolicyAttachment
```

Policy effect：

```text
ALLOW
DENY
REQUIRE_APPROVAL
```

需要支持：

```text
action selector
principal selector
resource selector
context conditions
priority
```

关键原则：

```text
explicit DENY > inherited ALLOW
```

但 B1 不提前实现完整策略引擎。

---

# 15. EntitlementGrant

Entitlement 回答：

> 是否拥有产品/能力许可？

不是：

> 当前用户有没有 read 权限？

例如：

```text
geophysics.aermod
geoagent.pro
envos.enterprise
```

用户最终执行必须：

```text
Authenticated
AND Entitled
AND Authorized
AND WithinQuota
AND PolicyAllowed
```

因此：

```text
Role ≠ Entitlement
```

---

# 16. Quota

Quota 回答：

> 最多可以使用多少？

例如：

```text
concurrent_jobs
storage_bytes
gpu_seconds
tile_qps
api_qps
ai_tokens
```

Quota 不存进 Role。

也不能用 Prometheus metric 临时推导成权威额度。

---

# 17. ShareGrant

资源级分享使用独立 `ShareGrant`：

```text
resource_ref
grantee_ref
actions
valid_from
valid_until
conditions
```

这允许：

```text
把某一张 Map 分享给外部协作者
```

而不需要给对方整个 Project 的 Role。

ShareGrant 不改变：

```text
resource owner
resource tenant
```

---

# 18. 权限动作与地图/模型的实际例子

## 18.1 Map

```text
view_metadata
+
tile_read
```

不自动包含：

```text
download Dataset
```

## 18.2 Dataset

可以：

```text
query
```

但不一定：

```text
export
```

## 18.3 ModelPack

可以：

```text
execute
```

但不一定：

```text
read artifact
 download runtime image
 edit model
```

## 18.4 Agent

Agent 不拥有“超级权限”。

最终有效权限必须做交集：

```text
Agent Role
∩ Delegated User Permission
∩ Tool Permission
∩ Project Policy
∩ Action Risk Policy
```

---

# 19. 数据库约束

B1 至少需要：

1. Privilege.key UNIQUE；
2. Group `(tenant, slug)` UNIQUE；
3. GroupMembership `(group, principal)` UNIQUE；
4. GroupMembership 作用域 Tenant 一致；
5. RoleDefinition 平台/租户条件唯一；
6. RolePrivilege `(role, privilege)` UNIQUE；
7. RoleAssignment principal/group XOR；
8. RoleAssignment scope shape CHECK；
9. RoleAssignment subject/role/scope 防重复；
10. `valid_until > valid_from`；
11. Principal / Group / Role / Scope 的 tenant 一致；
12. 已 REVOKED RoleAssignment 不允许参与有效权限计算。

---

# 20. 索引

第一批建议：

```text
Privilege(key, status)
Group(tenant, status)
GroupMembership(principal, status)
GroupMembership(group, status)
RoleDefinition(tenant, status)
RolePrivilege(role)
RoleAssignment(tenant, principal, status)
RoleAssignment(tenant, group, status)
RoleAssignment(workspace, status)
RoleAssignment(project, status)
RoleAssignment(environment, status)
```

不为 `conditions` JSONB 提前创建 GIN 索引。

---

# 21. AuthorizationService 边界

最终统一接口概念：

```text
Can(
  principal,
  action,
  resource,
  context
) -> AuthorizationDecision
```

B1 先提供 RBAC 输入：

```text
principal direct assignments
+
group assignments
+
scope inheritance
```

B2 再叠加：

```text
Policy
Entitlement
Quota
Permission Boundary
Approval
Delegation
```

任何业务 app 禁止自己写：

```python
if user.is_admin:
    ...
```

或者：

```python
if project.owner == request.user:
    ...
```

所有正式资源授权最终必须走 Fabric AuthorizationService。

---

# 22. 不直接采用 Django Permission 作为 Fabric 权限模型

Django 自带：

```text
Group
Permission
User.user_permissions
```

适合 Django Admin / Model CRUD。

但 Spatial Fabric 需要：

- Principal 不只是 User；
- 多 Tenant；
- Workspace/Project/Environment Scope；
- execute 与 download 分离；
- Group 临时有效期；
- Policy / Entitlement / Quota；
- Agent delegation；
- resource sharing；
- future ReBAC。

因此 Django 内建 Permission 不能作为 Fabric IAM System of Record。

Django `is_staff/is_superuser` 只用于控制 Django 管理站，不代表 Fabric 平台业务权限。

---

# 23. OpenFGA / OPA 的未来边界

B1/B2 定义的是稳定 Domain Contract。

以后：

```text
AuthorizationProvider
  ├── Internal Fabric
  ├── OpenFGA Adapter
  └── ...

PolicyProvider
  ├── Internal Fabric
  ├── OPA Adapter
  └── ...
```

因此数据库禁止出现：

```text
openfga_tuple_id
opa_bundle_url
keycloak_role_id
```

作为核心字段。

---

# 24. B1 migration 建议

在 Phase A `iam/0001_initial` 后追加：

```text
iam/0002_authorization_core
```

包含：

```text
Privilege
Group
GroupMembership
RoleDefinition
RolePrivilege
RoleAssignment
```

如果 Django 自动拆成多个 migration，只要求：

```text
无环
空库可重建
可回滚到 Phase A schema
```

不为了文件编号好看手工制造依赖。

---

# 25. B1 测试最低要求

必须覆盖：

1. Group 不能跨 Tenant 收成员；
2. Tenant Role 不能赋给其他 Tenant 的 Principal；
3. 平台 Role 可以用于 Tenant scope；
4. RoleAssignment 只能 principal/group 二选一；
5. TENANT/WORKSPACE/PROJECT/ENVIRONMENT scope shape 正确；
6. Workspace/Project/Environment 必须属于 assignment tenant；
7. Group Role 不复制为成员 Direct Role；
8. valid_from/valid_until 生效；
9. REVOKED assignment 不参与授权；
10. `execute` 与 `download` 是独立 Privilege；
11. Django superuser 不自动等于 Fabric admin；
12. Agent Principal 可以通过正常 RoleAssignment 获权，但不绕过后续 Policy。

---

# 26. B1 完成 Gate

```text
[ ] models 已实现
[ ] 中文领域注释完整
[ ] migration 由真实 Django 生成
[ ] makemigrations --check 通过
[ ] 空 PostGIS migrate 通过
[ ] invariant tests 通过
[ ] Ruff 通过
[ ] pip-audit 通过
[ ] Docker build 通过
[ ] Preview smoke test 通过
[ ] handoff / CURRENT_STATUS 已同步
```

B1 全部完成后，才进入 B2 Policy / Entitlement / Quota。

---

# 27. 最终冻结结论

Phase B 必须长期保持：

```text
Account ≠ Principal
Principal ≠ Group
Group ≠ OrgUnit
Privilege ≠ Role
Role ≠ RoleAssignment
RoleAssignment ≠ ShareGrant
Role ≠ Policy
Role ≠ Entitlement
Entitlement ≠ Quota
Authorization ≠ Authentication
```

以及：

> **RoleAssignment 负责管理层级 Scope 的 RBAC；ShareGrant 负责具体资源分享；Policy 负责条件与显式拒绝；Entitlement 负责商业许可；Quota 负责使用上限。**

这五类语义禁止为了“表少一点”而合并。
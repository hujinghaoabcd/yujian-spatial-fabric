# ADR-0002 — Account 与 Principal 分离

**Status:** ACCEPTED  
**Date:** 2026-08-30

## Context

Django `User` 解决登录认证，但 Spatial Fabric 的授权主体还包括 ServiceAccount、GeoAgent、ExternalApplication 等。

## Decision

- `iam.Account`：Django `AUTH_USER_MODEL`，负责登录认证；
- `iam.Principal`：Fabric 领域授权主体；
- Human Principal 可 OneToOne 关联 Account；
- Machine/Agent Principal 不需要伪装成 Account。

外部 Keycloak/OIDC/SAML 身份以后通过 IdentityLink 关联 Principal。

## Consequences

后续 RoleAssignment、ShareGrant、Audit actor、Agent Delegation 全部引用 Principal，而不是直接绑定 Django Account。

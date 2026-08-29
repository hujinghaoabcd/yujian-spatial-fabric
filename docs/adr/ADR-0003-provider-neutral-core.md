# ADR-0003 — Core Domain 保持 Provider Neutral

**Status:** ACCEPTED  
**Date:** 2026-08-30

## Decision

核心领域模型不得保存具体 Provider 的内部概念作为业务字段，例如：

```text
geoserver_workspace_id
martin_source_name
titiler_url
kubernetes_pod
keycloak_role
openfga_tuple
minio_bucket
```

通过：

```text
Provider Interface
ProviderMapping
provider_config
stable Fabric reference
```

接入。

## Rationale

Spatial Fabric 的核心价值来自稳定的资产、执行、治理和空间契约，而不是某个基础设施组件。Provider 可以根据部署环境、国产化要求和技术演进替换。

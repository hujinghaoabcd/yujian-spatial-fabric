# ADR-0001 — Control Plane 采用模块化单体

**Status:** ACCEPTED  
**Date:** 2026-08-30

## Context

Spatial Fabric 产品架构包含多个引擎和大量能力，但创业/首版阶段尚不存在足够真实负载来支撑按产品名称拆分微服务。

## Decision

Control Plane 采用：

```text
Django Modular Monolith
+
Independent Workers / Runners / Providers
```

Tenancy、IAM、Asset、Execution、Service 等首先作为明确 Django bounded-context app 存在。

地图服务、科学模型、AI inference、容器 Runner 等天然独立的重运行组件可以独立部署。

## Consequences

优点：事务边界清楚、开发效率高、交接成本低、早期运维简单。  
要求：必须保持模块依赖规则，不能因为同库就任意跨 app 改表。

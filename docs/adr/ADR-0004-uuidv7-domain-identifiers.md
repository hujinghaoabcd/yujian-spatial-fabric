# ADR-0004 — 核心 Domain ID 使用 UUIDv7

**Status:** ACCEPTED  
**Date:** 2026-08-30

## Decision

核心 Aggregate Root 使用 UUIDv7 作为公开 Domain ID。

Python 3.12 当前没有内置 `uuid.uuid7()`，项目在 `spatial_fabric.common.ids` 提供 RFC 9562 布局实现。未来最低 Python 版本原生支持后，可以替换生成实现而不改变数据库字段和 API。

## Consequences

- 全局唯一；
- 基本按创建时间有序，有利于 B-tree locality；
- 不暴露连续行数；
- 支持多节点/私有化产生 ID；
- 外部 Provider ID 永远不能替代 Fabric ID。

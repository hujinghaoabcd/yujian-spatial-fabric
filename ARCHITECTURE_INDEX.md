# Spatial Fabric 架构文档索引

本文件定义仓库内设计文档的权威顺序。

## 优先级

```text
00_PROJECT_HANDOFF.md
        ↓
Spatial Fabric Architecture FINAL
        ↓
Technology Architecture FINAL
        ↓
Domain Model FINAL
        ↓
ADR
        ↓
Database / ERD
        ↓
Django Models / Migrations
        ↓
API / Provider / Runner 实现
```

如果下位设计与上位规范冲突，不得直接以代码“事实”覆盖架构；必须先确认是否能通过 Adapter、Provider、Projection、Typed Facet 或 Package 扩展解决。

## 当前已在仓库中的数据库设计

- `docs/database/phase-a-erd.md`
- `docs/database/phase-a-migration-plan.md`

## 三份 FINAL 上位规范

三份全文来自项目正式设计阶段，文件名固定为：

```text
docs/architecture/spatial-fabric-architecture.md
docs/architecture/spatial-fabric-technology-architecture.md
docs/domain-model/spatial-fabric-domain-model.md
```

当前首个远程 PR 优先落地可运行骨架和 Phase A 代码；上述大型全文需要完整同步，**不得用截断版或摘要冒充 FINAL 全文**。在全文同步前，冻结 Contract 以 `00_PROJECT_HANDOFF.md` 和 Phase A ERD 为最低可执行依据。

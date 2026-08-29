# Contributing｜开发协作规范

## 1. 开始开发前

必须先阅读：

1. `00_PROJECT_HANDOFF.md`
2. `ARCHITECTURE_INDEX.md`
3. 当前 Phase 的 ERD / ADR
4. `docs/project/CURRENT_STATUS.md`

## 2. 分支

推荐：

```text
feat/<topic>
fix/<topic>
refactor/<topic>
docs/<topic>
chore/<topic>
```

禁止直接在 `main` 上进行大规模未审查开发。

## 3. 代码原则

- 核心领域模型优先写详细中文 docstring / 注释，重点解释“为什么这样设计”；
- 英文类名、协议名、标准字段保持标准英文；
- Domain Model 不依赖 GeoServer/Martin/TiTiler/Kubernetes 等具体 Provider；
- Web 请求不执行长任务；
- Published Version 不原地覆盖；
- 历史证据默认 `PROTECT` / append-only；
- Tenant 隔离是所有 Repository / Query 的默认条件；
- Secret 不进入源代码、JSON spec、日志或异常消息。

## 4. 数据库变更

任何模型变化必须：

```text
Domain/ERD review
→ migration
→ migration check
→ empty-db migrate
→ tests
→ CURRENT_STATUS update
```

已应用的 migration 原则上不重写。

## 5. 架构变更

以下变化必须 ADR：

- 修改冻结边界；
- 引入新的 Aggregate Root；
- 更换 System of Record；
- 引入新的跨模块依赖方向；
- Provider 要求核心表增加其内部字段；
- 权限/多租户/不可变版本语义发生变化。

## 6. Pull Request

PR 至少说明：

- 为什么做；
- 改了哪些 Domain Contract；
- Migration 影响；
- Security/Tenant 影响；
- 测试；
- 回滚方式；
- 是否更新 handoff / status / ADR。

## 7. Definition of Done

一个后端任务不能只以“代码写完”为完成：

```text
[ ] 代码
[ ] 中文领域注释
[ ] tests
[ ] migrations（如需要）
[ ] docs/ADR（如需要）
[ ] security / tenant review
[ ] CI green
[ ] CURRENT_STATUS updated
```

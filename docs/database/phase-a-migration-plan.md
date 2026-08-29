# Phase A Migration Plan

> 目标：确保 Spatial Fabric 第一次正式数据库迁移可重复、可回滚，并避免自定义 `AUTH_USER_MODEL` 的依赖问题。

## 推荐顺序

```text
iam/0001_initial
  └── Account only

tenancy/0001_initial
  ├── Tenant
  ├── Workspace
  ├── Project
  └── Environment

iam/0002_principal
  └── Principal → tenancy.Tenant

assets/0001_initial
  ├── Asset
  ├── AssetVersion
  ├── AssetAlias
  ├── Artifact
  └── Distribution
```

## 为什么拆开 Account 与 Principal

`Account` 是 Django auth 的 swappable user dependency，必须出现在 `iam` 的第一迁移中；而 `Principal` 依赖 `Tenant`。如果把二者强塞进同一个首迁移，会不必要地增加 `auth ↔ iam ↔ tenancy` 的依赖复杂度。

## 当前策略

当前生成环境无法访问 Python 软件包仓库，不能运行 Django 5.2 的 `makemigrations`。因此：

- 不手写未经真实 Django 解析的 migration；
- GitHub Actions 先运行 `makemigrations --dry-run --verbosity 3` 生成预览；
- 根据真实输出固化 migration；
- migration 固化后把 CI 切换到 `makemigrations --check --dry-run + migrate + pytest`。

## 正式验收命令

```bash
uv sync --group dev
uv run python backend/manage.py check
uv run python backend/manage.py makemigrations --check --dry-run
uv run python backend/manage.py migrate --noinput
uv run pytest
```

生产设置还必须运行：

```bash
uv run python backend/manage.py check --deploy --settings=config.settings.production
```

## 验收要求

- 空数据库可完整 migrate；
- migrations 可在测试环境 rollback / re-migrate；
- custom Account 可创建普通用户和 superuser；
- PostGIS 扩展可用；
- 条件唯一约束真正落入 PostgreSQL；
- Phase A 跨租户不变量测试全部通过；
- 后续模型变更必须生成新 migration，不修改已经发布/应用的历史 migration。

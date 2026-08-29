# Current Project Status｜当前项目状态

**Last updated:** 2026-08-30  
**Active phase:** Phase A — Tenancy + Principal + Asset Kernel  
**Remote branch:** `feat/phase-a-foundation`

## 已完成

- [x] 企业级 Django/PostGIS 工程骨架
- [x] split settings / API / health / OpenAPI / request ID / JSON logging
- [x] UUIDv7 基础实现
- [x] 自定义 `iam.Account`
- [x] `Principal` 模型初稿，保持 Account ≠ Principal
- [x] `Tenant / Workspace / Project / Environment` 模型初稿
- [x] `Asset / AssetVersion / AssetAlias / Artifact / Distribution` 模型初稿
- [x] Phase A ERD
- [x] Phase A migration 顺序设计
- [x] Phase A 正式 migrations 已由 GitHub CI 的 Django 5.2.17 生成并固化
- [x] provider-neutral PostGIS baseline migration
- [x] Phase A 跨租户与资产引用不变量测试
- [x] 中文领域注释/docstring 基线
- [x] 严格 GitHub Actions：migration sync / migrate / pytest / Provider leak / Ruff / audit / Docker build
- [x] 跨对话 handoff 入口
- [x] 免费 Preview 部署配置：Render Blueprint + 通用 DATABASE_URL 适配

## 当前真实验证状态

### 已验证

- [x] Django `manage.py check`
- [x] `makemigrations --check --dry-run`
- [x] PostgreSQL/PostGIS 空库 `migrate --noinput`
- [x] Phase A pytest（真实 migrations）
- [x] Provider leakage check
- [x] Ruff
- [x] pip-audit

### 当前 CI 仍需确认

- [ ] 最新提交的生产 Docker image build 全绿
- [ ] 新增 DATABASE_URL / Render Preview 适配后的完整 CI 回归
- [ ] Render 外部真实 Preview URL（需要在 Render 账号中 Apply Blueprint 后产生）

**纪律：未实际运行的项目不得标记为通过。**

## 下一任务

1. 完成 Preview 适配提交的严格 CI 回归；
2. CI 全绿后把 PR #1 从 Draft 收敛到可 Review 状态；
3. 在 Render 中 Apply `render.yaml`，验证 `/health/ready` 与 `/api/docs/`；
4. 更新 `00_PROJECT_HANDOFF.md` 和本文件中的真实外部部署状态；
5. Phase A 合并后进入 Phase B IAM & Governance。

## 当前禁止

- 不集成 GeoServer/Martin/TiTiler；
- 不开始 GeoAgent；
- 不加入 EnvOS/ParkOS 行业专属表；
- 不拆微服务；
- 不新增 provider-specific 核心字段；
- 不绕过版本发布服务直接修改 Published AssetVersion；
- 不把免费 Preview 环境当成生产环境或保存正式客户数据。

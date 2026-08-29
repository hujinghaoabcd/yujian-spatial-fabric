# Current Project Status｜当前项目状态

**Last updated:** 2026-08-30  
**Active phase:** Phase A — Tenancy + Principal + Asset Kernel  
**Remote branch:** `feat/phase-a-foundation`  
**Pull request:** #1 — Phase A Foundation

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
- [x] provider-neutral `DATABASE_URL` / `POSTGRES_*` 数据库配置
- [x] 免费 Preview 部署配置：Render Blueprint + `start-preview.sh`
- [x] 生产 Docker image 可构建
- [x] CI 中真实启动生产镜像并验证 Preview API
- [x] 跨对话 handoff 入口

## 当前真实验证状态

以下检查已在 GitHub Actions 的 PostgreSQL/PostGIS 环境真实执行并通过：

- [x] Django `manage.py check`
- [x] `makemigrations --check --dry-run`
- [x] PostgreSQL/PostGIS 空库 `migrate --noinput`
- [x] Phase A pytest（真实 migrations）
- [x] Provider leakage check
- [x] Ruff
- [x] pip-audit
- [x] production Docker image build
- [x] production image + `start-preview.sh` 启动
- [x] `/health/ready`
- [x] `/api/schema/`
- [x] `/api/docs/`

最后一轮完整验证对应提交 `dd6fb1ab`。CI 已覆盖：

```text
empty PostGIS database
        ↓
formal migrations
        ↓
pytest / Ruff / audit
        ↓
production Docker build
        ↓
start-preview.sh
        ↓
migrate -> Uvicorn
        ↓
readiness + OpenAPI + Swagger smoke test
```

## 尚未完成

- [ ] Render 账号侧真实 Apply Blueprint
- [ ] 外部 `*.onrender.com` Preview URL 验证
- [ ] Phase B Role / Policy / Entitlement / Quota
- [ ] Job / Run / Result
- [ ] ServiceDeployment / ServiceInstance
- [ ] Dataset / Layer / Style / Map / Scene 正式模型实现
- [ ] 三份大型 FINAL 架构/技术/领域规范全文同步

## Known Risk / 待收敛

- 当前仓库尚未固化 `uv.lock`。开发与 Preview 仍受 `pyproject.toml` 中有界版本范围约束，但生产可重复构建尚未达到最终冻结标准。
- 在进入正式发布/生产冻结前，应生成并提交 `uv.lock`，随后把 Docker 安装切换为 `uv sync --frozen --no-dev --no-editable`。
- Render Free Preview 仅用于演示，不能保存正式客户数据，也不能承担生产 SLA。

## 下一任务

1. 将 PR #1 标记为 Ready for Review；
2. 在 Render 账号中 Apply `render.yaml`；
3. 获得真实外部 URL 后验证 `/health/ready` 与 `/api/docs/`；
4. 外部 Preview 验证完成后同步 `00_PROJECT_HANDOFF.md`；
5. Phase A 合并后进入 Phase B IAM & Governance；
6. Map subsystem 的正式实现仍按既定 `Dataset / Layer / Style / Map / Scene / Representation` 领域设计推进，不因 Preview 部署改变核心架构。

## 当前禁止

- 不集成 GeoServer/Martin/TiTiler；
- 不开始 GeoAgent；
- 不加入 EnvOS/ParkOS 行业专属表；
- 不拆微服务；
- 不新增 provider-specific 核心字段；
- 不绕过版本发布服务直接修改 Published AssetVersion；
- 不把免费 Preview 环境当成生产环境或保存正式客户数据。

**纪律：只有真实执行过的检查才能标记为通过；外部 Render URL 尚未产生，禁止虚构链接。**

# 00 — Spatial Fabric 项目交接入口

> **任何新对话、新开发会话或新开发者接手时，第一份必须阅读的文件。**  
> 项目状态必须保存在 GitHub，而不是依赖聊天记忆。

## 1. 项目标识

- Repository：`hujinghaoabcd/yujian-spatial-fabric`
- Python namespace：`spatial_fabric`
- 当前阶段：**Phase A — Tenancy + Principal + Asset Kernel**
- 当前开发分支：`feat/phase-a-foundation`
- 当前 PR：**#1（Draft，Phase A Foundation）**
- 架构：**Django Modular Monolith Control Plane + Independent Workers/Providers**
- Domain ID：UUIDv7
- API prefix：`/api/v1/`

## 2. 接手阅读顺序

1. 本文件；
2. `ARCHITECTURE_INDEX.md`；
3. `docs/architecture/` 中的架构基线；
4. `docs/domain-model/` 中的领域模型；
5. `docs/database/phase-a-erd.md`；
6. `docs/database/phase-a-migration-plan.md`；
7. `docs/adr/`；
8. `docs/project/CURRENT_STATUS.md`；
9. 涉及免费演示部署时再读 `docs/deployment/preview.md`。

若下层代码和上位规范冲突，**禁止静默重构上位架构**。优先使用 Adapter / Provider / Projection / Typed Facet / Package；核心 Contract 确需改变时必须写 ADR。

> 注意：三份大型 FINAL 上位规范目前仍未全文同步进仓库。`ARCHITECTURE_INDEX.md` 已标记该缺口。不得把当前摘要文档误称为 FINAL 全文。

## 3. 冻结边界

```text
Asset ≠ AssetVersion ≠ Artifact ≠ Distribution
Task ≠ Workflow ≠ Job ≠ Run ≠ Result
Capability ≠ Runtime ≠ Runner
Dataset ≠ Layer ≠ Style ≠ Map ≠ Scene
Tenant ≠ OrgUnit ≠ Workspace ≠ Project ≠ Environment
Principal ≠ Role ≠ Entitlement ≠ Quota
AssetVersion ≠ ServiceDeployment ≠ ServiceInstance
Policy ≠ Provenance ≠ Evaluation ≠ Audit ≠ Observability
```

Published Version 不可原地修改；执行前 Alias 必须解析成具体 Version；Provider-specific 概念不得泄漏到 Core Model。

## 4. 当前已写入远程分支

已包含：

- Python 3.12 / Django 5.2 LTS / DRF / PostgreSQL/PostGIS 工程基线；
- GeoDjango 所需 GDAL/GEOS 运行时依赖；
- split settings、health、OpenAPI/Swagger、JSON logging、request ID；
- UUIDv7；
- `iam.Account` 自定义 Django 用户模型；
- `Principal` 领域主体，保持 `Account ≠ Principal`；
- `Tenant / Workspace / Project / Environment`；
- `Asset / AssetVersion / AssetAlias / Artifact / Distribution`；
- Phase A 跨租户与资产引用不变量测试；
- PostgreSQL/PostGIS baseline migration；
- Django 5.2.17 实际生成并固化的 Phase A migrations；
- 严格 GitHub Actions：model/migration sync、空库 migrate、真实 migration pytest、Provider leak、Ruff、pip-audit、Docker build；
- provider-neutral `DATABASE_URL` / `POSTGRES_*` 数据库配置适配；
- Render Free Preview Blueprint 与中文部署说明。

## 5. 当前 migrations 的真实状态

首批 migrations 已经固化，禁止重新按旧计划拆分。当前核心图为：

```text
common/0001_enable_postgis
        ↓
tenancy/0001_initial
        ↓
Django contenttypes/auth built-ins
        ↓
iam/0001_initial
  ├── Account
  └── Principal
        ↓
assets/0001_initial
        ↓
assets/0002_initial
```

其中 `assets/0002_initial` 用于补齐对 `iam.Principal`、Tenancy 和自身版本对象的跨 app/自引用外键，从而避免人为制造迁移依赖环。

`common/0001_enable_postgis` 使用：

```sql
CREATE EXTENSION IF NOT EXISTS postgis;
```

反向迁移故意为 no-op，不自动 `DROP EXTENSION postgis`，避免未来已有空间对象时造成破坏性回滚。

已经由 GitHub Runner 的 PostgreSQL/PostGIS 空库真实验证：

- `manage.py check`：通过；
- `makemigrations --check --dry-run`：通过；
- `migrate --noinput`：通过；
- Phase A 核心不变量测试：通过；
- `DATABASE_URL` 适配单测：已加入并在最新回归中执行；
- Provider leakage check：通过。

**最新提交的 Ruff / pip-audit / Docker build 完整回归状态仍以 `docs/project/CURRENT_STATUS.md` 和 GitHub Actions 为准。严禁把尚未实际跑完的检查写成“通过”。**

## 6. 当前明确尚未完成

- Phase B Role / Policy / Entitlement / Quota；
- Job / Run / Result；
- ServiceDeployment / ServiceInstance；
- Temporal、Object Storage、Martin、TiTiler、GeoServer 等 Provider；
- Dataset / Layer / Style / Map / Scene 的正式模型实现；
- Portal / GeoStudio / Fabric Console 前端；
- 三份大型 FINAL 架构/技术/领域规范全文同步；
- Render 账号侧真实 Apply Blueprint 和外部 `*.onrender.com` URL 验证。

## 7. 免费 Preview 部署

当前仓库已经具备后端免费演示部署适配：

```text
GitHub
  ↓
Render Free Web Service (Docker)
  ↓ DATABASE_URL
Render Free PostgreSQL + PostGIS
```

关键文件：

```text
render.yaml
scripts/start-preview.sh
backend/config/database.py
docs/deployment/preview.md
```

演示成功后主要检查：

```text
/health/live
/health/ready
/api/schema/
/api/docs/
```

免费 Preview **不是生产环境**。Render Free PostgreSQL 有容量、期限和备份限制，不得保存正式客户数据。长期 Preview 可把通用 `DATABASE_URL` 改接 Neon Free；核心 Domain Model 不得因此出现 Render/Neon 专属字段。

当前工具没有已连接的 Render 账号写入能力，所以外部 URL 只有在 Render 账号中 Apply Blueprint 后才会真实产生；不得虚构链接。

## 8. Account 与 Principal

- `Account`：登录认证账户；
- `Principal`：授权主体。

一个 Principal 可以代表 Human、ServiceAccount、Agent、ExternalApplication。Agent 不应伪装成 Django User。未来 Keycloak/OIDC/SAML 通过 IdentityLink 关联，不使用外部 IdP ID 替代 Fabric UUID。

## 9. 前端边界

三个逻辑产品面：

```text
Portal          = 资源/应用/客户伙伴入口
GeoStudio       = 地图/数据/模型/Workflow/Scenario/Agent 专业工作台
Fabric Console  = 租户/权限/安全/服务/计算/用量/运维管理控制台
```

它们不是三套“中台”。建议以后建立 `yujian-spatial-web` monorepo，共享 design-system、map-kit、auth、api-sdk。

前端建立后优先使用 Cloudflare Pages 的 branch/PR Preview URL，让每次界面修改都可以直接通过浏览器审查。

## 10. 中文注释规范

核心领域代码必须优先使用详细中文注释/docstring 解释：

- 为什么存在这个对象；
- 它和相邻对象为什么必须分开；
- 哪些修改会破坏长期架构；
- 哪些字段只是临时稳定引用；
- 哪些约束不能只依赖 ORM。

公开标准、协议名、类名、字段 key 保留英文，避免生硬翻译影响互操作。

Ruff 的 `RUF001/RUF002/RUF003` 已明确关闭，因为这些规则会把中文全角标点当作“易混淆字符”；其余语法、导入、安全、Django 等质量规则继续启用，禁止以“中文注释”为理由扩大豁免范围。

## 11. 每次开发结束必须更新

1. `docs/project/CURRENT_STATUS.md`；
2. 本文件中的阶段/下一任务（如有变化）；
3. structural decision 对应 ADR；
4. migration 和测试状态；
5. Known Risk；
6. Preview/生产部署的真实验证状态。

新对话可直接使用：

```text
继续 yujian-spatial-fabric。先读取 00_PROJECT_HANDOFF.md，再按其中优先级读取文档；不要重设计已冻结架构，从 CURRENT_STATUS.md 的下一任务继续。
```

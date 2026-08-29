# Yujian Spatial Fabric｜域见空间智能底座

> 面向真实世界空间智能任务的企业级统一资产、空间数据、运行计算、任务编排、治理、安全、追溯与互操作基座。

`yujian-spatial-fabric` 是域见科技 `1+5+1+N` 产品架构的共同后端基础。项目采用 **Django 模块化单体 Control Plane + 独立 Worker/Runner/Provider** 的长期架构，不以“先拆微服务”为目标，也不把 GeoServer、Martin、TiTiler、Temporal、Keycloak 等具体产品写进核心领域模型。

## 当前阶段

**Phase A — 企业级领域地基：Tenancy + Principal + Asset Kernel。**

首次接手项目时，请严格按以下顺序阅读：

1. [`00_PROJECT_HANDOFF.md`](00_PROJECT_HANDOFF.md) —— 当前阶段、交接规则与下一任务；
2. [`ARCHITECTURE_INDEX.md`](ARCHITECTURE_INDEX.md) —— 文档优先级；
3. `docs/architecture/` —— Spatial Fabric 总体与技术架构基线；
4. `docs/domain-model/` —— 领域模型冻结基线；
5. [`docs/database/phase-a-erd.md`](docs/database/phase-a-erd.md) —— 当前数据库设计；
6. [`docs/project/CURRENT_STATUS.md`](docs/project/CURRENT_STATUS.md) —— 当前实现状态。

## 项目标识

- GitHub 仓库：`hujinghaoabcd/yujian-spatial-fabric`
- Python namespace：`spatial_fabric`
- API 根路径：`/api/v1/`
- 领域主键：UUIDv7
- 当前版本：`0.1.0-alpha.0`

## 最重要的架构边界

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

所有开发者必须优先保护这些边界。若代码与上位规范冲突，不得静默“按方便的方式改掉架构”；需要先提交 ADR。

## 后端技术基线

- Python 3.12
- Django 5.2 LTS
- Django REST Framework
- PostgreSQL + PostGIS
- Psycopg 3
- drf-spectacular / OpenAPI
- pytest / pytest-django
- Ruff / mypy
- Docker / OCI

地图、模型、AI、工作流等具体 Provider 均通过 Adapter / Provider / Runner Contract 接入。

## 本地开发（依赖可下载的环境）

```bash
cp .env.example .env
uv sync --group dev
docker compose up -d db
uv run python backend/manage.py check
uv run python backend/manage.py migrate
uv run pytest
uv run python backend/manage.py runserver
```

健康检查：`GET /health/live`、`GET /health/ready`。API 文档：`GET /api/schema/`、`GET /api/docs/`。

## 前端产品面

Spatial Fabric 计划服务三个逻辑前端：

- **Portal**：客户/伙伴门户、资源中心、应用入口；
- **GeoStudio**：地图、数据、空间计算、科学模型、Workflow、Scenario、GeoAgent 的专业工作台；
- **Fabric Console**：租户、权限、安全、服务、任务、计算、用量和运维管理控制台。

三者不是三套“中台”。推荐后续使用独立前端 monorepo `yujian-spatial-web`，共享 Design System、Map Kit、Auth 和 API SDK。

## 开发规范

- 核心代码优先使用清晰、详细的**中文注释/中文 docstring**解释领域意图和“不这样设计的原因”；
- 对公开 API、第三方标准名、协议字段和通用代码标识保留英文；
- 不提交 Secret；
- 不在 Web 请求进程直接执行长任务；
- 不把 Provider 内部 ID 写成核心领域字段；
- 不使用危险级联删除清理历史证据；
- migrations、测试、ADR、交接状态必须与代码同步维护。

## License

当前未选择开源许可证。除非后续通过正式决策变更，否则按 **Proprietary / All Rights Reserved** 处理。

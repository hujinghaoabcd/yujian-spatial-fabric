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
- [x] Phase A 跨租户与资产引用不变量测试代码
- [x] 中文领域注释/docstring 基线
- [x] GitHub Actions migration preview CI
- [x] 跨对话 handoff 入口

## 当前真实验证状态

### 已验证

- [x] 生成环境 Python compileall 语法检查
- [x] UUIDv7 version / variant / 基础唯一性 smoke test
- [x] provider leakage 脚本（生成环境）

### 等待 GitHub CI / 可运行环境验证

- [ ] Django `manage.py check`
- [ ] migration preview
- [ ] 正式 migration 文件
- [ ] PostgreSQL/PostGIS migrate
- [ ] pytest
- [ ] Ruff
- [ ] pip-audit

当前生成环境无法解析 Python 包仓库 DNS，因此无法安装 Django/Psycopg；**未运行的项目不得标记为通过**。

## 下一任务

1. 打开首个 Phase A Pull Request；
2. 读取 GitHub Actions 的 `makemigrations --dry-run --verbosity 3` 输出；
3. 修复真实 Django system check / test 暴露的问题；
4. 固化 migrations；
5. CI 切换成严格 `makemigrations --check + migrate + pytest`；
6. 更新本文件与 `00_PROJECT_HANDOFF.md`；
7. Phase A 全绿后进入 Phase B IAM & Governance。

## 当前禁止

- 不集成 GeoServer/Martin/TiTiler；
- 不开始 GeoAgent；
- 不加入 EnvOS/ParkOS 行业专属表；
- 不拆微服务；
- 不新增 provider-specific 核心字段；
- 不绕过版本发布服务直接修改 Published AssetVersion；
- 不在 migrations 真实通过前宣称数据库地基完成。

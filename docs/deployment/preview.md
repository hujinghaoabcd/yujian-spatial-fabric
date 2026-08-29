# 免费 Preview 部署

## 目标

本方案只解决一个问题：开发过程中让产品负责人、开发者和合作方无需本地安装环境，就能通过浏览器直接查看 Spatial Fabric 当前 API 与后续前端页面。

当前后端 Preview 采用：

```text
GitHub branch / main
        ↓ CI checksPass
Render Free Web Service (Docker)
        ↓ DATABASE_URL
Render Free PostgreSQL + PostGIS
        ↓
/health/live
/health/ready
/api/schema/
/api/docs/
```

这不是生产架构。正式生产环境仍需独立 migration/release step、长期数据库、备份、监控、SecretProvider、容量规划与高可用设计。

## 为什么当前先选 Render

- 可以直接连接 GitHub 仓库并从 Dockerfile 构建；
- 免费 Web Service 会提供 HTTPS `*.onrender.com` 地址；
- Blueprint 可以把 Web 与 PostgreSQL 一起声明在 `render.yaml`；
- Render PostgreSQL 支持 PostGIS；
- 很适合阶段性接口演示和 Swagger 预览。

免费层的重要限制：

- Free Web Service 长时间无请求会休眠，首次访问可能出现冷启动；
- Free PostgreSQL 固定 1 GB；
- Free PostgreSQL 创建 30 天后过期；
- 免费环境禁止承担生产数据和正式业务 SLA。

## 部署步骤

1. 登录 Render Dashboard，并授权读取本 GitHub 仓库；
2. 选择 **New → Blueprint**；
3. 选择 `hujinghaoabcd/yujian-spatial-fabric`；
4. Blueprint 文件使用仓库根目录默认的 `render.yaml`；
5. 首次验证建议选择已经通过 CI 的 `main`。若仅临时审查功能分支，也可以先部署该分支；
6. Apply Blueprint；
7. 等待数据库创建、Docker 构建、migration 与 Web 服务启动；
8. Render 页面出现 `https://<service>.onrender.com` 后，依次检查下面地址。

```text
https://<service>.onrender.com/health/live
https://<service>.onrender.com/health/ready
https://<service>.onrender.com/api/schema/
https://<service>.onrender.com/api/docs/
```

`/api/docs/` 是当前最直观的接口预览入口。

## 为什么启动脚本里会 migrate

Render Free Web Service 不提供我们希望使用的独立 pre-deploy migration 能力，因此 Preview 使用 `scripts/start-preview.sh`：

```text
manage.py migrate --noinput
        ↓
uvicorn
```

Django migration 本身要求可重复执行，因此这对单副本免费演示环境是可接受的。**正式生产环境禁止沿用这个模式**，届时必须把 migration 迁回独立部署阶段。

## DATABASE_URL 的边界

`render.yaml` 只把 Render 数据库的 `connectionString` 注入通用 `DATABASE_URL`。Django 通过 `config.database` 把 URL 转成 PostGIS backend 配置。

因此核心代码没有 `render_host`、`neon_id`、`rds_xxx` 一类 Provider 字段。以后切换到 Neon、自建 PostgreSQL 或其他 PostgreSQL/PostGIS 服务时，不需要修改 Domain Model。

## 长期免费 Preview：Neon 方案

如果希望预览地址长期保存，而不是每 30 天重建 Render Free PostgreSQL，可以：

```text
Render Free Web Service
        ↓ DATABASE_URL
Neon Free PostgreSQL + PostGIS
```

只需要把 Web Service 的 `DATABASE_URL` 改成 Neon 提供的 PostgreSQL 连接串，并启用 PostGIS；应用代码和数据模型不变。

## 前端 Preview

以后建立 `yujian-spatial-web` 后，建议使用 Cloudflare Pages：

```text
Pull Request / branch
        ↓
Cloudflare Pages Preview
        ↓
独立 Preview URL
```

这样每次修改 Portal / GeoStudio / Fabric Console，都可以直接发一个网页链接审查，而不必合并到正式环境。

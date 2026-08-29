# GitHub Codespaces 免费 Preview（无需绑定银行卡）

> 目标：不使用 Render 卡片验证，也不要求本地环境，直接通过 GitHub 浏览器环境启动 Spatial Fabric API。

## 1. 当前拓扑

```text
GitHub Codespace
  ├── app: Python 3.12 + GeoDjango + uv + Django/Uvicorn
  └── db : PostgreSQL 17 + PostGIS 3.5

app :8000
  ↓ Codespaces Port Forwarding
https://<codespace-name>-8000.app.github.dev
```

所有数据库都运行在该 Codespace 自己的 Docker Compose 环境中，不依赖 Render/Neon 等外部数据库。

## 2. 创建 Codespace

当前 Codespaces 配置位于分支：

```text
chore/codespaces-preview
```

在 GitHub 仓库页面：

1. 切换到 `chore/codespaces-preview` 分支；
2. 点击 **Code**；
3. 点击 **Codespaces**；
4. 点击 **Create codespace on chore/codespaces-preview**；
5. 等待开发容器完成构建。

首次创建时会自动：

```text
构建 Python/GeoDjango 开发容器
        ↓
启动 PostgreSQL/PostGIS
        ↓
uv sync --group dev
        ↓
manage.py migrate --noinput
        ↓
manage.py check
        ↓
Uvicorn :8000
```

所以正常情况下不需要在终端手工输入启动命令。

## 3. 打开接口

Codespace 启动后，在浏览器版 VS Code 下方找到 **PORTS** 标签。

应看到：

```text
8000  Spatial Fabric API / Swagger
```

点击 8000 行右侧的浏览器/地球图标即可打开。

主要地址：

```text
/health/live
/health/ready
/api/schema/
/api/docs/
```

最直观的是：

```text
/api/docs/
```

它是 Swagger UI。

## 4. 默认 Private 与 Public 的区别

GitHub Codespaces 转发端口默认是 **Private**。

Private：
- 只有当前 GitHub 用户登录后可以访问；
- 适合自己查看接口；
- 默认更安全。

如果需要把链接发给其他人临时查看：

1. 在 **PORTS** 面板找到 8000；
2. 右键/长按该端口；
3. 选择 **Port Visibility → Public**；
4. 复制 Forwarded Address。

公开 URL 形式类似：

```text
https://<codespace-name>-8000.app.github.dev
```

Public 端口任何知道 URL 的人都可访问，因此只用于临时演示。公开可见性在端口被移除/重新添加或 Codespace 重启后可能恢复为 Private，应按需重新设置。

## 5. 手机操作提示

手机浏览器 GitHub 页面：

```text
仓库 → Code → Codespaces → Create codespace
```

Codespace 打开的是 Web VS Code。屏幕较窄时，`PORTS` 面板可能在底部面板的 `...` 中。

如果找不到：

```text
Terminal → New Terminal
```

确认服务后，再打开底部 `PORTS`。

## 6. 自检

如果 8000 没有自动出现，在 Codespace 终端执行：

```bash
cat /tmp/spatial-fabric-codespaces-api.log
```

检查 API 日志。

也可手工测试：

```bash
curl http://127.0.0.1:8000/health/ready
```

预期数据库与 PostGIS readiness 正常。

如果 API 没有启动：

```bash
bash .devcontainer/start-api.sh
```

## 7. 数据生命周期

Codespaces Preview 不是生产环境。

PostgreSQL 数据保存在 Codespaces 的 compose volume 中；删除该 Codespace 后，不应假设演示数据长期保留。

禁止放入：

- 正式客户数据；
- 真实生产 Secret；
- 正式业务凭证；
- 不应公开的敏感空间数据。

## 8. 费用边界

GitHub 个人 Free/Pro 账户包含每月一定的 Codespaces 免费 compute/storage 配额。应把 Codespace 当作按需开发/演示环境：不用时停止或删除，避免消耗免费时长。

本方案不要求在项目代码中配置任何 GitHub/Codespaces 专属领域字段，仍然保持部署适配与 Spatial Fabric Core 分离。

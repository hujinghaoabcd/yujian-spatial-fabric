# Security Policy｜安全策略

Spatial Fabric 面向企业/政企空间数据、科学模型和智能体任务，安全默认采用 **deny by default / least privilege / traceable execution**。

## 1. 不要通过公开 Issue 披露安全漏洞

如发现凭据泄漏、越权、跨租户数据访问、远程代码执行、任意文件读取、SSRF、Agent 高风险动作绕过审批等问题，应通过仓库维护者的私有安全渠道报告；在项目正式建立 Security Advisory 流程前，不要附带真实客户数据或 Secret 发布公开 Issue。

## 2. 严禁提交

- API Key / Token / Password；
- 客户数据库凭据；
- 私钥、证书私钥；
- 大模型 Token；
- 真实敏感客户数据；
- 生产 `.env`；
- 含 Secret 的 Provider 配置。

## 3. 重点威胁

开发与审查必须特别关注：

```text
跨租户访问
IDOR / 资源级越权
Tile/Cache 越权
SSRF / 任意 URL 导入
对象存储路径穿透
任意容器/命令执行
模型包供应链
恶意文件上传
Agent 权限升级
重试造成重复现实副作用
审计日志篡改
```

## 4. Provider 边界

GeoServer、Martin、TiTiler、对象存储、Workflow Runtime 等不作为 Fabric IAM 权威源。外部 Provider 必须位于受控网络/服务边界，并由 Fabric 的 Principal/Policy/Entitlement/Quota 决定最终访问。

## 5. Tenant Isolation

任何 tenant-owned 查询默认要求 tenant context。后续启用 PostgreSQL RLS 前仍必须在 Application/Repository 层做租户过滤；RLS 只能是额外防线，不能替代业务授权。

## 6. 安全发布

正式生产发布将逐步要求：

- dependency audit；
- SBOM；
- container scan；
- immutable OCI digest；
- artifact signature/attestation；
- production approval；
- rollback plan。

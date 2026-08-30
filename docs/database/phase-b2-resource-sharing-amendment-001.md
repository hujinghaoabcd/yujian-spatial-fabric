# B2.2 Contract Amendment 001 — ShareGrant temporal multiplicity

> 状态：ACCEPTED BEFORE FIRST MIGRATION  
> 修正对象：`phase-b2-resource-sharing-spec.md` §9 条目 6–7

## 问题

原草案同时规定：

1. ShareGrant 过期由 `valid_until` 在解析时推导，不依赖后台任务把状态改成 EXPIRED；
2. 同一 ResourceRef + 同一 Principal/Group 最多一个 `status=ACTIVE` ShareGrant。

两条同时成立会产生时态死锁：

```text
Grant A
status = ACTIVE
valid_until < now
```

A 在授权语义上已经无效，但数据库 partial UNIQUE 仍把它视为 ACTIVE，因此用户无法创建下一条授权。

不能把这种正确性依赖交给“后台任务及时改状态”，否则调度延迟会直接阻塞授权业务。

## 修正

删除“同资源同主体最多一条 ACTIVE ShareGrant”的数据库唯一约束。

允许：

```text
同一 ResourceRef
+ 同一 Principal / Group
+ 多条独立 ShareGrant evidence
```

每条 Grant 独立拥有：

- privilege links；
- valid_from / valid_until；
- conditions；
- granted_by；
- revoke evidence。

`ShareGrantResolver`：

- 逐条过滤 ACTIVE + 时间窗口 + 空 conditions；
- 保留全部 evidence；
- `effective_privilege_keys` 只对 Privilege key 去重。

因此撤销某一条 Grant 不会误删来自另一条 Grant 的独立授权来源。

## 安全性

这不会扩大最终权限，因为：

```text
ShareGrant = ALLOW candidate only
```

最终仍满足：

```text
explicit DENY > REQUIRE_APPROVAL > ShareGrant/RBAC ALLOW
```

## 后续

如未来需要阻止“完全相同的重复 Grant”，应在 Application Service 做幂等键/请求幂等控制，
而不是用与时间语义冲突的永久 partial UNIQUE 约束。

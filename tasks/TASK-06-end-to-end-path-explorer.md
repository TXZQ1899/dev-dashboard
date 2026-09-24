# TASK-06 — End-to-End Ingress Path Explorer

## Goal

把前 5 个 Task 的 Resolver 串成完整的：

```text
URL
→ Infrastructure
→ Routing
→ Endpoint
→ Application
→ Repository
```

并集成到现有 Path Explorer。

---

## Prerequisite

TASK-01 ~ TASK-05 已完成并通过测试。

不要在本 Task 重新设计前面各 Resolver。

---

## Step 1 — End-to-End Orchestration

完善：

```ts
resolveRequestPath(query, graph)
```

Resolver chain 不得假设固定拓扑。

合法路径可能包括：

```text
DNS
→ ECS
→ Nginx
→ Application
```

```text
DNS
→ NAT
→ ECS
→ Nginx
→ Application
```

```text
DNS
→ NAT
→ CLB
→ Application
```

```text
DNS
→ CLB
→ Nginx
→ Application
```

```text
DNS
→ CLB
→ multiple backend Applications
```

每一个 Resolver 只在 `canResolve()` 成立时执行。

---

## Step 2 — Status Semantics

统一：

```text
RESOLVED
PARTIAL
AMBIGUOUS
UNRESOLVED
```

建议：

### RESOLVED

至少已经可靠到达 Application。

### PARTIAL

链路已解析一部分，但因缺数据/unsupported semantics 停止。

### AMBIGUOUS

存在无法唯一确定的 routing/application interpretation。

### UNRESOLVED

无法从起点建立有效链路。

不要把合法 LB 多 backend 自动标记 AMBIGUOUS。

---

## Step 3 — Explainability

每个 hop 显示：

- from
- relation
- to
- resolver
- rule/reason
- confidence
- evidence
- warning

例如：

```text
api.example.com
→ 47.1.1.1

Resolver: DNSResolver
Rule: DNS A record
Confidence: EXACT
```

```text
47.1.1.1:443
→ 10.1.1.10:443

Resolver: NatResolver
Rule: DNAT fwd-xxx
Confidence: EXACT
```

```text
10.1.1.20:8080
→ order-service

Resolver: DeploymentResolver
Rule: DevOps PRODUCT deployment IP + port
Confidence: EXACT
```

---

## Step 4 — Path Explorer Input

支持：

```text
api.example.com
```

和：

```text
https://api.example.com/order/123
```

### Domain-only query

只能解析 domain-only 信息。

如果后续 routing 依赖 path：

必须明确显示：

```text
URI required to continue routing
```

不要自动假设 `/` 就代表用户想查的业务 API。

### URL query

使用完整：

- scheme
- host
- port
- path

执行 request-aware resolution。

---

## Step 5 — Path Explorer UI

在现有 UI 上做增量扩展。

至少展示：

- node type
- node name
- endpoint
- edge/relation
- confidence
- evidence
- warnings
- unresolved reason

不要为了此功能重写整个前端。

---

## Step 6 — Synthetic End-to-End Fixtures

至少实现：

### Case A

```text
DNS
→ ECS
→ Nginx
→ Application
```

### Case B

```text
DNS
→ NAT
→ ECS
→ Nginx
→ Application
```

### Case C

```text
DNS
→ NAT
→ CLB
→ Application
```

### Case D

```text
DNS
→ CLB
→ Nginx
→ Application
```

### Case E

```text
DNS
→ CLB
→ multiple backend Applications
```

### Case F

```text
DNS
→ unresolved external target
```

### Case G

```text
Endpoint
→ multiple possible Applications
```

---

## Step 7 — Regression

运行项目现有：

- unit tests
- integration tests
- type-check
- lint
- build

确认：

- existing `/resources` unaffected
- existing topology builder unaffected
- existing Path Explorer behavior does not regress unexpectedly

---

## Step 8 — Real Snapshot Validation

使用当前真实 snapshot 至少挑选若干已知域名验证。

优先找：

1. DNS → direct host
2. DNS → NAT
3. DNS → CLB
4. Nginx upstream
5. DevOps deployment

记录：

```text
resolved
partial
unresolved
ambiguous
```

以及停止原因。

不要修改数据来“让示例通过”。

---

## Non-goals

本 Task 不实现应用内部调用链：

- Spring RequestMapping
- Spring Cloud Gateway code/config scanning
- Feign
- RestTemplate
- WebClient
- Dubbo
- MQ
- database
- Redis

也不实现：

- process listening-port collector

这些应作为下一阶段独立 Epic。

---

## Acceptance Criteria

输入：

```text
https://api.example.com/order/123
```

在数据充分时，可以输出类似：

```text
DOMAIN
→ DNS IP
→ EXTERNAL ENDPOINT
→ DNAT
→ INTERNAL ENDPOINT
→ CLB
→ LISTENER
→ RULE
→ SERVER GROUP
→ BACKEND ENDPOINT
→ HOST
→ NGINX ROUTE
→ UPSTREAM
→ ENDPOINT
→ DEPLOYMENT
→ APPLICATION
→ REPOSITORY
```

数据不足时必须明确显示：

- stoppedAt
- reason
- warning
- evidence
- confidence

严禁为了图连通而伪造 edge。

---

## Final Report

完成后输出最终报告：

1. files changed
2. final resolver architecture
3. supported path patterns
4. test cases/results
5. real snapshot validation results
6. unresolved topology gaps
7. ambiguous cases
8. unsupported routing semantics
9. recommended next Epic

推荐下一阶段：

```text
Application Dependency Discovery V1
```

包括：

- process/listening port
- pom finalName
- Spring MVC RequestMapping
- Spring Cloud Gateway
- Feign
- HTTP clients
- RPC
- MQ
- DB
- Redis

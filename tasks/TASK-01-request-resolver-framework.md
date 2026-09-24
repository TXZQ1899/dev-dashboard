# TASK-01 — Request Resolver Framework

## Goal

在现有 `TopologyGraph` 和 Path Explorer 基础上，引入一个 **request-aware path resolution framework**。

本 Task 只建立模型和 Resolver 框架。

**不要实现 DNS、NAT、CLB、Nginx、Application 的具体解析逻辑。**

---

## Context

EnvScope 已经存在：

- TopologyGraph
- TopologyNode
- TopologyEdge
- Evidence
- Confidence
- TopologyPath
- Topology Builder
- Path Explorer

现有 Topology Graph 仍然是事实拓扑模型，本 Task 不重新设计或替换它。

新的 Request Resolver 应建立在现有 Graph 之上。

---

## Step 1 — Review Existing Code

开始修改前，先阅读并确认：

- `RESOURCE_SCHEMAS*.md`
- topology graph schema
- topology builder
- graph index
- path explorer
- endpoint normalization
- 当前 Domain → Application traversal

先确定：

1. 哪些类型可以直接复用
2. 哪些模块应该保持不变
3. Resolver 应放在哪个目录
4. 当前测试框架和命令

不要在没有理解现有结构的情况下重构 topology builder。

---

## Step 2 — Add PathQuery

增加请求级查询模型。

建议：

```ts
interface PathQuery {
  raw?: string;

  scheme?: "http" | "https";

  host: string;

  port?: number;

  path?: string;

  method?: string;

  environment?: "TEST" | "SIMULATION" | "PRODUCT";
}
```

增加统一 normalize helper。

规则：

- hostname lowercase
- remove trailing `.`
- `http` 默认 port = 80
- `https` 默认 port = 443
- path 默认 `/`
- URL query string 不参与 V1 infrastructure routing
- 保留原始输入用于 UI 展示

示例：

```text
https://api.example.com/order/1?x=1
```

归一化：

```text
scheme=https
host=api.example.com
port=443
path=/order/1
```

---

## Step 3 — Add Request Resolution Models

建议建立：

```ts
interface RequestTrace {
  query: PathQuery;

  paths: ResolvedPath[];

  status:
    | "RESOLVED"
    | "PARTIAL"
    | "AMBIGUOUS"
    | "UNRESOLVED";

  warnings: string[];
}
```

以及：

```ts
interface ResolutionStep {
  resolver: string;

  inputNodeIds: string[];

  outputNodeIds: string[];

  rule: string;

  confidence:
    | "EXACT"
    | "INFERRED"
    | "AMBIGUOUS"
    | "UNKNOWN";

  evidence: Evidence[];

  warnings: string[];
}
```

尽量复用现有：

- `TopologyNode`
- `TopologyEdge`
- `Evidence`
- `Confidence`

不要创建一套与现有 Graph 冲突的新节点体系。

---

## Step 4 — Resolver Interface

定义小型 Resolver 接口。

例如：

```ts
interface PathResolver {
  name: string;

  canResolve(context: ResolverContext): boolean;

  resolve(context: ResolverContext): ResolutionResult;
}
```

建立：

- `ResolverContext`
- `ResolutionResult`
- Resolver registry
- orchestration skeleton

第一阶段可以预留以下 Resolver 名称：

```text
DNSResolver
NatResolver
ClbResolver
NginxResolver
DeploymentResolver
RepositoryResolver
```

但本 Task 不实现其业务逻辑。

---

## Step 5 — Resolution Engine Skeleton

建立一个入口，例如：

```ts
resolveRequestPath(query, graph)
```

V1 skeleton 应考虑：

- cycle protection
- maxDepth
- deterministic ordering
- candidate branching
- unresolved result
- warnings
- environment filter
- graph index reuse

不要每次 traversal 都扫描全部 edges。

---

## Non-goals

本 Task 不实现：

- DNS resolution
- CNAME traversal
- Endpoint → DNAT
- CLB listener/rule matching
- Nginx routing
- DevOps application mapping
- Repository mapping
- Spring RequestMapping
- Feign
- MQ
- DB
- Redis
- UI redesign

---

## Tests

至少增加：

### PathQuery normalize tests

- HTTP URL
- HTTPS URL
- custom port
- path
- query string stripping
- hostname lowercase
- trailing dot
- domain-only input

### Resolver framework tests

- empty resolver result
- unresolved request
- max depth
- cycle guard
- deterministic ordering

---

## Acceptance Criteria

完成后必须满足：

1. Existing topology graph remains compatible.
2. Existing Path Explorer still works.
3. New Request Resolver framework compiles.
4. PathQuery normalization has automated tests.
5. Resolution engine can return a valid UNRESOLVED result.
6. Resolver interfaces can be extended by later Tasks.
7. No DNS/NAT/CLB/Nginx/Application business logic is implemented prematurely.

---

## Final Report

完成后输出：

1. changed files
2. new models/interfaces
3. architecture decisions
4. tests added
5. test/type-check/build results
6. known limitations
7. anything that may affect TASK-02

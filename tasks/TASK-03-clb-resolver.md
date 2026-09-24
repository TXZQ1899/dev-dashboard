# TASK-03 — CLB Resolver

## Goal

实现：

```text
Endpoint
→ CLB
→ Listener
→ Rule
→ Server Group
→ Backend Endpoint[]
```

支持 request host/path-aware routing。

---

## Prerequisite

TASK-01、TASK-02 已完成。

---

## Step 1 — Identify CLB

根据当前 endpoint IP 匹配现有 CLB instance。

必须保留：

- CLB ID
- CLB name
- IP
- addressType
- status
- evidence

---

## Step 2 — Match Listener

根据 Request Context：

- port
- protocol / scheme

匹配 listener。

至少支持：

- HTTP
- HTTPS
- TCP

例如：

```text
https request
port 443
```

应优先匹配：

```text
HTTPS:443
```

如果无法唯一确定，不要猜。

---

## Step 3 — HTTP/HTTPS Rule Matching

对于七层 listener：

利用：

- request host
- request path

匹配：

- Rule.domain
- Rule.path

需要支持：

- exact host
- wildcard host（现有数据足够时）
- path prefix
- empty/default path
- listener default server group

如果多个 rule 合理匹配：

遵循明确、可测试的匹配优先级。

不要使用数组顺序作为隐式业务规则，除非源数据明确有顺序语义。

---

## Step 4 — Server Group

解析：

```text
Listener / Rule
→ ServerGroup
```

然后展开：

```text
ServerGroup
→ BackendServer[]
```

Backend 应转为：

```text
Endpoint
IP + Port + Protocol
```

---

## Important Semantic Rule

多个 Load Balancer backend：

```text
backend-1
backend-2
backend-3
```

不是自动等价于：

```text
AMBIGUOUS
```

它们是合法的：

```text
load-balanced runtime candidates
```

RequestTrace 可以保留多条运行路径。

只有“无法确定应该使用哪个 Rule / Listener”时才属于 routing ambiguity。

---

## Step 5 — Unsupported Cases

如果遇到当前 schema 无法可靠解释的 CLB 行为：

- 不猜
- 保留 partial path
- 加 warning

---

## Non-goals

本 Task 不实现：

- Nginx route matching
- DevOps Application mapping
- Repository
- Spring code scanning
- UI redesign

---

## Tests

至少覆盖：

1. HTTPS:443 listener
2. HTTP:80 listener
3. TCP listener
4. exact domain rule
5. wildcard domain
6. path rule
7. default server group
8. virtual server group
9. multiple backend servers
10. listener not found
11. rule not found
12. competing rules
13. deterministic output

---

## Acceptance Criteria

示例请求：

```text
https://api.example.com/order/123
```

当 DNS/NAT 后进入一个 CLB endpoint 时，应可以继续得到：

```text
CLB
→ HTTPS:443 Listener
→ matching Rule
→ ServerGroup
→ Backend Endpoint[]
```

每一步具有：

- rule/reason
- evidence
- confidence
- warnings when needed

---

## Final Report

输出：

1. changed files
2. CLB matching algorithm
3. host/path precedence
4. backend branching behavior
5. tests/results
6. unsupported CLB semantics
7. anything affecting TASK-04

# TASK-04 — Nginx Resolver

## Goal

实现基于真实 Request Context 的 Nginx 路由解析：

```text
Host / Endpoint
→ Nginx Route
→ Upstream
→ Backend Endpoint[]
```

---

## Prerequisite

TASK-01 ~ TASK-03 已完成。

现有 JumpServer / Nginx snapshot 已包含：

- server_name/domains
- listen
- location uri
- directive
- proxy_pass target
- upstream
- backend host
- backend port
- context
- Nginx instance
- configurationVersion

必须优先复用已有 normalized `nginxRoutes`。

---

## Step 1 — Locate Nginx Host

根据当前 Endpoint：

```text
IP + port
```

找到对应 HOST / JumpServer asset。

如果该 host：

- nginxStatus = complete
- nginxRoutes available

则进入 Nginx Resolver。

如果：

```text
loginStatus = cannot_login
```

不能推断“没有 Nginx”。

应该返回 UNKNOWN / warning。

---

## Step 2 — Match Listen

基于 request destination port：

```text
80
443
...
```

匹配 `listen`。

V1 只实现当前数据足够支持的明确语义。

---

## Step 3 — Match server_name

至少支持：

1. exact hostname
2. wildcard hostname
3. default/fallback（能够可靠判断时）

不要对复杂 regex server_name 做未经验证的推断。

---

## Step 4 — Match location

V1 至少支持：

1. exact location `=`
2. longest prefix
3. normal prefix
4. fallback `/`

如果现有 normalized schema 未保留 location modifier，需要先检查原实现是否已经解析。

如果信息不足：

- 不伪造精确匹配
- 给出 warning
- 必要时标记 INFERRED / UNKNOWN

复杂 regex location 可以在 V1 标记 unsupported。

---

## Step 5 — Resolve proxy_pass / upstream

根据 route：

```text
proxy_pass
upstream
backends[]
```

生成：

```text
NGINX_ROUTE
→ UPSTREAM
→ ENDPOINT[]
```

Backend：

```text
host
port
resolution
```

如果：

```text
resolution = ip
```

直接建立 Endpoint candidate。

如果 hostname 无法通过现有数据继续解析：

- 保留 unresolved backend
- 不自行 DNS 推测
- 加 warning

如果：

```text
dynamic
unix
```

V1 无法继续时应明确停止。

---

## Step 6 — Preserve Request Context

Nginx 匹配必须始终使用：

- host
- destination port
- request path
- scheme/protocol

不要只使用 IP 做 route selection。

---

## Unsupported / Warning Cases

包括但不限于：

- regex location
- variable proxy_pass
- map
- Lua routing
- dynamic service discovery
- runtime DNS unknown
- rewrite changing routing path
- nested complex location semantics

对于这些情况：

```text
partial > guessing
```

---

## Non-goals

本 Task 不实现：

- Java process → listening port collection
- Spring RequestMapping
- Feign
- DevOps Application mapping changes
- UI redesign

---

## Tests

synthetic fixtures 至少覆盖：

1. exact server_name
2. wildcard server_name
3. listen 80
4. listen 443
5. exact location
6. longest prefix
7. root fallback
8. direct proxy_pass IP
9. named upstream
10. multiple upstream backends
11. unresolved hostname backend
12. unsupported dynamic backend
13. no route found
14. deterministic route selection

---

## Acceptance Criteria

请求：

```text
https://api.example.com/order/123
```

进入一台 Nginx host 后，可以基于：

```text
server_name
listen
location
```

找到正确 route，并得到：

```text
NGINX_ROUTE
→ UPSTREAM
→ Backend Endpoint[]
```

无法确认时必须：

- partial/unresolved
- warning
- no guessing

---

## Final Report

输出：

1. changed files
2. Nginx matching rules
3. exact/prefix precedence
4. unsupported semantics
5. tests/results
6. real-data examples if available
7. anything affecting TASK-05

# TASK-02 — DNS, Endpoint and NAT Resolver

## Goal

实现 Ingress Path Resolution 的第一段：

```text
URL / Domain
→ DNS
→ IP / External Endpoint
→ NAT / DNAT
→ Internal Endpoint
```

本 Task 不实现 CLB、Nginx、Application。

---

## Prerequisite

TASK-01 已完成。

必须复用：

- PathQuery
- RequestTrace
- ResolutionStep
- Resolver framework
- existing TopologyGraph
- existing Evidence / Confidence

---

## Step 1 — Endpoint Utilities

Endpoint identity 必须使用：

```text
IP + Port + Protocol
```

统一 ID，例如：

```text
endpoint:10.179.1.10:8080:tcp
```

增加或统一 helper：

```ts
normalizeProtocol()
normalizePort()
makeEndpointId()
parsePortRange()
portMatches()
protocolMatches()
```

必须避免：

```text
10.1.1.1:80
10.1.1.1:8080
```

被错误合并。

---

## Step 2 — DNS Resolver

支持：

- A
- AAAA（如果现有数据模型支持）
- CNAME
- CNAME chain
- multiple A records
- unresolved external CNAME

规则：

1. 禁止随机选择多个 DNS candidate 中的一个。
2. CNAME chain 必须 cycle safe。
3. disabled DNS record 在状态可靠时不可进入 active path。
4. 外部 CNAME 无法继续解析时必须保留 unresolved path。
5. 每个 hop 保留 evidence。

示例：

```text
api.example.com
→ CNAME
api-gateway.example.net
→ A
47.1.1.1
```

---

## Step 3 — Create External Endpoint

如果 PathQuery 已经确定：

```text
scheme=https
port=443
```

DNS 得到：

```text
47.1.1.1
```

则创建/解析逻辑 endpoint：

```text
47.1.1.1:443/TCP
```

HTTP/HTTPS V1 底层 transport protocol 可以归一化为 TCP。

不要为了请求方便破坏现有 TopologyGraph identity。

---

## Step 4 — NAT / DNAT Resolver

根据：

- externalIp
- externalPort
- protocol

匹配 DNAT。

输出：

- internalIp
- internalPort
- protocol

示例：

```text
47.1.1.1:443/TCP
→
10.1.1.10:8443/TCP
```

必须支持：

- exact port
- `Any`
- port range
- exact protocol
- protocol `Any`

当多个 DNAT rule 同时匹配：

- 返回多个 candidates
- 不随机选择
- 标记 warning / ambiguity

---

## Step 5 — Evidence

每一个 ResolutionStep 必须能够说明：

```text
为什么从 A 到 B
```

例如：

```text
api.example.com → 47.1.1.1
rule: DNS A record
source: dns
```

```text
47.1.1.1:443 → 10.1.1.10:8443
rule: DNAT fwd-xxx
source: nat
```

---

## Non-goals

本 Task 不实现：

- CLB resolution
- CLB listener
- ServerGroup
- Nginx routing
- DevOps application mapping
- Repository
- Spring scanning
- UI redesign

---

## Tests

使用 synthetic fixtures。

至少覆盖：

### DNS

1. A record
2. CNAME → A
3. CNAME → CNAME → A
4. multiple A
5. disabled record
6. unresolved external CNAME
7. CNAME cycle

### Endpoint

1. http → 80
2. https → 443
3. custom port
4. TCP normalization

### DNAT

1. exact port
2. `443 → 8443`
3. Any port
4. port range
5. Any protocol
6. multiple matching rules
7. no matching rule

---

## Acceptance Criteria

可以输入：

```text
https://api.example.com/order/1
```

在已有数据允许时得到：

```text
DOMAIN
→ IP
→ EXTERNAL ENDPOINT
→ DNAT
→ INTERNAL ENDPOINT
```

同时满足：

- no random candidate selection
- evidence preserved
- confidence preserved
- unresolved paths preserved
- deterministic result

---

## Final Report

输出：

1. changed files
2. resolver implementation
3. endpoint identity rules
4. DNS rules implemented
5. DNAT rules implemented
6. tests/results
7. unsupported cases
8. anything affecting TASK-03

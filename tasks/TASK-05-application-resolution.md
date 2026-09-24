# TASK-05 — Endpoint to Application Resolution

## Goal

实现：

```text
Endpoint
→ Deployment
→ Application
→ Repository
```

优先利用现有 DevOps 数据完成可靠关联。

---

## Prerequisite

TASK-01 ~ TASK-04 已完成。

---

## Step 1 — Deployment Identity

DevOps Deployment 当前已经包含：

- appId
- app name
- env
- IP
- port
- deploy ID
- branch
- repository
- lastPublishedAt

首选 identity：

```text
IP + Port + Environment
```

---

## Step 2 — Endpoint → Deployment

匹配优先级：

### Level 1

```text
IP + Port + Environment
```

唯一匹配：

```text
EXACT
```

### Level 2

```text
IP + Port
```

唯一匹配，但 query 没有 environment：

可根据证据标记 EXACT 或 INFERRED。

### Level 3

只有 IP 且该 host 上只有唯一候选 deployment：

只能按现有证据谨慎判断。

### Level 4

只有 IP，且 host 上存在多个应用 / 多个端口：

```text
AMBIGUOUS
```

严禁随机选择应用。

---

## Step 3 — Deployment → Application

使用 DevOps appId 建立稳定关联。

生成：

```text
APPLICATION
--HAS_DEPLOYMENT-->
DEPLOYMENT
--LISTENS_ON-->
ENDPOINT
```

尽量复用现有 Topology Builder 已存在的节点和 edge。

不要重复建立冲突模型。

---

## Step 4 — Application → Repository

优先：

```text
DevOps repository URL
```

然后复用现有 repository normalization。

生成：

```text
APPLICATION
--BUILT_FROM-->
REPOSITORY
```

如果 DevOps/Codeup 当前 match 本身不确定：

不能强制设置 EXACT。

---

## Step 5 — Preserve Metadata

结果需要保留：

- app ID
- application name
- environment
- deploy ID
- IP
- port
- branch
- repository
- lastPublishedAt
- publish status

---

## Step 6 — Prepare for Future Process Matching

本 Task 不要求实现：

```text
Endpoint → Process
```

但设计不能阻止以后加入：

```text
ENDPOINT
→ PROCESS
→ ARTIFACT
→ APPLICATION
```

不要把 Application identity 永久绑定为“只能来自 DevOps”。

---

## Non-goals

不实现：

- `ss -lntup` collector
- PID/listening port mapping
- pom.xml finalName scanner
- Spring RequestMapping
- Feign
- RestTemplate
- WebClient
- Dubbo
- MQ
- DB
- Redis

这些属于后续 Application Dependency Discovery。

---

## Tests

至少覆盖：

1. exact IP+port+env
2. exact IP+port
3. same host multiple ports
4. same host multiple applications
5. environment filtering
6. ambiguous match
7. no deployment
8. repository exact
9. repository uncertain
10. deterministic output

---

## Acceptance Criteria

在已有 DevOps 数据支持时：

```text
10.179.1.10:8080
```

能够解析到：

```text
DEPLOYMENT
→ APPLICATION
→ REPOSITORY
```

如果服务器上：

```text
8080 → app-a
8081 → app-b
```

则必须根据端口区分。

只有 IP 时不得随意选择。

---

## Final Report

输出：

1. changed files
2. matching precedence
3. confidence rules
4. ambiguous cases
5. repository behavior
6. tests/results
7. unresolved application mappings
8. anything affecting TASK-06

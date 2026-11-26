# Application Atlas Project Guide

> 本文件是 Application Atlas 面向 AI Coding Agent 的主要项目规则与长期上下文。
> Codex、TRAE、GitHub Copilot 等工具应尽量复用本文件，而不是各维护一套完整项目说明。

## 1. Project Purpose

Application Atlas collects, normalizes and correlates application deployment and infrastructure metadata.

The project currently covers:

- DevOps applications and deployments
- Codeup / GitLab repositories
- DNS
- EIP
- NAT / DNAT
- ECS
- CLB / SLB
- JumpServer
- Nginx routes and upstreams
- resource inventory
- topology graph

Current product direction:

```text
Inventory
  ↓
Topology
  ↓
Path Exploration
  ↓
Risk Analysis
  ↓
Architecture Visualization
```

Current engineering focus:

```text
Topology Builder   ✅
Path Explorer      current
Risk Analyzer      next
Renderer           later
```

## 2. Source of Truth

Before changing code, use the following sources in this order:

1. Current source code and tests for implementation behavior.
2. `RESOURCE_SCHEMAS.md` for data contracts and source semantics.
3. `README.md` for runtime, project purpose and operator-facing usage.
4. This file for AI working conventions.

Do not invent fields, relationships or runtime behavior that are not supported by code or source data.

If documentation and implementation disagree:

- identify the mismatch;
- do not silently “fix” one side based on assumption;
- make the smallest justified change;
- update the relevant documentation if the task changes the contract.

## 3. Container-First Runtime Contract

Docker is the formal runtime boundary for Application Atlas.

Application Atlas is built from the `environment-web` source directory and runs in Docker context `lenovo` as container `envscope-local`, using image tag `envscope:local`. All production behavior must be designed with that runtime model in mind.

### Required Docker context and data checks

Use Docker context `lenovo` for all application builds and runtime operations. Always select it explicitly for Docker commands (for example, `docker --context lenovo ...`) so the result does not depend on the host's currently selected context.

Perform all runtime data checks against the `envscope-local` container in the `lenovo` context. The container mounts a persistent local data volume, where daily snapshots and other persistent runtime data are stored. Inspect the mounted data through that container; do not substitute repository snapshot copies or assume data in the disposable container layer is persistent. Apply the existing targeted-reading rules when inspecting data.

The container is responsible for:

- Web UI
- collectors and synchronization
- scheduled collection
- data queries
- topology generation
- Path Explorer
- future Risk Analyzer
- future architecture rendering

Persistent runtime data belongs in the configured Docker volume, currently:

```text
envscope-data
```

### Container-first engineering rules

Unless a task explicitly says otherwise:

- implement features so they run inside the application container;
- do not depend on host-only absolute paths;
- do not depend on host cron for production scheduling;
- do not assume globally installed host Node/Python packages;
- do not store required persistent state only in the container writable layer;
- keep required runtime binaries, Python modules, Node packages and scripts available in the image;
- treat the container filesystem as replaceable;
- use the mounted persistent data location for generated snapshots/topology data that must survive container recreation.

When adding a new command or worker, consider both:

```text
source-code execution during development
```

and:

```text
execution after docker build inside the final image
```

Container execution is the production acceptance criterion.

When changing dependencies, entrypoints, generated-file locations or scheduling behavior, inspect the relevant:

- Dockerfile / image build definition
- container entrypoint / start command
- volume paths
- environment variables
- package/runtime dependencies

Do not require an unrelated Docker refactor for every task; inspect these only when the change can affect container execution.

## 4. Repository Reading Strategy

Do not scan the whole repository by default.

Start from the smallest set of files directly relevant to the task.

For topology work, prefer:

```text
RESOURCE_SCHEMAS.md
environment-web/lib/topology/
related tests / fixtures
```

Only inspect collectors when the task explicitly concerns collection or when a missing field must be traced back to its source.

Large files such as:

```text
topology.json
production snapshots
raw nginx archives
large logs
```

must not be read in full unless necessary.

Prefer:

- targeted search
- small representative samples
- statistics
- minimal fixtures
- focused log excerpts

Avoid repeatedly reading unchanged large files.

## 5. Change Scope

Keep changes focused on the current task.

Do not:

- perform unrelated refactors;
- rename large parts of the codebase without need;
- modify collectors while working only on topology analysis;
- change raw snapshot schemas merely to simplify downstream code;
- redesign working modules unless the current design blocks the requested task.

If a prerequisite issue is found:

1. explain it briefly;
2. make the minimum prerequisite change;
3. continue the requested task.

## 6. Topology Design Principles

### 5.1 Raw data remains the evidence

Topology is derived from snapshots. It must not become a source of invented facts.

Every important relationship should be traceable to evidence.

### 5.2 Endpoint is a first-class entity

Use Endpoint to correlate traffic and deployments.

Identity should include, when available:

```text
IP + Port + Protocol
```

Do not reduce endpoint relationships to IP-only when a port is available.

### 5.3 Host is a logical machine

ECS, JumpServer Asset and DevOps deployment host records may describe the same machine.

Prefer one logical HOST with multiple evidence sources instead of parallel duplicate host nodes.

### 5.4 Preserve ambiguity

Confidence levels:

```text
EXACT
INFERRED
AMBIGUOUS
UNKNOWN
```

Rules:

- only use `EXACT` for directly supported or uniquely matched relationships;
- inferred edges must retain evidence;
- multiple plausible candidates must remain `AMBIGUOUS`;
- never choose a random candidate;
- never create an edge only to make a graph look complete.

### 5.5 Preserve unresolved topology

An incomplete path is useful information.

Do not convert:

```text
Domain → ... → Endpoint → ?
```

into a false complete path.

Return or persist:

- stopped node
- unresolved status
- reason
- relevant evidence / warnings

This information will later drive topology-gap analysis.

## 7. Environment Semantics

Supported deployment environments:

```text
TEST
SIMULATION
PRODUCT
```

Do not mix environments in analysis unless the caller explicitly asks for all environments.

Risk analysis should generally treat PRODUCT separately.

Do not rename `SIMULATION` to UAT unless an explicit project decision or source contract says so.

### 已确认下线的 JumpServer 资产

用户于 2026-11-02 确认：JumpServer 快照版本 `20261102-094723-114269` 中 232 台 `cannot_login` 且进程 `not_collected` 的资产已经下线，只是尚未从 JumpServer 清单移除。部署单元梳理时将这批具体资产视为无效资产，不再逐项追问；资产 ID 清单位于数据卷 `checks/asset-scope/retired-20261102-094723-114269.json`，按资产 ID 而非仅按数量或 IP 识别。保留原始快照及采集状态，不推断确切下线时间，也不把此结论扩大到清单以外的新登录失败、提权失败或其他未知资产。若后续出现相反的新证据，应重新核实。

## 8. DNS Semantics

For resource-wide IP inventory, A/AAAA may be the only DNS types that map directly to IP rows.

For topology, preserve CNAME relationships.

Do not treat external or unresolved CNAME targets as resolved internal resources.

Paused DNS records must not silently be treated as active traffic paths.

## 9. NAT Semantics

DNAT identity includes:

```text
externalIp
externalPort
internalIp
internalPort
protocol
```

Never collapse DNAT to a simple IP → IP relationship when ports/protocol are known.

## 10. CLB Semantics

Preserve:

- CLB
- listener
- rule
- server group
- backend endpoint
- health-check / status information when available

Do not infer high availability merely from application deployment count.

Actual traffic topology may have fewer active backends than deployment topology.

## 11. Nginx Semantics

Use normalized `nginxRoutes` before parsing raw Nginx text again.

A route may carry:

- domains
- listen
- URI
- directive
- target
- upstream
- expanded backends
- context
- configuration version

Only inspect archived raw Nginx configuration when normalized data is insufficient for the task.

## 12. DevOps Semantics

Application and Deployment are distinct concepts.

Preserve:

```text
Application
  → Deployment(env)
  → Endpoint
  → Host
```

Use deployment-level IP, port, branch and repository where available.

Do not assume an application-level default port always reflects every deployment instance.

## 13. Path Explorer Guidance

Path Explorer should be implemented on generic graph traversal primitives.

Do not hardcode one complete chain per query type.

Preferred base capability:

```text
findPaths(start, targetTypes, direction, filters, maxDepth)
```

Then expose task-level APIs such as:

```text
Domain → Application
Application → Domain
Application → Host
Host → Application
Domain → Endpoint
```

Requirements:

- return complete paths, not only targets;
- preserve edges and evidence;
- compute path confidence from edge confidence;
- detect cycles;
- enforce maxDepth;
- support environment filtering;
- support unresolved paths;
- use graph indexes rather than scanning all edges per hop;
- deterministic path de-duplication.

## 14. Risk Analyzer Guidance

Do not implement risk checks by parsing raw snapshots again if topology already represents the required fact.

Initial rules:

```text
Application single-host SPOF
multiple deployments on one host
same-AZ concentration
CLB backend SPOF
Nginx ingress SPOF
shared production host
topology gap
stale / unknown evidence
```

Risk findings should include:

- rule id
- severity or classification if the product defines one
- affected nodes
- evidence
- explanation
- path(s), when relevant

Do not claim certainty if the underlying path is AMBIGUOUS or UNKNOWN.

## 15. Testing Strategy

Prefer small focused fixtures.

Do not use full real production `topology.json` as the normal unit-test fixture.

For topology/path tests include cases such as:

- one complete path
- multiple backends
- multiple hosts
- ambiguous match
- unresolved path
- cycle
- environment filtering
- duplicate evidence / logical path de-duplication

Run targeted tests first.

Run a broader test suite only when the change affects shared behavior or before final verification.

## 16. Generated and Sensitive Data

Avoid committing:

- cookies
- tokens
- credentials
- SSH keys
- real authentication headers
- sensitive production Nginx config
- large raw logs
- unnecessary full infrastructure snapshots

Treat real topology exports as potentially sensitive.

Use sanitized fixtures for tests and examples.

## 17. Context / Token Efficiency

This project may be developed with multiple AI tools and limited model credits.

Optimize context usage:

- read only task-relevant files;
- prefer summaries and indexes over full generated outputs;
- do not paste large build logs when only the error section matters;
- avoid repeated broad repository searches;
- finish one bounded task before starting another;
- use a new session for a substantially different task when practical.

Stable project rules belong here, not in every task prompt.

## 18. Task Execution Pattern

For a normal implementation task:

1. Read this guide and relevant schema/code.
2. Give a short implementation plan.
3. Make focused changes.
4. Run targeted tests.
5. Fix failures caused by the change.
6. Summarize:
   - files changed;
   - behavior implemented;
   - tests run;
   - known gaps or ambiguities.

Do not spend a long first response restating project documentation.

## 19. Commit Style

Prefer Conventional Commits when practical.

Examples:

```text
feat(topology): implement infrastructure topology builder
feat(topology): add topology path explorer
feat(analysis): add topology risk detection
feat(topology): add architecture diagram renderer
fix(topology): preserve ambiguous endpoint matches
test(topology): add path traversal fixtures
docs: update topology schema
```

## 20. Documentation Ownership

Use:

```text
README.md
```

for human-facing project purpose, operation and high-level architecture.

Use:

```text
RESOURCE_SCHEMAS.md
```

for data contracts, source schemas and topology graph schema.

Use:

```text
PROJECT_GUIDE.md
```

for AI development rules and stable engineering constraints.

Tool-specific instruction files should remain thin and point back to this guide.

## 21. 服务器登录与远程命令（仅限 JumpServer）

需要登录相关服务器核实问题（包括查看日志、进程或配置）时，必须通过 Docker context `lenovo` 的 `envscope-local` 容器，经 JumpServer 建立终端，禁止从宿主机直接 SSH 目标机器，也不要使用仓库内的明文密码自行拼装登录命令。

原因：

- 目标机器的 SSH 登录由 JumpServer 统一授权，宿主机直连通常会被拒绝或无权限；
- 登录账号、密码、提权方式和登录态都已在容器内维护，直接 SSH 会绕过同一套授权与提权逻辑；
- 采集与运维证据必须来自同一条访问路径，便于复现和审计。

### 标准做法

1. 资产记录取当前快照 `jumpserver-snapshot.json`（版本见 `/data/current.json`），按 `ip` 精确匹配。
2. 登录态与凭据位于数据卷：
   - `/data/secrets/cookies.json`（`jumpserver` Cookie、`folidev` / `rootAlt1` / `rootAlt2` 密码）；
   - `/data/secrets/ip-password-map.json`（每台机器的提权方式与已验证密码）。
3. 使用 `application_check.open_terminal(asset, saved, ip_map)`：
   - 通过 JumpServer API 读取该资产的授权系统账号；
   - 经 koko WebSocket 建立终端并完成登录校验；
   - 按 `ip-password-map` 依次尝试 `sudo -i` / `su` 完成提权。
4. 命令通过 `Terminal.run()` 执行，一次只支持**单行**命令；多行脚本必须包成 `bash -c`。
5. 任务结束关闭终端（`terminal.close()`）。

可直接复用 Skill `jumpserver-server-access` 的脚本：

```bash
docker --context lenovo exec -i envscope-local python3 - \
  --ip 10.58.9.86 \
  --cmd "hostname; id -u; ps -eo pid,args | grep -i tomcat | grep -v grep" \
  < .trae/skills/jumpserver-server-access/scripts/remote_exec.py
```

### 约束

- 只读排查：不改配置、不重启服务、不清理文件；确需变更时先向用户确认。
- Cookie 失效时 `open_terminal` 会报“无法通过 JumpServer 登录资产”，此时应更新 JumpServer Cookie，而不是改用宿主机 SSH。
- 快照中 `loginStatus=cannot_login` 只代表上次采集失败，不阻止本次实时登录；登录失败要以本次报错为准。
- 不得在输出、提交内容或文档中回显密码、Cookie、Token。
- 大日志只做定向检索和有限行尾读取，不整份拉取。

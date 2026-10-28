# Env-Scope

Env-Scope 是一个面向应用部署、基础设施资源与网络访问链路的采集、关联、拓扑与治理工具。

当前系统已经能够采集并关联多类数据，包括：

- DevOps 应用、环境与部署实例
- Codeup / GitLab 仓库
- DNS 解析
- EIP
- NAT 网关 / DNAT
- ECS
- CLB / SLB
- JumpServer 资产
- Nginx 进程、配置、Route 与 Upstream
- 基于 IP 的资源宽表
- 基于 Node / Edge 的 Topology Graph

项目当前的发展重点已经从“资源采集”进入：

1. **Topology**：把分散资源串成完整部署和访问链路。
2. **Path Explorer**：查询 Domain → Application、Application → Host 等路径。
3. **Risk Analyzer**：发现单点、伪 HA、共机、链路缺失等问题。
4. **Architecture Renderer**：生成可视化部署架构图。

---

## 1. 项目目标

Env-Scope 希望回答三类问题。

### 1.1 Inventory：我们有什么资源？

例如：

- 某 IP 是哪台 ECS？
- 是否纳入 JumpServer？
- 上面部署了哪些应用？
- 对应哪个代码仓库？
- 是否关联 EIP / NAT / CLB？

### 1.2 Topology：这些资源如何连接？

例如：

```text
Domain
  → DNS
  → EIP
  → NAT / CLB
  → Nginx
  → Upstream
  → Endpoint(IP:Port)
  → Deployment
  → Application
```

### 1.3 Governance：这个架构有什么问题？

例如：

- 生产应用只有一个 Host
- 多实例实际部署在同一台服务器
- 多 Host 但全部位于同一 AZ
- CLB ServerGroup 实际只有一个 Backend
- Nginx 入口是单点
- 一台服务器承载过多生产应用
- Domain 无法追踪到最终 Application
- 采集数据过旧、缺失或只能模糊匹配

---

## 2. 当前数据来源

| 数据域 | 主要用途 |
|---|---|
| DevOps | 应用、环境、部署 IP、端口、仓库、分支、发布时间 |
| Codeup / GitLab | 仓库、代码组、仓库与应用关系 |
| DNS | Domain、A/AAAA/CNAME 等解析关系 |
| EIP | 公网 IP 与云资源绑定 |
| NAT / DNAT | External Endpoint → Internal Endpoint |
| ECS | Host、私网/公网 IP、Zone、规格等 |
| CLB / SLB | Listener、Rule、ServerGroup、Backend |
| JumpServer | 资产、分组、主机信息 |
| Nginx | server_name、listen、location、proxy_pass、upstream、backend |
| Resource Wide Table | 以 IP 为中心的资源盘点与来源汇总 |
| Topology Graph | 以 Node / Edge 为中心的部署和访问拓扑 |

字段细节见：

- [`RESOURCE_SCHEMAS.md`](RESOURCE_SCHEMAS.md)

AI 开发约束与项目工作方式见：

- [`PROJECT_GUIDE.md`](PROJECT_GUIDE.md)

---

## 3. 运行模型：Docker 是正式运行边界

Env-Scope 的正式运行方式是 **Docker 容器**。

`environment-web` 会被构建为 Docker 镜像，并在容器中启动。后续功能设计、代码实现、测试和运行方式都应以容器环境为默认目标，而不是以宿主机直接执行为默认前提。

正式运行模型：

```text
environment-web source
        ↓
npm / vinext build
        ↓
Docker image
        ↓
Docker container
        ↓
Env-Scope runtime
```

容器内负责：

- Web UI
- 数据采集
- 数据同步
- 数据查询
- Topology Builder
- topology 数据生成
- Path Explorer
- 后续 Risk Analyzer
- 后续 Architecture Renderer
- 定时任务与版本切换

也就是说，后续新增任务默认都应满足：

> **在 Docker 容器中可以直接运行、验证和交付。**

不要新增依赖宿主机固定路径、宿主机 cron、宿主机全局 Node/Python 包或宿主机私有运行状态的实现，除非任务明确要求。

### 3.1 当前采集入口

当前采集入口为容器中的 Env-Scope 看板：

```text
http://localhost:3001/settings
```

该页面负责：

- Cookie / AccessKey 配置和阿里云租户管理
- 一键同步
- 北京时间每日定时
- 同步历史
- 数据版本切换

每个启用的阿里云租户分别采集 ECS、CLB、DMS、NAT / DNAT、DNS；Codeup 固定使用原有租户。支持按租户独立同步，快照和资源页面保留租户信息，DNS 两份静态导入保留，云 DNS 单独按公网/内网及域名/记录两层采集到 `cloud-dns-snapshot.json`，零记录域名跳过详情；EIP 保持静态导入。配置、历史兼容与 API 约定见 [`environment-web/SETTINGS.md`](environment-web/SETTINGS.md#阿里云多租户采集)。

持久化数据保存在 Docker 命名卷：

```text
envscope-data
```

容器应被视为可替换的运行实例；需要持久化的数据不得只写入容器临时文件系统。

### 3.2 部署单元目录

工作空间提供「部署单元」菜单（`/deployment-units`），使用已有应用快照自动建立部署单元与分环境实例。Compose 增加 MySQL 8.4 服务及独立持久卷 `envscope-mysql`；领域、系统先建空表，归属待维护。采集仍写原有 JSON 快照，数据库作为独立管理目录，不改变 snapshot 定义。启动、配置、更新及备份方式见 [`environment-web/SETTINGS.md`](environment-web/SETTINGS.md#部署单元与-mysql)，本期规则见 [`Analysis/06-deployment-catalog-implementation.md`](Analysis/06-deployment-catalog-implementation.md)。

### 3.3 单应用上下游核验

JumpServer 服务器进程页保留无 root 账号可见的主机 TCP 监听端口；未确认 PID 的端口仍可在服务器展开行查看。唯一进程候选以「推断」显示并支持端口搜索，与直接采集的 PID 监听分开保存；RabbitMQ 可按 socket UID 与主进程角色排除 EPMD / Erlang 辅助进程，多节点或证据冲突则保留歧义。端口查询失败不会显示为已确认的零监听。此调整在部署新镜像并重新采集后生效，旧快照不回填推断结果。

在 `/jumpserver` 按 IP 查询后，Tomcat / Java 及中间件进程行可点击 **检测上下游**，在新标签页打开独立页面。页面提供 **TCP 连接检测**、**配置文件**、**日志文件** 三个页签，默认打开 TCP；配置和日志可直接使用，无需先执行 TCP 检测。切换页签保留已打开的内容和任务状态。

TCP 页设置 10–300 秒观察时间。部署环境根据目标进程、监听端口和部署记录自动识别；无法唯一识别时保留未确认，不跨环境关联应用。后台复用 JumpServer 授权会话，在目标进程及全部已发现、能唯一匹配资产的对端服务器上并行检查 TCP socket，上游、下游均不设对端台数上限；同机连接独立核验。每次仍最多观察 100 条连接，超限明确警告。输出对端 IP、端口、PID、进程名以及 DevOps 应用 ID / 名称。应用多候选和不可登录的对端保留为未解析结果。

检测读取应用自身的 Tomcat `conf`、`webapps/*/WEB-INF`、`server.xml` 中 Context 的展开目录 `WEB-INF` 和外部 Spring 配置目录，将配置中的数据库、Redis、Elasticsearch、MQ Endpoint 与实际连接核对。常用端口仅作为类型推断；配置声明不表示连接正在使用，也不表示该 profile 已生效。报告不保存原始配置、密码或终端 transcript。

对于应用 YAML 中明确的 `nacos.config` 地址和 Data ID，检测和配置文件列表可通过容器网络读取 Nacos v1 配置。列表刷新后原文保存在数据卷的会话目录，可能包含凭据；页面预览显示密码，仍遮盖常见令牌和私钥。未解析的变量、不可达地址和认证失败会保留警告。

**配置文件** 页展示最近一次配置快照，点击刷新重新采集；展开内容时密码明文显示（预览最多 256 KiB，超限明确提示）。原始配置保存在数据卷。**日志文件** 页提供当前日志预览与历史日志打包下载。配置与日志是可复用的独立组件与 API，文件会话绑定当前快照版本、资产和 PID，每次远程操作复核进程启动身份。配置与日志目录由进程参数、工作目录和已识别的应用推导；支持 Tomcat ROOT 部署目录的符号链接。Logback 配置声明的独立日志路径会按文件名限定范围，避免将共享日志目录中其他应用的文件混入；不扫描整个服务器。

配置列表与检测时的 Endpoint 解析共用 `environment-web/runtime/application-config.ignorelist`：默认跳过 `classes/mapper` 目录、`mybatis-config` / `mybatis-config.*`、Tomcat `conf` 下的 `catalina.properties`、`jaspic-providers.xml`、`logging.properties`、`tomcat-users.xml`、`web.xml`、`conf/yum/yum.conf`，以及 `WEB-INF/web.xml`。规则按完整路径 glob 匹配，每行一条，支持空行与 `#` 注释；目录在远端遍历时剪枝，被忽略文件不读取、不占用采集限额。可在容器数据卷 `${ENVSCOPE_DATA:-/data}/application-config.ignorelist` 追加规则，下次请求即生效，无需重启；默认规则随镜像发布。日志列表与打包不受配置 ignorelist 影响。

**应用日志** 分为「当前日志」和「历史日志」两个 Tab。当前日志仅列出 `.log`、`.out`、`.txt` 普通文本文件（空文件保留，前 4 KiB 含二进制内容的文件排除），默认读取最近更新的非空日志末尾 100 行，可切换文件和刷新。历史日志展示这些扩展名的压缩文件（如 `.log.gz`、`.log.tar.gz`、`.out.gz`）及带数字或日期后缀的轮转文件（如 `.log.1`、`.log.2026-10-15.gz`）；Logback 明确声明的其他压缩滚动文件也按原文件名范围保留。两类分别最多展示最近更新的 1000 个文件。日志列表和打包均跳过 `apache-flume-*` 目录及其内容，不列出目录、符号链接、jar、XML 等无关文件。

历史 Tab 支持打包全部符合规则的历史日志，包含 30 天以前的文件，不混入当前日志（最多 10000 个文件、512 MiB 原始文件；超过限制明确报错，不静默丢弃）。后台分块传输并校验 SHA-256，压缩包保存在数据卷的 `checks/<task-id>/logs.tar.gz`，重启后仍可下载；中断的任务需重新打包。日志压缩包包含原始日志内容，按现有 Settings 的本机/允许主机访问策略提供下载。原 API 不传 `group` 时仍按最近 30 天打包符合筛选规则的当前及历史日志。

任务通过容器内 `runtime/application_check.py` 执行，探针位于 `runtime/probes/`，持久化结果存入 `${ENVSCOPE_DATA:-/data}/checks/<task-id>/`。任务可取消，容器重启会将进行中的任务标记为中断。检测与采集/版本切换互斥，不会生成或切换新的采集版本。

连接探针在远端按目标 0.2 秒周期连续采集最多 5 帧后回传，每帧独立保留连接和计数，避免只保留批次结束时仍存在的连接。对端按已发现的服务端点提前采样并缓冲，最终仍按完整反向连接（含客户端源端口）和时间证据确认 PID，不直接借用同一 IP/端口其他连接的进程。实际频率受目标机查询和堡垒机传输影响，结果页显示目标及对端的有效采样次数、平均和最长观察间隔；短连接仍可能漏采。TCP 状态表示最近一次观察，方向推断与对端进程归属分别说明。

目标端通过所选 PID 的实时文件描述符与 socket inode 关联确认归属，并复用同一帧的监听信息，减少全机进程扫描；读取不到文件描述符时显示明确错误，不把它解释成没有连接。

从 `docker-proxy` 进程进入检测时，会通过实时 Docker 发布端口及容器内监听定位唯一的服务 PID，再检测该进程的全部 TCP 连接。报告同时显示代理入口、容器、端口映射和实际检测 PID，保留容器内真实地址。端口未监听、多候选、权限不足或容器身份变化会明确提示，不将代理的空连接当作整个服务没有连接。容器目标暂不读取配置；配置/日志页仍使用原入口 PID。

失败报告显示检查阶段、错误代码、退出码和安全诊断；对端检查警告可在连接行的“查看连接证据”中查看。探针已验证 Bash 4.2 与旧版 `iproute2-ss170501` 的兼容性，不保存原始终端输出或 stderr。

Socket 探针兼容不支持 `-H` 的 `iproute2-ss130716`。等待 JumpServer 终端返回超时时，只对只读 socket 探针重试一次；持续失败显示 `SOCKET_PROBE_TIMEOUT` 和检查阶段。部分失败仍保留已观察时长、连接及最后有效进程身份，不把失败等待时间当成新增观察覆盖。

下游连接遇到 Docker 发布端口时，会继续进入对端候选容器的网络空间核对实际 socket 和服务进程，展示“宿主机发布端口 → 容器端口”、容器名及实际进程 PID，可识别使用自定义端口的 Redis、MQ 等中间件。Docker 代理仅作为转发证据，服务进程归属与 DevOps 应用归属独立判断。仅找到端口映射/监听进程时保留推断；多个容器候选、权限不足、容器重启或客户端地址被代理/SNAT 改写时保留未解析结果。对端需要具备 Docker 查询权限与 `nsenter`，无需在业务容器中安装诊断工具，也无需给 EnvScope 挂载 Docker socket。

当前采用 `ss` 周期采样及 socket 字节计数差值，最多记录 100 条连接。短连接可能漏采，UDP / Unix Socket、代理或 NAT 前的原始调用方、jar 内配置和配置中心暂不覆盖。Tomcat 共用 JVM 时，只能确认进程级连接；应用归属保留多候选。网络活动不能单独证明存在业务请求，也不能用无流量结果直接判断可删除。

### 3.3 历史宿主机方式

历史的宿主机每日调度方式已经移除。

单项 Python 脚本继续保留，用于：

- 容器内采集复用
- 开发期手工调试
- 单项数据验证

手工从宿主机执行脚本只属于开发/诊断方式，不是正式生产运行架构。

---

## 4. 主要目录职责

具体目录以当前仓库为准，核心职责建议保持如下边界：

```text
env-scope/
├── README.md
├── RESOURCE_SCHEMAS.md
├── PROJECT_GUIDE.md
├── AGENTS.md
├── .github/
│   └── copilot-instructions.md
├── environment-web/
│   ├── lib/
│   │   ├── topology/
│   │   └── ...
│   └── ...
├── tests/
└── ...
```

### 4.1 采集层

负责从各来源获取原始事实，不负责为了“让拓扑连起来”而推断不存在的关系。

### 4.2 Resource Inventory

`/resources` 宽表以 IP 为主要键，适合：

- 资源盘点
- 来源比对
- 纳管检查
- IP 维度查询

### 4.3 Topology

Topology 层使用统一 Node / Edge 模型，适合：

- 完整链路追踪
- Graph Traversal
- 架构图
- 风险分析

不要继续把所有拓扑能力堆进 IP 宽表。

---

## 5. Topology 核心思想

### 5.1 Endpoint 是关键关联点

Endpoint 建议至少由以下字段确定身份：

```text
IP + Port + Protocol
```

例如：

```text
endpoint:10.179.1.10:8080:tcp
```

它是以下来源的交汇点：

- DevOps Deployment
- Nginx Backend
- CLB Backend
- NAT / DNAT Target

只按 IP 做关联容易把同机不同端口、不同服务错误合并。

### 5.2 Host 是逻辑主机

同一台机器可能同时出现在：

- ECS
- JumpServer
- DevOps Deployment

Topology 中应尽量归并为同一个逻辑 `HOST`，并保留各来源 evidence。

### 5.3 不确定关系必须显式表达

关系可信度统一使用：

```text
EXACT
INFERRED
AMBIGUOUS
UNKNOWN
```

原则：

- 有精确 IP + Port 等事实依据时才使用 `EXACT`
- 推断关系必须保留 evidence
- 多候选不能随机选择
- 不允许为了让图完整而制造不存在的关系

---

## 6. Topology 的主要 Node

第一阶段建议至少包含：

```text
DOMAIN
EIP
NAT_GATEWAY
DNAT_RULE
CLB
CLB_LISTENER
SERVER_GROUP
HOST
ENDPOINT
NGINX_ROUTE
UPSTREAM
APPLICATION
DEPLOYMENT
REPOSITORY
```

未来可扩展：

```text
SYSTEM
VPC
ZONE
CERTIFICATE
DATABASE
REDIS
MQ
K8S_SERVICE
POD
```

其中 `SYSTEM` 很适合用于表达：

```text
System
  → Applications
  → Deployments
  → Endpoints
  → Hosts
```

---

## 7. Path Explorer

Path Explorer 基于生成后的 `topology.json` 做通用 Graph Traversal。

第一阶段重点支持：

```text
Domain → Application
Application → Domain
Application → Host
Host → Application
Domain → Endpoint
```

查询结果应保留：

- 完整 nodes
- 完整 edges
- evidence
- path confidence
- environment
- unresolved 状态与原因

不要只返回最终节点。

---

## 8. Risk Analyzer 规划

Path Explorer 验证稳定后，再实现 Risk Analyzer。

第一阶段优先规则：

1. **Application 单点**
   - PRODUCT Application 只有一个 distinct Host。

2. **同主机伪 HA**
   - Deployment 多于 1，但 distinct Host = 1。

3. **同 AZ 风险**
   - Host 多于 1，但全部在同一 Zone。

4. **CLB Backend 单点**
   - ServerGroup 最终只有一个有效 Host / Endpoint。

5. **Nginx 单点**
   - 一个对外链路只有单一入口 Nginx Host。

6. **服务器共用**
   - 单 Host 承载多个 PRODUCT Application。

7. **Topology Gap**
   - Domain 无法遍历到目标 Application，或中途链路缺失。

8. **Stale / Unknown Data**
   - 关键 evidence 过旧、采集失败或可信度不足。

---

## 9. 架构图规划

不要直接从原始快照画图。

建议流程：

```text
Raw Snapshots
      ↓
Resource Normalization
      ↓
Topology Builder
      ↓
topology.json
      ↓
Path Explorer / Risk Analyzer
      ↓
Mermaid / Graphviz / SVG
```

建议支持的视图：

- 全局概览
- 单系统部署架构
- 单 Domain 完整链路
- 单 Application 部署拓扑
- 单 Host 应用分布
- 风险节点高亮图

---

## 10. 开发、构建与运行

### 10.1 正式运行

正式交付物是 Docker 镜像。

开发完成后的验证目标应优先是：

```text
docker build
    ↓
container start
    ↓
Web / collector / topology / query functions work inside container
```

本地直接执行 `npm`、Node 或 Python 命令主要用于开发和调试；不能用“宿主机可以运行”替代“容器内可以运行”的验收。

### 10.2 Web 项目本地开发

进入 Web 项目目录后，以 `package.json` 中 scripts 为准。

常用开发流程：

```bash
npm install
npm run build
```

当前构建工具在生产构建完成后会提示使用：

```bash
vinext start
```

如果 `package.json` 已提供 `start` script，优先：

```bash
npm run start
```

开发环境通常使用项目已有的 dev script，例如：

```bash
npm run dev
```

### 10.2 Python 采集脚本

当前保留多个单项只读导出工具。

例如 DevOps：

```bash
python3 export_devops.py
```

单应用验证：

```bash
python3 export_devops.py --app-id 1913
```

Codeup 仓库去重与权限检查：

```bash
python3 check_codeup_access.py --input path/to/applications.csv
```

Codeup 代码组导出：

```bash
python3 export_codeup_groups.py
```

应用详细运行日志导出：

```bash
python3 export_devops_details.py --app-name fosun-aggregation-openapi
```

### 10.3 Python 测试

```bash
python3 -m unittest discover -s tests -v
```

Topology / Web 相关测试请以当前 `package.json` 与 topology 模块实际提供的命令为准。

---

## 11. 数据安全

本项目包含基础设施和内部部署数据，应避免将以下信息提交到公开仓库：

- 内部域名
- 内网 IP 清单
- EIP / NAT / CLB 全量配置
- Nginx 原始生产配置
- Cookie
- Access Token
- SSH Key
- 密码
- 认证 Header
- 未脱敏运行命令

Cookie 与其他认证信息不得写入采集输出、日志或 Git。

大型真实快照和生成的 `topology.json` 建议按项目实际情况加入 `.gitignore`，开发与测试优先使用最小 fixture。

---

## 12. AI 工具协作

项目可能交替使用：

- OpenAI Codex
- TRAE
- VS Code + GitHub Copilot

统一规则放在：

```text
PROJECT_GUIDE.md
```

工具入口：

```text
AGENTS.md                         # Codex / Agent
.github/copilot-instructions.md  # GitHub Copilot
```

稳定项目知识不要反复复制到每次 Prompt 中。

日常任务 Prompt 应只描述：

- 当前目标
- 当前范围
- 明确不做什么
- 验收标准

---

## 13. 当前路线

```text
Collectors / Snapshots         ✅
Resource Inventory             ✅
Topology Builder               ✅
Path Explorer                  ← 当前重点
Risk Analyzer                  下一阶段
Architecture Renderer          后续
```

目标是让 Env-Scope 从“资源采集工具”逐步演进为：

> **Application Infrastructure Topology & Architecture Governance Platform**

现有采集数据的数据库落库、同步和版本管理草案见 [DATA_PERSISTENCE_DESIGN.md](DATA_PERSISTENCE_DESIGN.md)。该文档为规划方案，尚未改变当前 JSON 快照运行方式。

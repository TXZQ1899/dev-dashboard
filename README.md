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

- Cookie 配置
- 一键同步
- 北京时间每日定时
- 同步历史
- 数据版本切换

持久化数据保存在 Docker 命名卷：

```text
envscope-data
```

容器应被视为可替换的运行实例；需要持久化的数据不得只写入容器临时文件系统。

### 3.2 历史宿主机方式

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

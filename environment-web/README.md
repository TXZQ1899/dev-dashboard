# Settings 与容器内同步

当前数据更新入口是 `http://localhost:3001/settings`：Cookie 配置、一键同步、北京时间每日定时、同步历史和版本切换。数据保存在命名卷 `envscope-data`，详见 [SETTINGS.md](SETTINGS.md)。现有页面仍使用同样的快照模型；同步及切换由本地服务构建并验证后原子启用，无需 Docker socket 或宿主机脚本。

# EnvScope 应用环境管理

基于本地采集快照的只读环境资产工作台。每日更新入口为 `http://localhost:3001/settings`，详见 [SETTINGS.md](SETTINGS.md)。

- `/`：应用数、各环境服务器数、生产单点、数据完整性、大屏全屏按钮。
- `/applications`：每应用一行，点击各环境数量展开 IP / 部署 ID / 配置 ID / 状态 / 错误；支持搜索、筛选和分页。
- `/repositories`：代码库全景，40 组 / 595 库；双环形占比、关联渐变条、搜索和筛选、代码组展开、仓库详情跳转。
- `/repositories/[id]`：仓库地址、组归属、关联应用、权限依据及原始托管平台链接。

## 代码库数据口径

代码库快照由容器内同步（DevOps + 当前账号 Codeup 可访问仓库双向对比）生成并随版本保存，不再依赖离线 Excel 导入脚本（该脚本已随旧流程移除）。

历史 Excel 快照口径（已废弃，仅存档说明）：当前 256 个 DevOps 去重仓库精确匹配 40 组，其中 26 个可访问、230 个不可访问；339 个数量差额没有地址及权限明细。79 个未精确匹配仓库另列，不进入 595 分母。组环图表示“含已确认可访问仓库的组”（6），并非组级权限核验结果；全量可访问代码组数及代码库数仍待补齐。

“未关联”是两批次清单的数量差额估算，不是已确认未关联仓库清单；出现负差额时显示冲突。仓库按域名和完整路径匹配，统一 HTTP/HTTPS/SSH 与 `.git` 后缀；一个库关联多个应用仅计一个库。权限来自 2026-09-02，组清单来自 2026-09-03。界面为静态快照，不提供实时权限探测。

## 查看拓扑数据

第一版拓扑模型不提供图形界面。打开 `http://localhost:3001/settings`，在“Topology 数据”区域点击 **基于当前数据版本生成 Topology**。生成会：

1. 读取 Docker 数据卷中当前激活的 DevOps、仓库、JumpServer、ECS、CLB 和 NAT 快照；
2. 结合镜像中的静态 DNS 与 EIP 快照；
3. 在 `envscope-data` 卷中保存新的 `topology-*` 版本；
4. 提供查看与下载链接。

生成后可打开 `http://localhost:3001/topology.json` 查看，或在 Settings 页面下载对应版本。文件包含 `nodes`、`edges`、`stats` 和每条边的 evidence。宿主机开发时也可运行：

```bash
cd environment-web
npm run topology
```

后续提供拓扑 UI 或架构图渲染时，可继续复用同一数据模型。

## 本地运行

```bash
npm ci
npm run dev
```

打开终端显示的本地地址。`npm run build` 生成部署产物。

## 更新快照

在 `http://localhost:3001/settings` 点击同步（或配置每日定时）。同步在容器内采集 DevOps、Codeup、Local GitLab、JumpServer、上海 ECS、CLB 与 NAT，完整并校验通过后原子启用新版本；快照仅保留展示必需字段，不保存 Cookie 或仓库 URL。

## 统计口径

应用按 ID 聚合；服务器数按环境内 IP 去重。列表按应用及环境内 IP 去重；不同部署记录保留。生产单点仅在该应用生产记录全部成功且有 IP、不同 IP 恰好为 1 时确认。失败、缺少 IP 或没有记录均为待核实，不当作零部署。单点是清单层面的部署风险，不代表已验证基础设施容灾状态。

数据为静态快照，无实时探测、增删改或登录管理。HTTP 名和端口来自同批次 applications.csv，空值显示未提供。

## 验证

`npm test` 覆盖 IP 去重、单点判断及不完整数据；`npx tsc --noEmit` 检查类型。

WebMCP 在支持的浏览器中注册 filter_applications，可更新同一搜索/筛选状态；当前环境没有可用的 WebMCP 校验上下文，尚未做运行时协议验证。

## 本地 Docker

```bash
docker compose up -d --build
```

打开 http://localhost:3001 。容器名为 `envscope-local`，镜像为 `envscope:local`。默认仅绑定本机，避免与现有 3000 端口预览冲突。容器使用构建后的 Worker 产物通过本地 workerd 运行，不连接或部署到 Cloudflare / Sites。

```bash
docker compose logs --tail=100
docker compose ps
docker compose down
```

更换本机端口：`ENVSCOPE_PORT=8080 docker compose up -d`。更新清单后重新执行导入和 `docker compose up -d --build`。镜像包含导入时的内部环境快照，应留在本地或授权的内部镜像仓库。

本机访问 Docker Hub 超时，Compose 默认使用 ECR 中的 Docker 官方 Node 镜像。网络恢复后可指定 `NODE_IMAGE=node:22-bookworm-slim docker compose up -d --build`。

页面之间使用标准 HTML 链接导航（保留筛选参数和应用 ID 锚点），避免当前 Vinext 生产构建中的客户端路由异常；页内搜索、分页和详情展开仍由 React 处理。

## 阿里云 ECS 资源

- `/aliyun`：项目与 env 分布、实例数量、配置 vCPU / GiB 合计、公网 IP 实例数；支持筛选后查看汇总，并跳转对应明细。
- `/aliyun/instances`：默认按项目 → env 分组，可添加任意标签层级、移除标签层级、全部展开或收起。支持搜索、项目、状态、公网 IP 和多个标签的交集筛选；页面跳转保留筛选。
- 明细包含实例名 / ID、CPU、内存、操作系统、内网 IP、公网 IP、状态和全部标签。公网地址合并普通公网 IP 与 EIP，去重后展示，缺失显示“无”。

更新数据：先更新项目外 `ecs-export-cn-shanghai/ecs-list-all.json` 与 `ResourceGroup.json`，再执行 `python3 scripts/import_ecs.py` 和 `docker compose up -d --build`。只导入显示字段，不导入 Cookie 或账号字段。

当前为上海区域 2026-09-07 快照：204 台、992 vCPU、3865.5 GiB；19 个项目目录。88 台 ResourceGroupId 为空，列为“未指定项目”，与默认资源组的 10 台分别统计。缺失标签为“未设置”，空字符串标签为“空值”；不根据实例名推断项目或环境。资源数量按实例 ID 计数，容量为实例配置合计，不是使用率。汇总保留无匹配实例的项目并显示 0。

## 每日采集更新

当前入口为 Settings 容器内同步，自动采集完整 DevOps 与当前账号 Codeup 清单并构建新版本；切换版本无需重建镜像。上方 40 组 / 595 库统计属于历史 Excel 快照口径，已被每日快照取代。代码库全景保留原有布局，改用当日“我的代码组 / 可访问仓库”分母，支持双向差异筛选及仓库统计；应用环境详情新增端口、分支、Push In 时间及状态。

### 按提交时间统计与导出

代码库全景支持按最近一周、一月、三月、半年、一年及历史区间筛选，并展示所选区间的实际提交数。采集器从现存分支的固定提交 SHA 追踪全部父提交，跨分支按 SHA 去重（包含合并提交），按北京时间日期聚合到 `commitDailyCounts`。`commits` 为此范围的完整总数，Codeup 原始统计保留为 `reportedCommits`；不使用仓库活动时间，不包含已不可达的删除分支提交或仅标签可达的提交。

时间筛选以当前快照采集日期为基准。“超过 2 年未提交”筛选最后提交早于两年前的仓库，其区间数量展示两年前的提交数。历史快照缺少提交明细时不会拿总提交数冒充区间提交数。CSV 导出遵循页面当前搜索、代码组条件、时间及流水线关联条件，包含折叠中的匹配仓库和筛选说明；使用 UTF-8 BOM 便于 Excel 打开。

## DNS 与 CLB 整合

CLB 全景默认保留“域名证书”视图，全部实例在“CLB 实例”视图展示。DNS 关联补充在证书和实例详情中，独立“DNS 域名”视图仅用于查看静态解析记录，不限制原有 CLB 展示。静态来源仅限
`folidaymall.com`（268 条）和 `fosunholiday.com`（213 条），保留 Excel 文件名、行号、线路、TTL、权重与启停状态。
启用的 A / AAAA 记录按地址关联当前版本 CLB，CNAME 仅沿这两份表内的启用记录追踪，保留链路证据。匹配同线路或默认线路，不跨其他特定线路；通配符仅展示原记录，不展开未知子域名。
外部 CNAME、循环、暂停和非地址记录不会通过证书猜测 CLB。DNS 关联只确认实例 IP，展开后的全部监听器、默认路由和条件路由仍需按端口及请求路径判断，不代表已经验证实际请求。
DNS 是独立静态补充数据，CLB 同步及历史版本切换时按所选 CLB 快照重新关联；没有 CLB 的版本仍能查看 DNS。

### EIP 静态数据联动

CLB 页面增加“弹性公网 IP”视图，包含 2026-06-26 三份 CSV：泛宥 2 条、修平 35 条、thomascookjv 16 条，共 53 条。可搜索来源、地址、绑定实例、域名与备注，并在 EIP、DNS 和 CLB 之间跳转。证书和 CLB 实例详情均展示关联 EIP。

EIP 通过 SLB/CLB 绑定实例 ID 或相同公网 IP 匹配所选 CLB 快照；DNS 的启用 A/AAAA 和表内 CNAME 链也可经过 EIP 绑定到内网 CLB。保留关联依据、解析链、CSV 文件名及原始起始行号。ECS 绑定按实例 ID 查询当前 ECS 快照 IP；NAT 绑定按网关 ID 与 EIP 查询当前版本 DNAT 映射，保留协议及内外端口。可用 DNAT 的后端 IP 可进一步匹配内网 CLB。未命名补充列仅保留源信息，不用于推断转发关系。无 CLB、无 DNS 匹配的 EIP 仍完整展示。静态历史绑定不代表实时网络状态。

`lib/eip-snapshot.json` 与 DNS 一样独立于每日采集和版本数据，切换版本后重新计算关联。更新静态 CSV 后运行标准库导入器，再重新构建应用：

```bash
python3 scripts/import_eip.py /path/泛宥-弹性公网IP\ eip_list_cn-shanghai_2026-06-26.csv /path/修平-弹性公网IP\ eip_list_cn-shanghai_2026-06-26.csv /path/thomascookjv-弹性公网IP\ eip_list_cn-shanghai_2026-06-26.csv
```

更新两份静态导出（使用安装了 openpyxl 的 Python）：

```bash
python3 scripts/import_dns.py /path/folidaymall.com_export.xlsx /path/fosunholiday.com_export.xlsx
```

导入只读取文件，不修改 DNS 服务或源 Excel。修改后需重新构建应用。`npm test` 覆盖限定范围、暂停、CNAME 链、线路、循环与导入数量。

### 批量 Git 搜索与应用环境详情导出

代码库列表支持展开“批量 Git 地址搜索与详情导出”，粘贴每行一个 Git 地址或 Markdown 链接表格；将多行内容粘贴到普通搜索框也会进入批量输入。批量结果独立于普通筛选，按主机和完整路径精确匹配，保留路径大小写，统一 HTTP/HTTPS/SSH 与 `.git` 后缀，重复地址只处理一次。无效输入、未匹配和同地址多条仓库记录都显式保留。

详情 CSV 包含仓库属性、权限、提交统计和关联应用，并按应用的测试／仿真／生产环境展开部署记录（IP、端口、分支、部署与配置 ID、采集状态和错误、发布状态、Push In 步骤）。应用、环境、部署三个层级分别导出最新成功 Push In 时间；失败步骤仅保留在步骤明细中，缺失时间留空。无关联应用、缺失应用详情、无环境记录也有明确行，不因缺失而丢失输入仓库。末两列记录代码库与应用快照时间。CSV 使用 UTF-8 BOM、标准引号转义与公式注入防护。

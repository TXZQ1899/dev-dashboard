# 本地数据同步与历史版本

## 阿里云多租户采集

Settings 顶部的「阿里云租户管理」支持新增租户、修改名称、启停、更新 AccessKey 和验证账号。已有凭据自动作为 `legacy-aliyun`（现有租户）保留；新租户使用稳定 UUID。内部租户 ID、阿里云账号 UID、DMS Tid、Codeup 企业 ID 是独立字段。新增或更换 AccessKey 时通过 STS GetCallerIdentity 校验 UID，已绑定账号不能被另一账号的凭据覆盖。Secret 不回显，留空保留；更换 ID 时需同时填写 Secret。

阿里云配置统一通过顶部「管理租户」维护。标记为「Codeup 绑定租户」的编辑区额外提供采集方式、Cookie 兼容入口和 Codeup 企业 ID（留空自动识别）；其他租户只配置各自的 AccessKey。下方「凭据与每日同步」仅保留 DevOps、JumpServer、Local GitLab Cookie、服务器密码及同步调度，保存时不再提交旧阿里云字段，避免覆盖已保存的租户配置。原 Cookie、AccessKey、企业 ID 及旧客户端 API 保持兼容。

每个启用租户都采集 ECS、CLB、DMS、NAT / DNAT、DNS。ECS、CLB、NAT 按配置地域采集（默认 `cn-shanghai`），DMS 按 Tid 采集一次；Tid 留空时通过 GetUserActiveTenant 获取。原有 Cookie 方式仅适用于现有租户的上海地域。Codeup 固定绑定现有租户，新增账号不调用 Codeup；全量及每日任务仍需要现有租户启用且原有平台凭据完整。DNS 保留两份静态导入，新增独立 `cloud-dns-snapshot.json`；EIP 仍使用静态导入。云 DNS 必须使用租户 AccessKey，分别采集公网域名与内网 PrivateZone，每个账号只采集一次，不按地域重复。域名记录数为 0 时保留列表项并跳过详情请求。

「一键同步」和每日任务覆盖所有启用租户及现有平台，沿用总计三个采集槽位。「ECS / CLB / DMS / NAT / DNS 独立同步范围」可选全部启用租户或一个租户；「同步所选租户全部云资源」只更新五类云资源，不调用 Codeup、DevOps 或 JumpServer。独立任务按租户替换切片，保留其他租户、其他来源及其观察时间。任务启动后固定配置和凭据版本，运行中保存设置只影响后续任务。

任一计划任务失败均不启用新版本；合法空清单可成功。进度、日志槽位及错误显示租户和数据源。失败来源可按现有互斥规则重试，恢复判定区分租户。停用停止后续采集，不修改当前快照或删除历史文件；下一次全量同步的采集范围仅包含启用租户。

租户管理中的采集错误保存在数据卷 `tenant-alerts.json`，记录租户、数据源、地域、凭据版本和失败任务版本。相同范围、相同凭据的后续同步成功并激活后自动清除；其他来源同步成功或仅继承旧快照不算恢复。服务启动时也会核对当前版本，兼容旧错误记录，从原始 `collection-error.json` 还原来源后清除已恢复的提示。历史失败版本和日志保留。

为使旧客户端和租户更新在一次原子写入中保持一致，租户配置及凭据统一保存在原 `/data/secrets/cookies.json` 的 `aliyunTenants` 对象中（0600），现有租户保留旧字段兼容别名。`/api/settings/state` 只返回脱敏租户列表；`POST /api/settings/config` 的 `aliyunTenants` 是单租户或多租户增量 upsert 数组，更新携带 `id` 和 `credentialVersion`，省略的租户不删除。`POST /api/settings/sync/{ecs|clb|dms|nat|cloud-dns|aliyun}` 可传 `{ "tenantId": "..." }`，省略则处理全部启用租户。`aliyun` 表示五类云资源。

快照保持原文件入口，增加 schemaVersion=2、逐资源租户信息及逐租户/地域的观察元数据。ECS、CLB、NAT、DMS 和 IP 全景支持租户筛选；DMS CSV 附加租户 ID、名称、采集账号 UID。按预先规划的不重叠网段沿用 IP 关联，拓扑增加租户来源信息，云 DNS 同步支持全部启用租户或单租户。历史快照文件不重写。

## Topology 版本生成与下载

Settings 页面提供“Topology 数据”区域。点击“基于当前数据版本生成 Topology”后，服务读取 `/data/current.json` 指向的当前激活版本，使用其中 DevOps、Codeup/仓库、JumpServer、ECS、CLB 和 NAT 数据；DNS 使用当前版本的公网云采集快照补充镜像内静态导入（同 Zone 优先云采集）；内网解析保留在 DNS 页面，不添加全局域名边。EIP 继续取自应用镜像内静态导入。生成结果写入 `envscope-data` 卷的 `topology/<topology-id>/topology.json`，并保存 `version.json` 与 `topology.log`。每次点击都会创建新的 `topology-*` ID，不覆盖历史结果；`topology/latest.json` 只指向最近一次成功版本。

页面显示拓扑节点数、边数、待确认边、未解析或外部节点，并提供“查看 JSON”和“下载当前 Topology”链接。`/topology.json` 仅返回最近一次成功生成的数据卷版本；如果没有生成记录，会提示先到 Settings 生成，不会回退到镜像内旧拓扑。生成失败会保留 failed 版本目录和日志，且不改变 latest 指针。

打开 `http://localhost:3001/settings`。先在顶部租户管理中配置阿里云账号，再在下方填写 DevOps、JumpServer、Local GitLab Cookie 后点击“保存并一键同步”。空输入保留已保存 Cookie，页面不会回显 Cookie。每日同步使用北京时间，默认时间为 18:00；需勾选并保存后开启。Docker 停机后当天错过的任务会在启动后补跑，每天最多自动启动一次；失败可手动重试。

同步范围：DevOps 全量应用、环境、分支、Push In 发布时间；Codeup 当前账号代码组和仓库及双向对比；JumpServer 当前账号授权资产和分组。上海 ECS 实例和 CLB 实例、监听器、证书绑定、转发规则及全部后端服务器组。ECS 项目名称目录沿用前版，实例及 ResourceGroupId 每次重新请求。旧 Codeup Cookie 自动迁移为 aliyun，上述旧字段仅作为现有租户的兼容别名；新版本按上方多租户配置分别读取凭据。

## 持久化

Compose 使用命名卷 `envscope-data`，挂载到 `/data`。Cookie、运行记录和完整历史快照都在该卷；镜像中不包含 Cookie，也不挂载 Docker socket。

- `/data/secrets/cookies.json`：凭据，仅容器用户可读写（0600）。API 只返回是否配置和保存时间。
- `/data/versions/<时间戳>/`：每次同步新建，包含原始采集记录、六份页面快照、版本清单和日志。失败目录也保留，不能激活。
- `/data/current.json`：当前启用版本。
- `/data/schedule.json`：每日同步设置。
- `/data/job.json`：最近任务状态；容器重启后中断任务标为失败。
- `/data/extra-jobs.json`：与主任务并发运行的单独数据源重试任务；容器重启后中断任务标为失败。
- `/data/catalog-job.json`：最近一次部署目录导入结果；不包含数据库密码。

`docker compose up -d --build`、`docker compose restart` 和普通 `docker compose down` 不删除数据卷。`docker compose down -v` 会删除全部历史和 Cookie，不应用于日常更新。备份时备份整个数据卷，并将其作为包含凭据的私密备份保存。

## 部署单元与 MySQL

`/applications?tab=deployment-units` 展示部署单元。一个应用创建一个部署单元，三个环境分别保存关联；真实部署记录生成实例。点击环境可查看 IP、声明端口、分支、部署/配置 ID、发布时间和发布状态。实例 ID、来源观察及最后观察时间保留在数据库与 API 中，不在展开表格展示。支持搜索、分页及包含未再观察到的来源。业务领域、业务系统表已建立，暂不自动填充归属。

在 `environment-web` 中执行 `docker compose up -d --build`，会同时启动 MySQL 8.4，健康后启动应用。首次启动自动迁移、读取当前已完成应用快照；随后独立线程每 30 秒检查当前版本，也可使用 Tab 中的「从当前快照创建 / 更新」。数据库失败不会使原采集或版本激活失败；Tab 显示最近任务错误，并保留上次成功目录。

MySQL 持久数据独立保存于 `envscope-mysql:/var/lib/mysql`，不发布宿主机端口。首次初始化前可在 Compose `.env` 设置 `ENVSCOPE_DB_PASSWORD`、`ENVSCOPE_DB_ROOT_PASSWORD`；内置默认值只供本地启动。修改已有数据库密码需同步修改数据库账号，修改环境变量不会重置已初始化卷内账号。外部 MySQL 部署可直接配置应用环境变量 `ENVSCOPE_DB_HOST`、`ENVSCOPE_DB_PORT`、`ENVSCOPE_DB_NAME`、`ENVSCOPE_DB_USER`、`ENVSCOPE_DB_PASSWORD`，可选 `ENVSCOPE_DB_SSL_CA`（容器内 CA 文件路径）。账号需要目标库的建表、索引及读写权限。

`ENVSCOPE_DEVOPS_SOURCE` 默认 `devops-default`，是稳定的来源命名空间；日常更新不要更换，否则会被当作另一套来源创建对象。相同快照内容重复导入不会重复生成单元或实例。完整清单缺失仅标记未再观察到，不物理删除；采集不完整保留旧观察。快照版本切换为更早采集时间时，应用页使用历史数据，部署目录保留较新的管理状态并给出说明。

备份需同时考虑 `envscope-data`（来源快照、凭据）与 MySQL 管理数据（领域、系统、单元、实例、观察及变更）。MySQL 使用 `mysqldump --single-transaction` 等一致性备份方式；不把运行中数据库目录的普通文件复制视为一致性备份。执行 `down -v` 也会删除 MySQL 管理数据。当前不提供领域/系统或单元的人工编辑页面、进程识别、历史目录回放。

## 激活机制

本地 Python 服务接收 Settings API 请求，调用复用的只读采集器，并代理现有网页。全量同步与版本切换独占，任一任务运行期间都不能再发起。单个数据源仅在其本轮全量同步中已失败、且没有其他并发重试在运行时，才可立即并发重试：该来源失败会让全量同步必然失败、永不激活新版本，因此重试构建的独立版本不会覆盖主任务数据；同一时刻只允许一个并发重试。采集完整后使用六份快照构建候选页面，在另一个内部端口启动并检查，成功后原子更新当前版本指针和转发目标。激活过程串行执行，避免并发任务与主任务争用端口。旧页面在准备期间仍可访问。失败保留旧版本；完整采集但构建失败的版本可再次尝试切换。

版本目录不保存 Cookie。临时凭据文件用于采集，任务结束自动删除。每次切换重新构建当前应用代码，避免旧版 UI 与旧数据强绑定；临时网页产物在容器可写层，不占历史数据卷。容器启动时按持久化的当前版本恢复网页，因此重建镜像不会意外覆盖用户选择的数据版本。

服务只发布到 `127.0.0.1:3001`，写操作要求同源 JSON 请求；此设计用于个人本机运行，不是多人远程服务。不要直接暴露到公网。

## 验证

`python3 -m unittest discover -s runtime -p 'test_*.py'` 检查凭据保护、无效版本拒绝、完整性、任务互斥、启动中断与失败回退。前端使用现有 `npm test` 和 TypeScript 检查。Docker 部署包含 Python 运行时和固定的采集脚本，不需要宿主机 Python 或 Codex 才能执行定时任务。

宿主机日常调度（旧 `run_daily_collection.py --update-web` 及其 `publish_daily.py` 发布流程）已移除，采集只通过本服务的 Settings 调度执行，避免与容器内调度重复。数据版本完全以 `envscope-data` 卷中的当前版本指针为准。

## DNS 域名

CLB 全景的「DNS 域名」先展示域名列表，分「公网权威解析」「内网域名解析」两个 Tab；点击域名查看解析记录。列表显示租户 / 静态来源、记录数和采集时间，零记录域名明确显示已跳过。两份 Excel 的 481 条静态记录继续保留，不强行归属现有租户；公网同名的静态与云采集分别展示。CNAME 不跨租户或公网/内网范围补齐，暂停和未知状态不参与当前关联。

## CLB 全景

`/aliyun/clb` 展示 CLB IP → 监听器 → 默认路由或域名/路径规则 → 服务器组 → 后端 IP/端口，支持搜索及展开。域名不代表已验证 DNS。区分服务器 ID 去重数量和服务器组成员记录，跳转监听器单独显示。旧版本未采集 CLB 时展示空状态，不借用当前或镜像中的 CLB 数据。任一采集源失败不激活新版本。

AccessKey 模式先检查每个 CLB 的 `LoadBalancerStatus`：列表中为 `inactive`（已停止）或 `locked`（已锁定）时直接跳过；列表未明确停用时先查实例详情，并再次检查状态。状态缺失不能直接认定为停用。网络超时、连接异常或 HTTP 408/429/500/502/503/504 最多尝试 3 次，每次超时仍为 45 秒，两次重试分别等待 1 秒和 2 秒，并增加最多 0.5 秒随机退避；权限、认证和参数错误不重试。

单个 LB 重试后仍超时、接口异常或详情结构缺失时，记录一条包含 LB ID、状态、接口和原因的 `Warning`，跳过整个 LB 并继续下一个，不阻止任务成功或版本激活。证书元数据及后端 ECS IP 补查失败也仅记录 Warning，未知 IP 保留为空。实例列表枚举失败、枚举鉴权失败或前后清单不稳定仍属于整个来源失败。成功快照的 `complete` 表示清单已枚举并复核、所有 LB 已按此策略处理；存在跳过或补查失败时同时标记 `partial: true`，`warnings[]` 随原始结果、规范化快照及逐租户/地域元数据持久保存，不把这些记录解释为完整详情。

## NAT / DNAT 同步与 EIP 后端

Settings 的“单独同步 NAT”按所选租户和地域分别读取其 AccessKey（现有租户兼容 Cookie），采集可见的全部 NAT 网关及各 DNAT 表。全量和每日同步也包含 NAT。只调用 DescribeNatGateways、DescribeForwardTableEntries，核对分页总数、唯一 ID 和网关／表清单稳定性。完整采集并构建成功后才启用新版本；失败保留当前版本。单独同步任何来源都保留其他来源的快照。

`nat-snapshot.json` 随历史版本保存；旧版本没有此文件时显示未采集，不借用其他版本数据。CLB 页增加 NAT 视图和 EIP 后端详情。ECS 类型通过静态 CSV 的绑定实例 ID 精确查询当前版本 ECS 的内网／公网 IP；NAT 类型同时匹配网关 ID 与 EIP 地址，展示每条 DNAT 的协议、公网 IP/端口、后端 IP/端口和状态。Any、端口范围原样保留。可用 DNAT 规则还可通过后端 IP 关联内网 CLB，并显示端口转换证据；DNS 不限定端口，仍需结合规则和监听器判断。

未采集 NAT、当前账号快照未包含网关、已采集但 EIP 无 DNAT 三种情况分别展示。静态 EIP 保留全部三个来源，其他账号的网关不会凭名称或备注推断映射。

## 服务器进程与 Nginx 采集

Settings 增加 **folidev 用户密码**。密码保存到同一私密凭据文件（0600），API 只返回配置状态和更新时间；空值保留原密码，密码前后空格原样保存。全量、每日、单独 JumpServer 同步均通过 Koko 终端检查本次授权资产，不直接绕过堡垒机 SSH。按资产独立检查，最多 6 台并行，优先尝试授权的 folidev 账号，失败再尝试其他授权 SSH 账号。folidev 密码仅用于该账号的交互登录及 sudo 提示，不嵌入命令行或快照。未配置密码仍可使用堡垒机托管凭据及免密 sudo。

JumpServer 页面新增登录结果、原因、检查时间、进程和 Nginx 明细，以及按登录状态、IP/主机名、最短运行天数筛选。365 天按钮按采集时的 `etimes >= 31536000` 统计；它反映当前存活进程，不是历史重启事件或去重应用数量。旧版本显示“未检查”，不会混用其他版本的结果。不能登录的资产会保留在新版本中，不使整个同步失败。

进程命令为 `LC_ALL=C TZ=UTC ps -eww`，保存 PID、PPID、用户、启动时间（UTC）、运行秒数及脱敏命令。排除内核线程和明确识别的系统守护进程，保留未知进程供核对，不能仅凭名称保证业务/系统分类绝对准确。先执行 `sudo -i` 进入 root 登录 shell，免密则直接进入，否则仅向密码提示输入一次 folidev 密码。密码错误立即中断 sudo，明确标记“sudo 密码错误”并跳过 Nginx 采集，不重试密码；普通账号仍采集可见进程并标记不完整。登录成功后采集失败仍标为可登录，并单独记录采集失败。

对每个可见的 Nginx master，使用 `/proc/<pid>/exe -T` 和其 `-c`、`-p`、`-g` 参数读取磁盘配置；不会 reload/restart。展开 `-T` 输出中的 include，保存 server_name、listen、location、proxy/fastcgi/grpc 等目标及 upstream 成员的主机与端口，支持 IPv6。动态变量、域名和 Unix socket 不伪造成 IP；缺失 include、配置解析或权限问题标记不完整。不保存原始终端记录。Nginx 原始配置按 `-T` 返回的文件清单逐个以 Base64 传回，保留精确文件字节、完整路径及 master 实例。原文单独存放在版本目录 `nginx-configs/`（0600），不打包进前端静态脚本；页面按需读取，支持查看和下载原始文件。其他来源独立同步沿用原 JumpServer 配置版本引用，切换历史版本不会混用配置。磁盘配置未必已经 reload 到进程；容器内进程、无法读取的命名空间及特殊进程标题可能无法完整定位 Nginx，此时显示无法确认。

### JumpServer 进程页签与时间筛选

页面默认显示“可登录”，另有“不可登录”页签（支持按当前搜索结果导出与展示同格式的 CSV），旧快照可查看“未检查”。可登录列表按进程展开，每行包含 IP、PID、进程名、启动时间、经过时长和详细命令。二级页签按 Tomcat、普通 Java Jar、其他 Java、Nginx、Kafka、Node 等分类；每页可选 25 / 50 / 100 条。输入命令、Jar 名、IP 或 PID 时回到全部类型搜索，多词搜索取交集。服务器详情保留 sudo 状态、Nginx 路由及原文下载。

当前运行时长为采集时 `elapsedSeconds` 加上采集后经过秒数，每分钟刷新，不使用 CPU 时间。它是进程持续存活假设下的推算，不能确认采集后的退出或重启；历史快照同样保留该说明。前四档累计筛选 ≤7 / ≤14 / ≤30 / ≤60 天，后三档为 (60,180]、(180,365]、>365 天，边界互不重复。切换类型、搜索或筛选时返回第一页。

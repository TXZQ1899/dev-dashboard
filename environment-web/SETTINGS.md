# 本地数据同步与历史版本

## Topology 版本生成与下载

Settings 页面提供“Topology 数据”区域。点击“基于当前数据版本生成 Topology”后，服务读取 `/data/current.json` 指向的当前激活版本，使用其中 DevOps、Codeup/仓库、JumpServer、ECS、CLB 和 NAT 数据；DNS 与 EIP 是静态导入数据，继续取自应用镜像。生成结果写入 `envscope-data` 卷的 `topology/<topology-id>/topology.json`，并保存 `version.json` 与 `topology.log`。每次点击都会创建新的 `topology-*` ID，不覆盖历史结果；`topology/latest.json` 只指向最近一次成功版本。

页面显示拓扑节点数、边数、待确认边、未解析或外部节点，并提供“查看 JSON”和“下载当前 Topology”链接。`/topology.json` 仅返回最近一次成功生成的数据卷版本；如果没有生成记录，会提示先到 Settings 生成，不会回退到镜像内旧拓扑。生成失败会保留 failed 版本目录和日志，且不改变 latest 指针。

打开 `http://localhost:3001/settings`。填写 DevOps、统一阿里云（ECS / Codeup / CLB / NAT 共用）、JumpServer 的 Cookie 后点击“保存并一键同步”。空输入保留已保存 Cookie，页面不会回显 Cookie。每日同步使用北京时间，默认时间为 18:00；需勾选并保存后开启。Docker 停机后当天错过的任务会在启动后补跑，每天最多自动启动一次；失败可手动重试。

同步范围：DevOps 全量应用、环境、分支、Push In 发布时间；Codeup 当前账号代码组和仓库及双向对比；JumpServer 当前账号授权资产和分组。上海 ECS 实例和 CLB 实例、监听器、证书绑定、转发规则及全部后端服务器组。ECS 项目名称目录沿用前版，实例及 ResourceGroupId 每次重新请求。旧 Codeup Cookie 自动迁移为 aliyun，阿里云采集器只读取这一份凭据。

## 持久化

Compose 使用命名卷 `envscope-data`，挂载到 `/data`。Cookie、运行记录和完整历史快照都在该卷；镜像中不包含 Cookie，也不挂载 Docker socket。

- `/data/secrets/cookies.json`：凭据，仅容器用户可读写（0600）。API 只返回是否配置和保存时间。
- `/data/versions/<时间戳>/`：每次同步新建，包含原始采集记录、六份页面快照、版本清单和日志。失败目录也保留，不能激活。
- `/data/current.json`：当前启用版本。
- `/data/schedule.json`：每日同步设置。
- `/data/job.json`：最近任务状态；容器重启后中断任务标为失败。

`docker compose up -d --build`、`docker compose restart` 和普通 `docker compose down` 不删除数据卷。`docker compose down -v` 会删除全部历史和 Cookie，不应用于日常更新。备份时备份整个数据卷，并将其作为包含凭据的私密备份保存。

## 激活机制

本地 Python 服务接收 Settings API 请求，调用复用的只读采集器，并代理现有网页。一次只允许一个同步或切换任务。采集完整后使用六份快照构建候选页面，在另一个内部端口启动并检查，成功后原子更新当前版本指针和转发目标。旧页面在准备期间仍可访问。失败保留旧版本；完整采集但构建失败的版本可再次尝试切换。

版本目录不保存 Cookie。临时凭据文件用于采集，任务结束自动删除。每次切换重新构建当前应用代码，避免旧版 UI 与旧数据强绑定；临时网页产物在容器可写层，不占历史数据卷。容器启动时按持久化的当前版本恢复网页，因此重建镜像不会意外覆盖用户选择的数据版本。

服务只发布到 `127.0.0.1:3001`，写操作要求同源 JSON 请求；此设计用于个人本机运行，不是多人远程服务。不要直接暴露到公网。

## 验证

`python3 -m unittest discover -s runtime -p 'test_*.py'` 检查凭据保护、无效版本拒绝、完整性、任务互斥、启动中断与失败回退。前端使用现有 `npm test` 和 TypeScript 检查。Docker 部署包含 Python 运行时和固定的采集脚本，不需要宿主机 Python 或 Codex 才能执行定时任务。

宿主机日常调度（旧 `run_daily_collection.py --update-web` 及其 `publish_daily.py` 发布流程）已移除，采集只通过本服务的 Settings 调度执行，避免与容器内调度重复。数据版本完全以 `envscope-data` 卷中的当前版本指针为准。

## CLB 全景

`/aliyun/clb` 展示 CLB IP → 监听器 → 默认路由或域名/路径规则 → 服务器组 → 后端 IP/端口，支持搜索及展开。域名不代表已验证 DNS。区分服务器 ID 去重数量和服务器组成员记录，跳转监听器单独显示。旧版本未采集 CLB 时展示空状态，不借用当前或镜像中的 CLB 数据。任一采集源失败不激活新版本。

## NAT / DNAT 同步与 EIP 后端

Settings 的“单独同步 NAT”使用统一阿里云 Cookie，采集上海区域当前账号可见的全部 NAT 网关及各 DNAT 表。全量和每日同步也包含 NAT。只调用 DescribeNatGateways、DescribeForwardTableEntries，核对分页总数、唯一 ID 和网关／表清单稳定性。完整采集并构建成功后才启用新版本；失败保留当前版本。单独同步任何来源都保留其他来源的快照。

`nat-snapshot.json` 随历史版本保存；旧版本没有此文件时显示未采集，不借用其他版本数据。CLB 页增加 NAT 视图和 EIP 后端详情。ECS 类型通过静态 CSV 的绑定实例 ID 精确查询当前版本 ECS 的内网／公网 IP；NAT 类型同时匹配网关 ID 与 EIP 地址，展示每条 DNAT 的协议、公网 IP/端口、后端 IP/端口和状态。Any、端口范围原样保留。可用 DNAT 规则还可通过后端 IP 关联内网 CLB，并显示端口转换证据；DNS 不限定端口，仍需结合规则和监听器判断。

未采集 NAT、当前账号快照未包含网关、已采集但 EIP 无 DNAT 三种情况分别展示。静态 EIP 保留全部三个来源，其他账号的网关不会凭名称或备注推断映射。

## 服务器进程与 Nginx 采集

Settings 增加 **folidev 用户密码**。密码保存到同一私密凭据文件（0600），API 只返回配置状态和更新时间；空值保留原密码，密码前后空格原样保存。全量、每日、单独 JumpServer 同步均通过 Koko 终端检查本次授权资产，不直接绕过堡垒机 SSH。按资产独立检查，最多 6 台并行，优先尝试授权的 folidev 账号，失败再尝试其他授权 SSH 账号。folidev 密码仅用于该账号的交互登录及 sudo 提示，不嵌入命令行或快照。未配置密码仍可使用堡垒机托管凭据及免密 sudo。

JumpServer 页面新增登录结果、原因、检查时间、进程和 Nginx 明细，以及按登录状态、IP/主机名、最短运行天数筛选。365 天按钮按采集时的 `etimes >= 31536000` 统计；它反映当前存活进程，不是历史重启事件或去重应用数量。旧版本显示“未检查”，不会混用其他版本的结果。不能登录的资产会保留在新版本中，不使整个同步失败。

进程命令为 `LC_ALL=C TZ=UTC ps -eww`，保存 PID、PPID、用户、启动时间（UTC）、运行秒数及脱敏命令。排除内核线程和明确识别的系统守护进程，保留未知进程供核对，不能仅凭名称保证业务/系统分类绝对准确。先执行 `sudo -i` 进入 root 登录 shell，免密则直接进入，否则仅向密码提示输入一次 folidev 密码。密码错误立即中断 sudo，明确标记“sudo 密码错误”并跳过 Nginx 采集，不重试密码；普通账号仍采集可见进程并标记不完整。登录成功后采集失败仍标为可登录，并单独记录采集失败。

对每个可见的 Nginx master，使用 `/proc/<pid>/exe -T` 和其 `-c`、`-p`、`-g` 参数读取磁盘配置；不会 reload/restart。展开 `-T` 输出中的 include，保存 server_name、listen、location、proxy/fastcgi/grpc 等目标及 upstream 成员的主机与端口，支持 IPv6。动态变量、域名和 Unix socket 不伪造成 IP；缺失 include、配置解析或权限问题标记不完整。不保存原始终端记录。Nginx 原始配置按 `-T` 返回的文件清单逐个以 Base64 传回，保留精确文件字节、完整路径及 master 实例。原文单独存放在版本目录 `nginx-configs/`（0600），不打包进前端静态脚本；页面按需读取，支持查看和下载原始文件。其他来源独立同步沿用原 JumpServer 配置版本引用，切换历史版本不会混用配置。磁盘配置未必已经 reload 到进程；容器内进程、无法读取的命名空间及特殊进程标题可能无法完整定位 Nginx，此时显示无法确认。

### JumpServer 进程页签与时间筛选

页面默认显示“可登录”，另有“不能登录”页签，旧快照可查看“未检查”。可登录列表按进程展开，每行包含 IP、PID、进程名、启动时间、经过时长和详细命令。二级页签按 Tomcat、普通 Java Jar、其他 Java、Nginx、Kafka、Node 等分类；每页可选 25 / 50 / 100 条。输入命令、Jar 名、IP 或 PID 时回到全部类型搜索，多词搜索取交集。服务器详情保留 sudo 状态、Nginx 路由及原文下载。

当前运行时长为采集时 `elapsedSeconds` 加上采集后经过秒数，每分钟刷新，不使用 CPU 时间。它是进程持续存活假设下的推算，不能确认采集后的退出或重启；历史快照同样保留该说明。前四档累计筛选 ≤7 / ≤14 / ≤30 / ≤60 天，后三档为 (60,180]、(180,365]、>365 天，边界互不重复。切换类型、搜索或筛选时返回第一页。

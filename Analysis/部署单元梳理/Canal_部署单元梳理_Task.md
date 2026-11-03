# Canal 部署单元梳理 Task

状态：实施中；已建档 9 个 Server（其中 1 个编组待确认），另有 5 个候选因成员或配置冲突待核实。共同规则见 [00-共同约束](00-共同约束.md)。

## 目标与输出

- 创建技术类型为 `Canal`、单元类型建议为 `MIDDLEWARE` 的部署单元；名称为 `Canal_cluster_序号`。明确独立部署同样使用此命名，并有一个节点。
- 输出候选进程清单、应用关联排除清单、拟创建单元与节点、证据和置信度、待核实项、覆盖与失败情况；环境初始为「待识别」。类型归类为设计建议，实施时须与现有目录枚举及产品口径核对。

## 专项核查和归并任务

- 识别 Canal Server 进程及配置，排除客户端、adapter 和其他组件；识别同服务器多个独立 Server 及容器。
- 确认版本和部署模式、server 身份、实际配置、协调服务的 namespace/节点声明及运行状态；区分 server 节点、destination、客户端及被订阅数据库。涉及配置时只保留必要字段并遮盖数据库密码。
- 同一协调范围不一定等于同一逻辑集群：结合明确 server 编组、destination 的 HA/选主关系与运行证据；一个 server 可承载多个 destination，不能按 destination 数量重复创建服务实例。仅订阅同一 MySQL 不证明集群。
- 明确无集群/HA 的独立 server 可单节点；配置未生效、协调信息不可读或 destination 分组重叠时保留待核实，并记录需要人工决定的单元边界。

## 执行步骤

1. 从已完成快照建立本类型候选，记录每台资产的采集状态、进程与直接/推断端口；评估 DevOps 关联，排除已关联项，冲突保留待核实。
2. 确认版本和部署方式，按上述专项规则定位有效配置，实施最小只读检查；分别保存观察时间、节点身份、Endpoint、证据来源与限制。
3. 汇总节点视图，识别单节点、多节点和未解析成员；给出证据与置信度，列出冲突、无法访问主机和可能的同机多实例，生成导入预览。
4. 核对预览并确认后，事务写入单元、待识别环境、实例、来源观察及变更；重复执行保持身份和序号稳定，失败回滚。
5. 验证容器运行、持久化和结果可追溯；输出覆盖范围与剩余未解决项，不将未观察到等同停机。

## 专项测试与验收

- 两个 server 形成同一已核实 HA 编组 → 一个单元；仅订阅同一数据库的不同 server 不合并。
- 多 destination、协调服务不可读、adapter 混淆及明确独立 server 均覆盖。

- 执行共同约束中的通用测试；检查不同证据环节置信度独立、敏感信息遮盖、较旧及不完整观察不能误更新、预览与落库一致。
- 验收时逐条核对单元名称、环境、节点、证据、未解析原因及排除原因；不可将无法证明的集群或独立部署写成确定结果。

## 2026-11-02 候选初筛及本地单节点建档

活动版本 `20261102-133150-818922` 的 JumpServer 快照采集于 09:47:57，DevOps 快照采集于 13:36:15（北京时间），二者并非同步。440 台资产中 206 台进程采集完整、2 台部分采集；用户已确认的 232 台无法登录且未采集资产按固定资产 ID 清单排除，原始采集状态不变。

按 `com.alibaba.otter.canal.deployer.CanalLauncher` 识别 14 个 Server 候选；另识别 8 个 Admin 和 2 个 Adapter 进程，不作为 Server。`10.58.46.136` 的 `scrm-canal.jar` 含 Canal 字样，实时核对进程身份和 JAR 清单后发现其入口为业务应用 `net.trueland.tcloud.scrm.ScrmCanalApplication`，不是 Canal Server 启动类，未计入 Server；其业务用途及依赖仍未核实。该规则不能排除其他启动方式。

对 14 个 Server 的进程、监听和启动配置执行只读检查。以下两个 Server 的主配置文件在当前进程启动前最后修改；`canal.zkServers` 为空、未声明 Admin、JVM 未观察到其他 Canal 属性覆盖、未发现 Canal 环境变量，且 PID、挂载及网络命名空间与宿主机一致。配置指定 `spring/file-instance.xml`，实际文件的类名、导入和哈希经核对，包含文件型元数据管理；[Canal 官方文档](https://github.com/alibaba/canal/wiki/AdminGuide/17a4500e8984e2c51af5e6974cc6eec18de4f866) 将文件型实例描述为不支持 Server HA 的模式。两台主机在本版本 DevOps 快照中无同 IP 部署记录。

| 单元 | 资产 | 直接观察到的监听 | destination |
|---|---|---|---|
| Canal_cluster_1 | 10.179.2.14 | 127.0.0.1:11110、*:11112/TCP | dw_dms |
| Canal_cluster_2 | 10.58.46.186 | 127.0.0.1:11110、*:11112/TCP | dw_dms |

按 `canal-local-single-v1` 分别建档，环境 `UNIDENTIFIED`、覆盖 `PARTIAL`、归组置信度 `INFERRED`。相同 destination 或订阅对象不证明同一 Server 编组；回环和通配地址原样保留，不将配置中的 11111 写成实际监听。两者当前进程连接到了不同的 RabbitMQ 和 MySQL 端点；这些连接只作为时点观察，不推断历史、完整上下游或实时健康。

12 个 Server 候选的本地配置包含 Admin 地址；其中 `10.179.4.47` 还声明 ZooKeeper 地址。Admin 注册集群字段可能为空，也可能是注册时的线索，不能替代 Admin 当前配置与成员视图。以下归属均待核实：

| 资产 | 快照 PID | 配置中的 Admin | 注册集群线索 |
|---|---:|---|---|
| 10.58.9.90 | 22264 | 10.58.1.34:8089 | 空 |
| 10.179.2.144 | 3275 | 10.179.1.224:8089 | 空 |
| 10.179.1.224 | 7036 | 10.179.1.224:8089 | uatProcessTest |
| 10.58.1.34 | 26358 | 10.58.1.34:8089 | uat |
| 10.179.4.148 | 28118 | 10.179.4.148:8089 | 空 |
| 10.58.6.170 | 31140 | 10.179.4.63:8089 | 空 |
| 10.58.16.36 | 1721 | 10.58.16.36:8089 | prd |
| 10.58.9.76 | 9942 | 10.58.1.34:8089 | fat |
| 10.179.4.47 | 16584 | 10.179.4.63:8089 | 未声明 |
| 10.179.4.63 | 3830 | 127.0.0.1:8089 | 空 |
| 10.179.2.24 | 12283 | 10.179.1.224:8089 | ftriptest |
| 10.179.2.30 | 734 | 10.179.2.10:8089 | ftripuat |

其中 `10.179.1.224`、`10.58.1.34`、`10.179.2.24` 和 `10.179.2.30` 另有缺端口的同机 DevOps 应用记录，应用归属也未排除。RabbitMQ 任务中针对同机 RabbitMQ 的用户确认不自动扩展到 Canal。

初筛及只读报告位于数据卷 `checks/canal-review/`，包含来源版本、进程观察时间、脱敏配置字段和访问限制；导入组合证据为 `evidence/34bfdf2c8e9310ee0335f7b6911fcf6dd0d6b2b75ca9ffff898b16b33f8ded25.json`。本批次已验证重复导入幂等；未获取或保存 Admin、数据库凭据。下一步需通过适当的只读访问核实 Admin 当前 Server、集群和 destination HA 配置，分别检查已知应用关联与未覆盖成员。

## 2026-11-02 Admin 与 ZooKeeper 补充核查

按用户提供的账号只读访问 Canal Admin，五个管理端完成 Server、集群和 destination 清单分页核查，记录总数和完整性；`10.58.16.36:8089` 从应用容器不可达，但目标服务器本机 HTTP 端口可响应。只保存成员及配置白名单字段，不保存账号、密码、token 或配置正文。通过只读 ZooKeeper 协议核对管理端声明的命名空间、Server 注册、destination 成员和选主地址；未授权或不存在的节点保留原始错误码，不视为空清单。

| 单元 | 资产 | 编组依据 | 核查范围 |
|---|---|---|---|
| Canal_cluster_3 | 10.58.9.90 | Admin 将 Server 登记为单机，主配置 ZooKeeper 为空，11 个 destination 归属于该 Server | 当前单机视图；不代表历史或未来状态 |
| Canal_cluster_4 | 10.179.2.144 | Admin `msg` 集群与 ZooKeeper Server 注册和 destination 选主一致 | 当前注册 1 个 Server；31 个 Admin destination 中 28 个与运行节点一致，3 个在 Admin 为未运行 |
| Canal_cluster_5 | 10.179.4.148 | Admin `order` 集群与 ZooKeeper Server 注册及 3 个 destination 选主一致 | 当前注册 1 个 Server；命名空间另有未分配的空成员记录 |
| Canal_cluster_6 | 10.58.9.76 | Admin `fat` 集群与 ZooKeeper Server 注册及 4 个 destination 选主一致 | 当前注册 1 个 Server；命名空间另有空成员的历史记录 |

以上归组均为 `INFERRED`，环境待识别，覆盖 `PARTIAL`。Cluster_4–6 是 Admin 中声明的集群，当前各只观察到一个注册 Server，不据此宣称配置了单机模式或没有历史失联成员。Cluster_3 使用规则 `canal-admin-single-v1`，组合证据 `evidence/139a05a893bff550f5b82250eb5beb8a2953132a0d99e34edccaa67d55f710e7.json`；Cluster_4–6 使用 `canal-admin-zk-v1`，组合证据 `evidence/f289c1063ed3535a8718afbf920ab121d1b06e41211168ba3ae04e1de66f2eb0.json`。

仍有 8 个初筛 Server 未建档：

- `10.179.1.224`：Admin 登记同组还有 `10.58.9.50`，该资产可登录，不属于用户确认的下线清单；对后者实时进程检查未发现 CanalLauncher 候选，但这不证明资产或历史成员已下线。Admin 主配置未声明 ZooKeeper，但集群记录声明了命名空间，且同机应用归属待核实。
- `10.58.1.34`：Admin 和 ZooKeeper 的 Server 注册可见，但其 14 个 Admin destination 均未观察到当前 ZooKeeper 成员，同机 `dolphin-open` 应用端口缺失。
- `10.58.6.170` 和 `10.179.4.63`：Admin 分别登记为单机和集群，二者共享 ZooKeeper 命名空间；当前 `fotour_new` 与集群其他 destination 的成员不同，不能仅按相同命名空间合并。`10.179.4.63` 另有 ZooKeeper 注册的 `user_db` 不在该 Admin 集群的当前 destination 清单中；`10.58.6.170` 同机 `fotour-internalapi` 的归属仍需排除。
- `10.58.16.36`：Admin 在服务器本机可响应，但从应用容器不可达，未获取成员或生效配置。
- `10.179.4.47`：Admin 配置声明带 `/canal` 的 ZooKeeper 命名空间，但该路径未发现 Server 注册；其本地启动配置声明不带该前缀，根路径中观察到该节点及 `public_order_center`、`example` 注册。前者有当前运行节点，后者未读到 running 节点。来源存在冲突，暂不决定有效编组。
- `10.179.2.24`：Admin 集群记录的 ZooKeeper 地址与 Admin 主配置不一致，同机有缺端口 DevOps 应用记录。
- `10.179.2.30`：Admin 与 ZooKeeper 的注册和 destination 选主一致，但同机有缺端口 DevOps 应用记录，应用归属未排除。

另有已建档的 `Canal_cluster_1`（10.179.2.14）与 `10.179.2.10` Admin 中一条旧 Server 记录冲突：Admin 记录属于另一个集群且状态为 `-1`，当前进程则使用 2025 年后启动的无 ZooKeeper 本地配置。该差异已保留待复核，不自动覆盖或删除既有单元。

## 2026-11-02 用户确认后的补充建档

用户明确确认，`10.179.1.224`、`10.58.1.34`、`10.179.2.24`、`10.179.2.30` 的同机 DevOps 应用，以及 `10.58.6.170` 的 `fotour-internalapi`，均不负责部署或管理对应的 Canal Server。该确认仅应用于来源版本 `20261102-133150-818922` 中这五个资产、对应环境和 249 条应用记录，保存于数据卷 `checks/canal-review/user-canal-app-scope-20261102-133150-818922.json`；不扩展到其他资产或未来快照。

| 单元 | Server | 依据与限制 |
|---|---|---|
| Canal_cluster_7 | 10.179.2.30 | Admin `ftripuat` 集群与 ZooKeeper Server 注册、`tem_expense_platform` 成员及选主一致；当前观察到一个注册 Server |
| Canal_cluster_8 | 10.58.6.170 | Admin 登记为单机；与 `10.179.4.63` 共享 ZooKeeper 命名空间，但 `fotour_new` 当前仅由本 Server 注册并选主，Admin 分配与其他 destination 不重叠；不因此合并 |
| Canal_cluster_9 | 10.58.16.36 | 用户明确要求先为该 IP 单独建档；Admin 从应用容器不可达，节点身份已按进程、配置参数和监听核查，编组待确认 |

Cluster_7 和 Cluster_8 的归组为 `INFERRED`，组合证据 `evidence/d782f5d9ef94f4166c83938cf01ad26476c6762769634cdb90797b4067df95e9.json`。Cluster_9 为 `groupingState=PENDING`、`groupingConfidence=UNKNOWN`、`groupingBasis=USER_PER_NODE_PENDING`；单节点仅是用户指定的目录展示，不能解释为已确认独立部署。该节点的 destination 和 Server 模式均未知，目录保留 `null` 与未知状态；对先前误用空列表的观察追加了修正观察和变更记录，原观察未删除。用户指令及核查证据保存在 `evidence/759d63576579268ffc930eb00d55b941942d1204cc9f471a0c1e3fdc97a59809.json`。

目前仍未建档的 5 个候选为 `10.179.1.224`（Admin 存在另一未确认成员且协调配置冲突）、`10.58.1.34`（未观察到运行中 destination）、`10.179.4.47`（Admin 与本地 ZooKeeper 命名空间冲突）、`10.179.4.63`（共享命名空间且有未在 Admin 当前清单中的 destination）、`10.179.2.24`（Admin 集群记录与生效配置的协调地址冲突）。用户对应用归属的确认不消除这些独立的成员和配置冲突。Cluster_9 自身的 Admin 编组、成员及 destination 仍待后续有访问途径时复核。

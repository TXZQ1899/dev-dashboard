# Env-Scope 路由核查数据位置

所有路径均在 `envscope-local` 容器内，根目录 `/data`（可用 `ENVSCOPE_DATA` 覆盖）。容器通过 Docker context `lenovo` 访问。

## 快照

```text
/data/current.json                     # {"id": "<versionId>", "activatedAt": ...}
/data/versions/<versionId>/
├── jumpserver-snapshot.json           # 资产 + inspection
├── nginx-configs/<sha256(assetId)>.json
└── ...
```

`jumpserver-snapshot.json` 顶层为 `collectedAt`、`groups`、`assets`。每台资产关注 `inspection`：

| 字段 | 用途 |
|---|---|
| `checkedAt` | 采集时间 |
| `loginStatus` / `processStatus` / `portStatus` | 采集完整性；`failed` 不能当作“无监听” |
| `processes[]` | `pid`、`user`、`name`、`command`、`startedAt`、`elapsedSeconds`、`ports[]` |
| `appPorts[]` | `app`、`kind`、`port`、`addresses`、`pids`、`candidatePids`、`ownershipConfidence` |
| `nginxStatus` | `complete` / `not_running` 等 |
| `nginxRoutes[]` | 规范化路由，优先于原始文本 |
| `configurationCount` / `configurationVersion` | 原始配置归档线索 |

`nginxRoutes[]` 的关键字段：

- `domains[]`、`listen[]`、`uri`、`directive`、`target`
- `upstream`：upstream 名称或直接目标
- `backends[]`：`{host, port, resolution}`；`resolution` 为 `ip` / `hostname` / `dynamic` / `unix`
- `context`：`http` / `stream`

只匹配 `backends[].host` 等于目标 IP 的路由。`resolution=hostname` 时不要自行 DNS 解析后冒充静态证据。

## 原始 Nginx 配置

```text
/data/versions/<versionId>/nginx-configs/<sha256(assetId)>.json
```

结构：`assetId`、`collectedAt`、`files[]`；每个文件含 `path`、`instance`、`content`、`base64`、`bytes`。

定向读取，不整份输出。常用查询：

- 字面量 `10.58.6.2:3600`
- `upstream <name> { ... server 10.58.6.2:3600; ... }`

限制：按字面 IP 匹配；upstream 用主机名或变量时可能漏检，需在报告中说明。

## Kong 配置导出

```text
/data/download_config/Kong/<cluster>/
├── manifest.json     # unit、ip、nodeId、snapshotVersion、collections
├── routes.json
├── services.json
├── upstreams.json
└── targets.json
```

`targets.json` 有两种形态，必须都兼容：

1. 分组形态：`[{upstreamId, upstreamName, targets: [{target, weight, ...}]}]`
2. 扁平形态：`[{target, weight, upstream: {id}, ...}]`

关联链：

```text
targets.target (IP:port)
  → upstream.id / upstream.name
  → services.host == upstream.name
  → routes.service.id == services.id
```

`manifest.collections` 记录各集合是否 `complete`，报告中保留导出时间 `finishedAt` 与 `snapshotVersion`。

限制：这是配置导出，不是实时 Admin API；导出后可能已变更。连接采样文件 `/data/checks/kong-review/workers.json` 只能作为运行时旁证，不能单独否定配置中的 target。

## 进程复核

```text
/data/checks/secondpass-app-<scanDate>-<versionId>-r<n>/
├── <assetId>-host.json
└── <assetId>-<pid>-<hash>.json
```

`host.json` 含 `scanAt`、`status`、`processes`、`businessCandidates`、`elevation`；逐进程文件含 `pid`、`type`、`name`、`startedAt`、`app`、`businessClass`、`observedAt`。该目录通常比当前快照新，引用时注明 `scanAt`。

## 拓扑（可选）

```text
/data/topology/latest.json
/data/topology/<topologyId>/topology.json
```

仅在需要 DNAT / CLB / 已构建图边时使用。`latest.json` 只是指针；按 `nodes[].id` 定向查找，不要整份加载后打印。Nginx 边类型为 `FORWARDS_TO`，来源节点 id 形如 `nginx-upstream:<assetId>:<hash>`。

## 判定口径

- `EXACT`：Nginx 规范化 backends 直接给出 `IP:port`；Kong target 直接等于 `IP:port`。
- `INFERRED`：由进程命令、端口归属候选或容器映射推断，必须标注依据。
- `AMBIGUOUS`：多个候选无法唯一匹配，不能选一个作为结论。
- `UNKNOWN`：采集失败或缺失；不能写成“没有”。

## 常见陷阱

- `name` 字段是 `comm`，通常截断到 15 字符，`node /home/tcop` 不是完整路径；以 `command` 为准。
- `appPorts` 按（应用名, 端口）归并，同一端口可能有多行或推断候选。
- `127.0.0.1` 绑定不等于对外监听；检查 `addresses`。
- 同一 upstream 可能同时包含目标机与另一台机器，目标机只是部分流量入口。
- 配置存在失效后端（有转发、无监听）时单独列为风险，不改写原始数据。

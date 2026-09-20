# EnvScope 资源采集数据 Schema 整理

- 整理对象：`environment-web` 当前使用的快照数据与 `/resources` 宽表计算结果。
- 说明：字段类型按 JSON 实际数据和代码定义归纳；`?` 表示可空/条件存在；`[]` 后的 `T` 表示数组元素类型。
- 样本原则：每个实体保留 1 条最小但有代表性的记录，数组适当截短，避免泄露大面积内部清单。
- 当前 checked-in 快照状态：DevOps 545 个应用、Codeup/仓库 556 条、ECS 204 台、JumpServer 421 台、DNS 481 条、EIP 53 条。当前仓库中的 `clb-snapshot.json` 和 `nat-snapshot.json` 是 `available: false` 占位，但 CLB 有 2026-09-14 原始导出，NAT 结构由采集器定义；二者样本按实际/规范化结构展示。

## 总览

| 数据域 | 主要文件 | 主键 | 实体数量 | 与宽表的关联 |
|---|---|---|---:|---|
| DevOps 应用环境 | `environment-web/lib/snapshot.json` | `apps[].id`；部署记录建议 `appId + env + ip + deploy` | 545 app / 1947 环境部署记录 | 提供 `sources.devops`、应用/环境/端口/仓库关联 |
| Codeup / Local GitLab 仓库 | `environment-web/lib/repositories.json` | `repos[].id`（Git URL 哈希） | 556 repo / 15 group | 提供仓库 URL 与代码组归属 |
| DNS 解析 | `environment-web/lib/dns-snapshot.json` | `records[].id = zone:row` | 481 | A/AAAA 地址进入宽表 `sources.dns` |
| EIP | `environment-web/lib/eip-snapshot.json` | `records[].id` | 53 | EIP 地址进入宽表 `sources.eip` |
| NAT 网关 / DNAT | `environment-web/lib/nat-snapshot.json` | `gateways[].id`；`gateways[].entries[].id` | 当前 checked-in 为空占位 | 外网/内网 IP 均进入宽表 `sources.nat` |
| ECS | `environment-web/lib/ecs-snapshot.json` | `instances[].id` | 204 | 私网/公网 IP 进入宽表 `sources.aliyun` |
| CLB | `environment-web/lib/clb-snapshot.json` | `instances[].id` | 当前 checked-in 为空占位；原始导出 19 台 | CLB IP 与后端 IP 进入宽表 |
| JumpServer 资产 / Nginx | `environment-web/lib/jumpserver-snapshot.json`；版本目录 `nginx-configs/<sha256(assetId)>.json` | `assets[].id`；分组 `groups[].id` | 421 asset / 20 group | 资产 IP 进入宽表 `sources.jumpserver` |
| 宽表 | 运行时由 `environment-web/lib/resource-comparison.ts` 生成，不单独落盘 | `ip` | 610 | 按 IP 汇总以上来源 |

## 1. DevOps 应用环境

### 根对象

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `collectedAt` | `string` | 是 | ISO-8601 时间戳，含时区 |
| `source` | `string` | 是 | 采集版本/来源标识 |
| `scope` | `string` | 是 | 采集范围，当前为 `all` |
| `complete` | `boolean` | 是 | 采集完整性声明 |
| `apps` | `Application[]` | 是 | 应用清单 |

### `Application`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `id` | `string` | 是 | DevOps 应用 ID |
| `name` | `string` | 是 | 应用名 |
| `http` | `string` | 是 | HTTP 名称/路径 |
| `port` | `string` | 是 | 应用端口；空字符串表示未提供 |
| `repository` | `string` | 是 | DevOps 记录的仓库地址 |
| `branch` | `string` | 是 | 应用默认/声明分支 |
| `envs` | `Record<'TEST' \| 'SIMULATION' \| 'PRODUCT', Deployment[]>` | 是 | 三环境部署记录 |
| `capturedAt` | `string` | 是 | 该应用采集时间 |

### `Deployment`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `ip` | `string` | 是 | 部署服务器 IP |
| `deploy` | `string` | 是 | 部署实例 ID |
| `config` | `string` | 是 | 配置 ID；可为空 |
| `status` | `string` | 是 | DevOps 采集状态 |
| `error` | `string` | 是 | 错误信息；成功时为空 |
| `port` | `string` | 是 | 部署端口 |
| `branch` | `string` | 是 | 实例实际/推断分支 |
| `branchSource` | `string` | 是 | 分支推断依据 |
| `repository` | `string` | 是 | 该部署使用的仓库地址 |
| `lastPublishedAt` | `string` | 是 | 最近发布时间，`YYYY-MM-DD HH:mm:ss`；可为空 |
| `publishStatus` | `string` | 是 | 最近 PushIn 状态 |
| `publishTimeSource` | `string` | 是 | 发布时间来源说明 |
| `pushIn` | `PushIn[]` | 是 | PushIn 步骤明细；可为空数组 |

### `PushIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `build_id` | `string` | 是 | 构建 ID |
| `resource_id` | `string` | 是 | DevOps 资源 ID |
| `action_id` | `string` | 是 | 动作 ID |
| `operation` | `string` | 是 | 动作名，当前主要是 `PushIn` |
| `resource_name` | `string` | 是 | 资源名 |
| `ip` | `string` | 是 | 目标 IP |
| `startTime` | `string?` | 条件 | 开始时间 |
| `endTime` | `string?` | 条件 | 结束时间 |
| `status` | `string` | 是 | 步骤状态 |
| `toActionRunId` | `number?` | 条件 | 关联动作运行 ID |

### 样本

```json
{
  "id": "1913",
  "name": "fosun-aggregation-processor",
  "http": "/aggregation/processor",
  "port": "9015",
  "repository": "https://codeup.aliyun.com/61a9cf2465322dff5b9e01f2/fcrm/fosun-aggregation-center.git",
  "branch": "master",
  "envs": {
    "TEST": [{
      "ip": "10.58.10.91",
      "deploy": "19130",
      "config": "18551",
      "status": "成功",
      "error": "",
      "port": "9015",
      "branch": "release-test",
      "branchSource": "测试实例配置 versionControlName",
      "repository": "https://codeup.aliyun.com/61a9cf2465322dff5b9e01f2/fcrm/fosun-aggregation-center.git",
      "lastPublishedAt": "2026-08-13 14:10:00",
      "publishStatus": "SUCCESS",
      "publishTimeSource": "Push In endTime",
      "pushIn": [{
        "build_id": "1386813",
        "resource_id": "38272",
        "action_id": "24",
        "operation": "PushIn",
        "resource_name": "TEST-fosun-aggregation-processor-APP-19130",
        "ip": "10.58.10.91",
        "startTime": "2026-08-13 14:10:00",
        "endTime": "2026-08-13 14:10:00",
        "status": "SUCCESS",
        "toActionRunId": 3629940
      }]
    }]
  },
  "capturedAt": "2026-09-07T18:20:49.520091+08:00"
}
```

## 2. Codeup / Local GitLab 仓库

### 根对象

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `source` | `string` | 是 | 快照来源版本 |
| `snapshotDate` / `accessDate` | `string` | 是 | 快照和权限确认时间 |
| `complete` | `boolean` | 是 | 完整性声明 |
| `scope` | `string` | 是 | 当前账号可见范围 |
| `groups` | `CodeGroup[]` | 是 | Codeup 代码组 |
| `repos` | `Repository[]` | 是 | 归一化仓库清单 |
| `comparison` | `object` | 是 | 差异汇总：`both`、`codeup_only`、`devops_only` |
| `invalidReferences` | `InvalidReference[]` | 是 | DevOps 无效仓库引用 |
| `devopsCollectedAt` / `codeupCollectedAt` | `string` | 是 | 两来源采集时间 |

### `Repository`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `id` | `string` | 是 | Git URL 规范化后哈希 |
| `codeupId` | `string` | 是 | Codeup 仓库 ID；非 Codeup 域名时为空 |
| `name` | `string` | 是 | 仓库名 |
| `path` | `string` | 是 | 组/命名空间 + 仓库 |
| `url` | `string` | 是 | 原始 Git URL |
| `groupName` | `string` | 是 | 展示用组名 |
| `groupId` | `string` | 是 | 关联 `groups[].id`；可为空 |
| `access` | `string` | 是 | 访问结论 |
| `reason` | `string` | 是 | 访问结论依据 |
| `match` | `string` | 是 | DevOps/Codeup 匹配方式 |
| `apps` | `{ id, name, branch }[]?` | 条件 | 关联 DevOps 应用 |
| `inCodeup` | `boolean` | 是 | 是否出现在 Codeup/Local GitLab 清单 |
| `inDevops` | `boolean` | 是 | 是否被 DevOps 引用 |
| `difference` | `string` | 是 | `both` / `codeup_only` / `devops_only` |
| `branches` | `number?` | 条件 | 可确认分支数 |
| `mergeRequests` | `number?` | 条件 | 可确认 MR 数 |
| `commits` | `number?` | 条件 | 统计提交数 |
| `namespaceId` | `string` | 条件 | Codeup 命名空间 ID |
| `updatedAt` | `string` | 是 | 仓库更新时间；可为空 |
| `description` | `string` | 是 | 描述；可为空 |

### `CodeGroup`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `id` | `string` | 是 | Codeup 组 ID |
| `name` | `string` | 是 | 组名 |
| `description` | `string` | 是 | 描述；可为空 |
| `path` | `string` | 是 | Codeup 路径 |
| `total` | `number` | 是 | 组内仓库总数 |
| `listed` | `boolean` | 是 | 是否已进入详细仓库清单 |

### 样本

```json
{
  "groups": [{
    "id": "1022919",
    "name": "tcg-spark",
    "description": "分销平台",
    "path": "61a9cf2465322dff5b9e01f2/tcg-spark",
    "total": 9,
    "listed": true
  }],
  "repos": [{
    "id": "2c32b56e03c1f94934cd",
    "codeupId": "",
    "name": "bank-session-portal",
    "path": "Fotel/bank-session-portal",
    "url": "https://code.aliyun.com/Fotel/bank-session-portal.git",
    "groupName": "Fotel",
    "groupId": "",
    "access": "无法确认",
    "reason": "非 Codeup 域名，不在本次 Codeup 清单范围",
    "match": "完整地址未匹配",
    "apps": [{ "id": "1652", "name": "bank-session-portal", "branch": "master" }],
    "inCodeup": false,
    "inDevops": true,
    "difference": "devops_only",
    "branches": null,
    "mergeRequests": null,
    "commits": null,
    "namespaceId": null,
    "updatedAt": "",
    "description": ""
  }],
  "invalidReferences": [{ "appId": "1865", "appName": "cat", "reason": "仓库路径无效" }]
}
```

## 3. DNS 解析

### 根对象

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `zones` | `string[]` | 是 | 静态导出的 Zone |
| `sources` | `{ file, zone }[]` | 是 | Excel 来源 |
| `records` | `DnsRecord[]` | 是 | 解析记录 |

### `DnsRecord`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `id` | `string` | 是 | `zone:row` |
| `zone` | `string` | 是 | DNS Zone |
| `name` | `string` | 是 | 记录名/FQDN |
| `type` | `string` | 是 | A、AAAA、CNAME、MX 等 |
| `value` | `string` | 是 | 记录值 |
| `line` | `string` | 是 | 解析线路 |
| `status` | `string` | 是 | 启用/暂停 |
| `ttl` | `number` | 是 | TTL 秒数 |
| `weight` | `number?` | 条件 | 权重；仅权重策略存在 |
| `policy` | `string` | 是 | 解析策略 |
| `remark` | `string` | 是 | 备注；可为空 |
| `source` | `string` | 是 | 来源 Excel 文件 |
| `row` | `number` | 是 | Excel 原始行号 |

### 样本

```json
{
  "id": "folidaymall.com:2",
  "zone": "folidaymall.com",
  "name": "psimage.folidaymall.com",
  "type": "CNAME",
  "value": "psimage.folidaymall.com.w.cdngslb.com",
  "line": "默认",
  "status": "启用",
  "ttl": 600,
  "weight": 1,
  "policy": "权重",
  "remark": "",
  "source": "folidaymall.com_1789445988602.xlsx",
  "row": 2
}
```

## 4. EIP

### 根对象

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `region` | `string` | 是 | 当前 `cn-shanghai` |
| `snapshotDate` | `string` | 是 | CSV 导出日期 |
| `sources` | `{ file, owner, count }[]` | 是 | 三份 CSV 来源及数量 |
| `records` | `EipRecord[]` | 是 | EIP 清单 |

### `EipRecord`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `id` | `string` | 是 | EIP 实例 ID |
| `name` | `string` | 是 | EIP 名称 |
| `protection` | `string` | 是 | 安全保护属性 |
| `tags` | `string` | 是 | 标签；CSV 空值以 `-` 或空串表示 |
| `ip` | `string` | 是 | 公网 IP |
| `bindingType` | `string` | 是 | 绑定资源类型 |
| `bindingId` | `string` | 是 | 绑定资源 ID |
| `bindingName` | `string` | 是 | 绑定资源名称 |
| `status` | `string` | 是 | 分配状态 |
| `bandwidth` | `string` | 是 | 带宽，含单位 |
| `network` | `string` | 是 | 线路类型 |
| `bandwidthPackage` | `string` | 是 | 带宽包状态 |
| `poolId` | `string` | 是 | 资源池 ID |
| `billing` | `string` | 是 | 计费模式 |
| `allocatedAt` | `string` | 是 | 分配时间 |
| `resourceGroup` | `string` | 是 | 资源组 ID |
| `key` | `string` | 是 | 源表唯一键，通常 `owner:id` |
| `owner` | `string` | 是 | CSV 归属/负责人 |
| `source` | `string` | 是 | 来源 CSV 文件 |
| `row` | `number` | 是 | CSV 原始行号 |
| `notes` | `{ value: string, ... }[]` | 是 | 补充备注列表；可为空数组 |

### 样本

```json
{
  "id": "eip-uf6cy9vtbvyopmajt0clm",
  "name": "eroaduateip",
  "protection": "-",
  "tags": "",
  "ip": "47.117.144.217",
  "bindingType": "NAT网关",
  "bindingId": "ngw-uf6gthlzntscth3modciw",
  "bindingName": "nat_gw",
  "status": "已分配",
  "bandwidth": "10 Mbps",
  "network": "BGP(多线)/公网",
  "bandwidthPackage": "未加入带宽包服务",
  "poolId": "-",
  "billing": "后付费",
  "allocatedAt": "2025年3月25日 19:48:24",
  "resourceGroup": "rg-aek2gjsbyw5o5my",
  "key": "泛宥:eip-uf6cy9vtbvyopmajt0clm",
  "owner": "泛宥",
  "source": "泛宥-弹性公网IP eip_list_cn-shanghai_2026-06-26.csv",
  "row": 2,
  "notes": []
}
```

## 5. NAT 网关与 DNAT

### 根对象

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `available` | `boolean` | 是 | 是否有完整 NAT 快照 |
| `collectedAt` | `string` | 是 | 采集时间 |
| `region` | `string` | 是 | 区域 |
| `gateways` | `NatGateway[]` | 是 | NAT 网关；可为空数组 |

### `NatGateway`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `id` | `string` | 是 | NAT 网关 ID |
| `name` | `string` | 是 | 名称；可为空 |
| `status` | `string` | 是 | 网关状态 |
| `vpcId` | `string` | 是 | VPC ID |
| `entries` | `DnatEntry[]` | 是 | DNAT 映射 |

### `DnatEntry`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `id` | `string` | 是 | DNAT 条目 ID |
| `tableId` | `string` | 是 | 转发表 ID |
| `name` | `string` | 是 | 条目名；可为空 |
| `externalIp` | `string` | 是 | 公网/外部 IP |
| `externalPort` | `string` | 是 | 外部端口，可为数字、范围或 `Any` |
| `internalIp` | `string` | 是 | 内部 IP |
| `internalPort` | `string` | 是 | 内部端口，可为数字、范围或 `Any` |
| `protocol` | `string` | 是 | 协议，可为 `Any` |
| `status` | `string` | 是 | 条目状态 |

### 样本（按采集器规范化结构）

```json
{
  "available": true,
  "collectedAt": "2026-09-14T15:00:00+08:00",
  "region": "cn-shanghai",
  "gateways": [{
    "id": "ngw-uf6gthlzntscth3modciw",
    "name": "nat_gw",
    "status": "Available",
    "vpcId": "vpc-uf6xxxxxxxxxxxxxxxx",
    "entries": [{
      "id": "fwd-uf6xxxxxxxxxxxxxxxx",
      "tableId": "ftb-uf6xxxxxxxxxxxxxxxx",
      "name": "web",
      "externalIp": "47.117.144.217",
      "externalPort": "443",
      "internalIp": "10.179.6.15",
      "internalPort": "443",
      "protocol": "TCP",
      "status": "Available"
    }]
  }]
}
```

## 6. ECS

### 根对象

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `fetchedAt` | `string` | 是 | 原始 ECS 清单采集时间 |
| `region` | `string` | 是 | 区域 |
| `projects` | `Project[]` | 是 | 资源组 |
| `instances` | `Instance[]` | 是 | ECS 实例 |

### `Project`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `id` | `string` | 是 | 资源组 ID |
| `name` | `string` | 是 | 资源组名 |
| `code` | `string` | 是 | 资源组编码 |

### `Instance`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `id` | `string` | 是 | ECS 实例 ID |
| `name` | `string` | 是 | 实例名 |
| `projectId` | `string` | 是 | 资源组 ID；可为空 |
| `cpu` | `number` | 是 | vCPU 数 |
| `memoryGiB` | `number` | 是 | 内存 GiB |
| `os` | `string` | 是 | 操作系统名 |
| `privateIps` | `string[]` | 是 | 去重后的私网 IP |
| `publicIps` | `string[]` | 是 | 去重后的公网/EIP IP；可为空 |
| `tags` | `Record<string,string>` | 是 | 标签字典 |
| `status` | `string` | 是 | 实例状态 |
| `region` | `string` | 是 | 区域 |
| `zone` | `string` | 是 | 可用区 |
| `instanceType` | `string` | 是 | 规格族 |

### 样本

```json
{
  "id": "i-uf6htx2n3srj1akyreoz",
  "name": "ai-staff",
  "projectId": "",
  "cpu": 4,
  "memoryGiB": 16.0,
  "os": "Alibaba Cloud Linux  4 LTS 64位",
  "privateIps": ["10.179.0.202"],
  "publicIps": ["8.153.146.109"],
  "tags": {},
  "status": "Running",
  "region": "cn-shanghai",
  "zone": "cn-shanghai-g",
  "instanceType": "ecs.u1-c1m4.xlarge"
}
```

## 7. CLB / SLB

> 当前 `lib/clb-snapshot.json` 是空占位；以下样本来自 `slb-export-cn-shanghai/20260914-145141/slb-all.json` 经 `normalize_clb()` 规范化后的结果。

### 根对象

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `available` | `boolean` | 是 | 是否有完整 CLB 快照 |
| `collectedAt` | `string` | 是 | 采集时间 |
| `region` | `string` | 是 | 区域 |
| `instances` | `ClbInstance[]` | 是 | CLB 实例 |

### `ClbInstance`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `id` | `string` | 是 | 负载均衡实例 ID |
| `name` | `string` | 是 | 名称 |
| `ip` | `string` | 是 | CLB 地址 |
| `addressType` | `string` | 是 | `intranet` / `internet` |
| `status` | `string` | 是 | 实例状态 |
| `listeners` | `Listener[]` | 是 | 监听器 |
| `groups` | `ServerGroup[]` | 是 | 默认、虚拟、主备服务器组 |

### `Listener`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `protocol` | `string` | 是 | HTTP/HTTPS/TCP/UDP 等 |
| `port` | `number` | 是 | 监听端口 |
| `status` | `string` | 是 | 监听状态 |
| `description` | `string` | 是 | 描述 |
| `groupId` | `string` | 是 | 默认或指定服务器组 |
| `backendPort` | `number?` | 条件 | 后端端口 |
| `forwardPort` | `number?` | 条件 | 重定向/转发端口 |
| `healthCheck` | `string` | 是 | 健康检查状态 |
| `certificates` | `Certificate[]` | 是 | 证书列表 |
| `rules` | `Rule[]` | 是 | 七层转发规则 |

### `ServerGroup` / `BackendServer` / `Rule` / `Certificate`

| 字段 | 类型 | 说明 |
|---|---|---|
| `ServerGroup.id` | `string` | 服务器组 ID，默认组为 `default` |
| `ServerGroup.name` | `string` | 组名 |
| `ServerGroup.kind` | `string` | `default` / `virtual` / `masterSlave` |
| `ServerGroup.servers` | `BackendServer[]` | 后端列表 |
| `BackendServer.id` | `string` | ECS/ENI/ IP 后端 ID |
| `BackendServer.ip` | `string` | 后端 IP |
| `BackendServer.port` | `number` | 后端端口 |
| `BackendServer.weight` | `number` | 权重 |
| `BackendServer.type` | `string` | 后端类型 |
| `Rule.id` | `string` | 规则 ID |
| `Rule.domain` | `string` | 域名 |
| `Rule.path` | `string` | URI/路径 |
| `Rule.groupId` | `string` | 转发目标服务器组 |
| `Certificate.id` | `string` | 证书 ID |
| `Certificate.name` | `string` | 证书名 |
| `Certificate.domain` | `string` | 域名扩展；默认为空 |
| `Certificate.commonName` | `string` | 证书 CN |
| `Certificate.expiresAt` | `string` | 过期时间 |

### 样本

```json
{
  "id": "lb-uf6ilo3pkp5rdccw53kt9",
  "name": "prod-aigo-slb",
  "ip": "10.179.6.15",
  "addressType": "intranet",
  "status": "active",
  "listeners": [{
    "protocol": "HTTPS",
    "port": 443,
    "status": "running",
    "description": "https_443",
    "groupId": "rsp-uf6j5njnrgca7",
    "backendPort": null,
    "forwardPort": null,
    "healthCheck": "off",
    "certificates": [{
      "id": "1182781005114574_19a7c76178d_-2004459417_988617003",
      "name": "*.folidaymall.com",
      "domain": "",
      "commonName": "*.folidaymall.com",
      "expiresAt": "2026-12-14T23:59:59Z"
    }],
    "rules": [{ "id": "rule-uf6820hrphe5o", "domain": "*.folidaymall.com", "path": "", "groupId": "rsp-uf6j5njnrgca7" }]
  }],
  "groups": [{
    "id": "rsp-uf6j5njnrgca7",
    "name": "prod-aigo",
    "kind": "virtual",
    "servers": [{ "id": "i-uf617oajn8u89ccrt3tp", "ip": "10.179.6.4", "port": 80, "weight": 100, "type": "ecs" }]
  }]
}
```

## 8. JumpServer 与 Nginx

### 根对象

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `collectedAt` | `string` | 是 | 采集时间 |
| `assets` | `Asset[]` | 是 | 当前账号授权资产 |
| `groups` | `Node[]` | 是 | 授权分组/节点 |

### `Asset`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `id` | `string` | 是 | JumpServer 资产 ID |
| `hostname` | `string` | 是 | 主机名 |
| `ip` | `string` | 是 | 管理地址/主 IP |
| `os` | `string?` | 条件 | 操作系统；49 条为 `null` |
| `platform` | `string` | 是 | 平台 |
| `inspection` | `Inspection?` | 条件 | 服务器进程/Nginx 采集结果；当前 checked-in 快照没有该字段 |

### `Node`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `id` | `string` | 是 | 节点 ID |
| `name` | `string` | 是 | 节点名 |
| `key` | `string` | 是 | JumpServer 树形 key |
| `value` | `string` | 是 | 展示名 |
| `org_id` | `string` | 是 | 组织 ID |
| `assets_amount` | `number` | 是 | API 报告资产数 |
| `parentKey` | `string?` | 条件 | 父节点 key |
| `path` | `string` | 是 | `Default / 组 / 子组` 路径 |
| `fetchedAssetCount` | `number` | 是 | 实际分页拉取资产数 |
| `assetIds` | `string[]` | 是 | 节点资产 ID 列表 |
| `assetsOutsideChildGroups` | `number` | 是 | 本节点直属、非子节点资产数 |

### `Inspection`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `checkedAt` | `string` | 是 | 服务器检查时间 |
| `loginStatus` | `string` | 是 | `can_login` / `cannot_login` |
| `reason` | `string` | 是 | 不能登录原因或空 |
| `account` | `string?` | 条件 | 成功登录账号 |
| `sudoStatus` | `string?` | 条件 | root、passwordless、password、denied 等 |
| `processStatus` | `string` | 是 | complete / partial / failed / not_collected |
| `processes` | `Process[]` | 是 | 非系统进程 |
| `excludedSystemProcesses` | `number?` | 条件 | 排除的系统进程行数 |
| `nginxStatus` | `string` | 是 | complete / partial / failed / not_running 等 |
| `nginxRoutes` | `Route[]` | 是 | 静态解析出的 Nginx 路由 |
| `configurationCount` | `number?` | 条件 | 原始配置文件数 |
| `configurationVersion` | `string?` | 条件 | 配置归档版本 ID |
| `warnings` | `string[]` | 是 | 采集警告 |
| `attempts` | `{ account, reason }[]?` | 条件 | 登录尝试 |

### `Process`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `pid` | `number` | 是 | 进程 ID |
| `ppid` | `number` | 是 | 父进程 ID |
| `user` | `string` | 是 | 运行用户 |
| `name` | `string` | 是 | `comm` |
| `kind` | `string` | 是 | 采集器识别类型 |
| `startedAt` | `string` | 是 | UTC ISO-8601 启动时间 |
| `elapsedSeconds` | `number` | 是 | 采集时已运行秒数 |
| `command` | `string` | 是 | 完整命令；敏感参数已脱敏 |

### `Route`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `domains` | `string[]` | 是 | `server_name` |
| `listen` | `string[]` | 是 | `listen` 指令参数 |
| `uri` | `string` | 是 | `location` URI；非 location 为空 |
| `directive` | `string` | 是 | `proxy_pass`、`fastcgi_pass` 等；静态/其他为 `static/other` |
| `target` | `string` | 是 | 原转发目标；静态/其他为空 |
| `upstream` | `string` | 是 | upstream 名称或直接目标 |
| `instance` | `string` | 是 | 运行中的 Nginx 可执行文件 |
| `backends` | `Backend[]` | 是 | upstream 展开后的后端 |
| `context` | `string` | 是 | `http` / `stream` |

`Backend`：`{ host: string, port: string | null, resolution: 'ip' | 'hostname' | 'dynamic' | 'unix' }`。

### 原始 Nginx 配置归档

每个可采集资产保存为 `versions/<version>/nginx-configs/<sha256(assetId)>.json`。

| 字段 | 类型 | 说明 |
|---|---|---|
| `assetId` | `string` | JumpServer 资产 ID |
| `collectedAt` | `string` | 与 `inspection.checkedAt` 一致 |
| `files` | `ConfigFile[]` | 原始配置文件 |

`ConfigFile`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `path` | `string` | 远端配置文件绝对路径 |
| `instance` | `string` | 使用的 Nginx 可执行文件 |
| `content` | `string` | UTF-8 解码文本，失败字符替换 |
| `base64` | `string` | 精确原始字节 Base64 |
| `bytes` | `number` | 原始字节数 |

### 样本

```json
{
  "id": "f5f90b74-cbdf-40a1-8c79-fd943ba75053",
  "hostname": "121.58.110.120_pos1",
  "ip": "121.58.110.120",
  "os": "CentOS",
  "platform": "Linux",
  "inspection": {
    "checkedAt": "2026-09-20T02:00:00+00:00",
    "loginStatus": "can_login",
    "reason": "",
    "account": "folidev",
    "sudoStatus": "password",
    "processStatus": "complete",
    "processes": [{
      "pid": 1234,
      "ppid": 1,
      "user": "root",
      "name": "nginx",
      "kind": "Nginx",
      "startedAt": "2026-09-01T00:00:00+00:00",
      "elapsedSeconds": 172800,
      "command": "nginx: master process /usr/sbin/nginx -c /etc/nginx/nginx.conf"
    }],
    "nginxStatus": "complete",
    "nginxRoutes": [{
      "domains": ["api.example.internal"],
      "listen": ["443", "ssl"],
      "uri": "/v1/",
      "directive": "proxy_pass",
      "target": "http://app_backend",
      "upstream": "app_backend",
      "instance": "/proc/1234/exe",
      "backends": [{ "host": "10.179.1.10", "port": "8080", "resolution": "ip" }],
      "context": "http"
    }],
    "configurationCount": 1,
    "configurationVersion": "20260920-020000-000000",
    "warnings": [],
    "attempts": []
  }
}
```

## 9. `/resources` 宽表

宽表不是落盘 JSON，而是 `resource-comparison.ts` 在构建/运行时把 DevOps、ECS、JumpServer、Codeup、DNS、EIP、NAT、CLB 聚合成 `ResourceRow[]`。当前 checked-in 数据计算结果为 610 个唯一 IP。

### `ResourceRow`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `ip` | `string` | 是 | 归一化后的唯一键；非 IPv4 原样保留 |
| `resourceType` | `string` | 是 | 阿里云 / 阿里云 CLB / 阿里云 EIP / NAT 映射 / CLB 后端 / DNS 记录 / 公司机房 / 空字符串 |
| `classificationConflict` | `boolean` | 是 | 多来源资源类型分类冲突 |
| `cloudInstanceIds` | `string[]` | 是 | 匹配到的 ECS 实例 ID |
| `clbInstanceIds` | `string[]` | 是 | 匹配到的 CLB 实例 ID |
| `projectNames` | `string[]` | 是 | ECS 资源组名 |
| `jumpGroups` | `string[]` | 是 | JumpServer 节点 path |
| `associations` | `Association[]` | 是 | DevOps 应用/环境与 Codeup 仓库关联 |
| `references` | `{ label, href }[]` | 是 | 可点击证据链接 |
| `searchTerms` | `string[]` | 是 | 参与搜索的域名、状态、类型等 |
| `sources` | `Sources` | 是 | 七个来源命中布尔值 |
| `otherManagedIps` | `string[]` | 是 | 同 ECS 实例但本 IP 未纳管时的其他已纳管 IP |

### `Association`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `key` | `string` | 是 | `appId/env/port/git` JSON 数组字符串 |
| `appId` | `string` | 是 | DevOps 应用 ID |
| `name` | `string` | 是 | 应用名 |
| `env` | `string` | 是 | TEST / SIMULATION / PRODUCT |
| `port` | `string` | 是 | 端口；可为空 |
| `git` | `string` | 是 | 规范化 Git URL；可为空 |
| `gitLink` | `string` | 是 | 展示链接；可为空 |
| `codeGroup` | `string` | 是 | Codeup 组名；可为空 |
| `pushIn` | `string` | 是 | 最近发布时间；可为空 |

### `Sources`

| 字段 | 类型 | 说明 |
|---|---|---|
| `devops` | `boolean` | DevOps 部署 IP |
| `aliyun` | `boolean` | ECS 私网/公网 IP |
| `jumpserver` | `boolean` | JumpServer 资产 IP |
| `eip` | `boolean` | EIP 记录 IP |
| `nat` | `boolean` | NAT 外网或 DNAT 内部 IP |
| `dns` | `boolean` | DNS A/AAAA 地址 |
| `clbBackend` | `boolean` | CLB 后端 IP |

### 样本

```json
{
  "ip": "10.179.2.11",
  "resourceType": "阿里云",
  "classificationConflict": false,
  "cloudInstanceIds": ["i-uf62pf615c0j8odiufrk"],
  "clbInstanceIds": [],
  "projectNames": ["商旅"],
  "jumpGroups": ["Default / VPC_kuyi / UAT", "Default / VPC上海 / UAT"],
  "associations": [{
    "key": "[\"1865\",\"TEST\",\"8098\",\"https://code.aliyun.com/foliday/tims-uaa.git\"]",
    "appId": "1865",
    "name": "cat",
    "env": "TEST",
    "port": "8098",
    "git": "https://code.aliyun.com/foliday/tims-uaa.git",
    "gitLink": "https://code.aliyun.com/foliday/tims-uaa.git",
    "codeGroup": "",
    "pushIn": ""
  }],
  "references": [],
  "searchTerms": [],
  "sources": {
    "devops": true,
    "aliyun": true,
    "jumpserver": true,
    "eip": false,
    "nat": false,
    "dns": false,
    "clbBackend": false
  },
  "otherManagedIps": []
}
```

## 统计与质量口径

当前 checked-in 数据经 `/resources` 计算后的汇总：

- 唯一 IP：610
- DevOps IP：145；ECS/阿里云 IP：265；JumpServer IP：415
- JumpServer 比较范围：337；未匹配 JumpServer：157
- DevOps 未匹配 JumpServer：30；阿里云未匹配 JumpServer：140
- 公司机房 `10.58.0.0/16`：153；位置未知：147
- 同 ECS 实例存在其他已纳管 IP：20

注意事项：

1. 所有数据均为静态快照，不表示实时状态。
2. DNS 的暂停记录、CNAME 和外部目标不会推断为当前生效链路；A/AAAA 地址才进入宽表。
3. EIP 是 2026-06-26 静态 CSV 导出，绑定关系不代表实时状态。
4. NAT、CLB 的空占位只说明当前 checked-in `lib` 文件不可用，不代表采集器不支持。
5. Nginx 原始配置归档按资产 ID SHA-256 命名；采集过程中不保存终端 transcript、密码、Cookie。

# EnvScope 资源采集数据 Schema 整理

- 整理对象：`environment-web` 当前使用的快照数据与 `/resources` 宽表计算结果。
- 说明：字段类型按 JSON 实际数据和代码定义归纳；`?` 表示可空/条件存在；`[]` 后的 `T` 表示数组元素类型。
- 样本原则：每个实体保留 1 条最小但有代表性的记录，数组适当截短，避免泄露大面积内部清单。
- 当前 checked-in 快照状态：DevOps 545 个应用、Codeup/仓库 556 条、ECS 204 台、JumpServer 421 台、DNS 481 条、EIP 53 条。当前仓库中的 `clb-snapshot.json`、`nat-snapshot.json` 和 `dms-snapshot.json` 是 `available: false` 占位，但 CLB 有 2026-09-14 原始导出，NAT 与 DMS 结构由采集器定义；三者样本按实际/规范化结构展示。

## 总览

### 多租户增量契约（schemaVersion=2）

以下是新采集版本的附加契约；后文无租户字段的示例是兼容的单账号旧结构。

- ECS、CLB、DMS、NAT 每个启用租户均采集；Codeup 仅使用 `legacy-aliyun` 现有租户。DNS / EIP 保持静态来源，不能批量归属某个新租户。
- ECS `instances[]` / `projects[]`、CLB `instances[]`、NAT `gateways[]` / `entries[]`、DMS `instances[]` 增加 `tenantId`（AppAtlas 稳定 ID）、`tenantName`、`accountId`（采集账号 UID）。资源行增加 `resourceKey`、`region`、`observedAt`。ECS / CLB 有来源字段时保留 `vpcId`、`vswitchId`。
- `resourceKey` 是 `[tenantId, resourceType, region, nativeId]` 的规范 JSON 字符串，原生 `id` 保持不变。DNAT 的 nativeId 包含网关 ID 和条目 ID。资源组名称按租户 + 资源组 ID 查询。
- DMS 的 `resourceKey` 在 nativeId 中包含 Tid + 实例地址；`sourceKeys[]` 保留 `[tenantId, "dms-record", dmsTenantId, 原生DMS记录ID]`。DMS `dmsTenantId` 是 DMS Tid，独立于 AppAtlas 租户 ID。旧版根 `tenantId` 表示 Tid，新版汇总根移除此字段。
- DMS Cookie 记录的 `实例ID` 来源是控制台云产品实例 ID，AK 记录的同名字段来源是 DMS ListInstances 原生记录 ID；不要互换解释。新增 `cloudInstanceId` 仅在有明确云产品 ID 时填写，否则为空。`accountId` 表示采集账号，不证明被登记数据库的真实云账号所有权。
- 四类快照根增加 `tenants[]`：`tenantId`、`tenantName`、`accountId`、`region`、`dmsTenantId?`、`collectedAt`、`complete`、`count`、`credentialVersion`、`scope: credential-visible`、`sourceVersion`。DMS 的范围 `region` 为空，表示按 DMS Tid 枚举，不随 ECS 地域重复。`regions[]` 汇总实际地域；旧根 `region` 仅在单一地域时有值。
- 根 `fetchedAt` / `collectedAt` 为切片时间的最大值；独立同步保留其他切片时间，不能将该最大值解释为全部租户都已刷新。每次任务的非敏感 `cloudPlan` 保存在 `version.json`，验证缺失切片、配置版本和完整性后才允许原子激活。
- 新采集原始输出在 `versions/<version>/sources/aliyun/<tenantId>/<source>/<region-or-all>/`，不共用输出目录或凭据上下文。独立同步的旧切片先复制到版本内 `base/`，采集成功后按租户替换；零条记录也会替换旧清单。
- 旧快照保持可读，不原地重写；派生新版本并保留旧单账号切片时，仅将旧云资源标记为 `legacy-aliyun`，`tenants[].legacy=true`，账号 UID 留空，保留旧观察时间。静态 DNS / EIP 和独立 DevOps / JumpServer 不套用该映射。
- IP 宽表仍按 IP 聚合，新增 `tenants[] = {id, name, accountId}[]`，只从云资源证据附加。拓扑沿用现有关联与置信度，云资源节点 identity / attributes 及 Evidence 保留租户 ID 和采集账号，不改变 UNKNOWN / AMBIGUOUS 等结论。当前前提是租户网段已规划为互不重叠。

| 数据域 | 主要文件 | 主键 | 实体数量 | 与宽表的关联 |
|---|---|---|---:|---|
| DevOps 应用环境 | `environment-web/lib/snapshot.json` | `apps[].id`；部署记录建议 `appId + env + ip + deploy` | 545 app / 1947 环境部署记录 | 提供 `sources.devops`、应用/环境/端口/仓库关联 |
| Codeup / Local GitLab 仓库 | `environment-web/lib/repositories.json` | `repos[].id`（Git URL 哈希） | 556 repo / 15 group | 提供仓库 URL 与代码组归属 |
| DNS 解析 | `environment-web/lib/dns-snapshot.json` | `records[].id = zone:row` | 481 | A/AAAA 地址进入宽表 `sources.dns` |
| EIP | `environment-web/lib/eip-snapshot.json` | `records[].id` | 53 | EIP 地址进入宽表 `sources.eip` |
| NAT 网关 / DNAT | `environment-web/lib/nat-snapshot.json` | `gateways[].id`；`gateways[].entries[].id` | 当前 checked-in 为空占位 | 外网/内网 IP 均进入宽表 `sources.nat` |
| ECS | `environment-web/lib/ecs-snapshot.json` | `instances[].id` | 204 | 私网/公网 IP 进入宽表 `sources.aliyun` |
| CLB | `environment-web/lib/clb-snapshot.json` | `instances[].id` | 当前 checked-in 为空占位；原始导出 19 台 | CLB IP 与后端 IP 进入宽表 |
| 阿里云 DMS 实例 | `environment-web/lib/dms-snapshot.json` | `instances[].实例ID` + `instances[].实例地址` | 当前 checked-in 为空占位；2026-10-20 实测 65 实例 | 仅 `/aliyun/dms` 页面展示，IP 暂不进入宽表与拓扑 |
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

> 当前 `lib/clb-snapshot.json` 是空占位；以下样本来自一次 CLB 控制台导出经 `normalize_clb()` 规范化后的结果。

AccessKey 采集按 LB 隔离故障。列表或详情明确为 `inactive` / `locked` 的 LB 跳过；其他 LB 的详情、监听器、规则或服务器组请求持续超时、异常或缺少必要字段时，跳过整个 LB，避免输出虚假的空服务器组或不完整路由。证书元数据或后端 ECS IP 补查失败保留已知数据，未知 IP 不补造。实例枚举失败、枚举鉴权失败、前后清单不稳定仍使来源失败。

此模式的原始结果及多租户快照中，`complete: true` 表示列表枚举和稳定性复核完成、所有 LB 已按跳过策略处理，允许任务成功并激活版本；它不保证所有实例详情齐全。存在跳过或补查失败时，根与对应 `tenants[]` 额外保存 `partial: true` 和 `warnings[]`。Warning 包含 `action`、`params`、`reason`、脱敏 `error`、`skipped`；实例 Warning 额外包含 `loadBalancerId` 和实际观察到的 `status`。规范化及汇总时附加 `tenantId` / `tenantName` / `accountId` / `region`，独立刷新替换当前租户 Warning 并保留其他租户 Warning。被跳过的 LB 不进入 `instances[]`，不将其当作已确认删除，也不复用旧详情伪装为本次成功采集。旧 Cookie 采集完整性规则不变。

### 根对象

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `available` | `boolean` | 是 | 是否有可用 CLB 快照；AccessKey 模式允许按上述策略跳过个别 LB |
| `collectedAt` | `string` | 是 | 采集时间 |
| `region` | `string` | 是 | 区域 |
| `instances` | `ClbInstance[]` | 是 | CLB 实例 |
| `partial` | `boolean` | 否 | 是否存在跳过或补查失败；旧快照可能缺少 |
| `warnings` | `object[]` | 否 | 按实例/请求保留的 Warning，包含跳过原因与来源范围 |

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
| `appPorts` | `AppPort[]` | 是 | 应用监听端口，按 (应用名, 端口) 归并；未采集或无应用端口为空数组 |
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

### `AppPort`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `app` | `string` | 是 | 应用名；Java 取 `-Dappid` / `-Dspring.application.name` / jar 名，其他取 `kind` |
| `kind` | `string` | 是 | 采集器识别的进程类型（Nginx / Java / Redis ...） |
| `port` | `number` | 是 | 监听端口 |
| `addresses` | `string[]` | 是 | 绑定地址（去括号；`*` 表示全部接口） |
| `pids` | `number[]` | 是 | 占用该端口的进程 PID |

### `Route`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `domains` | `string[]` | 是 | `server_name` |
| `listen` | `string[]` | 是 | `listen` 指令参数 |
| `uri` | `string` | 是 | `location` URI；非 location 为空 |
| `directive` | `string` | 是 | `proxy_pass`、`fastcgi_pass` 等；静态/其他为 `static/other` |
| `target` | `string` | 是 | 原转发目标；`static/other` 时为由 `alias`/`root` 解析出的静态目录（如 `/var/www/webapps/tc-fostay`），无 alias/root 或旧快照为空 |
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
    "appPorts": [
      { "app": "Nginx", "kind": "Nginx", "port": 80, "addresses": ["0.0.0.0"], "pids": [1234] },
      { "app": "fosun_cashier", "kind": "Java", "port": 7084, "addresses": ["::"], "pids": [384] }
    ],
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

---

# 10. Topology Graph Schema

> 本节描述 Env-Scope 在原始快照和 `/resources` 宽表之上的统一拓扑模型。  
> 原始快照仍然是事实来源；Topology 不取代原始 Schema，而是为路径查询、架构图和风险分析提供统一图模型。

## 10.1 设计目标

Topology 层主要解决：

1. 将 DNS、EIP、NAT、CLB、ECS、JumpServer、Nginx、DevOps、Repository 串成统一链路。
2. 支持 Domain → Application 等 Graph Traversal。
3. 保留每条关系的 evidence 与 confidence。
4. 不因缺失数据而伪造关系。
5. 为 Risk Analyzer 和 Architecture Renderer 提供稳定输入。

## 10.2 Graph 根对象

建议结构：

```ts
interface TopologyGraph {
  generatedAt: string;
  nodes: TopologyNode[];
  edges: TopologyEdge[];
  stats: {
    nodeCount: number;
    edgeCount: number;
    ambiguousEdges: number;
    unresolvedNodes: number;
  };
}
```

## 10.3 Node 通用结构

```ts
interface TopologyNode {
  id: string;
  type: NodeType;
  name?: string;
  environment?: "TEST" | "SIMULATION" | "PRODUCT";
  attributes?: Record<string, unknown>;
  evidence?: Evidence[];
  observedAt?: string;
}
```

第一阶段 `NodeType`：

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

后续可扩展：

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

## 10.4 Edge 通用结构

```ts
interface TopologyEdge {
  id: string;
  from: string;
  to: string;
  type: EdgeType;
  environment?: "TEST" | "SIMULATION" | "PRODUCT";
  evidence: Evidence[];
  confidence: "EXACT" | "INFERRED" | "AMBIGUOUS" | "UNKNOWN";
  observedAt?: string;
  attributes?: Record<string, unknown>;
}
```

建议的 `EdgeType` 包括：

```text
RESOLVES_TO
CNAME_TO
BOUND_TO
HAS_DNAT_RULE
FORWARDS_TO
HAS_LISTENER
ROUTES_TO
HAS_SERVER_GROUP
SERVED_BY
USES_UPSTREAM
HAS_DEPLOYMENT
LISTENS_ON
ON_HOST
BUILT_FROM
CONTAINS
```

## 10.5 Evidence

所有推断或关联关系必须能够追溯来源。

建议：

```ts
interface Evidence {
  source:
    | "devops"
    | "codeup"
    | "dns"
    | "eip"
    | "nat"
    | "ecs"
    | "clb"
    | "jumpserver"
    | "nginx"
    | "derived";
  sourceId?: string;
  file?: string;
  field?: string;
  value?: string;
  note?: string;
}
```

原则：

- `EXACT` 也应尽量保留 evidence。
- `INFERRED` / `AMBIGUOUS` 必须有 evidence。
- 不允许为了让图连通而创建无法解释来源的 edge。

## 10.6 Confidence

统一使用：

| 值 | 含义 |
|---|---|
| `EXACT` | 原始数据直接给出，或多个来源通过稳定唯一键精确匹配 |
| `INFERRED` | 由可信规则推断，但不是原始来源直接声明 |
| `AMBIGUOUS` | 有多个合理候选，无法唯一确认 |
| `UNKNOWN` | 关系或状态无法确认 |

Path confidence 建议采用“最弱边决定整条路径”的规则。

## 10.7 HOST

`HOST` 是逻辑主机，不应机械拆成 ECS Host、JumpServer Host、DevOps Host 三套节点。

应尽量把：

- ECS instance
- JumpServer Asset
- DevOps Deployment IP

归并成同一个逻辑 Host，并将各来源信息放入 attributes / evidence。

建议属性：

```ts
{
  ips: string[];
  hostname?: string;
  ecsInstanceId?: string;
  jumpserverAssetId?: string;
  region?: string;
  zone?: string;
  cpu?: number;
  memoryGiB?: number;
  os?: string;
}
```

## 10.8 ENDPOINT

Endpoint 是拓扑中最重要的关联实体之一。

身份应尽量包含：

```text
IP + Port + Protocol
```

建议 ID：

```text
endpoint:<ip>:<port>:<protocol>
```

例如：

```text
endpoint:10.179.1.10:8080:tcp
```

Endpoint 用于连接：

- DevOps Deployment
- Nginx Backend
- CLB Backend
- NAT / DNAT target
- Host

只有 IP、没有 Port 的数据不能随意绑定到某一个 Application；当 Host 上存在多个候选应用端口时，应使用 `AMBIGUOUS` 或保持 unresolved。

## 10.9 APPLICATION 与 DEPLOYMENT

建议关系：

```text
APPLICATION
  --HAS_DEPLOYMENT-->
DEPLOYMENT
  --LISTENS_ON-->
ENDPOINT
  --ON_HOST-->
HOST
```

Deployment 至少应保留：

- appId
- env
- deploy id
- IP
- port
- branch
- repository
- publish state
- observedAt

## 10.10 DNS

Topology 与 `/resources` 宽表的处理方式不同。

宽表可以只让 A/AAAA IP 进入 IP 汇总；Topology 应保留 CNAME：

```text
DOMAIN
  --CNAME_TO-->
DOMAIN
```

A/AAAA：

```text
DOMAIN
  --RESOLVES_TO-->
IP / EIP / Endpoint-related node
```

外部 CNAME 或无法继续解析的目标可以保留：

```text
external = true
unresolved = true
```

不应直接丢弃。

## 10.11 NAT / DNAT

NAT 关系必须保留端口与协议：

```text
External Endpoint
  --FORWARDS_TO-->
Internal Endpoint
```

例如：

```text
47.117.144.217:443/TCP
  →
10.179.6.15:443/TCP
```

不能退化成：

```text
47.117.144.217 → 10.179.6.15
```

否则同一 IP 的多个服务会被错误合并。

## 10.12 CLB

建议结构：

```text
CLB
  --HAS_LISTENER-->
CLB_LISTENER

CLB_LISTENER
  --ROUTES_TO-->
SERVER_GROUP

SERVER_GROUP
  --FORWARDS_TO-->
ENDPOINT
```

七层规则的 domain / path 应作为 Listener/Rule 的 attributes 或独立 Rule 节点保留。

Risk Analyzer 后续可利用 ServerGroup 的实际 distinct Host / Endpoint 数量判断后端单点。

## 10.13 Nginx

根据 `nginxRoutes` 建模：

```text
DOMAIN
  --SERVED_BY-->
NGINX_ROUTE

NGINX_ROUTE
  --USES_UPSTREAM-->
UPSTREAM

UPSTREAM
  --FORWARDS_TO-->
ENDPOINT
```

Route 建议保留：

- domains
- listen
- uri
- directive
- target
- upstream
- context
- Nginx host / asset
- configurationVersion

## 10.14 Repository

建议：

```text
APPLICATION
  --BUILT_FROM-->
REPOSITORY
```

如果 DevOps 与 Codeup 之间存在无法唯一确认的 repository match，应保留原始 match 结论，不应强制 EXACT。

## 10.15 SYSTEM（规划）

当前采集 Schema 没有稳定 System 主数据。

后续建议增加：

```text
SYSTEM
  --CONTAINS-->
APPLICATION
```

System → Application 关系优先使用人工维护或明确主数据，命名规则、Codeup group 等只能作为辅助推断。

建议属性：

```text
name
businessDomain
owner
criticality
```

## 10.16 Path Explorer 输出建议

```ts
interface TopologyPath {
  startNodeId: string;
  endNodeId?: string;
  nodes: TopologyNode[];
  edges: TopologyEdge[];
  confidence: "EXACT" | "INFERRED" | "AMBIGUOUS" | "UNKNOWN";
  hops: number;
  status: "RESOLVED" | "UNRESOLVED";
  stoppedAt?: string;
  reason?: string;
  warnings: string[];
}
```

第一阶段查询：

```text
Domain → Application
Application → Domain
Application → Host
Host → Application
Domain → Endpoint
```

要求：

- 返回完整 path
- 支持 environment filter
- 防止 cycle
- 支持 maxDepth
- 保留 unresolved path
- 多候选 lookup 不得随机选择
- traversal 应基于 graph index，而不是每次扫描全部 edges

## 10.17 Risk Analyzer 首批规则

| 规则 | 基础判定 |
|---|---|
| Application 单点 | PRODUCT Application 的 distinct Host = 1 |
| 同主机伪 HA | Deployment > 1 且 distinct Host = 1 |
| 同 AZ 风险 | distinct Host > 1 且 distinct Zone = 1 |
| CLB Backend 单点 | ServerGroup 的有效 distinct Host / Endpoint = 1 |
| Nginx 单点 | 关键 Domain 链路只有一个入口 Nginx Host |
| 服务器共用 | 单 Host 承载多个 PRODUCT Application |
| Topology Gap | 预期链路无法遍历到 Application |
| Stale / Unknown | 关键 evidence 过旧、采集失败或 confidence 不足 |

## 10.18 数据质量与时间

所有 Node / Edge 应尽量有：

```text
observedAt
```

分析时区分：

```text
NOT_FOUND
UNKNOWN
STALE
```

例如：

- `nginxStatus = not_running` 可以支持“不存在运行中 Nginx”的判断。
- `loginStatus = cannot_login` 只能说明无法确认，不应判断为“没有 Nginx”。
- 静态 CSV / 历史导出不能被当成实时事实。

## 10.19 Topology Builder 校验建议

至少检查：

- duplicate node id
- duplicate edge id
- edge 引用不存在的 node
- endpoint identity / format 异常
- deployment 缺少 endpoint
- 理论可归属 Host 的 endpoint 未归属 Host
- ambiguous edge 数量
- unresolved node / path 数量

# 11. 单应用运行核验报告

核验数据与采集快照、Topology 分开保存，不覆盖 `Inspection` 或自动生成拓扑边。

- 启动：`POST /api/settings/application-check`，参数 `{ asset, pid, environment, duration }`。`environment` 可省略，由实时目标进程、监听端口与部署匹配自动识别；显式传入时为 `TEST / SIMULATION / PRODUCT`，`duration` 为 10–300 秒；目标 PID 必须存在于当前版本的目标资产快照中，执行前重新验证启动时间。
- 查询：`GET /api/settings/application-check?id=<task-id>`；省略 ID 返回最近一次任务。
- 取消：`POST /api/settings/application-check/cancel`，参数 `{ id }`。
- 存储：`${ENVSCOPE_DATA:-/data}/checks/<task-id>/job.json`、`report.json`；`checks/latest.json` 保存最近任务。文件权限为 `0600`。

响应为 `{ job, report }`，执行中 `report` 可为空。任务/报告状态为 `running`（仅任务）、`complete`、`partial`、`failed`、`cancelled`。容器重启把运行中任务标为 `failed`。

任务新增可选实时进度字段：`updatedAt` 为最近一次活动上报时间；`progress` 包含 `samplingStartedAt / samplingFinishedAt`（采样窗口起止）、`samples`（已返回的目标有效帧数）、`elapsedSeconds`（最近一批返回时的采样耗时）、`lastObservedAt`、`upstream / downstream`（当前已观察的连接数）。`activities[]` 按目标机或对端资产保留最新活动 `{ key, scope: target | peer, serverIp, phase, stage, updatedAt, samples? }`；`events[]` 保留最近 60 条安全活动记录 `{ at, key, scope, serverIp, message }`。仅记录程序生成的阶段说明与统计，不保存终端原文、命令或凭证；随 `job.json` 在原数据卷持久化，旧任务可缺少以上字段。页面每 2 秒读取进度、每秒更新本地耗时，明确区分采样窗口计时、已返回帧数和最终有效观察覆盖；等待较久及同步中断会提示，取消、失败、容器重启保留已有记录。进度统计不改变报告证据或归属置信度。

失败报告附带 `error: { stage, code, exitCode, message, diagnostics[] }`。`stage` 区分登录、目标探针、对端探针与 worker；`exitCode` 未知时为 `null`。诊断只保存已识别的安全错误分类，不保存原始终端输出或 stderr。后台内部异常也生成失败报告，避免只有笼统状态而无法定位原因。

只读 socket 探针等待终端返回的单次上限为 35 秒，实际终端超时后关闭会话，不在已失步会话上重试；仍可同步的兼容终端至多重试一次。超时返回 `SOCKET_PROBE_TIMEOUT` 及对应探针阶段，不包含原始异常内容。部分失败报告保留最后一次有效采样和目标进程身份，失败等待不计入额外观察覆盖；尚无有效采样时为 `0`。页面不重复展示已在错误块中出现的错误和诊断。探针不使用旧 `iproute2-ss130716` 不支持的 `-H`，解析器兼容带表头输出。

正式 socket 会话把探针脚本及长过滤参数分段写入私有远端临时目录：先 gzip 压缩再 Base64 编码，每段最多 768 字符，每段作为独立短命令发送，在远端解码并校验原文 SHA-256，文件权限为 `0600`。相同脚本和参数在同一会话内复用，稳态采样只发送短执行命令；未通过校验的内容不会执行，会话关闭时清理目录。避免每次交互输入整段长脚本或嵌套引号；完整 Endpoint 与源端口匹配条件保持不变。非缓存执行路径仍兼容按 512 字符分段转义的参数。

目标会话初始化在观察计时开始前完成，任务总耗时还包含登录、初始化与配置核对；对端初始化前仍有发现空档。显式传输/校验失败使用 `PROBE_UPLOAD_FAILED`、阶段 `socket_probe`，不作为 TCP 连接失败或没有连接的证据。

快速批次在发送下一条命令前、收到结束标记后，均等待交互 Shell 的命令提示符就绪；结束标记不等同于交互终端已准备接收下一条输入。该行为仅由 socket 探针显式启用，其他终端调用维持原有默认行为，等待仍计入单次 35 秒上限。

正式检测采用 `ss-batched-polling`：每次在远端连续采集最多 5 帧，目标周期为 0.2 秒；按上一次查询开始时间扣除查询耗时后等待，默认批次之间不再额外等待 2 秒。累计约 5 秒后不再开始下一帧，已开始的只读查询仍按原有超时上限执行。每帧保留独立 socket、字节计数、PID 启动计数和观察时间，不以批次最后一帧覆盖短连接。进程元数据只在同一短批次、启动计数、executable 和网络空间均未改变时复用，并以版本引用对应每帧；长命令行元数据每批只在采样完成后传输一次，避免阻塞逐帧观察。目标与对端仍为异步采样，短于实际间隔的连接和对端启动前的连接可能漏采。

目标端不再通过 `ss -p` 扫描全机进程：使用所选 PID 的 `/proc/<pid>/fd` 在枚举前后的 socket inode 交集，与同一帧 `ss -antnie` 结果关联，监听端口也从该帧提取。`coverage.targetOwnershipMethod = "proc-fd-inode"`；目标端 `pids` 表示已核验的所选 PID，不声明共享 socket 的完整进程所有者清单。无法读取文件描述符时显式失败，不当作没有连接。对端仍检查实时 socket/PID；两端 socket cookie 或 inode 不可跨机器互相比较。

探针仅回传原有报告所需的 socket 行、`bytes_acked` 和 `bytes_received`，省略未使用的 TCP 内部统计以减少终端传输；字节差值、缺失计数为未知等语义不变。文件描述符枚举期间有条目消失或不可读时保留已验证交集并警告；整个目标目录不可读时失败。

对端发现查询按已观察的服务 Endpoint 和客户端 IP 预采样，不要求先发现每个临时客户端端口；最终归属始终检查完整反向五元组。每台对端最多缓冲 2000 条 socket 观察，单帧最多接收 400 条发现记录；缓冲区按完整端点索引，匹配保留源端口及协议。PID 不能从另一个连接复制到本条连接。目标采样中的前后缺失帧限制连接的可能存续窗口，窗口外的对端观察不作为归属依据；同一五元组在时间边界内有多个本地 socket 候选时保留 `AMBIGUOUS`。

历史对端观察按序号增量关联，环境或应用清单上下文变化时才重新匹配历史；同帧、同 PID、同主机及同环境的应用归属匹配复用，不复用连接所有者证据。缓存仅在该帧存续期间有效，新帧重新读取身份与监听证据。每帧最多核验 40 个对端 PID，超过时显式告警；不能把未核验进程当作不存在。对端观察时间上下界写入可选 `coverage.peers[].observationStart / observationEnd`；明确无时间重叠时在未解析原因中说明。

同一资产上的对端继续按已发现的完整反向连接查询，避免相同客户端 IP 把其他本机应用的连接一并扩大到当前检查。每个已发现且能唯一匹配资产的对端（含同机及回环地址）保留独立采样 worker，不设上游/下游对端台数上限，避免对端排队至观察窗口结束后被跳过。采样结束前新发现的对端、登录较慢的对端仍尝试至少一次查询（取消检测除外），观察窗口外的结果仍不作为原连接归属证据。对端 400 条限制在远端传输、进程元数据枚举前执行，并保留超限警告；不把截断后的结果当作全量。

每帧记录远端 `ss` 枚举开始/结束时间，以容器请求发出/接收时间及远端批次时钟边界估计时钟偏移上下界，不假设两台服务器时钟同步。`observedAt` 为换算后的观察区间中点，`observationStart / observationEnd` 包含枚举和传输误差。只有整个对端观察区间被同一目标 socket 的连续观察包围、两端 socket 身份存在时，进程归属才可为 `EXACT`；单次或时间边界不足仍为 `INFERRED`。缺失 TCP 状态为 `UNKNOWN`，不能默认填充 `ESTAB`。

报告 `schemaVersion = 1`，包含 `taskId`、`sourceVersion`、`environment`、`startedAt`、`finishedAt`、`target`、`upstream[]`、`downstream[]`、`configuration`、`coverage`、`warnings[]`。

每条连接记录包含：

| 字段 | 语义 |
|---|---|
| `local` / `peer` | `{ ip, port, protocol: "tcp" }`，保留临时源端口；IPv4-mapped IPv6 归一化为 IPv4 |
| `socketId` / `pids` | 本机 socket cookie（缺少时 inode）与所有者 PID；两端 socket ID 不可互相比较 |
| `direction` / `directionConfidence` / `directionBasis` | `upstream` 或 `downstream` 是基于所选进程监听端口的连接角色推断，未对握手取证 |
| `firstSeenAt` / `lastSeenAt` / `samples` | 本次窗口内的观察时间及采样次数 |
| `sentDelta` / `receivedDelta` | 同一 socket 的 TCP ACK 字节/接收字节差值；缺少计数、仅一次采样或计数回退时为 `null`，不能解释成零 |
| `networkActivity` | `DATA_OBSERVED / NO_DATA_OBSERVED / UNKNOWN`，不代表业务分类或可删除结论 |
| `remoteProcesses[]` | `{ serverIp, pid, startedAt, processName, application, service, observedAt, confidence }`，来自对端实时反向五元组检查；无法确认时为空 |
| `peerListeningProcesses[]` | 可选下游监听进程候选，含 `{ serverIp, pid, startedAt, processName, application, service, observedAt, confidence, endpoint, basis }`。在反向连接归属未解析时，按对端 IP、端口、TCP 协议查询实时监听，保留全部 PID 候选；进程关联置信度为 `INFERRED`，不填入 `remoteProcesses`，不提升 `resolution`。`endpoint` 保留实际监听地址（含通配绑定），`observedAt` 可晚于原连接观察窗口，不代表历史连接所有者 |
| `remoteProcesses[].observationStart` / `.observationEnd` | 可选观察时间上下界，包含远端枚举耗时与时钟/传输不确定性；`observedAt` 不是整次探针回传时间 |
| `remoteProcesses[].container` / `.connection` | Docker 容器服务的可选证据：`container = { id, name, initPid }`；`connection = { published, local, peer, socketId }`，Endpoint 均为 `{ ip, port, protocol }`。`pid` 保留宿主机 PID，`local` 是容器实际地址/端口；原连接 `peer` 仍为宿主机发布 Endpoint |
| `dockerMappings[]` | 可选 `{ containerId, containerName, initPid, published, target, observedAt?, service? }`，来自实时 Docker 发布端口配置；仅为映射候选，不单独证明连接归属。容器监听进程可提供 `INFERRED` 服务类型，不能当作连接所有者 |
| `proxyProcesses[]` | 可选 `{ pid, startedAt, processName, observedAt }`，记录反向连接匹配到的 Docker 代理，不把代理当作最终业务进程 |
| `resolution` / `reason` | 对端进程归属的置信度与说明；连接消失、资产多候选和权限不足保留未解析结果 |
| `service` | `{ name, confidence, basis }`；常用端口与配置声明仅为类型推断，进程命令可提供直接类型证据 |
| `configurationMatches[]` | 与对端地址、端口匹配的配置声明；域名需使用目标机 DNS 解析结果辅助关联 |

`application` 独立包含 `appId`、`appName`、`confidence`、`basis`、`candidates[]`。仅在已识别/显式指定的单一环境、目标主机部署范围内匹配；环境不唯一时应用归属为 `UNKNOWN`，不合并多环境候选。进程声明与监听/部署端口共同唯一匹配时可为 `EXACT`；名称/目录或端口推断为 `INFERRED`；多个候选保留 `AMBIGUOUS`，不会任意选择 ID。

对端应用匹配在检测环境未识别时，使用已实时确认的对端进程、对端主机部署记录与该 PID 的监听端口单独识别唯一环境；环境仍不唯一时保留 `UNKNOWN`。已指定/识别的检测环境继续约束对端匹配，不自动跨环境回退。Tomcat 优先使用 `catalina.base`、其次 `catalina.home` 的实例业务名，Java 使用启动 jar/war 名，忽略大小写及 `-_.` 分隔符差异；业务名完全匹配优先于仅子串/目录提示，冲突的显式声明或监听端口候选仍保留。连接归属置信度与应用匹配置信度独立，不因已精确确认 PID 就提升应用归属。

页面提供「连接明细」（默认）与「连接聚合」Tab。两种视图优先显示 `remoteProcesses`，缺少时显示 `peerListeningProcesses` 并标注「端口监听（推断）」。聚合仅用于展示，上游、下游分别按 `peer.ip + 进程证据类型 + 对端进程 PID/名称集合` 分组，端口不参与分组；监听候选与连接所有者分别聚合，多候选集合按相同集合聚合，未确认进程仅与同 IP 的未确认记录聚合。聚合表仅展示对端 IP、类型、对端进程 PID/名称、DevOps 应用 ID/名称，保留不同服务/应用归属及置信度，不展示网络活动、配置核对/连接证据；聚合不修改原始报告或 JSON 导出。

`configuration.endpoints[]` 仅保存 `{ host, port, protocol, kind, file, key, line, resolvedIps? }`，不保存 URL 认证、查询参数、密码或原始配置。读取限定为进程推导目录，最多 20 个文件、单文件 128 KiB、总量 1 MiB，最多解析 8 个域名；配置中心、占位符、生效 profile 与 jar 内配置暂不展开。

配置读取与实时配置文件列表共用 `runtime/application-config.ignorelist`，并追加数据卷 `${ENVSCOPE_DATA:-/data}/application-config.ignorelist` 中的规则。每行是按完整路径匹配的 glob（空行与 `#` 注释忽略），匹配目录在远端剪枝，匹配文件在列表输出和内容读取前排除，不占用文件数/大小限额；规则每次请求重新读取。默认排除 `classes/mapper`、`mybatis-config` / `mybatis-config.*`、Tomcat 通用配置文件及 `WEB-INF/web.xml`，保留 `server.xml`、`context.xml` 和业务配置。旧列表中的已忽略配置也不可继续读取；日志操作不应用这些规则。报告和文件列表的响应字段不变。

`coverage` 明确记录 TCP 周期采样、实际观察时长与局限。短连接可能漏采；最多 100 条连接，所有已发现且能唯一匹配资产的对端均参与周期连接采样，不按对端台数截断；目标服务器上的同机对端另行核验。未解析下游的唯一资产在采样结束后追加一次监听端口核验，按主机合并查询、最多并发 4 台并遍历全部待查主机，不查询上游临时客户端端口；取消检测后不再启动核验。结果统计独立写入可选 `coverage.listenerChecks[]`，包含 `serverIp`、`status` 和观察统计，不计入原始连接采样覆盖。未纳管托管数据库、CLB/NAT 地址和共享 Tomcat JVM 不能强制归属到一个应用。

新报告额外包含 `requestedSamplePeriodSeconds`、`samplesPerBatch`、`batchPauseSeconds`、`samples`、`firstObservedAt`、`lastObservedAt`、`averageIntervalSeconds`、`maxIntervalSeconds` 及 `peers[]` 中每台对端的相同观察统计。旧字段 `intervalSeconds` 保留，表示批次完成后的额外等待，默认 `0`，不表示实际观察间隔。平均/最长间隔按有效帧测算，包含批次间查询与传输空档；只有一次采样时为 `null`。页面明确显示最近一次观察到的 TCP 状态与时间。默认未匹配原因仅说明未确认对端进程，不把连接消失或权限不足当作已查明原因；实际未提权或无有效对端采样时单独说明。

新报告以 `coverage.samplingElapsedSeconds` 区分采样阶段耗时与有效观察跨度：有该字段时，`actualSeconds` 为最后与最早有效目标帧 `observedAt` 的差值（不足两帧为 `0`），不把回传后的匹配、共享锁等待和失败等待计作观察。跨度包含帧间空档，不代表连续抓包。旧报告没有该字段时保留原展示口径；任务总耗时还包含登录、准备、配置核对及等待对端结束。采样循环在开始下一批前也检查时限，已发出的只读查询仍按原超时上限等待。

下游未确认连接或 Docker 代理连接会继续核验 Docker 发布端口（区分宿主机 IP、TCP/UDP、宿主机和容器端口），最多读取 64 个运行容器的网络元数据、核验 8 个匹配容器。对端通过 `nsenter -t <initPid> -n -- ss` 查询容器网络空间，保留宿主机 PID；不依赖容器安装 `ss`，不读取 Docker Env/完整 inspect、不挂载本服务的 Docker socket。容器 init PID 启动计数和网络空间在探针执行前后核验，并重新读取该容器映射，容器重启/映射变化保留未解析结果。只有容器反向连接的客户端 IP、源端口、协议和映射目标 Endpoint 完整匹配、进程处于该网络空间、socket 身份及目标观察时间重叠充分时，连接归属才为 `EXACT`；多个容器候选为 `AMBIGUOUS`。代理重建连接、SNAT、缺少 Docker/nsenter 或权限不足时不猜测客户端映射。Docker 服务类型可由实际进程确认为 Redis、MQ 等，DevOps 应用独立保留 `UNKNOWN`，不按宿主机部署端口强制关联。字节统计仍使用所选进程原 socket，缺失计数仍为未知。

### 11.1 实时配置与日志文件

- 独立页面：`/jumpserver/check/<asset-id>/<pid>`；所有入口新标签页打开，移除手动环境选择。
- `POST /api/settings/application-check/files`：`{ id, action: "list" }` 返回 `{ files[], roots: {config[], log[]}, warnings[], observedAt, pid, startTicks }`。文件包含不透明 `id`、`kind: config | log`、`path`、`root`、`size`（bytes）、`modifiedAt`（Unix seconds）。日志额外包含 `logGroup: current | history`，分别对应「当前日志」和「历史日志」Tab；当前日志只接收 `.log/.out/.txt` 普通文本文件（空文件保留，前 4 KiB 二进制检测）；历史日志接收这些扩展名后的数字/日期轮转后缀及 `gz/tar.gz/bz2/xz/zip` 压缩后缀。Logback 明确声明的 `gz/bz2/xz/zip/tgz` 滚动文件按文件名范围接收，不能扩展为共享目录内其他应用文件。列表和打包在遍历时跳过 `apache-flume-*` 目录及其内容。额外保留 `logScopes: { root, patterns[] }[]`：进程推导目录为 `*`，Logback 共享目录按配置的文件名和滚动规则限定；配置、当前日志、历史日志分别最多列出最近更新的 1000 个文件，目录不可读/截断显示警告。
- 同接口 `{ id, action: "read", file: <file-id> }` 返回 `{ id, content, observedAt, lines, truncated, redacted }`。配置读取前 256 KiB并遮盖常见凭证；当前日志读取末尾 100 行（最多 256 KiB）；历史日志不支持文本预览，需打包下载。旧列表中的无关文件、已排除目录内日志及压缩日志也不能继续预览。配置/日志文本仅实时返回，不写入报告或文件列表。
- 操作使用原任务的版本、资产和进程，实时复核 `startedAt` / `startTicks`；客户端不能指定任意路径。目录符号链接在列目录时解析，内容读取拒绝路径重定向到列表外。
- `{ id, action: "archive", group: "history" }` 打包全部符合规则的历史日志，排除当前日志，`cutoff = 0`（不限制最近 30 天）。省略 `group` 或传 `all` 兼容原 API，按修改时间打包最近 30 天的当前及历史日志；其他范围拒绝。`{ id, action: "cancel-archive" }` 请求取消，在打包结束后的分块传输阶段生效。
- `GET /api/settings/application-check/logs?id=<task-id>` 返回 `{ status, logGroup?: all | history, phase, startedAt, finishedAt?, transferred, total, size?, fileCount?, cutoff?, sha256?, warnings? }`；`cutoff` 为修改时间下限：历史打包为 `0`，兼容模式为服务器当前时刻减 30×86400 秒。范围从启动阶段即持久化，完成后历史 Tab 仅展示历史范围下载链接。
- `GET /api/settings/application-check/logs/download?id=<task-id>` 下载 gzip tar 压缩包。包内目录相对 `/` 保存，无绝对路径，不跟随目录内符号链接；共享目录仅打包 `logScopes` 指定的文件，列表与打包共用文件名及目录排除规则（进程目录深度最多 6 层，按文件名限定的共享目录仅当前层），文件列表去重；最多 10000 个文件、512 MiB 原始文件，超限失败，不生成不完整下载。
- 元数据持久化为 `checks/<task-id>/files.json`、`archive.json`；最终压缩包为 `logs.tar.gz`，均在数据卷中，权限 `0600`。临时分块文件 `logs.part` 在失败/重启时清理。远端私有临时目录在传输结束后清理。下载任务与检测、采集、版本切换互斥。

## 部署管理目录（MySQL，独立于采集快照）

入口为应用页「部署单元」Tab。数据来自当前已完成版本 `versions/<id>/snapshot.json`，不改变该文件的任何定义。MySQL 8.4 库默认 `envscope`；迁移文件为 `environment-web/runtime/migrations/001_deployment_catalog.sql`，使用 `catalog_schema_migration` 记录迁移校验和。MySQL 数据保存在独立的 `envscope-mysql` 卷。

| 表 | 语义与身份 |
|---|---|
| `business_domain` | 业务领域，平台 UUID，唯一 code；本期空表 |
| `business_system` | 业务系统，平台 UUID，唯一 code，`domain_id` 外键；本期空表 |
| `deployment_unit` | 部署单元，平台 UUID 与唯一 code；`system_id` 可空；单元类型 APPLICATION / DATABASE / MIDDLEWARE / OTHER，无必填应用 ID |
| `deployment_unit_environment` | 唯一 `unit_id + environment`，仅 TEST / SIMULATION / PRODUCT；配置状态与本次观察完整性分开 |
| `deployment_instance` | 分环境部署成员，平台 UUID；IP、声明端口、身份质量、来源可见性及最近观察引用；同 IP 可有多实例 |
| `deployment_unit_source` | 唯一来源实例 + 对象种类 + 原生键哈希；应用 ID 在 JSON 原生键中；本期一个单元只允许一个 DevOps 应用主绑定 |
| `deployment_instance_source` | 单元环境内的部署成员来源别名；不把 deploy 或 IP 当作全局身份，保留匹配依据与证据 |
| `catalog_import_run` | 来源版本路径、原始内容 SHA-256、规则版本、范围、完整性、采集/导入时间；来源内只允许一个当前批次 |
| `catalog_observation` | 不可变的应用、环境、实例或待解析观察，来源定位、原始字段、采集时间及置信度 |
| `catalog_change` | 创建、来源更新、未再观察到等管理变更；关联导入批次，保留前后内容 |

所有业务记录在一个事务中导入成功后才切换当前批次。同一来源、快照内容哈希及映射规则版本幂等；更旧或无法排序的旧快照不会覆盖已有可确定时间的新目录。迁移是单独的可重试建表过程，不属于导入事务。失败任务状态保存在 `envscope-data` 的 `catalog-job.json`，不会留下半成品业务批次。

### 创建与更新约定

- 应用 ID 在来源命名空间内绑定部署单元 UUID；后续没有应用 ID 的中间件可使用其他来源种类。
- 实例在单元 + 环境内按已绑定 deploy/IP/端口匹配；唯一 deploy/IP 可延续端口变化；新旧均为唯一成员的 deploy 可延续 IP 变化。多旧成员无法唯一定位时保留 `AMBIGUOUS`。实例连续性为 `INFERRED`，直接应用绑定为 `EXACT`。
- 无环境配置、无部署实例只生成环境记录。空数组、缺环境字段为 `UNKNOWN`；缺 IP、非法 IP、冲突成员只保留待解析观察。未知协议、规格、主机归属保持空。
- 部署记录的显式端口（含空值）优先；仅字段缺失时取应用默认端口，观察中保存 `portBasis`。端口是声明值，不是实测监听。
- 完整且 `scope=all` 的快照才能标记缺失应用；完整且无冲突的环境才能标记缺失实例。`NOT_OBSERVED` 只表示未再观察到，不是删除、停机或真实发布事件。不完整环境保留旧记录，结合 lastSeenAt 和 coverageState 判断陈旧程度。
- 配置状态为 UNCONFIGURED / CONFIGURED_EMPTY / HAS_DEPLOYMENTS / UNKNOWN；覆盖状态为 COMPLETE / PARTIAL / UNKNOWN。管理启停状态独立于来源可见性。
- 名称默认 FOLLOW_SOURCE，可预留 MANUAL 策略；领域/系统、说明、管理状态不随采集覆盖。原始部署、配置 ID、分支、发布时间、发布状态等来源属性保存在观察 JSON。
- 当前目录不随应用历史快照切换而回滚。来源时间不一致时页面明确说明；本期无人工编辑、进程识别或完整历史目录回放 API。

### 查询与触发 API

- `GET /api/catalog/deployment-units`：`q` 搜索名称/编码/应用 ID，`page`、`pageSize`（1–100），`presence=present|all`。返回 `source`、`units`、`summary`、`pagination`、`activeSnapshot`、`job`。
- 同接口传 `unitId`、可选 `environment`，返回 `source`、`instances`、`activeSnapshot`、`job`；实例当前属性、最近时间和来源可见性分开返回。
- `source` 含原始快照版本、哈希、采集/导入时间；`activeSnapshot.matchesCatalog` 表示当前页面快照是否与该目录来源相同。
- `POST /api/catalog/deployment-units/import`：同源 JSON 空对象，返回 202 和任务状态；异步读取当前完整快照，无客户端文件路径参数。
- 配置 MySQL 后容器独立线程每 30 秒检查。数据库故障只影响目录功能，不改变采集任务或快照激活状态。

## 阿里云 DMS 数据库实例（`dms-snapshot.json`）

数据源：Cookie 模式使用控制台内部接口 `POST https://dms.aliyun.com/dms/metastore/instances/list`（`export_dms.py`）；AccessKey 模式使用 DMS Enterprise OpenAPI `ListInstances`（`aliyun_openapi.py`）。多租户编排对 AK 模式明确传入配置或查询得到的 Tid。容器内随全量同步或「单独同步 DMS」写入版本目录，经 `OPTIONAL_FILES` 激活后由 `/aliyun/dms` 页面展示。

### 根对象

| 字段 | 类型 | 说明 |
|---|---|---|
| `available` | `boolean` | 本次是否成功采集；缺文件时 `SERVICE.EMPTY_DMS` 兜底为 `false` |
| `source` | `string` | 接口地址，用于回溯 |
| `collectedAt` | `string` | 采集时间（UTC ISO） |
| `tenantId` | `number?` | DMS 租户 ID |
| `rawRecordCount` | `number` | 接口原始记录条数（去重前） |
| `totalCount` | `number` | 合并重复记录后的实例数 |
| `instances` | `DmsInstance[]` | 实例清单 |

### `DmsInstance`

| 字段 | 类型 | 说明 |
|---|---|---|
| `实例名称` | `string` | DMS 实例别名，如 `Prod-UserCenter` |
| `实例ID` | `string` | 云产品实例 ID，如 `rm-uf6od1a9f4zc7oes4` |
| `dmsInstanceIds` | `string[]` | 合并前各条 DMS 记录 ID；未合并时为单元素 |
| `实例地址` | `string` | `host:port`，端口缺失时只有 host |
| `host` / `port` | `string` / `number?` | 地址拆分，供后续解析使用 |
| `类型` / `dbType` | `string` | 展示名与接口原值（`MySQL` / `mysql`） |
| `实例地区` / `regionId` | `string` | 中文地区名与 `cn-*` 代码 |
| `实例IP` | `string` | 由 `host` 经 AliDNS DoH（`https://223.5.5.5/resolve`）解析；解析不到为空串 |
| `登录状态` | `string` | `免登录` / `未登录`，口径见下 |
| `controlMode` | `string` | `COMMON` / `STABLE` / `NONE_CONTROL`，由 `sellCommon`、`sellStable` 推导 |
| `sellTrust` | `boolean` | 是否启用信任登录 |
| `实例来源` / `环境` / `实例状态` / `登录方式` / `版本` | `string` | 接口原始 `instanceSource` / `envType` / `instanceStatus` / `loginType` / `version` |
| `重复记录` | `number` | 同地址被合并掉的记录条数 |

### 判定口径

- 当前采集器在同一租户 / DMS Tid 的一次采集中按实例地址聚合展示，`dmsInstanceIds` 保留全部记录 ID，地址不同的副本集成员各自保留。不同 AppAtlas 租户分别采集再汇总，不跨租户按地址去重；该地址聚合不证明多个登记记录是同一个物理云实例。
- `登录状态` 按 DMS 控制台实例列表逻辑还原：`controlMode = COMMON`（安全模式）或 `sellTrust = true` 时为 `免登录`；其余实例在采集会话没有登录凭据，记为 `未登录`。采集过程不执行任何数据库登录。
- `实例IP` 只做 DNS 解析：本机 DNS 若处于代理 fake-IP 模式会返回 `198.18.0.0/15` 假地址，因此统一走 AliDNS DoH；解析为空表示该地址仅在 VPC 私网解析或实例已不存在。
- 该快照目前不参与 `/resources` 宽表与拓扑构建，仅用于 DMS 资源页面。

### 样本

```json
{
  "available": true,
  "source": "https://dms.aliyun.com/dms/metastore/instances/list",
  "collectedAt": "2026-10-20T09:39:24Z",
  "rawRecordCount": 68,
  "totalCount": 65,
  "instances": [
    {
      "实例名称": "Prod-UserCenter",
      "实例ID": "rm-uf6od1a9f4zc7oes4",
      "dmsInstanceIds": ["1308342"],
      "实例地址": "rm-uf6od1a9f4zc7oes4641.mysql.rds.aliyuncs.com:3306",
      "host": "rm-uf6od1a9f4zc7oes4641.mysql.rds.aliyuncs.com",
      "port": 3306,
      "类型": "MySQL",
      "dbType": "mysql",
      "实例地区": "华东2（上海）",
      "regionId": "cn-shanghai",
      "实例IP": "10.179.0.182",
      "登录状态": "免登录",
      "controlMode": "COMMON",
      "sellTrust": true,
      "envType": "product",
      "重复记录": 0
    }
  ]
}
```


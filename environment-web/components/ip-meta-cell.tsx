'use client';
import { Fragment } from 'react';
import { envs, shortLabels, apps, type Env } from '@/lib/inventory';
import { ipDeployEnvironments } from '@/lib/process-comparison';
import {
  isLocalDatacenterIp,
  RESOURCE_OWNER_LOCAL,
  RESOURCE_OWNER_ALIYUN,
} from '@/lib/datacenter-networks';

// 环境芯片：测试 / 仿真 / 生产；无任何环境部署记录时显示灰色"环境待识别"。
// empty='blank' 时（进程级环境，未映射到 DevOps 应用）无环境芯片直接留空。
export function IpEnvChips({ list, empty = 'pending' }: { list: Env[]; empty?: 'pending' | 'blank' }) {
  return (
    <Fragment>
      {envs
        .filter((e) => list.includes(e))
        .map((e) => (
          <span
            key={e}
            className={`env-chip env-chip-${e.toLowerCase()}`}
            title={`DevOps 应用列表中该 IP 存在${shortLabels[e]}环境的部署记录`}
          >
            {shortLabels[e]}
          </span>
        ))}
      {list.length === 0 && empty === 'pending' && (
        <span
          className="env-chip env-chip-none"
          title="未通过 DevOps 流水线部署业务应用，环境待识别"
        >
          环境待识别
        </span>
      )}
    </Fragment>
  );
}

// 资源归属芯片：本地机房（橙）/ 阿里云ECS（蓝）。
export function IpOwnerChip({ ip }: { ip: string }) {
  const local = isLocalDatacenterIp(ip);
  const label = local ? RESOURCE_OWNER_LOCAL : RESOURCE_OWNER_ALIYUN;
  return (
    <span
      className={`owner-chip ${local ? 'owner-local' : 'owner-cloud'}`}
      title={
        local
          ? `本地机房网段（${ip} 属于 10.58.1 / 10.58.9 / 10.58.10 / 10.58.140 之一）`
          : '非本地机房网段，归属阿里云 ECS'
      }
    >
      {label}
    </span>
  );
}

// IP 单元格统一内容：IP、服务器名称（无名称回退 IP）、DevOps 环境、资源归属。
// envList 可由调用方（IP 集查询行模型）显式传入；缺省时实时从 DevOps 清单计算。
export function IpMetaCell({
  ip,
  hostName,
  envList,
  envEmpty = 'pending',
}: {
  ip: string;
  hostName?: string;
  envList?: Env[];
  envEmpty?: 'pending' | 'blank';
}) {
  const name = hostName?.trim() || ip;
  const list = envList ?? ipDeployEnvironments(ip, apps);
  return (
    <span className="ip-meta-cell">
      <code>{ip}</code>
      <small className="block text-muted-foreground ip-meta-name">{name}</small>
      <span className="ip-meta-chips">
        <IpEnvChips list={list} empty={envEmpty} />
        <IpOwnerChip ip={ip} />
      </span>
    </span>
  );
}

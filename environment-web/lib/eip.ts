import snapshot from './eip-snapshot.json';
import type { Clb } from './clb';
import type { DnsView } from './dns';
import type { Instance } from './ecs';
import type { NatSnapshot } from './nat';

export type Eip = {
  key: string; id: string; name: string; owner: string; ip: string;
  bindingType: string; bindingId: string; bindingName: string; status: string;
  bandwidth: string; network: string; bandwidthPackage: string; billing: string;
  allocatedAt: string; resourceGroup: string; protection: string; tags: string; poolId: string;
  source: string; row: number; notes: { column: number; value: string }[];
};
export const eip = snapshot as { snapshotDate: string; region: string; sources: { file: string; owner: string; count: number }[]; records: Eip[] };

export function eipNatMappings(record: Eip, nat?: NatSnapshot) {
  if (record.bindingType !== 'NAT网关' || !nat?.available) return [];
  return nat.gateways.filter(gateway => gateway.id === record.bindingId).flatMap(gateway =>
    gateway.entries.filter(entry => entry.externalIp === record.ip).map(entry => ({ gateway: { id: gateway.id, name: gateway.name }, entry })));
}
export function eipClbEvidence(record: Eip, instance: Clb, nat?: NatSnapshot): string[] {
  const evidence = [];
  if (/SLB|CLB/i.test(record.bindingType) && record.bindingId && record.bindingId === instance.id) evidence.push('绑定实例 ID 一致');
  if (record.ip && record.ip === instance.ip) evidence.push('公网 IP 一致');
  for (const { gateway, entry } of eipNatMappings(record, nat)) {
    if (entry.internalIp === instance.ip && entry.status.toLowerCase() === 'available') {
      evidence.push(`DNAT ${gateway.id} / ${entry.id}：${entry.protocol} ${entry.externalIp}:${entry.externalPort} → ${entry.internalIp}:${entry.internalPort}`);
    }
  }
  return evidence;
}

export function integrateEip(instances: Clb[], domains: DnsView[], records: Eip[] = eip.records, ecs: Instance[] = [], nat?: NatSnapshot) {
  return records.map(record => {
    const gateway = record.bindingType === 'NAT网关' && nat?.available ? nat.gateways.find(gateway => gateway.id === record.bindingId) : undefined;
    return ({
    record,
    matches: instances.flatMap(instance => {
      const evidence = eipClbEvidence(record, instance, nat);
      return evidence.length ? [{ instance, evidence }] : [];
    }),
    domains: domains.filter(domain => domain.eipMatches.some(match => match.eip.key === record.key)),
    ecsMatches: record.bindingType === 'ECS实例' ? ecs.filter(instance => instance.id === record.bindingId) : [],
    natGateway: gateway ? { id: gateway.id, name: gateway.name, status: gateway.status, vpcId: gateway.vpcId } : undefined,
    mappings: eipNatMappings(record, nat),
  }); });
}
export type EipView = ReturnType<typeof integrateEip>[number];

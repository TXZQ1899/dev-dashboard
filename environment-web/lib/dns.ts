import snapshot from './dns-snapshot.json';
import type { Clb } from './clb';
import { eip, eipClbEvidence, type Eip } from './eip';
import type { NatSnapshot } from './nat';

export type DnsRecord = { id: string; zone: string; name: string; type: string; value: string; line: string; status: string; ttl: number | null; weight: number | null; policy: string; remark: string; source: string; row: number };
export const dns = snapshot as { zones: string[]; sources: { file: string; zone: string }[]; records: DnsRecord[] };
const normalize = (value: string) => value.trim().toLowerCase().replace(/\.$/, '');
const inScope = (name: string) => ['folidaymall.com', 'fosunholiday.com'].some(zone => name === zone || name.endsWith('.' + zone));
export function integrateDns(instances: Clb[], records: DnsRecord[] = dns.records, eips: Eip[] = eip.records, nat?: NatSnapshot) {
  const scoped = records.filter(r => inScope(normalize(r.name)));
  const resolve = (record: DnsRecord, seen: Set<string>): { ip: string; chain: string[] }[] => {
    if (record.status !== '启用' || seen.has(record.id)) return [];
    if (record.type === 'A' || record.type === 'AAAA') return [{ ip: normalize(record.value), chain: [record.name, record.value] }];
    if (record.type !== 'CNAME') return [];
    const next = new Set(seen).add(record.id);
    return scoped.filter(r => normalize(r.name) === normalize(record.value) && (r.line === record.line || r.line === '默认'))
      .flatMap(r => resolve(r, next).map(result => ({ ...result, chain: [record.name, ...result.chain] })));
  };
  return scoped.map(record => {
    const resolutions = resolve(record, new Set());
    const eipMatches = eips.flatMap(eip => {
      const paths = resolutions.filter(result => result.ip === normalize(eip.ip));
      return paths.length ? [{ eip, paths }] : [];
    });
    const matches = instances.flatMap(instance => {
      const paths = resolutions.flatMap(result => {
        if (result.ip === normalize(instance.ip)) return [{ ...result, via: '' }];
        const bound = eipMatches.filter(match => match.eip.ip === result.ip && eipClbEvidence(match.eip, instance, nat).length);
        return bound.map(match => ({ ...result, via: `${match.eip.owner} / ${match.eip.id} → ${instance.id}（${match.eip.bindingType === 'NAT网关' ? eipClbEvidence(match.eip, instance, nat).join('；') : '静态绑定实例 ID'}）` }));
      });
      return paths.length ? [{ instance, paths }] : [];
    });
    const status = record.status !== '启用' ? '未启用，不参与关联'
      : !['A', 'AAAA', 'CNAME'].includes(record.type) ? '非地址记录'
      : matches.length ? '已关联 CLB'
      : eipMatches.length ? '已关联 EIP，未匹配当前 CLB'
      : !resolutions.length ? 'CNAME 未能在静态数据内解析（外部目标、暂停或循环）' : '未匹配当前 CLB IP';
    return { record, resolutions, matches, eipMatches, status };
  });
}
export type DnsView = ReturnType<typeof integrateDns>[number];

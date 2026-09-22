import type { GraphCollector, SnapshotInput } from '../common.ts';
import { asArray, asRecord, domainId, hostId, normalizeDomain, normalizeIp, text, toIso } from '../common.ts';
import type { HostIndex } from './hosts.ts';
import type { Evidence, TopologyNode } from '../types.ts';

export type DnsRecordInfo = {
  recordId: string;
  name: string;
  type: string;
  value: string;
  status: string;
  sourceNode: TopologyNode;
  evidence: Evidence;
};

export type DnsBuildResult = {
  records: DnsRecordInfo[];
};

export function buildDnsNodes(graph: GraphCollector, input: SnapshotInput, hosts: HostIndex, knownIps: Set<string>): DnsBuildResult {
  const root = asRecord(input.dns);
  const records = asArray(root.records);
  const names = new Set(records.map(record => normalizeDomain(record.name)));
  const recordsInfo: DnsRecordInfo[] = [];

  const domainNode = (domain: string, status: TopologyNode['status'], evidence: Evidence, attributes: Record<string, unknown>) => graph.node({
    id: domainId(domain),
    type: 'DOMAIN',
    label: domain,
    identity: { name: domain },
    status,
    environment: 'GLOBAL',
    evidence: [evidence],
    attributes,
  });

  for (const record of records) {
    const type = text(record.type).toUpperCase();
    if (type !== 'A' && type !== 'AAAA' && type !== 'CNAME') continue;
    const name = normalizeDomain(record.name);
    const value = text(record.value);
    const status = text(record.status) === '暂停' ? 'paused' : 'active';
    const evidence: Evidence = {
      source: 'dns',
      sourceId: text(record.id),
      reference: `environment-web/lib/dns-snapshot.json (${text(record.source)} row ${text(record.row)})`,
      detail: `DNS ${type} ${name} -> ${value}, line ${text(record.line)}, status ${text(record.status)}`,
      observedAt: toIso(root.collectedAt) ?? toIso(root.snapshotDate),
    };
    const source = domainNode(name, status, evidence, {
      zone: text(record.zone),
      line: text(record.line),
      ttl: record.ttl,
      weight: record.weight ?? null,
      policy: text(record.policy),
      remark: text(record.remark),
    });
    recordsInfo.push({ recordId: text(record.id), name, type, value, status: text(record.status), sourceNode: source, evidence });

    if (type === 'CNAME') {
      const target = normalizeDomain(value);
      const targetStatus = names.has(target) ? 'active' : isManagedDomain(target) ? 'unresolved' : 'external';
      domainNode(target, targetStatus, evidence, { unresolvedReason: targetStatus === 'unresolved' ? '目标域名存在记录但未形成有效链路' : undefined });
    } else {
      const ip = normalizeIp(value);
      if (!ip || knownIps.has(ip) || hosts.byIp.has(ip)) continue;
      // Keep otherwise-unresolved address targets in the graph rather than discarding the DNS relation.
      graph.node({
        id: hostId(ip),
        type: 'HOST',
        label: ip,
        identity: { ip },
        status: 'unresolved',
        environment: 'GLOBAL',
        evidence: [evidence],
        attributes: { unresolvedReason: 'DNS address target not present in ECS, JumpServer, EIP, NAT, or CLB snapshots' },
      });
      knownIps.add(ip);
    }
  }

  return { records: recordsInfo };
}

function isManagedDomain(domain: string): boolean {
  return domain === 'folidaymall.com' || domain === 'fosunholiday.com' || domain.endsWith('.folidaymall.com') || domain.endsWith('.fosunholiday.com');
}

import type { GraphCollector } from '../common.ts';
import type { HostIndex } from '../node-builders/hosts.ts';
import { domainId, hostId, normalizeDomain, normalizeIp } from '../common.ts';
import type { CloudBuildResult } from '../node-builders/cloud.ts';
import type { DnsBuildResult } from '../node-builders/dns.ts';

export function buildDnsEdges(graph: GraphCollector, dns: DnsBuildResult, cloud: CloudBuildResult, hosts: HostIndex): void {
  for (const record of dns.records) {
    if (record.type === 'CNAME') {
      const target = normalizeDomain(record.value);
      graph.edge({
        id: `edge:CNAME_TO:${record.sourceNode.id}:${domainId(target)}`,
        from: record.sourceNode.id,
        to: domainId(target),
        type: 'CNAME_TO',
        environment: 'GLOBAL',
        evidence: [record.evidence],
        confidence: 'EXACT',
      });
      continue;
    }

    const ip = normalizeIp(record.value);
    if (!ip) continue;
    const targets: string[] = [];
    const eip = cloud.eipByIp.get(ip);
    if (eip) targets.push(eip.id);
    for (const clb of cloud.clbByIp.get(ip) || []) targets.push(clb.id);
    if (!targets.length) targets.push(hosts.byIp.get(ip)?.id ?? hostId(ip));
    for (const target of targets) {
      graph.edge({
        id: `edge:RESOLVES_TO:${record.sourceNode.id}:${target}`,
        from: record.sourceNode.id,
        to: target,
        type: 'RESOLVES_TO',
        environment: 'GLOBAL',
        evidence: [record.evidence],
        confidence: 'EXACT',
      });
    }
  }
}

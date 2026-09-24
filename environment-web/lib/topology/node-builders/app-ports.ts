import type { GraphCollector, SnapshotInput } from '../common.ts';
import { asArray, asRecord, endpointId, normalizeIp, normalizePort, text, toIso } from '../common.ts';
import type { Evidence, TopologyNode } from '../types.ts';

/**
 * JumpServer `inspection.appPorts` → topology endpoints.
 *
 * For each (host, port) observed at runtime by `ss -ltnp`, the running process
 * self-declares an application name (Java `-Dappid`, etc.). This builder turns
 * that ground truth into topology:
 *
 * - When an ENDPOINT node already exists at the same `ip:port` (built by the
 *   DevOps or Nginx builders), it is ENRICHED with the runtime app identity
 *   (`runtimeApp` / `runtimeKind` / `runtimePids`) and jumpserver evidence.
 *   This carries the self-declared app name onto the request resolver's main
 *   chain (which traverses the nginx `:http` / devops `:unknown` endpoint)
 *   instead of leaving it stranded on a parallel node.
 * - When no endpoint exists at `ip:port`, a concrete `endpoint:<ip>:<port>:tcp`
 *   node is created. These are returned so `buildAppPortEdges` can attach the
 *   `ON_HOST` edge; enriched nodes already carry their `ON_HOST` edge from the
 *   DevOps/Nginx edge builders.
 *
 * Only IPv4/IPv6 host IPs produce endpoints (the validator requires a valid
 * address); JumpServer assets with non-IP or multi-address fields are skipped.
 */
export type AppPortEndpoint = {
  endpointId: string;
  ip: string;
  evidence: Evidence[];
};

const IPv4 = /^(?:\d{1,3}\.){3}\d{1,3}$/;
const IPv6 = /^(?:[A-Fa-f0-9]{1,4}:){2,7}[A-Fa-f0-9]{1,4}$|^::1$|^(?:[A-Fa-f0-9]{1,4}:){1,7}:$/;

function isIp(value: string): boolean {
  return IPv4.test(value) || IPv6.test(value);
}

export function buildAppPortNodes(graph: GraphCollector, input: SnapshotInput): AppPortEndpoint[] {
  const root = asRecord(input.jumpserver);
  const jumpAt = toIso(root.collectedAt);

  // Index existing ENDPOINT nodes by normalized ip:port so appPorts enriches
  // them instead of spawning parallel :tcp nodes that fragment ip:port lookups.
  const byIpPort = new Map<string, TopologyNode[]>();
  for (const node of graph.getNodes()) {
    if (node.type !== 'ENDPOINT') continue;
    const ip = normalizeIp(node.identity.ip);
    const port = normalizePort(node.identity.port);
    if (!ip || port === 'unknown') continue;
    const key = `${ip}:${port}`;
    const list = byIpPort.get(key);
    if (list) list.push(node);
    else byIpPort.set(key, [node]);
  }

  const created: AppPortEndpoint[] = [];
  for (const asset of asArray(root.assets)) {
    const inspection = asRecord(asset.inspection);
    const appPorts = asArray(inspection.appPorts);
    if (!appPorts.length) continue;
    const ip = normalizeIp(text(asset.ip).split(/[\s,]+/)[0] ?? '');
    if (!isIp(ip)) continue;
    const assetId = text(asset.id);
    const checkedAt = toIso(inspection.checkedAt) ?? jumpAt;

    for (const entry of appPorts) {
      const port = normalizePort(entry.port);
      if (port === 'unknown' || port === 'any' || !/^\d+$/.test(port)) continue;
      const app = text(entry.app);
      const kind = text(entry.kind);
      const pids = asArray<number>(entry.pids);
      const addresses = asArray<string>(entry.addresses);
      const evidence: Evidence = {
        source: 'jumpserver',
        sourceId: `${assetId}:${app}:${port}`,
        reference: 'environment-web/lib/jumpserver-snapshot.json inspection.appPorts',
        detail: `Runtime ${kind} ${app} listening on ${ip}:${port}`,
        observedAt: checkedAt,
      };
      const attributes = {
        runtimeApp: app,
        runtimeKind: kind,
        runtimePids: pids,
        runtimeAddresses: addresses,
        discoveredBy: ['jumpserver'],
      };

      const existing = byIpPort.get(`${ip}:${port}`) ?? [];
      if (existing.length) {
        for (const node of existing) {
          graph.node({
            id: node.id,
            type: 'ENDPOINT',
            label: node.label,
            evidence: [evidence],
            attributes,
          });
        }
        continue;
      }

      const id = endpointId(ip, port, 'tcp');
      graph.node({
        id,
        type: 'ENDPOINT',
        label: `${ip}:${port}:tcp`,
        identity: { ip, port, protocol: 'tcp' },
        status: 'active',
        environment: 'GLOBAL',
        evidence: [evidence],
        attributes,
      });
      created.push({ endpointId: id, ip, evidence: [evidence] });
    }
  }
  return created;
}

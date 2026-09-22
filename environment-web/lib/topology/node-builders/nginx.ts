import type { GraphCollector, SnapshotInput } from '../common.ts';
import { asArray, asRecord, domainId, endpointId, normalizeDomain, normalizeIp, normalizePort, sha256Short, text, toIso } from '../common.ts';
import type { Evidence, TopologyNode } from '../types.ts';

export type NginxBridge = {
  routeNode: TopologyNode;
  upstreamNode: TopologyNode;
  assetId: string;
  domains: string[];
  backends: { host: string; port: string; resolution: string }[];
  endpointIds: string[];
  evidence: Evidence[];
};

export function buildNginxNodes(graph: GraphCollector, input: SnapshotInput): NginxBridge[] {
  const root = asRecord(input.jumpserver);
  const bridges: NginxBridge[] = [];
  for (const asset of asArray(root.assets)) {
    const inspection = asRecord(asset.inspection);
    const routes = asArray(inspection.nginxRoutes);
    if (!routes.length) continue;
    const assetId = text(asset.id);
    const checkedAt = toIso(inspection.checkedAt);
    for (const route of routes) {
      const domains = asArray<string>(route.domains).map(normalizeDomain).filter(Boolean);
      const uri = text(route.uri);
      const directive = text(route.directive);
      const target = text(route.target);
      const upstreamName = text(route.upstream) || target || `${directive}:${target}`;
      const key = `${assetId}|${domains.join(',')}|${uri}|${directive}|${target}|${upstreamName}`;
      const evidence: Evidence[] = [{
        source: 'jumpserver',
        sourceId: `${assetId}:${sha256Short(key)}`,
        reference: 'environment-web/lib/jumpserver-snapshot.json inspection.nginxRoutes',
        detail: `Nginx route ${domains.join(',') || '(no server_name)'}${uri} -> ${directive} ${target}`,
        observedAt: checkedAt,
      }];
      for (const domain of domains) {
        graph.node({
          id: domainId(domain),
          type: 'DOMAIN',
          label: domain,
          identity: { name: domain },
          status: 'active',
          environment: 'GLOBAL',
          evidence,
          attributes: { discoveredBy: ['nginx'] },
        });
      }

      const routeNode = graph.node({
        id: `nginx-route:${assetId}:${sha256Short(key)}`,
        type: 'NGINX_ROUTE',
        label: `${domains.join(',') || text(asset.hostname)}${uri}`,
        identity: { assetId, domains, uri, directive, target, upstream: upstreamName },
        status: directive === 'static/other' ? 'unknown' : 'active',
        environment: 'GLOBAL',
        evidence,
        attributes: {
          listen: asArray<string>(route.listen),
          context: text(route.context),
          instance: text(route.instance),
          nginxStatus: text(inspection.nginxStatus),
          configurationVersion: text(inspection.configurationVersion),
        },
      });

      const upstreamId = `nginx-upstream:${assetId}:${sha256Short(upstreamName)}`;
      const upstreamNode = graph.node({
        id: upstreamId,
        type: 'UPSTREAM',
        label: upstreamName,
        identity: { assetId, name: upstreamName },
        status: 'active',
        environment: 'GLOBAL',
        evidence,
        attributes: { target, directive },
      });

      const protocol = routeProtocol(route);
      const endpointIds: string[] = [];
      const backends = asArray<{host: string; port: string | null; resolution: string}>(route.backends).map(backend => ({
        host: text(backend.host),
        port: normalizePort(backend.port),
        resolution: text(backend.resolution),
      })).filter(backend => backend.host);
      for (const backend of backends) {
        // Only concrete IP backends can support an endpoint identity. Hostnames,
        // localhost, Nginx variables, and dynamic targets stay on the upstream
        // evidence instead of being fabricated as IP endpoints.
        if (backend.resolution !== 'ip') continue;
        const ip = normalizeIp(backend.host);
        if (!ip) continue;
        const id = endpointId(ip, backend.port, protocol);
        graph.node({
          id,
          type: 'ENDPOINT',
          label: `${ip}:${backend.port}:${protocol}`,
          identity: { ip, port: backend.port, protocol },
          status: 'active',
          environment: 'GLOBAL',
          evidence,
          attributes: { discoveredBy: ['nginx'], resolution: backend.resolution },
        });
        endpointIds.push(id);
      }
      bridges.push({ routeNode, upstreamNode, assetId, domains, backends, endpointIds, evidence });
    }
  }
  return bridges;
}

function routeProtocol(route: Record<string, unknown>): string {
  const target = text(route.target).toLowerCase();
  if (target.startsWith('https://')) return 'https';
  if (target.startsWith('http://')) return 'http';
  if (text(route.context) === 'stream') return 'tcp';
  return 'unknown';
}

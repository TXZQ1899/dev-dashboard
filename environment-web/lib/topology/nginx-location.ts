import { text } from './common.ts';
import type { TopologyEdge, TopologyEnvironment, TopologyNode } from './types.ts';
import { buildGraphIndex, type GraphIndex } from './path-explorer.ts';
import { buildListenerIndex, matchBackend, type ChainBackend } from './domain-chain.ts';

/**
 * Task-level projection for the operator question:
 *
 *   URI location (如 /tc-fostay) -> 命中的 Nginx 路由（server_name / listen /
 *   来源配置文件） -> upstream -> 后端 IP:端口 -> DevOps 应用
 *
 * This is a reverse lookup on collected nginx configs: the query string is
 * matched as a case-insensitive substring of the route's location URI, so
 * "/tc-fostay" finds `location /tc-fostay` and `location /tc-fostay/api`.
 * Exact matches are listed first. Backend correlation reuses the domain-chain
 * rules: IP+PORT only, protocol labels normalized, same-host other-port
 * deployments stay non-authoritative hints.
 */

export type NginxLocationQuery = {
  uri: string;
  /** Restrict application matches to one deployment environment; null = all. */
  environment?: TopologyEnvironment | null;
};

export type NginxLocationUpstream = {
  upstream: TopologyNode;
  backends: ChainBackend[];
};

export type NginxLocationMatch = {
  route: TopologyNode;
  uri: string;
  domains: string[];
  listen: string[];
  directive: string;
  target: string;
  /** Source config file path collected with the route ('' when the topology predates the attribute). */
  file: string;
  assetId: string;
  /** Host owning this nginx asset; null when the asset is absent from HOST inventory. */
  host: TopologyNode | null;
  upstreams: NginxLocationUpstream[];
};

export type NginxLocationResult = {
  query: { uri: string; normalizedUri: string; environment: TopologyEnvironment | null };
  status: 'found' | 'not-found';
  matches: NginxLocationMatch[];
  stats: {
    routeCount: number;
    upstreamCount: number;
    backendCount: number;
    exactBackendCount: number;
    ambiguousBackendCount: number;
    unresolvedBackendCount: number;
    applicationIds: string[];
  };
  warnings: string[];
};

const MAX_MATCHES = 200;

export function findNginxLocations(index: GraphIndex, query: NginxLocationQuery): NginxLocationResult {
  const environment = query.environment ?? null;
  const raw = query.uri.trim();
  const normalized = raw.toLowerCase();
  const warnings: string[] = [];
  if (!normalized) {
    return emptyResult(raw, normalized, environment, ['查询为空，请输入要查找的 location URI（如 /tc-fostay）。']);
  }

  const hostByAssetId = new Map<string, TopologyNode>();
  for (const node of index.topology.nodes) {
    if (node.type !== 'HOST') continue;
    for (const assetId of StringArray(node.identity.jumpserverAssetIds)) hostByAssetId.set(assetId, node);
  }

  const routes = index.topology.nodes.filter(node => node.type === 'NGINX_ROUTE');
  const hits = routes.filter(node => text(node.identity.uri).toLowerCase().includes(normalized));
  hits.sort((a, b) => {
    const aExact = text(a.identity.uri).toLowerCase() === normalized ? 0 : 1;
    const bExact = text(b.identity.uri).toLowerCase() === normalized ? 0 : 1;
    if (aExact !== bExact) return aExact - bExact;
    return a.id.localeCompare(b.id);
  });
  if (hits.length === 0) return emptyResult(raw, normalized, environment, []);

  let truncated = false;
  const selected = hits.length > MAX_MATCHES ? hits.slice(0, MAX_MATCHES) : hits;
  if (hits.length > MAX_MATCHES) {
    truncated = true;
    warnings.push(`匹配到 ${hits.length} 条路由，已截断为前 ${MAX_MATCHES} 条；请输入更具体的 URI。`);
  }

  const listeners = buildListenerIndex(index, environment);
  const matches: NginxLocationMatch[] = [];
  for (const route of selected) {
    const assetId = text(route.identity.assetId);
    const upstreams: NginxLocationUpstream[] = [];
    for (const upStep of forwardSteps(index, route.id, 'USES_UPSTREAM')) {
      const upstream = index.nodeById.get(upStep.neighborId);
      if (!upstream) continue;
      const backends: ChainBackend[] = [];
      const seen = new Set<string>();
      for (const fwd of forwardSteps(index, upstream.id, 'FORWARDS_TO')) {
        const endpoint = index.nodeById.get(fwd.neighborId);
        if (!endpoint || seen.has(endpoint.id)) continue;
        seen.add(endpoint.id);
        backends.push(matchBackend(index, endpoint, fwd.edge, listeners));
      }
      backends.sort((a, b) => a.endpoint.id.localeCompare(b.endpoint.id));
      upstreams.push({ upstream, backends });
    }
    upstreams.sort((a, b) => a.upstream.id.localeCompare(b.upstream.id));
    matches.push({
      route,
      uri: text(route.identity.uri) || '(server-level)',
      domains: StringArray(route.identity.domains),
      listen: StringArray(route.attributes.listen),
      directive: text(route.identity.directive),
      target: text(route.identity.target),
      file: text(route.attributes.file),
      assetId,
      host: hostByAssetId.get(assetId) ?? null,
      upstreams,
    });
  }

  for (const match of matches) {
    if (!match.host) warnings.push(`Nginx 资产 ${match.assetId}（location ${match.uri}）不在主机清单中，无法定位接入机器。`);
  }
  const missingFile = matches.filter(match => !match.file).length;
  if (missingFile > 0) {
    warnings.push(`${missingFile} 条路由缺少来源配置文件路径：当前 Topology 版本生成于 file 字段加入之前，重新生成拓扑后可见。`);
  }
  if (truncated) warnings.push('提示：location 为 "/" 时会匹配全部路由，结果已被截断。');

  const allBackends = matches.flatMap(match => match.upstreams.flatMap(upstream => upstream.backends));
  const distinctApps = new Set<string>();
  for (const backend of allBackends) {
    for (const match of backend.matches) distinctApps.add(match.application.id);
  }

  return {
    query: { uri: raw, normalizedUri: normalized, environment },
    status: 'found',
    matches,
    stats: {
      routeCount: matches.length,
      upstreamCount: new Set(matches.flatMap(match => match.upstreams.map(upstream => upstream.upstream.id))).size,
      backendCount: allBackends.length,
      exactBackendCount: allBackends.filter(backend => backend.status === 'exact').length,
      ambiguousBackendCount: allBackends.filter(backend => backend.status === 'ambiguous').length,
      unresolvedBackendCount: allBackends.filter(backend => backend.status === 'unresolved').length,
      applicationIds: [...distinctApps].sort(),
    },
    warnings: [...new Set(warnings)],
  };
}

/** Convenience wrapper: build the index and answer one nginx-location query. */
export function findNginxLocationsInGraph(
  topology: Parameters<typeof buildGraphIndex>[0],
  query: NginxLocationQuery,
): NginxLocationResult {
  return findNginxLocations(buildGraphIndex(topology), query);
}

function emptyResult(
  uri: string,
  normalizedUri: string,
  environment: TopologyEnvironment | null,
  warnings: string[],
): NginxLocationResult {
  return {
    query: { uri, normalizedUri, environment },
    status: 'not-found',
    matches: [],
    stats: { routeCount: 0, upstreamCount: 0, backendCount: 0, exactBackendCount: 0, ambiguousBackendCount: 0, unresolvedBackendCount: 0, applicationIds: [] },
    warnings: [...new Set(warnings)],
  };
}

function forwardSteps(index: GraphIndex, nodeId: string, type?: TopologyEdge['type']): { edge: TopologyEdge; neighborId: string }[] {
  const steps: { edge: TopologyEdge; neighborId: string }[] = [];
  for (const step of index.adjacency.get(nodeId) ?? []) {
    if (step.direction === 'forward' && (!type || step.edge.type === type)) steps.push({ edge: step.edge, neighborId: step.neighborId });
  }
  return steps.sort((a, b) => a.neighborId.localeCompare(b.neighborId));
}

function StringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map(item => text(item)).filter(Boolean) : [];
}

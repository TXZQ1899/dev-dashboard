import { normalizeIp, text } from './common.ts';
import type { Confidence, TopologyEdge, TopologyEnvironment, TopologyGraph, TopologyNode } from './types.ts';
import { buildGraphIndex, resolveNodes, type GraphIndex } from './path-explorer.ts';

/**
 * Task-level projection for the operator question:
 *
 *   域名 -> EIP -> NAT/ECS/CLB 入口 -> Nginx 主机 -> Upstream 后端 IP:端口 -> 应用
 *
 * Unlike the generic path enumerator this is a deterministic, staged view:
 * nginx locations are folded into upstream groups, and backend endpoints are
 * correlated to deployments by IP+PORT only (protocol labels are normalized).
 * Traffic is never joined "through the host" to a different port: when no
 * IP+port listener exists the backend stays unresolved and same-IP other-port
 * deployments are reported as non-authoritative hints.
 */
export type DomainChainQuery = {
  domain: string;
  /** Restrict deployment/application matches to one environment. Stages 1-4 are GLOBAL evidence. */
  environment?: TopologyEnvironment;
};

export type ChainAppMatch = {
  application: TopologyNode;
  deployment: TopologyNode;
  /** The deployment's own LISTENS_ON endpoint (protocol label may differ from the forwarded endpoint). */
  listenerEndpoint: TopologyNode;
  environment: TopologyEnvironment;
  confidence: Confidence;
  /** True when listener and forwarded endpoint carry different protocol labels but share IP+port. */
  protocolMismatch: boolean;
};

export type ChainHostCandidate = {
  application: TopologyNode;
  deployment: TopologyNode;
  port: string;
  environment: TopologyEnvironment;
};

export type ChainBackend = {
  /** Endpoint the nginx upstream forwards to (nginx-side protocol label). */
  endpoint: TopologyNode;
  host: TopologyNode | null;
  forwardEdge: TopologyEdge;
  /** exact: unique IP+port deployment match; ambiguous: several apps/edges; unresolved: no IP+port match. */
  status: 'exact' | 'ambiguous' | 'unresolved';
  matches: ChainAppMatch[];
  /** Deployments on the same host IP but on different ports. Hints only — never treated as the target. */
  sameHostCandidates: ChainHostCandidate[];
};

export type ChainUpstreamGroup = {
  upstream: TopologyNode;
  /** proxy: IP backends exist; static: static/other directive; external: hostname/dynamic target only. */
  kind: 'proxy' | 'static' | 'external';
  routeIds: string[];
  uris: string[];
  uriCount: number;
  directives: string[];
  /** Raw proxy_pass/target text for routes without concrete IP backends. */
  targets: string[];
  backends: ChainBackend[];
};

export type ChainNginxHost = {
  assetId: string;
  /** Logical host owning this asset; null when the asset is absent from HOST inventory. */
  host: TopologyNode | null;
  nginxStatus: string;
  configurationVersion: string;
  listen: string[];
  context: string[];
  /** Number of routes on this asset that serve the queried domain. */
  routeCount: number;
  /** Total routes on this asset (all domains). 0 when not yet counted. */
  totalRouteCount: number;
  routeIds: string[];
};

export type ChainDnat = {
  rule: TopologyNode;
  external: TopologyNode | null;
  internal: TopologyNode | null;
  internalHost: TopologyNode | null;
};

export type ChainClbBackend = {
  listener: TopologyNode;
  group: TopologyNode;
  endpoint: TopologyNode;
  host: TopologyNode | null;
};

export type ChainBinding = {
  edge: TopologyEdge;
  kind: 'nat-gateway' | 'ecs-host' | 'clb';
  gateway: TopologyNode | null;
  host: TopologyNode | null;
  clb: TopologyNode | null;
};

export type ChainEntry = {
  resolveEdge: TopologyEdge;
  /** DNS target: EIP, CLB (A record equals CLB IP) or HOST placeholder. */
  target: TopologyNode;
  targetKind: 'eip' | 'clb' | 'host';
  binding: ChainBinding | null;
  /** DNAT rules whose external IP equals this EIP. */
  dnat: ChainDnat[];
  /** Backend endpoints when the entry (directly or via binding) is a CLB. */
  clbBackends: ChainClbBackend[];
};

export type ChainCnameHop = {
  edge: TopologyEdge;
  domain: TopologyNode;
};

export type DomainChainResult = {
  query: { domain: string; normalizedDomain: string; environment: TopologyEnvironment | null };
  resolution: { status: 'found' | 'not-found'; candidates: TopologyNode[] };
  status: 'resolved' | 'ambiguous' | 'unresolved';
  cnameChain: ChainCnameHop[];
  entries: ChainEntry[];
  nginxHosts: ChainNginxHost[];
  groups: ChainUpstreamGroup[];
  /** True when graph evidence connects the DNS entry layer to the nginx config layer. */
  connected: boolean;
  stats: {
    routeCount: number;
    upstreamCount: number;
    backendCount: number;
    exactBackendCount: number;
    ambiguousBackendCount: number;
    unresolvedBackendCount: number;
    applicationIds: string[];
  };
  gaps: string[];
};

const URI_SAMPLE = 20;
const TARGET_SAMPLE = 5;
const CANDIDATE_CAP = 8;
const GAP_LIST_CAP = 15;
const RANK: Record<Confidence, number> = { EXACT: 3, INFERRED: 2, AMBIGUOUS: 1, UNKNOWN: 0 };

type Step = { edge: TopologyEdge; neighborId: string; direction: 'forward' | 'reverse' };

export function exploreDomainChain(index: GraphIndex, query: DomainChainQuery): DomainChainResult {
  const gaps: string[] = [];
  const environment = query.environment ?? null;
  const resolution = resolveNodes(index, 'domain', query.domain);
  const normalizedDomain = resolution.candidates[0]?.identity.name
    ? String(resolution.candidates[0].identity.name)
    : query.domain.trim().replace(/\.$/, '').toLowerCase();

  if (resolution.status === 'not-found') {
    gaps.push(`No DOMAIN matches "${query.domain}" in the topology.`);
    return emptyResult(query.domain, normalizedDomain, environment, resolution.candidates, gaps);
  }
  if (resolution.candidates.length > 1) {
    gaps.push(`Query "${query.domain}" matched ${resolution.candidates.length} DOMAIN nodes; all were projected.`);
  }

  const startIds = resolution.candidates.map(node => node.id);

  // ---- Stage 1: CNAME chain (forward only, cycle-safe) --------------------
  const cnameChain: ChainCnameHop[] = [];
  const cnameVisited = new Set<string>(startIds);
  let frontier = [...startIds];
  while (frontier.length) {
    const next: string[] = [];
    for (const id of frontier) {
      for (const step of forwardSteps(index, id, 'CNAME_TO')) {
        if (cnameVisited.has(step.neighborId)) continue;
        cnameVisited.add(step.neighborId);
        const node = index.nodeById.get(step.neighborId);
        if (node) {
          cnameChain.push({ edge: step.edge, domain: node });
          next.push(node.id);
        }
      }
    }
    frontier = next;
  }
  cnameChain.sort((a, b) => a.edge.id.localeCompare(b.edge.id));
  for (const hop of cnameChain) {
    if (hop.domain.status === 'external' || hop.domain.status === 'unresolved') {
      gaps.push(`CNAME target ${hop.domain.id} is ${hop.domain.status}; traffic beyond it is not represented as internal resources.`);
    }
  }

  // ---- Stage 1b: A/AAAA resolutions from the domain and its CNAME targets --
  const dnsDomainIds = [...startIds, ...cnameChain.map(hop => hop.domain.id)];
  const entries: ChainEntry[] = [];
  const seenEntry = new Set<string>();
  for (const domainId of dnsDomainIds) {
    for (const step of forwardSteps(index, domainId, 'RESOLVES_TO')) {
      const target = index.nodeById.get(step.neighborId);
      if (!target || seenEntry.has(target.id)) continue;
      seenEntry.add(target.id);
      entries.push(buildEntry(index, step, target, () => text(target.identity.ip)));
    }
  }
  entries.sort((a, b) => a.target.id.localeCompare(b.target.id));
  if (entries.length === 0) gaps.push(`Domain has no A/AAAA resolution (RESOLVES_TO) to an EIP, CLB or host in the topology.`);

  // ---- Stage 3: nginx routes serving this domain / aliases ----------------
  // Build a set of domain names (lowercase) from the query and CNAME targets
  // to explicitly verify route.identity.domains — defense-in-depth on top of
  // the SERVED_BY edge, which may be over-broad in some topology builds.
  const domainNames = new Set<string>();
  for (const id of dnsDomainIds) {
    const n = index.nodeById.get(id);
    const name = text(n?.identity.name);
    if (name) domainNames.add(name.toLowerCase());
  }
  domainNames.add(query.domain.trim().replace(/\.$/, '').toLowerCase());

  const routeIds = new Set<string>();
  for (const domainId of dnsDomainIds) {
    for (const step of forwardSteps(index, domainId, 'SERVED_BY')) {
      const route = index.nodeById.get(step.neighborId);
      if (route?.type !== 'NGINX_ROUTE') continue;
      // Explicit domain filter: only routes whose server_name includes the
      // queried domain (or a CNAME target) are collected. Routes with no
      // domains field (server-level) are admitted as they serve all domains.
      const routeDomains = StringArray(route.identity.domains).map(d => d.toLowerCase());
      if (routeDomains.length === 0 || routeDomains.some(d => domainNames.has(d))) {
        routeIds.add(step.neighborId);
      }
    }
  }

  // Count total routes per asset (all domains) for display context.
  const totalRoutesByAsset = new Map<string, number>();
  for (const node of index.topology.nodes) {
    if (node.type !== 'NGINX_ROUTE') continue;
    const assetId = text(node.identity.assetId);
    if (assetId) totalRoutesByAsset.set(assetId, (totalRoutesByAsset.get(assetId) ?? 0) + 1);
  }

  const hostByAssetId = new Map<string, TopologyNode>();
  for (const node of index.topology.nodes) {
    if (node.type !== 'HOST') continue;
    for (const assetId of StringArray(node.identity.jumpserverAssetIds)) hostByAssetId.set(assetId, node);
  }

  const nginxByAsset = new Map<string, { host: TopologyNode | null; routeIds: Set<string>; statuses: Set<string>; versions: Set<string>; listen: Set<string>; context: Set<string> }>();
  for (const routeId of routeIds) {
    const route = index.nodeById.get(routeId);
    if (!route) continue;
    const assetId = text(route.identity.assetId);
    let bucket = nginxByAsset.get(assetId);
    if (!bucket) {
      bucket = { host: hostByAssetId.get(assetId) ?? null, routeIds: new Set(), statuses: new Set(), versions: new Set(), listen: new Set(), context: new Set() };
      nginxByAsset.set(assetId, bucket);
    }
    bucket.routeIds.add(routeId);
    const status = text(route.attributes.nginxStatus);
    if (status) bucket.statuses.add(status);
    const version = text(route.attributes.configurationVersion);
    if (version) bucket.versions.add(version);
    for (const item of StringArray(route.attributes.listen)) bucket.listen.add(item);
    const context = text(route.attributes.context);
    if (context) bucket.context.add(context);
  }
  const nginxHosts: ChainNginxHost[] = [...nginxByAsset.entries()]
    .map(([assetId, bucket]) => ({
      assetId,
      host: bucket.host,
      nginxStatus: [...bucket.statuses].sort().join(',') || 'unknown',
      configurationVersion: [...bucket.versions].sort()[0] ?? '',
      listen: [...bucket.listen].sort(),
      context: [...bucket.context].sort(),
      routeCount: bucket.routeIds.size,
      totalRouteCount: totalRoutesByAsset.get(assetId) ?? 0,
      routeIds: [...bucket.routeIds].sort(),
    }))
    .sort((a, b) => (a.host?.id ?? `asset:${a.assetId}`).localeCompare(b.host?.id ?? `asset:${b.assetId}`));
  for (const item of nginxHosts) {
    if (!item.host) gaps.push(`Nginx asset ${item.assetId} serves this domain but is absent from HOST inventory; the ingress machine cannot be located.`);
  }
  if (routeIds.size === 0) gaps.push(`No nginx route (SERVED_BY) serves this domain; either no nginx config was collected for it or traffic is served by a non-nginx entry.`);

  // ---- Stage 4/5: upstream groups and IP+port application matches ----------
  const listeners = buildListenerIndex(index, environment);
  const groups = new Map<string, ChainUpstreamGroup & { routes: Set<string> }>();
  for (const routeId of routeIds) {
    const route = index.nodeById.get(routeId);
    if (!route) continue;
    const upstreamSteps = forwardSteps(index, routeId, 'USES_UPSTREAM');
    for (const up of upstreamSteps) {
      const upstream = index.nodeById.get(up.neighborId);
      if (!upstream) continue;
      let group = groups.get(upstream.id);
      if (!group) {
        group = {
          upstream,
          kind: 'proxy',
          routes: new Set(),
          routeIds: [],
          uris: [],
          uriCount: 0,
          directives: [],
          targets: [],
          backends: [],
        };
        groups.set(upstream.id, group);
      }
      group.routes.add(routeId);
      const uri = text(route.identity.uri) || '(server-level)';
      if (!group.uris.includes(uri) && group.uris.length < URI_SAMPLE) group.uris.push(uri);
      const directive = text(route.identity.directive);
      if (directive && !group.directives.includes(directive)) group.directives.push(directive);
      const target = text(route.identity.target) || text(upstream.attributes.target);
      if (target && !group.targets.includes(target) && group.targets.length < TARGET_SAMPLE) group.targets.push(target);
    }
  }

  const allBackends: ChainBackend[] = [];
  const finalizedGroups: ChainUpstreamGroup[] = [];
  for (const group of groups.values()) {
    const seenEndpoint = new Set<string>();
    for (const routeId of group.routes) {
      for (const up of forwardSteps(index, routeId, 'USES_UPSTREAM')) {
        if (up.neighborId !== group.upstream.id) continue;
        for (const fwd of forwardSteps(index, group.upstream.id, 'FORWARDS_TO')) {
          const endpoint = index.nodeById.get(fwd.neighborId);
          if (!endpoint || seenEndpoint.has(endpoint.id)) continue;
          seenEndpoint.add(endpoint.id);
          const backend = matchBackend(index, endpoint, fwd.edge, listeners);
          group.backends.push(backend);
          allBackends.push(backend);
        }
      }
    }
    group.backends.sort((a, b) => a.endpoint.id.localeCompare(b.endpoint.id));
    const directive = group.directives.join(',');
    group.kind = group.backends.length ? 'proxy' : /static|other/.test(directive) ? 'static' : 'external';
    group.routeIds = [...group.routes].sort();
    group.uriCount = group.routes.size;
    group.uris.sort();
    group.directives.sort();
    group.targets.sort();
    finalizedGroups.push(group);
  }
  finalizedGroups.sort(compareGroups);

  // ---- Connectivity between entry layer and nginx config layer -------------
  const entryHostIps = collectEntryHostIps(entries);
  const nginxHostIps = new Set<string>();
  for (const item of nginxHosts) {
    const ip = normalizeIp(text(item.host?.identity.ip));
    if (ip) nginxHostIps.add(ip);
  }
  const connected = [...entryHostIps].some(ip => nginxHostIps.has(ip));
  if (entries.length && nginxHosts.length && !connected) {
    gaps.push(
      `Entry layer host(s) [${[...entryHostIps].slice(0, 10).join(', ') || 'no host IP'}] from DNS/EIP/DNAT/CLB evidence are not connected ` +
      `to the nginx config host(s) [${[...nginxHostIps].join(', ')}]: DNAT/CLB/front-proxy evidence is missing, so the public-to-nginx hop is unresolved.`,
    );
  }

  // ---- Per-entry binding diagnostics --------------------------------------
  for (const entry of entries) {
    const ip = text(entry.target.identity.ip);
    if (entry.targetKind === 'eip' && !entry.binding) {
      gaps.push(`EIP ${ip} has no BOUND_TO target (not linked to a NAT gateway, CLB or ECS) in the topology.`);
    }
    if (entry.binding?.kind === 'nat-gateway' && entry.dnat.length === 0) {
      gaps.push(`EIP ${ip} is bound to NAT gateway ${entry.binding.gateway?.id ?? ''} but no DNAT rule exposes this EIP IP; the internal IP:port is unknown.`);
    }
    if (entry.binding?.kind === 'ecs-host') {
      gaps.push(`EIP ${ip} is bound directly to ECS host ${entry.binding.host?.id ?? ''}; no port-level DNAT mapping exists, so the exposed server port is not evidenced.`);
    }
    if (entry.targetKind === 'host') {
      gaps.push(`DNS A record ${ip} resolves to a HOST without an EIP/CLB entity; only host-level (not port-level) placement is evidenced.`);
    }
  }

  const unresolved = allBackends.filter(backend => backend.status === 'unresolved');
  for (const backend of unresolved.slice(0, GAP_LIST_CAP)) {
    const ep = backend.endpoint.identity;
    gaps.push(`Backend ${String(ep.ip)}:${String(ep.port)} has no deployment listening on the same IP+port in DevOps${environment ? ` (${environment})` : ''}; the receiving application is unresolved.`);
  }
  if (unresolved.length > GAP_LIST_CAP) gaps.push(`… ${unresolved.length - GAP_LIST_CAP} more unresolved backends omitted from this list.`);

  const exactBackendCount = allBackends.filter(backend => backend.status === 'exact').length;
  const ambiguousBackendCount = allBackends.filter(backend => backend.status === 'ambiguous').length;
  const applicationIds = [...new Set(allBackends.flatMap(backend => backend.matches.map(match => match.application.id)))].sort();

  let status: DomainChainResult['status'] = 'unresolved';
  if (exactBackendCount > 0) status = 'resolved';
  else if (ambiguousBackendCount > 0 || unresolved.some(backend => backend.sameHostCandidates.length > 0)) status = 'ambiguous';

  return {
    query: { domain: query.domain.trim(), normalizedDomain, environment },
    resolution: { status: 'found', candidates: resolution.candidates },
    status,
    cnameChain,
    entries,
    nginxHosts,
    groups: finalizedGroups,
    connected,
    stats: {
      routeCount: routeIds.size,
      upstreamCount: finalizedGroups.length,
      backendCount: allBackends.length,
      exactBackendCount,
      ambiguousBackendCount,
      unresolvedBackendCount: unresolved.length,
      applicationIds,
    },
    gaps: [...new Set(gaps)],
  };
}

/** Convenience wrapper: build the index and answer one domain-chain query. */
export function findDomainChain(topology: TopologyGraph, query: DomainChainQuery): DomainChainResult {
  return exploreDomainChain(buildGraphIndex(topology), query);
}

function emptyResult(
  domain: string,
  normalizedDomain: string,
  environment: TopologyEnvironment | null,
  candidates: TopologyNode[],
  gaps: string[],
): DomainChainResult {
  return {
    query: { domain: domain.trim(), normalizedDomain, environment },
    resolution: { status: 'not-found', candidates },
    status: 'unresolved',
    cnameChain: [],
    entries: [],
    nginxHosts: [],
    groups: [],
    connected: false,
    stats: { routeCount: 0, upstreamCount: 0, backendCount: 0, exactBackendCount: 0, ambiguousBackendCount: 0, unresolvedBackendCount: 0, applicationIds: [] },
    gaps,
  };
}

function forwardSteps(index: GraphIndex, nodeId: string, type?: TopologyEdge['type']): Step[] {
  const steps: Step[] = [];
  for (const step of index.adjacency.get(nodeId) ?? []) {
    if (step.direction === 'forward' && (!type || step.edge.type === type)) steps.push(step);
  }
  return steps.sort((a, b) => a.neighborId.localeCompare(b.neighborId));
}

function StringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map(item => text(item)).filter(Boolean) : [];
}

function buildEntry(index: GraphIndex, resolveStep: Step, target: TopologyNode, eipIp: () => string): ChainEntry {
  const targetKind: ChainEntry['targetKind'] = target.type === 'EIP' ? 'eip' : target.type === 'CLB' ? 'clb' : 'host';
  let binding: ChainBinding | null = null;
  let dnat: ChainDnat[] = [];
  const clbBackends: ChainClbBackend[] = [];

  if (targetKind === 'eip') {
    const bound = forwardSteps(index, target.id, 'BOUND_TO')[0];
    if (bound) {
      const boundNode = index.nodeById.get(bound.neighborId) ?? null;
      const kind: ChainBinding['kind'] = boundNode?.type === 'NAT_GATEWAY' ? 'nat-gateway' : boundNode?.type === 'CLB' ? 'clb' : 'ecs-host';
      binding = {
        edge: bound.edge,
        kind,
        gateway: kind === 'nat-gateway' ? boundNode : null,
        clb: kind === 'clb' ? boundNode : null,
        host: boundNode?.type === 'HOST' ? boundNode : null,
      };
      if (boundNode?.type === 'NAT_GATEWAY') dnat = collectDnat(index, boundNode, eipIp());
      if (boundNode?.type === 'CLB') clbBackends.push(...collectClbBackends(index, boundNode));
    }
  }
  if (targetKind === 'clb') clbBackends.push(...collectClbBackends(index, target));

  return { resolveEdge: resolveStep.edge, target, targetKind, binding, dnat, clbBackends };
}

function collectDnat(index: GraphIndex, gateway: TopologyNode, externalIp: string): ChainDnat[] {
  const wanted = normalizeIp(externalIp);
  const result: ChainDnat[] = [];
  for (const step of forwardSteps(index, gateway.id, 'HAS_DNAT_RULE')) {
    const rule = index.nodeById.get(step.neighborId);
    if (!rule) continue;
    let external: TopologyNode | null = null;
    let internal: TopologyNode | null = null;
    for (const sub of forwardSteps(index, rule.id)) {
      const node = index.nodeById.get(sub.neighborId);
      if (!node) continue;
      if (sub.edge.type === 'EXPOSES') external = node;
      if (sub.edge.type === 'FORWARDS_TO') internal = node;
    }
    // Only rules that expose THIS EIP belong to the entry chain.
    if (wanted && external && normalizeIp(text(external.identity.ip)) !== wanted) continue;
    result.push({ rule, external, internal, internalHost: endpointHost(index, internal) });
  }
  return result.sort((a, b) => a.rule.id.localeCompare(b.rule.id));
}

function collectClbBackends(index: GraphIndex, clb: TopologyNode): ChainClbBackend[] {
  const result: ChainClbBackend[] = [];
  for (const listenerStep of forwardSteps(index, clb.id, 'HAS_LISTENER')) {
    const listener = index.nodeById.get(listenerStep.neighborId);
    if (!listener) continue;
    for (const routeStep of forwardSteps(index, listener.id, 'ROUTES_TO')) {
      const group = index.nodeById.get(routeStep.neighborId);
      if (!group) continue;
      for (const fwd of forwardSteps(index, group.id, 'FORWARDS_TO')) {
        const endpoint = index.nodeById.get(fwd.neighborId);
        if (!endpoint) continue;
        result.push({ listener, group, endpoint, host: endpointHost(index, endpoint) });
      }
    }
  }
  return result.sort((a, b) => a.endpoint.id.localeCompare(b.endpoint.id));
}

function endpointHost(index: GraphIndex, endpoint: TopologyNode | null): TopologyNode | null {
  if (!endpoint) return null;
  for (const step of forwardSteps(index, endpoint.id, 'ON_HOST')) {
    const host = index.nodeById.get(step.neighborId);
    if (host?.type === 'HOST') return host;
  }
  return null;
}

type Listener = { endpoint: TopologyNode; edge: TopologyEdge; deployment: TopologyNode; application: TopologyNode };

function buildListenerIndex(index: GraphIndex, environment: TopologyEnvironment | null): Map<string, Listener[]> {
  const byIpPort = new Map<string, Listener[]>();
  const allowed = (edge: TopologyEdge) => !environment || edge.environment === 'GLOBAL' || edge.environment === environment;
  for (const edge of index.topology.edges) {
    if (edge.type !== 'LISTENS_ON' || !allowed(edge)) continue;
    const deployment = index.nodeById.get(edge.from);
    const endpoint = index.nodeById.get(edge.to);
    if (!deployment || !endpoint) continue;
    const hasEdge = index.topology.edges.find(item => item.type === 'HAS_DEPLOYMENT' && item.to === deployment.id && (!environment || item.environment === 'GLOBAL' || item.environment === environment));
    if (!hasEdge) continue;
    const application = index.nodeById.get(hasEdge.from);
    if (!application) continue;
    const key = `${normalizeIp(text(endpoint.identity.ip))}:${text(endpoint.identity.port)}`;
    const list = byIpPort.get(key);
    const listener = { endpoint, edge, deployment, application };
    if (list) list.push(listener);
    else byIpPort.set(key, [listener]);
  }
  for (const list of byIpPort.values()) list.sort((a, b) => a.application.id.localeCompare(b.application.id) || a.deployment.id.localeCompare(b.deployment.id));
  return byIpPort;
}

function matchBackend(index: GraphIndex, endpoint: TopologyNode, forwardEdge: TopologyEdge, listeners: Map<string, Listener[]>): ChainBackend {
  const ip = normalizeIp(text(endpoint.identity.ip));
  const port = text(endpoint.identity.port);
  const host = endpointHost(index, endpoint);
  const exact = listeners.get(`${ip}:${port}`) ?? [];

  const matches: ChainAppMatch[] = [];
  const seenDeployment = new Set<string>();
  for (const listener of exact) {
    if (seenDeployment.has(listener.deployment.id)) continue;
    seenDeployment.add(listener.deployment.id);
    matches.push({
      application: listener.application,
      deployment: listener.deployment,
      listenerEndpoint: listener.endpoint,
      environment: listener.edge.environment,
      confidence: listener.edge.confidence,
      protocolMismatch: text(listener.endpoint.identity.protocol) !== text(endpoint.identity.protocol),
    });
  }

  const distinctApps = new Set(matches.map(match => match.application.id));
  const weakest = matches.reduce<Confidence>((weak, match) => (RANK[match.confidence] < RANK[weak] ? match.confidence : weak), 'EXACT');
  const status: ChainBackend['status'] = matches.length === 0
    ? 'unresolved'
    : (distinctApps.size > 1 || weakest === 'AMBIGUOUS' ? 'ambiguous' : 'exact');

  // Same IP, different port: hint candidates only, never the answer.
  const sameHostCandidates: ChainHostCandidate[] = [];
  if (status === 'unresolved' && host) {
    const seen = new Set<string>();
    for (const [key, list] of listeners) {
      const [candidateIp, candidatePort] = key.split(':');
      if (candidateIp !== ip || candidatePort === port) continue;
      for (const listener of list) {
        const dedupe = `${listener.application.id}:${listener.deployment.id}`;
        if (seen.has(dedupe)) continue;
        seen.add(dedupe);
        sameHostCandidates.push({
          application: listener.application,
          deployment: listener.deployment,
          port: candidatePort,
          environment: listener.edge.environment,
        });
      }
    }
    sameHostCandidates.sort((a, b) => a.port.localeCompare(b.port, undefined, { numeric: true }) || a.application.id.localeCompare(b.application.id));
    sameHostCandidates.splice(CANDIDATE_CAP);
  }

  return { endpoint, host, forwardEdge, status, matches, sameHostCandidates };
}

function collectEntryHostIps(entries: ChainEntry[]): Set<string> {
  const ips = new Set<string>();
  const addEndpoint = (endpoint: TopologyNode | null) => {
    const ip = normalizeIp(text(endpoint?.identity.ip));
    if (ip) ips.add(ip);
  };
  for (const entry of entries) {
    if (entry.targetKind === 'host') addEndpoint(entry.target);
    if (entry.binding?.host) addEndpoint(entry.binding.host);
    for (const rule of entry.dnat) addEndpoint(rule.internal);
    for (const backend of entry.clbBackends) addEndpoint(backend.endpoint);
  }
  return ips;
}

function compareGroups(a: ChainUpstreamGroup, b: ChainUpstreamGroup): number {
  const rank = (group: ChainUpstreamGroup) => (group.kind === 'proxy' ? 0 : group.kind === 'external' ? 1 : 2);
  const byKind = rank(a) - rank(b);
  if (byKind) return byKind;
  return a.upstream.id.localeCompare(b.upstream.id);
}

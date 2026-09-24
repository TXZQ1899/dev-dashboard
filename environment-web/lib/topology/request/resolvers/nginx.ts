/**
 * NginxResolver (TASK-04).
 *
 * Request-aware Nginx routing over the normalized `nginxRoutes` topology:
 *
 * 1. ENDPOINT -> HOST (`nginx:host`): locate the owning host via ON_HOST edges;
 *    when the edge is missing, fall back to an exact IP match against the HOST
 *    inventory (INFERRED). Endpoints without any host stay unresolved.
 * 2. HOST -> NGINX_ROUTE (`nginx:route:...`): select the server block that
 *    carries the request. Candidates are the routes collected for the host's
 *    JumpServer asset(s), filtered by `listen` port, then ranked by
 *    server_name (exact > wildcard > default/fallback) and location
 *    (exact `=` > longest prefix > stream port-only). Equal best matches are
 *    all kept with AMBIGUOUS confidence; regex locations are unsupported in V1.
 * 3. NGINX_ROUTE -> UPSTREAM (`nginx:upstream`): follow USES_UPSTREAM edges.
 * 4. UPSTREAM -> ENDPOINT[] (`nginx:backend`): follow FORWARDS_TO edges to
 *    concrete IP backends. Without edges, the proxy target is parsed: IP
 *    targets derive a logical endpoint (never written back to the graph),
 *    hostname targets continue through an existing DOMAIN node, and
 *    dynamic/unix targets stop with a warning — no DNS or runtime speculation.
 *
 * A host without collected routes never implies "no nginx": the branch stops
 * as unknown with a warning (partial > guessing). Moves are never marked
 * terminal — the APPLICATION stage is TASK-05.
 */
import { asArray, normalizeDomain, normalizeIp, text } from '../../common.ts';
import type { Confidence, Evidence, TopologyEdge, TopologyNode } from '../../types.ts';
import type { GraphIndex } from '../../path-explorer.ts';
import {
  RESERVED_RESOLVER_NAMES,
  type PathResolver,
  type ResolutionMove,
  type ResolutionResult,
  type ResolverContext,
} from '../resolver.ts';
import { resolveEndpointNodes } from '../endpoint.ts';

const MAX_STEP_EVIDENCE = 10;
const CONFIDENCE_RANK: Record<Confidence, number> = { EXACT: 3, INFERRED: 2, AMBIGUOUS: 1, UNKNOWN: 0 };

function capEvidence(evidence: Evidence[], warnings: string[]): Evidence[] {
  if (evidence.length <= MAX_STEP_EVIDENCE) return evidence;
  warnings.push(`Step evidence truncated to the first ${MAX_STEP_EVIDENCE} of ${evidence.length} records.`);
  return evidence.slice(0, MAX_STEP_EVIDENCE);
}

// ---------------------------------------------------------------------------
// Nginx route lookup (WeakMap cache per GraphIndex, mirroring endpoint.ts)
// ---------------------------------------------------------------------------

const routesByAssetCache = new WeakMap<GraphIndex, Map<string, TopologyNode[]>>();

/** NGINX_ROUTE nodes grouped by identity.assetId, built once per GraphIndex. */
function routesByAssetId(index: GraphIndex): Map<string, TopologyNode[]> {
  let byAsset = routesByAssetCache.get(index);
  if (!byAsset) {
    byAsset = new Map<string, TopologyNode[]>();
    for (const node of index.topology.nodes) {
      if (node.type !== 'NGINX_ROUTE') continue;
      const assetId = text(node.identity.assetId);
      if (!assetId) continue;
      const list = byAsset.get(assetId);
      if (list) list.push(node);
      else byAsset.set(assetId, [node]);
    }
    routesByAssetCache.set(index, byAsset);
  }
  return byAsset;
}

/** JumpServer asset ids attached to a HOST node (identity + attributes). */
function hostAssetIds(host: TopologyNode): string[] {
  const ids = new Set<string>();
  const identityIds = host.identity.jumpserverAssetIds;
  if (Array.isArray(identityIds)) {
    for (const id of identityIds) {
      const value = text(id);
      if (value) ids.add(value);
    }
  }
  const jump = host.attributes.jumpserver as { id?: unknown } | undefined;
  const jumpId = text(jump?.id);
  if (jumpId) ids.add(jumpId);
  return [...ids].sort();
}

function routesForHost(index: GraphIndex, host: TopologyNode): TopologyNode[] {
  const seen = new Set<string>();
  const routes: TopologyNode[] = [];
  for (const assetId of hostAssetIds(host)) {
    for (const route of routesByAssetId(index).get(assetId) ?? []) {
      if (seen.has(route.id)) continue;
      seen.add(route.id);
      routes.push(route);
    }
  }
  return routes.sort((a, b) => a.id.localeCompare(b.id));
}

// ---------------------------------------------------------------------------
// listen / server_name / location matching
// ---------------------------------------------------------------------------

/** Port from one `listen` entry (`443 ssl`, `1.2.3.4:8080`, `[::]:80`). */
export function parseListenPort(entry: string): number | null {
  const token = entry.trim().split(/\s+/)[0] ?? '';
  const addressPort = token.match(/^(?:\[[^\]]*\]|[^:]*):(\d+)$/);
  if (addressPort) return Number(addressPort[1]);
  if (/^\d+$/.test(token)) return Number(token);
  return null;
}

/** Does any listen entry of the route accept one of the candidate arrival ports? Missing/unparseable listen matches nothing. */
function listenMatchesPort(route: TopologyNode, ports: readonly number[]): boolean {
  const entries = asArray<string>(route.attributes.listen);
  return entries.some(entry => {
    const listenPort = parseListenPort(entry);
    return listenPort !== null && ports.includes(listenPort);
  });
}

/**
 * Ports the request may arrive on at this host. The client-facing query port
 * is always considered (direct EIP/ECS ingress); when the chain passed through
 * DNAT or a CLB backend, the endpoint that delivered traffic to this host
 * carries the rewritten arrival port (e.g. 443 → 8443), which is what the
 * server block actually listens on (TASK-06 Step 1, legal NAT/CLB→Nginx chains).
 */
function arrivalPorts(context: ResolverContext): number[] {
  const ports = new Set<number>([context.query.port]);
  for (let i = context.trail.length - 1; i >= 0; i -= 1) {
    for (const nodeId of context.trail[i].inputNodeIds) {
      // Canonical endpoint ids embed the port: endpoint:<ip>:<port>:<protocol>.
      const match = nodeId.match(/^endpoint:[^:]+:(\d+):/);
      if (match) {
        ports.add(Number(match[1]));
        return [...ports];
      }
    }
  }
  return [...ports];
}

/** Whether the route is the explicit `default_server` for its port. */
function listenHasDefaultServer(route: TopologyNode): boolean {
  return asArray<string>(route.attributes.listen).some(entry => /(?:^|\s)(?:default_server|default)(?:\s|$)/.test(entry));
}

function routeDomains(route: TopologyNode): string[] {
  return asArray<string>(route.identity.domains).map(normalizeDomain).filter(Boolean);
}

type ServerNameKind = 'exact' | 'wildcard' | 'default';

function serverNameKind(route: TopologyNode, host: string): 'exact' | 'wildcard' | null {
  for (const domain of routeDomains(route)) {
    if (domain === host) return 'exact';
    if (domain.startsWith('*.')) {
      const suffix = domain.slice(1); // ".example.com"
      if (host.endsWith(suffix) && host.length > suffix.length) return 'wildcard';
    }
  }
  return null;
}

type LocationMatch = { kind: 'exact' | 'prefix' | 'stream'; length: number };

/**
 * Match one location uri against the request path. Returns `null` when the
 * location does not serve the path and `'regex'` when it uses regex semantics
 * that V1 does not support (still flagged by the caller with a warning).
 */
function matchLocation(uri: string, path: string): LocationMatch | 'regex' | null {
  const raw = uri.trim();
  if (!raw || raw === '/') return path.startsWith('/') ? { kind: 'prefix', length: 1 } : null;
  if (raw === '(TCP/UDP)') return { kind: 'stream', length: 0 }; // stream-context marker from the collector
  if (raw.startsWith('~')) return 'regex'; // `~` / `~*` regex locations: unsupported in V1
  if (raw.startsWith('=')) {
    const exactPath = raw.slice(1).trim();
    return exactPath && exactPath === path ? { kind: 'exact', length: exactPath.length } : null;
  }
  const prefix = raw.startsWith('^~') ? raw.slice(2).trim() : raw;
  if (!prefix) return path.startsWith('/') ? { kind: 'prefix', length: 1 } : null;
  const normalized = prefix.startsWith('/') ? prefix : `/${prefix}`;
  return path === normalized || path.startsWith(normalized) ? { kind: 'prefix', length: normalized.length } : null;
}

const LOCATION_TIER: Record<LocationMatch['kind'], number> = { exact: 2, prefix: 1, stream: 0 };

type RouteMatch = {
  route: TopologyNode;
  serverName: Exclude<ServerNameKind, null>;
  location: LocationMatch;
};

/**
 * Select the nginx routes on one host that carry the request. Priority:
 * listen port filter, then server_name tier (exact > wildcard > default),
 * then location tier (exact `=` > longest prefix > stream port-only). Ties on
 * both tiers stay as candidates and surface as AMBIGUOUS.
 */
function selectRouteMatches(context: ResolverContext, host: TopologyNode): {
  matches: RouteMatch[];
  warnings: string[];
  candidates: TopologyNode[];
  portInferred: boolean;
} {
  const warnings: string[] = [];
  const routes = routesForHost(context.index, host);
  if (routes.length === 0) {
    return {
      matches: [],
      candidates: [],
      warnings: [
        `No nginx routes are collected for host ${host.id}; nginx presence is unknown (the host may be unreachable or the nginx inspection is incomplete).`,
      ],
      portInferred: false,
    };
  }

  const inspectionStatus = text(routes[0].attributes.nginxStatus);
  if (inspectionStatus && inspectionStatus !== 'complete') {
    warnings.push(`Nginx inspection on host ${host.id} is "${inspectionStatus}"; route data may be incomplete.`);
  }

  const ports = arrivalPorts(context);
  let portRoutes = routes.filter(route => listenMatchesPort(route, ports));
  let portInferred = false;
  if (portRoutes.length === 0) {
    // The front proxy (CLB/SLB) often terminates TLS on 443 and forwards to
    // nginx on a different port (e.g. 80); the CLB backend endpoint may not
    // carry a usable port.  Fall back to port-agnostic matching so the chain
    // can still reach the upstream, with INFERRED confidence.
    portRoutes = routes;
    portInferred = true;
    const listenPorts = [...new Set(
      routes.flatMap(route => asArray<string>(route.attributes.listen))
        .map(parseListenPort)
        .filter((p): p is number => p !== null),
    )].sort((a, b) => a - b);
    warnings.push(
      `No nginx route on host ${host.id} listens on port ${ports.join(' or ')}; ` +
      `falling back to port-agnostic matching (INFERRED). Nginx listens on: ${listenPorts.join(', ')}.`,
    );
  }

  // server_name tier: exact > wildcard > default/fallback (empty server_name or default_server flag).
  const exact = portRoutes.filter(route => serverNameKind(route, context.query.host) === 'exact');
  const wildcard = portRoutes.filter(route => serverNameKind(route, context.query.host) === 'wildcard');
  let pool: TopologyNode[];
  let serverName: Exclude<ServerNameKind, null>;
  if (exact.length > 0) {
    pool = exact;
    serverName = 'exact';
  } else if (wildcard.length > 0) {
    pool = wildcard;
    serverName = 'wildcard';
  } else {
    pool = portRoutes.filter(route => routeDomains(route).length === 0 || listenHasDefaultServer(route));
    serverName = 'default';
    if (pool.length > 0) {
      warnings.push(
        `No nginx server_name on host ${host.id} matches "${context.query.host}"; using the default/fallback server block (INFERRED).`,
      );
    }
  }
  if (pool.length === 0) {
    return {
      matches: [],
      candidates: portRoutes,
      warnings: [`No nginx server_name on host ${host.id} matches "${context.query.host}" and no default/fallback route exists.`],
      portInferred,
    };
  }

  // Location tier within the surviving server blocks.
  const matches: RouteMatch[] = [];
  let sawRegex = false;
  for (const route of pool) {
    const match = matchLocation(text(route.identity.uri), context.query.path);
    if (match === 'regex') {
      sawRegex = true;
      continue;
    }
    if (match) matches.push({ route, serverName, location: match });
  }
  if (matches.length === 0) {
    const warnings = [
      sawRegex
        ? `The matching nginx locations on host ${host.id} use regex semantics, which are unsupported in V1; no route was selected.`
        : `No nginx location on host ${host.id} serves path "${context.query.path}".`,
    ];
    // Domain-only queries must not guess business routing from the implicit `/`.
    if (context.query.domainOnly) {
      warnings.unshift(
        `URI required to continue routing: the query for "${context.query.host}" is domain-only, and the nginx locations on host ${host.id} are path-specific. Re-run with the full URL (e.g. ${context.query.scheme}://${context.query.host}/<path>).`,
      );
    }
    return { matches: [], candidates: pool, warnings, portInferred };
  }

  const bestTier = Math.max(...matches.map(match => LOCATION_TIER[match.location.kind]));
  let best = matches.filter(match => LOCATION_TIER[match.location.kind] === bestTier);
  const bestLength = Math.max(...best.map(match => match.location.length));
  best = best.filter(match => match.location.length === bestLength);
  if (bestTier === LOCATION_TIER.stream) {
    warnings.push(`Stream-context nginx route on host ${host.id} matched by listen port only; L7 server_name/location routing does not apply.`);
  }
  return { matches: best, warnings, candidates: portRoutes, portInferred };
}

// ---------------------------------------------------------------------------
// Upstream backend resolution
// ---------------------------------------------------------------------------

type UpstreamTarget =
  | { kind: 'none' }
  | { kind: 'unsupported'; reason: 'unix' | 'variable'; raw: string }
  | { kind: 'ip'; ip: string; port: string; protocol: string }
  | { kind: 'hostname'; host: string };

function isIpv4(value: string): boolean {
  return /^(?:\d{1,3}\.){3}\d{1,3}$/.test(value);
}

/**
 * Parse the upstream identity into a backend target. The normalized upstream
 * name is the proxy_pass netloc (`10.1.1.9:8080`, `uaa.tcc.cn:80`) or the
 * named upstream block (`order_backend`).
 */
function parseUpstreamTarget(upstream: TopologyNode): UpstreamTarget {
  const raw = text(upstream.identity.name) || text(upstream.attributes.target);
  if (!raw) return { kind: 'none' };
  if (raw.startsWith('unix:')) return { kind: 'unsupported', reason: 'unix', raw };
  if (raw.includes('$')) return { kind: 'unsupported', reason: 'variable', raw };
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(raw)) {
    try {
      const url = new URL(raw);
      const scheme = url.protocol.replace(/:$/, '').toLowerCase();
      const protocol = scheme === 'https' ? 'https' : scheme === 'http' ? 'http' : 'unknown';
      const port = url.port || (protocol === 'https' ? '443' : '80');
      const ip = normalizeIp(url.hostname);
      if (isIpv4(ip)) return { kind: 'ip', ip, port, protocol };
      return { kind: 'hostname', host: url.hostname };
    } catch {
      // Malformed URL — fall through to plain host[:port] parsing.
    }
  }
  const hostPort = raw.match(/^(.+):(\d+)$/);
  const host = hostPort ? hostPort[1] : raw;
  const port = hostPort ? hostPort[2] : 'unknown';
  const ip = normalizeIp(host);
  if (isIpv4(ip)) return { kind: 'ip', ip, port, protocol: 'unknown' };
  return { kind: 'hostname', host };
}

// ---------------------------------------------------------------------------
// Resolver
// ---------------------------------------------------------------------------

export class NginxResolver implements PathResolver {
  readonly name = RESERVED_RESOLVER_NAMES.NGINX;

  canResolve(context: ResolverContext): boolean {
    const node = context.current;
    if (!node) return false;
    switch (node.type) {
      case 'HOST':
        return true;
      case 'ENDPOINT':
        return Boolean(normalizeIp(node.identity.ip));
      case 'NGINX_ROUTE':
        return true;
      case 'UPSTREAM':
        return true;
      default:
        return false;
    }
  }

  resolve(context: ResolverContext): ResolutionResult {
    const node = context.current;
    if (!node) return { rule: 'nginx:none', moves: [] };
    switch (node.type) {
      case 'HOST':
        return this.resolveRoutes(context, node);
      case 'ENDPOINT':
        return this.resolveHost(context, node);
      case 'NGINX_ROUTE':
        return this.resolveUpstream(context, node);
      case 'UPSTREAM':
        return this.resolveBackends(context, node);
      default:
        return { rule: 'nginx:none', moves: [] };
    }
  }

  // Step 1: ENDPOINT → HOST via ON_HOST edges (IP fallback when the edge is missing).
  private resolveHost(context: ResolverContext, endpoint: TopologyNode): ResolutionResult {
    const warnings: string[] = [];
    const evidence: Evidence[] = [...endpoint.evidence];
    const hosted: { host: TopologyNode; edge: TopologyEdge | null }[] = [];
    for (const step of context.index.adjacency.get(endpoint.id) ?? []) {
      if (step.direction !== 'forward') continue;
      if (step.edge.type !== 'ON_HOST') continue;
      if (!context.edgeAllowed(step.edge)) continue;
      const host = context.index.nodeById.get(step.neighborId);
      if (host?.type === 'HOST') hosted.push({ host, edge: step.edge });
    }
    let confidence: Confidence = 'EXACT';
    if (hosted.length === 0) {
      const ip = normalizeIp(endpoint.identity.ip);
      const byIp = (context.index.lookup.host.get(ip) ?? []).filter(host => normalizeIp(host.identity.ip) === ip);
      if (byIp.length === 0) {
        return {
          rule: 'nginx:host',
          moves: [],
          warnings: [`Endpoint ${endpoint.id} is not associated with any HOST node; nginx routing cannot be inspected.`],
        };
      }
      warnings.push(`Endpoint ${endpoint.id} has no ON_HOST edge; host ${byIp.map(host => host.id).join(', ')} matched by IP (INFERRED).`);
      confidence = 'INFERRED';
      for (const host of byIp) hosted.push({ host, edge: null });
    }
    for (const item of hosted) {
      if (item.edge) evidence.push(...item.edge.evidence);
      evidence.push(...item.host.evidence);
    }
    if (hosted.length > 1) {
      warnings.push(`${hosted.length} hosts are associated with endpoint ${endpoint.id}; all are kept as candidates.`);
      confidence = 'AMBIGUOUS';
    }
    return {
      rule: 'nginx:host',
      confidence,
      evidence: capEvidence(evidence, warnings),
      moves: hosted.map(item => ({ node: item.host })),
      warnings,
    };
  }

  // Steps 2-4: HOST → NGINX_ROUTE (listen + server_name + location selection).
  private resolveRoutes(context: ResolverContext, host: TopologyNode): ResolutionResult {
    const selection = selectRouteMatches(context, host);
    if (selection.matches.length === 0) {
      return { rule: 'nginx:route', moves: [], warnings: selection.warnings };
    }
    const warnings: string[] = [...selection.warnings];
    const evidence: Evidence[] = [...host.evidence];
    for (const match of selection.matches) evidence.push(...match.route.evidence);
    if (selection.matches.length > 1) {
      warnings.push(
        `${selection.matches.length} nginx routes on host ${host.id} equally match host "${context.query.host}" path "${context.query.path}" (${selection.matches.map(match => match.route.id).join(', ')}); all are kept as candidates.`,
      );
    }
    const confidence: Confidence = selection.matches.length > 1
      ? 'AMBIGUOUS'
      : selection.portInferred || selection.matches[0].serverName === 'default' || selection.matches[0].location.kind === 'stream'
        ? 'INFERRED'
        : 'EXACT';
    return {
      rule: `nginx:route:${selection.matches[0].serverName}:${selection.matches[0].location.kind}`,
      confidence,
      evidence: capEvidence(evidence, warnings),
      moves: selection.matches.map(match => ({ node: match.route })),
      warnings,
    };
  }

  // Step 5a: NGINX_ROUTE → UPSTREAM via USES_UPSTREAM edges.
  private resolveUpstream(context: ResolverContext, route: TopologyNode): ResolutionResult {
    const warnings: string[] = [];
    const evidence: Evidence[] = [...route.evidence];
    const moves: ResolutionMove[] = [];
    let weakest: Confidence = 'EXACT';
    for (const step of context.index.adjacency.get(route.id) ?? []) {
      if (step.direction !== 'forward') continue;
      if (step.edge.type !== 'USES_UPSTREAM') continue;
      if (!context.edgeAllowed(step.edge)) continue;
      const upstream = context.index.nodeById.get(step.neighborId);
      if (upstream?.type !== 'UPSTREAM') continue;
      moves.push({ node: upstream });
      evidence.push(...step.edge.evidence);
      if (CONFIDENCE_RANK[step.edge.confidence] < CONFIDENCE_RANK[weakest]) weakest = step.edge.confidence;
    }
    if (moves.length === 0) {
      return { rule: 'nginx:upstream', moves: [], warnings: [`Nginx route ${route.id} has no USES_UPSTREAM edge to an upstream.`] };
    }
    return {
      rule: 'nginx:upstream',
      confidence: weakest,
      evidence: capEvidence(evidence, warnings),
      moves,
      warnings,
    };
  }

  // Step 5b: UPSTREAM → backend ENDPOINTs (FORWARDS_TO edges, then proxy target parsing).
  private resolveBackends(context: ResolverContext, upstream: TopologyNode): ResolutionResult {
    const warnings: string[] = [];
    const evidence: Evidence[] = [...upstream.evidence];
    const moves: ResolutionMove[] = [];
    let weakest: Confidence = 'EXACT';
    for (const step of context.index.adjacency.get(upstream.id) ?? []) {
      if (step.direction !== 'forward') continue;
      if (step.edge.type !== 'FORWARDS_TO') continue;
      if (!context.edgeAllowed(step.edge)) continue;
      const endpoint = context.index.nodeById.get(step.neighborId);
      if (endpoint?.type !== 'ENDPOINT') continue;
      moves.push({ node: endpoint });
      evidence.push(...step.edge.evidence);
      if (CONFIDENCE_RANK[step.edge.confidence] < CONFIDENCE_RANK[weakest]) weakest = step.edge.confidence;
    }
    if (moves.length > 0) {
      // Multiple backends are legitimate load-balanced candidates, not ambiguous.
      return {
        rule: 'nginx:backend',
        confidence: weakest,
        evidence: capEvidence(evidence, warnings),
        moves,
        warnings,
      };
    }
    return this.resolveTargetBackend(context, upstream, evidence, warnings);
  }

  /**
   * Fallback when the upstream has no FORWARDS_TO edges: parse the proxy
   * target. IP targets derive a logical endpoint; hostname targets continue
   * only through an existing DOMAIN node; dynamic/unix targets stop.
   */
  private resolveTargetBackend(
    context: ResolverContext,
    upstream: TopologyNode,
    evidence: Evidence[],
    warnings: string[],
  ): ResolutionResult {
    const target = parseUpstreamTarget(upstream);
    switch (target.kind) {
      case 'none':
        return {
          rule: 'nginx:backend',
          moves: [],
          warnings: [`Upstream ${upstream.id} has no backend endpoints and no parseable proxy target; branch stops.`, ...warnings],
        };
      case 'unsupported':
        return {
          rule: 'nginx:backend',
          moves: [],
          warnings: [
            target.reason === 'unix'
              ? `Upstream ${upstream.id} targets the unix socket ${target.raw}; V1 cannot resolve unix backends.`
              : `Upstream ${upstream.id} uses the variable target ${target.raw}; V1 cannot resolve dynamic backends.`,
            ...warnings,
          ],
        };
      case 'ip': {
        const resolution = resolveEndpointNodes(context.index, target.ip, target.port, target.protocol, [...upstream.evidence], 'internal');
        const node = resolution.nodes[0] ?? resolution.synthesized;
        if (!node) {
          return { rule: 'nginx:backend', moves: [], warnings: [`Upstream ${upstream.id} IP backend ${target.ip}:${target.port} could not be resolved.`, ...warnings] };
        }
        if (resolution.synthesized) {
          warnings.push(`Backend ${target.ip}:${target.port} of upstream ${upstream.id} is not in the topology graph; a logical endpoint was derived from the proxy_pass target.`);
        }
        return {
          rule: 'nginx:backend-derived',
          confidence: 'INFERRED',
          evidence: capEvidence([...evidence, ...upstream.evidence], warnings),
          moves: [{ node }],
          warnings,
        };
      }
      case 'hostname': {
        const domains = context.index.lookup.domain.get(normalizeDomain(target.host)) ?? [];
        if (domains.length === 0) {
          return {
            rule: 'nginx:backend',
            moves: [],
            warnings: [
              `Backend hostname "${target.host}" of upstream ${upstream.id} cannot be resolved with existing data; branch stops without DNS speculation.`,
              ...warnings,
            ],
          };
        }
        warnings.push(`Backend "${target.host}" of upstream ${upstream.id} is a hostname; continuing through its existing DOMAIN node(s) (no DNS speculation).`);
        return {
          rule: 'nginx:backend-domain',
          confidence: 'INFERRED',
          evidence: capEvidence([...evidence, ...domains.flatMap(domain => domain.evidence)], warnings),
          moves: domains.sort((a, b) => a.id.localeCompare(b.id)).map(domain => ({ node: domain })),
          warnings,
        };
      }
    }
  }
}

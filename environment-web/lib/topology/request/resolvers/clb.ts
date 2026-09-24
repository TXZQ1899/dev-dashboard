/**
 * ClbResolver (TASK-03).
 *
 * Ingress chain stages handled here:
 *
 * 1. EIP / Endpoint -> CLB: an EIP bound to a CLB (via BOUND_TO edge) or an
 *    endpoint whose IP matches a CLB instance's VIP. Both entry paths produce
 *    CLB nodes as moves so the engine can branch per instance.
 * 2. CLB -> CLB_LISTENER: follow HAS_LISTENER edges, then match by request
 *    port (exact) and protocol (exact L7 > TCP transport family > unknown
 *    fallback). HTTPS:443 wins over TCP:443 for an https request.
 * 3. CLB_LISTENER -> SERVER_GROUP: for L7 listeners (HTTP/HTTPS) match rules
 *    by host (exact > wildcard > empty) and path (longer prefix > empty),
 *    then resolve the rule's server group by id. When no rule matches, fall
 *    back to the listener's default server group via the ROUTES_TO edge. TCP
 *    listeners skip rule matching and use the default group directly.
 * 4. SERVER_GROUP -> ENDPOINT: follow FORWARDS_TO edges to backend endpoints.
 *    Multiple backends are legitimate load-balanced candidates — they fan out
 *    as moves without AMBIGUOUS confidence. Only rule/listener ambiguity
 *    (cannot determine which rule/listener to use) raises AMBIGUOUS.
 *
 * Moves are never marked terminal: the chain stays open for nginx (TASK-04)
 * and the APPLICATION terminal stage (TASK-05).
 */
import { normalizeIp, normalizePort, normalizeProtocol, text } from '../../common.ts';
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

function capEvidence(evidence: Evidence[], warnings: string[]): Evidence[] {
  if (evidence.length <= MAX_STEP_EVIDENCE) return evidence;
  warnings.push(`Step evidence truncated to the first ${MAX_STEP_EVIDENCE} of ${evidence.length} records.`);
  return evidence.slice(0, MAX_STEP_EVIDENCE);
}

// ---------------------------------------------------------------------------
// CLB lookup caches (WeakMap per GraphIndex, mirroring endpoint.ts pattern)
// ---------------------------------------------------------------------------

const clbByIpCache = new WeakMap<GraphIndex, Map<string, TopologyNode[]>>();

/** CLB nodes grouped by normalized IP, built once per GraphIndex. */
function clbsByIp(index: GraphIndex, ip: string): TopologyNode[] {
  let byIp = clbByIpCache.get(index);
  if (!byIp) {
    byIp = new Map<string, TopologyNode[]>();
    for (const node of index.topology.nodes) {
      if (node.type !== 'CLB') continue;
      const clbIp = normalizeIp(node.identity.ip);
      if (!clbIp) continue;
      const list = byIp.get(clbIp);
      if (list) list.push(node);
      else byIp.set(clbIp, [node]);
    }
    clbByIpCache.set(index, byIp);
  }
  return byIp.get(normalizeIp(ip)) ?? [];
}

/**
 * CLB nodes reachable from an EIP via a forward BOUND_TO edge.
 * Deterministic id order; environment filter applied by the caller.
 */
function clbsBoundToEip(context: ResolverContext, eip: TopologyNode): TopologyNode[] {
  const result: TopologyNode[] = [];
  for (const step of context.index.adjacency.get(eip.id) ?? []) {
    if (step.direction !== 'forward') continue;
    if (step.edge.type !== 'BOUND_TO') continue;
    if (!context.edgeAllowed(step.edge)) continue;
    const target = context.index.nodeById.get(step.neighborId);
    if (target?.type === 'CLB') result.push(target);
  }
  return result.sort((a, b) => a.id.localeCompare(b.id));
}

// ---------------------------------------------------------------------------
// Listener matching
// ---------------------------------------------------------------------------

/** Rank a listener protocol against the request scheme. Higher = more specific. */
function listenerProtocolRank(listenerProtocol: unknown, requestScheme: string): number {
  const protocol = normalizeProtocol(listenerProtocol);
  if (protocol === 'unknown') return 1; // Any/missing — weak fallback
  if (protocol === requestScheme) return 3; // exact L7 match (http→http, https→https)
  if (protocol === 'tcp') return 2; // L4, accepts any L7 riding TCP
  return 0; // L7 mismatch (http listener vs https request, or UDP, etc.)
}

type ListenerMatch = {
  listener: TopologyNode;
  edge: TopologyEdge;
  rank: number;
};

/**
 * Match listeners for a CLB by request port (exact) and protocol rank.
 * Returns the highest-ranked matches; multiple equal-rank matches are ambiguous.
 */
function matchListeners(context: ResolverContext, clb: TopologyNode): ListenerMatch[] {
  const matches: ListenerMatch[] = [];
  for (const step of context.index.adjacency.get(clb.id) ?? []) {
    if (step.direction !== 'forward') continue;
    if (step.edge.type !== 'HAS_LISTENER') continue;
    if (!context.edgeAllowed(step.edge)) continue;
    const listener = context.index.nodeById.get(step.neighborId);
    if (listener?.type !== 'CLB_LISTENER') continue;
    if (listener.status === 'paused') continue;
    const listenerPort = Number(normalizePort(listener.identity.port));
    if (!Number.isInteger(listenerPort) || listenerPort !== context.query.port) continue;
    const rank = listenerProtocolRank(listener.identity.protocol, context.query.scheme);
    if (rank === 0) continue;
    matches.push({ listener, edge: step.edge, rank });
  }
  if (matches.length === 0) return [];
  const maxRank = Math.max(...matches.map(item => item.rank));
  return matches.filter(item => item.rank === maxRank).sort((a, b) => a.listener.id.localeCompare(b.listener.id));
}

// ---------------------------------------------------------------------------
// Rule matching (L7 host/path)
// ---------------------------------------------------------------------------

type RuleRecord = { id: string; domain: string; path: string; groupId: string };

function readRules(listener: TopologyNode): RuleRecord[] {
  const raw = listener.attributes.rules;
  if (!Array.isArray(raw)) return [];
  return raw
    .map(item => {
      const record = item as Record<string, unknown>;
      return {
        id: text(record.id),
        domain: text(record.domain).toLowerCase(),
        path: text(record.path),
        groupId: text(record.groupId) || 'default',
      };
    })
    .filter(rule => rule.id || rule.groupId);
}

type DomainMatch = { kind: 'exact' | 'wildcard' | 'empty'; score: number; specificity: number };

/**
 * Alibaba CLB auto-sorts domain rules by specificity: exact match > narrower
 * wildcard (e.g. *.market.aliyun.com) > broader wildcard (*.aliyun.com). For a
 * wildcard match the specificity is the number of fixed labels in the suffix.
 */
function wildcardSpecificity(pattern: string): number {
  const suffix = pattern.startsWith('*.') ? pattern.slice(2) : pattern;
  return suffix ? suffix.split('.').filter(Boolean).length : 0;
}

function matchDomain(ruleDomain: string, host: string): DomainMatch {
  if (!ruleDomain) return { kind: 'empty', score: 0, specificity: 0 };
  if (ruleDomain === host) return { kind: 'exact', score: 2, specificity: host.split('.').length };
  // Comma-separated domains (some CLB implementations allow multi-domain rules)
  const parts = ruleDomain.includes(',') ? ruleDomain.split(',').map(p => p.trim()).filter(Boolean) : [ruleDomain];
  for (const d of parts) {
    if (d === host) return { kind: 'exact', score: 2, specificity: host.split('.').length };
  }
  // Pick the narrowest wildcard pattern that matches.
  let best = -1;
  for (const d of parts) {
    if (!d.startsWith('*.')) continue;
    const suffix = d.slice(1); // ".example.com"
    if (host.endsWith(suffix) && host.length > suffix.length) best = Math.max(best, wildcardSpecificity(d));
  }
  if (best > 0) return { kind: 'wildcard', score: 1, specificity: best };
  return { kind: 'empty', score: -1, specificity: 0 }; // no match at all
}

function pathPrefixLength(rulePath: string): number {
  if (!rulePath) return 0;
  return rulePath.startsWith('/') ? rulePath.length : `/${rulePath}`.length;
}

function pathMatches(rulePath: string, requestPath: string): boolean {
  if (!rulePath) return true; // empty path = catch-all
  const normalized = rulePath.startsWith('/') ? rulePath : `/${rulePath}`;
  return requestPath === normalized || requestPath.startsWith(normalized);
}

type RuleMatch = {
  rule: RuleRecord;
  domainMatch: DomainMatch;
  pathLength: number;
};

/**
 * Match rules against request host and path. Returns the best-scoring match(es).
 * Priority: exact domain > wildcard domain (narrower suffix wins) > empty domain;
 * within the same domain tier, longer path prefix wins. Equal-score rules are
 * ambiguous (all kept). Mirrors CLB's automatic domain-specificity ordering.
 */
function matchRules(rules: RuleRecord[], host: string, requestPath: string): RuleMatch[] {
  const matched: RuleMatch[] = [];
  for (const rule of rules) {
    const domainMatch = matchDomain(rule.domain, host);
    if (domainMatch.score < 0) continue; // domain didn't match at all
    if (!pathMatches(rule.path, requestPath)) continue;
    matched.push({ rule, domainMatch, pathLength: pathPrefixLength(rule.path) });
  }
  if (matched.length === 0) return [];
  // Sort by domain score desc, wildcard specificity desc, then path length desc.
  matched.sort(
    (a, b) =>
      b.domainMatch.score - a.domainMatch.score
      || b.domainMatch.specificity - a.domainMatch.specificity
      || b.pathLength - a.pathLength,
  );
  const top = matched[0];
  return matched.filter(
    item =>
      item.domainMatch.score === top.domainMatch.score
      && item.domainMatch.specificity === top.domainMatch.specificity
      && item.pathLength === top.pathLength,
  );
}

// ---------------------------------------------------------------------------
// Server group resolution
// ---------------------------------------------------------------------------

/** Look up a SERVER_GROUP node by clbId + groupId (deterministic id order). */
function serverGroupById(index: GraphIndex, clbId: string, groupId: string): TopologyNode | null {
  const id = `clb-server-group:${clbId}:${groupId}`;
  const node = index.nodeById.get(id);
  return node?.type === 'SERVER_GROUP' ? node : null;
}

/**
 * Follow the ROUTES_TO edge from a listener to its default server group.
 * Returns null when no default group edge exists.
 */
function defaultServerGroup(
  context: ResolverContext,
  listener: TopologyNode,
): { group: TopologyNode; edge: TopologyEdge } | null {
  for (const step of context.index.adjacency.get(listener.id) ?? []) {
    if (step.direction !== 'forward') continue;
    if (step.edge.type !== 'ROUTES_TO') continue;
    if (!context.edgeAllowed(step.edge)) continue;
    const target = context.index.nodeById.get(step.neighborId);
    if (target?.type === 'SERVER_GROUP') return { group: target, edge: step.edge };
  }
  return null;
}

// ---------------------------------------------------------------------------
// Backend endpoint resolution
// ---------------------------------------------------------------------------

/**
 * Numeric backend port configured on the CLB_LISTENER that routed the current
 * branch into this group. Alibaba CLB default-group backend servers returned by
 * DescribeLoadBalancerAttribute carry no Port; the authoritative backend port
 * is the listener-level BackendServerPort (TLS/listen port is terminated at the
 * CLB, e.g. 443 -> backend 80). The trail identifies the concrete listener even
 * when two listeners (80/443) share the same default group.
 */
function trailListenerBackendPort(context: ResolverContext): number | null {
  for (let i = context.trail.length - 1; i >= 0; i -= 1) {
    for (const nodeId of context.trail[i].inputNodeIds) {
      if (!nodeId.startsWith('clb-listener:')) continue;
      const listener = context.index.nodeById.get(nodeId);
      if (listener?.type !== 'CLB_LISTENER') continue;
      const port = Number(listener.attributes.backendPort);
      if (Number.isInteger(port) && port > 0) return port;
    }
  }
  return null;
}

/** Follow FORWARDS_TO edges from a server group to backend endpoint nodes. */
function backendEndpoints(
  context: ResolverContext,
  group: TopologyNode,
): { endpoint: TopologyNode; edge: TopologyEdge }[] {
  const result: { endpoint: TopologyNode; edge: TopologyEdge }[] = [];
  for (const step of context.index.adjacency.get(group.id) ?? []) {
    if (step.direction !== 'forward') continue;
    if (step.edge.type !== 'FORWARDS_TO') continue;
    if (!context.edgeAllowed(step.edge)) continue;
    const target = context.index.nodeById.get(step.neighborId);
    if (target?.type === 'ENDPOINT') result.push({ endpoint: target, edge: step.edge });
  }
  return result.sort((a, b) => a.endpoint.id.localeCompare(b.endpoint.id));
}

/** A FORWARDS_TO endpoint without a usable port; resolved via the listener's BackendServerPort. */
function isPortlessEndpoint(endpoint: TopologyNode): boolean {
  const port = normalizePort(endpoint.identity.port);
  return port === 'unknown' || port === 'any' || !/^\d+$/.test(port);
}

// ---------------------------------------------------------------------------
// Resolver
// ---------------------------------------------------------------------------

export class ClbResolver implements PathResolver {
  readonly name = RESERVED_RESOLVER_NAMES.CLB;

  canResolve(context: ResolverContext): boolean {
    const node = context.current;
    if (!node) return false;
    switch (node.type) {
      case 'EIP':
        return clbsBoundToEip(context, node).length > 0;
      case 'ENDPOINT': {
        const ip = normalizeIp(node.identity.ip);
        return Boolean(ip) && clbsByIp(context.index, ip).length > 0;
      }
      case 'CLB':
        return true;
      case 'CLB_LISTENER':
        return true;
      case 'SERVER_GROUP':
        return true;
      default:
        return false;
    }
  }

  resolve(context: ResolverContext): ResolutionResult {
    const node = context.current;
    if (!node) return { rule: 'clb:none', moves: [] };
    switch (node.type) {
      case 'EIP':
        return this.resolveEipToClb(context, node);
      case 'ENDPOINT':
        return this.resolveEndpointToClb(context, node);
      case 'CLB':
        return this.resolveListener(context, node);
      case 'CLB_LISTENER':
        return this.resolveListenerRules(context, node);
      case 'SERVER_GROUP':
        return this.resolveBackends(context, node);
      default:
        return { rule: 'clb:none', moves: [] };
    }
  }

  // Step 1a: EIP → CLB via BOUND_TO edge.
  private resolveEipToClb(context: ResolverContext, eip: TopologyNode): ResolutionResult {
    const matches = clbsBoundToEip(context, eip);
    if (matches.length === 0) {
      return {
        rule: 'clb:eip-bound',
        moves: [],
        warnings: [`EIP ${eip.id} has no BOUND_TO edge to a CLB instance.`],
      };
    }
    const warnings: string[] = [];
    const evidence: Evidence[] = [...eip.evidence];
    if (matches.length > 1) {
      warnings.push(`${matches.length} CLB instances are bound to EIP ${eip.id}; all are kept as candidates.`);
    }
    const confidence: Confidence = matches.length > 1 ? 'AMBIGUOUS' : 'EXACT';
    return {
      rule: 'clb:eip-bound',
      confidence,
      evidence: capEvidence(evidence, warnings),
      moves: matches.map(clb => ({ node: clb })),
      warnings,
    };
  }

  // Step 1b: Endpoint → CLB by IP match.
  private resolveEndpointToClb(context: ResolverContext, endpoint: TopologyNode): ResolutionResult {
    const ip = normalizeIp(endpoint.identity.ip);
    const matches = clbsByIp(context.index, ip).filter(clb => clb.status !== 'paused');
    if (matches.length === 0) {
      return {
        rule: 'clb:endpoint-ip',
        moves: [],
        warnings: [`Endpoint ${endpoint.id} IP ${ip} matches no CLB instance.`],
      };
    }
    const warnings: string[] = [];
    if (matches.length > 1) {
      warnings.push(`${matches.length} CLB instances share IP ${ip}; all are kept as candidates.`);
    }
    const confidence: Confidence = matches.length > 1 ? 'AMBIGUOUS' : 'EXACT';
    return {
      rule: 'clb:endpoint-ip',
      confidence,
      evidence: capEvidence([...endpoint.evidence], warnings),
      moves: matches.map(clb => ({ node: clb })),
      warnings,
    };
  }

  // Step 2: CLB → CLB_LISTENER (port + protocol match).
  private resolveListener(context: ResolverContext, clb: TopologyNode): ResolutionResult {
    const matches = matchListeners(context, clb);
    if (matches.length === 0) {
      return {
        rule: 'clb:listener',
        moves: [],
        warnings: [`No CLB listener on CLB ${clb.id} matches ${context.query.scheme}:${context.query.port}.`],
      };
    }
    const warnings: string[] = [];
    const evidence: Evidence[] = [...clb.evidence];
    for (const match of matches) evidence.push(...match.edge.evidence);
    let confidence: Confidence = 'EXACT';
    if (matches.length > 1) {
      warnings.push(
        `${matches.length} listeners on CLB ${clb.id} match ${context.query.scheme}:${context.query.port} with equal specificity; all are kept as candidates.`,
      );
      confidence = 'AMBIGUOUS';
    } else if (matches[0].rank < 3) {
      confidence = 'INFERRED'; // TCP fallback for http/https request, or unknown protocol
    }
    return {
      rule: 'clb:listener',
      confidence,
      evidence: capEvidence(evidence, warnings),
      moves: matches.map(match => ({ node: match.listener })),
      warnings,
    };
  }

  // Step 3: CLB_LISTENER → SERVER_GROUP (rule match or default group).
  private resolveListenerRules(context: ResolverContext, listener: TopologyNode): ResolutionResult {
    const protocol = normalizeProtocol(listener.identity.protocol);
    const isL7 = protocol === 'http' || protocol === 'https';
    const rules = isL7 ? readRules(listener) : [];
    const warnings: string[] = [];
    const evidence: Evidence[] = [...listener.evidence];

    // Try rule matching for L7 listeners.
    if (isL7 && rules.length > 0) {
      const ruleMatches = matchRules(rules, context.query.host, context.query.path);
      if (ruleMatches.length > 0) {
        const clbId = String(listener.identity.clbId);
        if (ruleMatches.length > 1) {
          warnings.push(
            `${ruleMatches.length} rules on listener ${listener.id} equally match host "${context.query.host}" path "${context.query.path}" (${ruleMatches.map(m => m.rule.id).join(', ')}); all are kept as candidates.`,
          );
        }
        const groups: TopologyNode[] = [];
        const groupEvidence: Evidence[] = [];
        for (const match of ruleMatches) {
          const group = serverGroupById(context.index, clbId, match.rule.groupId);
          if (group) {
            groups.push(group);
            groupEvidence.push(...group.evidence);
          } else {
            warnings.push(`Rule ${match.rule.id} references server group ${match.rule.groupId} which is not present in the topology; that branch is dropped.`);
          }
        }
        if (groups.length === 0) {
          return {
            rule: 'clb:rule',
            moves: [],
            warnings: [`No server group for matched rule(s) on listener ${listener.id} was found in the topology.`, ...warnings],
          };
        }
        evidence.push(...groupEvidence);
        const confidence: Confidence = ruleMatches.length > 1 ? 'AMBIGUOUS' : 'EXACT';
        const domainKind = ruleMatches[0].domainMatch.kind;
        const pathDesc = ruleMatches[0].pathLength > 0 ? 'path-prefix' : 'default-path';
        return {
          rule: `clb:rule:${domainKind}:${pathDesc}`,
          confidence,
          evidence: capEvidence(evidence, warnings),
          moves: groups.map(group => ({ node: group })),
          warnings,
        };
      }
      // Rules existed but none matched — fall through to default group with a warning.
      warnings.push(`No rule on listener ${listener.id} matched host "${context.query.host}" path "${context.query.path}"; falling back to the default server group.`);
      // Domain-only queries must not assume the default group is the business route.
      if (context.query.domainOnly) {
        warnings.push(
          `URI required to continue routing: the query for "${context.query.host}" is domain-only; the CLB rules on listener ${listener.id} are path-specific. Re-run with the full URL (e.g. ${context.query.scheme}://${context.query.host}/<path>).`,
        );
      }
    }

    // Default server group via ROUTES_TO edge.
    const defaultGroup = defaultServerGroup(context, listener);
    if (!defaultGroup) {
      return {
        rule: 'clb:default-group',
        moves: [],
        warnings: [`Listener ${listener.id} has no default server group (ROUTES_TO edge missing).`, ...warnings],
      };
    }
    evidence.push(...defaultGroup.edge.evidence, ...defaultGroup.group.evidence);
    return {
      rule: 'clb:default-group',
      confidence: 'EXACT',
      evidence: capEvidence(evidence, warnings),
      moves: [{ node: defaultGroup.group }],
      warnings,
    };
  }

  // Step 4: SERVER_GROUP → backend ENDPOINTs.
  private resolveBackends(context: ResolverContext, group: TopologyNode): ResolutionResult {
    const raw = backendEndpoints(context, group);
    if (raw.length === 0) {
      // No FORWARDS_TO edges — try synthesizing from the group's server list.
      return this.synthesizeBackends(context, group);
    }
    const warnings: string[] = [];
    const evidence: Evidence[] = [...group.evidence, ...raw.flatMap(b => b.edge.evidence)];
    const listenerPort = trailListenerBackendPort(context);

    // Default-group FORWARDS_TO edges may point at an unknown-port endpoint
    // (DescribeLoadBalancerAttribute omits backend Port). Re-anchor them on the
    // listener's BackendServerPort instead of carrying a port-less endpoint
    // into nginx/deployment matching.
    const moves: ResolutionMove[] = [];
    const seen = new Set<string>();
    let synthesized = false;
    for (const backend of raw) {
      let endpoint = backend.endpoint;
      if (isPortlessEndpoint(endpoint)) {
        if (listenerPort === null) {
          warnings.push(
            `Server group ${group.id} backend ${String(endpoint.identity.ip)} carries no port and the routing listener has no BackendServerPort; backend port stays unresolved.`,
          );
        } else {
          const ip = normalizeIp(endpoint.identity.ip);
          const protocol = normalizeProtocol(endpoint.identity.protocol);
          const resolution = resolveEndpointNodes(context.index, ip, listenerPort, protocol, [...group.evidence], 'internal');
          const node = resolution.nodes[0] ?? resolution.synthesized;
          if (node) {
            warnings.push(
              `Server group ${group.id} backend ${ip} has no explicit server port; using backend port ${listenerPort} configured on the routing CLB listener (BackendServerPort).`,
            );
            endpoint = node;
            if (!resolution.nodes[0]) synthesized = true;
          }
        }
      }
      if (!seen.has(endpoint.id)) {
        seen.add(endpoint.id);
        moves.push({ node: endpoint });
      }
    }

    // Multiple backends are legitimate load-balanced candidates, NOT ambiguous.
    return {
      rule: 'clb:backend',
      confidence: synthesized ? 'INFERRED' : 'EXACT',
      evidence: capEvidence(evidence, warnings),
      moves,
      warnings,
    };
  }

  /**
   * Fallback when no FORWARDS_TO edges exist: read the raw server list from
   * the group's attributes and synthesize endpoint nodes (never written back).
   */
  private synthesizeBackends(context: ResolverContext, group: TopologyNode): ResolutionResult {
    const rawServers = group.attributes.servers;
    if (!Array.isArray(rawServers) || rawServers.length === 0) {
      return {
        rule: 'clb:backend',
        moves: [],
        warnings: [`Server group ${group.id} has no backend servers.`],
      };
    }
    const warnings: string[] = [];
    const evidence: Evidence[] = [...group.evidence];
    const moves: ResolutionMove[] = [];
    const listenerPort = trailListenerBackendPort(context);
    const clbId = String(group.identity.clbId);
    // Prefer the listener protocol if discoverable; otherwise unknown.
    const guessedProtocol = this.guessBackendProtocol(context, clbId, String(group.identity.serverGroupId));
    for (const server of rawServers) {
      const record = server as Record<string, unknown>;
      const ip = normalizeIp(record.ip);
      if (!ip) continue;
      const rawPort = normalizePort(record.port);
      // Default-group servers omit Port; the routing listener's
      // BackendServerPort is the authoritative backend port.
      const portFromListener = !/^\d+$/.test(rawPort) && listenerPort !== null;
      const port = portFromListener ? String(listenerPort) : rawPort;
      // Derive protocol from the group's listener context (stored on identity).
      const protocol = normalizeProtocol(group.identity.kind) || 'unknown';
      const resolvedProtocol = guessedProtocol || protocol;
      const resolution = resolveEndpointNodes(context.index, ip, port, resolvedProtocol, [...group.evidence], 'internal');
      const node = resolution.nodes[0] ?? resolution.synthesized;
      if (!node) continue;
      if (portFromListener) {
        warnings.push(`Backend ${ip} of server group ${group.id} has no explicit server port; using backend port ${listenerPort} from the routing CLB listener (BackendServerPort).`);
      } else if (resolution.synthesized) {
        warnings.push(`Backend ${ip}:${port} is not in the topology graph; a logical endpoint was derived from server group ${group.id}.`);
      }
      moves.push({ node });
    }
    if (moves.length === 0) {
      return {
        rule: 'clb:backend',
        moves: [],
        warnings: [`Server group ${group.id} has no usable backend servers.`, ...warnings],
      };
    }
    return {
      rule: 'clb:backend-synthesized',
      confidence: 'INFERRED',
      evidence: capEvidence(evidence, warnings),
      moves,
      warnings,
    };
  }

  /**
   * Best-effort backend protocol: if all listeners referencing this group share
   * one L7 protocol, use it; otherwise 'unknown' (TCP family compatible).
   */
  private guessBackendProtocol(context: ResolverContext, clbId: string, groupId: string): string {
    const protocols = new Set<string>();
    for (const node of context.index.topology.nodes) {
      if (node.type !== 'CLB_LISTENER') continue;
      if (String(node.identity.clbId) !== clbId) continue;
      const listenerGroupId = text((node.attributes as Record<string, unknown>).groupId) || 'default';
      if (listenerGroupId !== groupId) continue;
      protocols.add(normalizeProtocol(node.identity.protocol));
    }
    return protocols.size === 1 ? [...protocols][0] : 'unknown';
  }
}

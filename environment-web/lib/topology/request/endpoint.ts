/**
 * Endpoint identity and DNAT matching utilities (TASK-02 Step 1/3/4).
 *
 * Endpoint identity is always IP + Port + Protocol (RESOURCE_SCHEMAS.md §10.9):
 * `endpoint:<ip>:<port>:<protocol>`. These helpers are shared by the DNS and
 * NAT resolvers so that `10.1.1.1:80` and `10.1.1.1:8080` can never collapse.
 *
 * The per-index memo caches use WeakMap: they live exactly as long as the
 * GraphIndex they were built from and never mutate the shared index object.
 */
import { endpointId, normalizeIp, normalizePort, normalizeProtocol } from '../common.ts';
import type { Evidence, TopologyNode } from '../types.ts';
import type { GraphIndex } from '../path-explorer.ts';

/** Canonical endpoint id; re-exported under the task's `makeEndpointId` name. */
export const makeEndpointId = endpointId;

/** `null` when the value is not a `min-max` port range. Bounds are inclusive. */
export function parsePortRange(value: unknown): [number, number] | null {
  const raw = normalizePort(value);
  if (!/^\d+-\d+$/.test(raw)) return null;
  const [min, max] = raw.split('-').map(Number);
  return min <= max ? [min, max] : null;
}

/**
 * Does a DNAT rule's normalized external port accept the concrete query port?
 * `any` accepts everything; missing port evidence (`unknown`) accepts nothing.
 */
export function portMatches(candidatePort: unknown, port: number): boolean {
  const raw = normalizePort(candidatePort);
  if (raw === 'any') return true;
  if (raw === 'unknown' || !Number.isInteger(port)) return false;
  if (/^\d+$/.test(raw)) return Number(raw) === port;
  const range = parsePortRange(raw);
  return range !== null && port >= range[0] && port <= range[1];
}

/** TCP-family application protocols share one transport; V1 requests are TCP. */
const TCP_FAMILY = new Set(['tcp', 'http', 'https', 'grpc', 'grpc-tls']);

export function transportFamily(protocol: unknown): 'tcp' | 'udp' | 'unknown' {
  const raw = normalizeProtocol(protocol);
  if (raw === 'unknown') return 'unknown';
  if (raw === 'udp') return 'udp';
  return TCP_FAMILY.has(raw) ? 'tcp' : 'unknown';
}

/**
 * Does a DNAT rule's protocol accept the query protocol? `any`/missing (`unknown`
 * after normalization) accept everything; TCP-family labels match each other so
 * an `http` rule still accepts a `https` request riding the same TCP transport.
 */
export function protocolMatches(candidateProtocol: unknown, wantedProtocol: unknown): boolean {
  const candidate = normalizeProtocol(candidateProtocol);
  const wanted = normalizeProtocol(wantedProtocol);
  if (candidate === 'unknown' || wanted === 'unknown') return true;
  if (candidate === wanted) return true;
  const candidateFamily = transportFamily(candidate);
  return candidateFamily !== 'unknown' && candidateFamily === transportFamily(wanted);
}

export type EndpointResolution = {
  /**
   * Existing graph ENDPOINT nodes for this ip:port whose protocol accepts the
   * query protocol, in deterministic id order. Empty when none exist.
   */
  nodes: TopologyNode[];
  /** True when the exact `endpointId(ip, port, protocol)` node exists in the graph. */
  exact: boolean;
  /**
   * Logical endpoint synthesized from the request when the graph has no matching
   * node. It reuses the canonical id so later chain stages stay linkable, and it
   * is never written back into the TopologyGraph.
   */
  synthesized: TopologyNode | null;
};

/**
 * Resolve (or derive) the endpoint for `ip:port/protocol` against the graph.
 *
 * 1. exact id match -> the graph node itself (EXACT evidence anchor);
 * 2. same ip:port with a protocol label the query accepts (e.g. `http` vs `tcp`)
 *    -> those nodes, deterministic order, no random pick;
 * 3. nothing -> a synthesized logical endpoint with caller-supplied evidence.
 */
export function resolveEndpointNodes(
  index: GraphIndex,
  ip: string,
  port: number | string,
  protocol: string,
  evidence: Evidence[],
  natSide: 'external' | 'internal',
): EndpointResolution {
  const normalizedIp = normalizeIp(ip);
  const normalizedPort = normalizePort(port);
  const normalizedProtocol = normalizeProtocol(protocol);
  const exactNode = index.nodeById.get(makeEndpointId(normalizedIp, normalizedPort, normalizedProtocol));
  if (exactNode?.type === 'ENDPOINT') return { nodes: [exactNode], exact: true, synthesized: null };

  const compatible = endpointsAt(index, normalizedIp, normalizedPort)
    .filter(node => protocolMatches(node.identity.protocol, normalizedProtocol))
    .sort((a, b) => a.id.localeCompare(b.id));
  if (compatible.length > 0) return { nodes: compatible, exact: false, synthesized: null };

  return {
    nodes: [],
    exact: false,
    synthesized: {
      id: makeEndpointId(normalizedIp, normalizedPort, normalizedProtocol),
      type: 'ENDPOINT',
      label: `${normalizedIp}:${normalizedPort}:${normalizedProtocol}`,
      identity: { ip: normalizedIp, port: normalizedPort, protocol: normalizedProtocol },
      status: 'active',
      environment: 'GLOBAL',
      evidence,
      attributes: { discoveredBy: ['request-resolver'], natSide, synthesized: true },
    },
  };
}

const endpointsByIpPortCache = new WeakMap<GraphIndex, Map<string, TopologyNode[]>>();

/** ENDPOINT nodes grouped by normalized `ip:port`, built once per GraphIndex. */
function endpointsAt(index: GraphIndex, ip: string, port: string): TopologyNode[] {
  let byIpPort = endpointsByIpPortCache.get(index);
  if (!byIpPort) {
    byIpPort = new Map<string, TopologyNode[]>();
    for (const node of index.topology.nodes) {
      if (node.type !== 'ENDPOINT') continue;
      const key = `${normalizeIp(node.identity.ip)}:${normalizePort(node.identity.port)}`;
      const list = byIpPort.get(key);
      if (list) list.push(node);
      else byIpPort.set(key, [node]);
    }
    endpointsByIpPortCache.set(index, byIpPort);
  }
  return byIpPort.get(`${ip}:${port}`) ?? [];
}

export type DnatRuleMatch = {
  rule: TopologyNode;
  /** exact: precise port and protocol; loose: Any/range port or Any protocol. */
  specificity: 'exact' | 'loose';
  internalIp: string;
  internalPort: string;
  protocol: string;
};

const dnatRulesCache = new WeakMap<GraphIndex, Map<string, TopologyNode[]>>();

/** DNAT_RULE nodes grouped by normalized externalIp, built once per GraphIndex. */
function dnatRulesByExternalIp(index: GraphIndex): Map<string, TopologyNode[]> {
  let byIp = dnatRulesCache.get(index);
  if (!byIp) {
    byIp = new Map<string, TopologyNode[]>();
    for (const node of index.topology.nodes) {
      if (node.type !== 'DNAT_RULE') continue;
      const ip = normalizeIp(node.identity.externalIp);
      if (!ip) continue;
      const list = byIp.get(ip);
      if (list) list.push(node);
      else byIp.set(ip, [node]);
    }
    dnatRulesCache.set(index, byIp);
  }
  return byIp;
}

/**
 * Match DNAT rules against an external endpoint. Identity-based (not edge-id
 * based) so `Any`/range ports and protocol-family divergence still match.
 * Multiple matches are all returned; selection is never random.
 */
export function matchDnatRules(index: GraphIndex, ip: string, port: number, protocol: string): DnatRuleMatch[] {
  const matches: DnatRuleMatch[] = [];
  for (const rule of dnatRulesByExternalIp(index).get(normalizeIp(ip)) ?? []) {
    if (rule.status === 'paused') continue;
    if (!portMatches(rule.identity.externalPort, port)) continue;
    if (!protocolMatches(rule.identity.protocol, protocol)) continue;
    const internalIp = normalizeIp(rule.identity.internalIp);
    if (!internalIp) continue;
    const rulePort = normalizePort(rule.identity.externalPort);
    const ruleProtocol = normalizeProtocol(rule.identity.protocol);
    matches.push({
      rule,
      specificity: rulePort === normalizePort(port) && ruleProtocol === normalizeProtocol(protocol) ? 'exact' : 'loose',
      internalIp,
      internalPort: normalizePort(rule.identity.internalPort),
      protocol: ruleProtocol,
    });
  }
  return matches.sort((a, b) => a.rule.id.localeCompare(b.rule.id));
}

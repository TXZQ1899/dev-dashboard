import { normalizeDomain, normalizeIp, text } from './common.ts';
import type { Confidence, NodeType, TopologyEdge, TopologyEnvironment, TopologyGraph, TopologyNode } from './types.ts';

/** Entry-point node kinds that Path Explorer can resolve from a human query. */
export type NodeKind = 'domain' | 'application' | 'host';

export const NODE_TYPE_BY_KIND: Record<NodeKind, NodeType> = {
  domain: 'DOMAIN',
  application: 'APPLICATION',
  host: 'HOST',
};

/** Fallback destination for each entry kind when the caller does not pass an explicit target. */
export const DEFAULT_TARGETS: Record<NodeKind, NodeType[]> = {
  domain: ['APPLICATION'],
  application: ['HOST'],
  host: ['APPLICATION'],
};

const CONFIDENCE_RANK: Record<Confidence, number> = { EXACT: 3, INFERRED: 2, AMBIGUOUS: 1, UNKNOWN: 0 };

const DEFAULT_MAX_DEPTH = 8;
const DEFAULT_MAX_PATHS = 50;
const REACHABLE_NODE_CAP = 5000;
const MAX_STATES = 200000;

type AdjacentStep = {
  edge: TopologyEdge;
  neighborId: string;
  direction: 'forward' | 'reverse';
};

/**
 * A reusable, query-time index. Built once per topology so traversal never rescans
 * the full edge list.
 */
export type GraphIndex = {
  topology: TopologyGraph;
  nodeById: Map<string, TopologyNode>;
  /** node id -> every incident edge, in both directions. */
  adjacency: Map<string, AdjacentStep[]>;
  /** normalized lookup key -> nodes, per resolvable kind. */
  lookup: Record<NodeKind, Map<string, TopologyNode[]>>;
};

export type NodeResolution = {
  kind: NodeKind;
  query: string;
  status: 'found' | 'ambiguous' | 'not-found';
  /** All matches, deterministically ordered. Never reduced to a single random pick. */
  candidates: TopologyNode[];
  matchedBy: ('name' | 'ip' | 'id')[];
};

export type PathStep = {
  edge: TopologyEdge;
  from: TopologyNode;
  to: TopologyNode;
  /** 'forward' follows edge.from -> edge.to, 'reverse' follows edge.to -> edge.from. */
  direction: 'forward' | 'reverse';
};

export type TraversalPath = {
  /** Full node chain, length = edges.length + 1. */
  nodes: TopologyNode[];
  /** Full edge chain with evidence preserved. */
  edges: TopologyEdge[];
  steps: PathStep[];
  length: number;
  /** Weakest edge confidence on the path. */
  confidence: Confidence;
  /** Edge ids whose confidence equals the path confidence. */
  weakEdges: string[];
  /** Distinct edge environments along the path, in traversal order. */
  environments: TopologyEnvironment[];
  /** True when the path crosses an unresolved/external node or an UNKNOWN edge. */
  unresolved: boolean;
};

export type PathQuery = {
  kind: NodeKind;
  query: string;
  /** Target node types. Defaults to DEFAULT_TARGETS[kind]. */
  to?: NodeType[];
  /** Restrict env-specific edges to one environment. GLOBAL edges stay traversable. */
  environment?: TopologyEnvironment;
  maxDepth?: number;
  maxPaths?: number;
  /** When false, GLOBAL edges are excluded while an environment filter is active. */
  includeGlobal?: boolean;
};

export type PathQueryResult = {
  query: {
    kind: NodeKind;
    query: string;
    to: NodeType[];
    environment: TopologyEnvironment | null;
    maxDepth: number;
  };
  resolution: NodeResolution;
  status: 'resolved' | 'ambiguous' | 'unresolved';
  paths: TraversalPath[];
  /** Diagnostic notes explaining unresolved or ambiguous outcomes. */
  gaps: string[];
  /** Node types reachable from the resolved start nodes; populated when no path is found. */
  reachableTypes: NodeType[];
};

export function buildGraphIndex(topology: TopologyGraph): GraphIndex {
  const nodeById = new Map<string, TopologyNode>();
  for (const node of topology.nodes) nodeById.set(node.id, node);

  const adjacency = new Map<string, AdjacentStep[]>();
  const push = (key: string, step: AdjacentStep) => {
    const list = adjacency.get(key);
    if (list) list.push(step);
    else adjacency.set(key, [step]);
  };
  for (const edge of topology.edges) {
    // Traversal is bidirectional: forward follows the edge, reverse walks it back.
    if (nodeById.has(edge.to)) push(edge.from, { edge, neighborId: edge.to, direction: 'forward' });
    if (nodeById.has(edge.from)) push(edge.to, { edge, neighborId: edge.from, direction: 'reverse' });
  }
  for (const list of adjacency.values()) {
    list.sort((a, b) => a.edge.id.localeCompare(b.edge.id) || a.neighborId.localeCompare(b.neighborId));
  }

  const lookup: GraphIndex['lookup'] = { domain: new Map(), application: new Map(), host: new Map() };
  const addLookup = (kind: NodeKind, key: string, node: TopologyNode) => {
    if (!key) return;
    const list = lookup[kind].get(key);
    if (list) {
      if (!list.some(item => item.id === node.id)) list.push(node);
    } else {
      lookup[kind].set(key, [node]);
    }
  };

  for (const node of topology.nodes) {
    if (node.type === 'DOMAIN') {
      addLookup('domain', normalizeDomain(node.identity.name ?? node.label), node);
    } else if (node.type === 'APPLICATION') {
      addLookup('application', node.label.trim().toLowerCase(), node);
      addLookup('application', text(node.identity.devopsAppId).trim(), node);
    } else if (node.type === 'HOST') {
      addLookup('host', normalizeIp(node.identity.ip ?? node.label), node);
      const ecs = node.attributes.ecs as { name?: unknown } | undefined;
      addLookup('host', text(ecs?.name).trim().toLowerCase(), node);
      const jump = node.attributes.jumpserver as { hostname?: unknown } | undefined;
      addLookup('host', text(jump?.hostname).trim().toLowerCase(), node);
    }
  }

  return { topology, nodeById, adjacency, lookup };
}

export function resolveNodes(index: GraphIndex, kind: NodeKind, query: string): NodeResolution {
  const keys = lookupKeys(kind, query);
  const matched = new Map<string, TopologyNode>();
  const matchedBy = new Set<NodeResolution['matchedBy'][number]>();
  for (const [key, by] of keys) {
    const nodes = index.lookup[kind].get(key);
    if (!nodes?.length) continue;
    matchedBy.add(by);
    for (const node of nodes) matched.set(node.id, node);
  }
  const candidates = [...matched.values()].sort((a, b) => a.id.localeCompare(b.id));
  return {
    kind,
    query,
    status: candidates.length === 0 ? 'not-found' : candidates.length === 1 ? 'found' : 'ambiguous',
    candidates,
    matchedBy: [...matchedBy].sort(),
  };
}

/**
 * Generic path search: enumerate every simple path from the resolved start node(s)
 * to any node whose type is in the query target set.
 */
export function explorePaths(index: GraphIndex, query: PathQuery): PathQueryResult {
  const targets = new Set(query.to?.length ? query.to : DEFAULT_TARGETS[query.kind]);
  const maxDepth = query.maxDepth ?? DEFAULT_MAX_DEPTH;
  const maxPaths = query.maxPaths ?? DEFAULT_MAX_PATHS;
  const includeGlobal = query.includeGlobal ?? true;
  const resolution = resolveNodes(index, query.kind, query.query);
  const gaps: string[] = [];

  const allowedEnvironments = query.environment
    ? new Set<TopologyEnvironment>([query.environment, ...(includeGlobal ? (['GLOBAL'] as TopologyEnvironment[]) : [])])
    : null;
  const edgeAllowed = (edge: TopologyEdge) => !allowedEnvironments || allowedEnvironments.has(edge.environment);

  const collected: TraversalPath[] = [];
  const seen = new Set<string>();

  // Target-directed search. Multi-source BFS over the *filtered* bidirectional
  // graph gives each node its undirected edge distance to the nearest target.
  // Nodes not connected to any target are pruned outright (they cannot appear
  // on a valid answer), and expansion is ordered by g + distanceToTarget so a
  // hub start (e.g. a wildcard domain) cannot burn the state budget exploring
  // huge target-free fan-outs before reaching the branch that answers.
  const distToTarget = distancesToTargets(index, targets, edgeAllowed);

  type Partial = { node: TopologyNode; steps: PathStep[]; visited: Set<string> };
  const buckets = new Map<number, Partial[]>();
  let minKey = Infinity;
  const enqueue = (state: Partial) => {
    const remaining = distToTarget.get(state.node.id);
    if (remaining === undefined) return;
    const key = state.steps.length + remaining;
    if (key > maxDepth) return;
    const bucket = buckets.get(key);
    if (bucket) bucket.push(state);
    else buckets.set(key, [state]);
    if (key < minKey) minKey = key;
  };
  for (const node of resolution.candidates) enqueue({ node, steps: [], visited: new Set([node.id]) });

  let states = 0;
  while (Number.isFinite(minKey) && collected.length < maxPaths && states < MAX_STATES) {
    const bucket = buckets.get(minKey);
    if (!bucket || bucket.length === 0) {
      buckets.delete(minKey);
      let next = minKey + 1;
      while (next <= maxDepth * 2 && !(buckets.get(next)?.length)) next += 1;
      minKey = buckets.get(next)?.length ? next : Infinity;
      continue;
    }
    const current = bucket.shift() as Partial;
    states += 1;

    if (current.steps.length > 0 && targets.has(current.node.type)) {
      const path = buildPath(current.steps);
      const signature = path.edges.map(edge => edge.id).join('>');
      if (!seen.has(signature)) {
        seen.add(signature);
        collected.push(path);
      }
      continue;
    }
    if (current.steps.length >= maxDepth) continue;

    for (const step of index.adjacency.get(current.node.id) ?? []) {
      if (!edgeAllowed(step.edge)) continue;
      // Cycle detection: a node may appear only once per candidate path.
      if (current.visited.has(step.neighborId)) continue;
      const next = index.nodeById.get(step.neighborId);
      if (!next) continue;
      const visited = new Set(current.visited);
      visited.add(step.neighborId);
      enqueue({
        node: next,
        steps: [...current.steps, { edge: step.edge, from: current.node, to: next, direction: step.direction }],
        visited,
      });
    }
  }

  collected.sort(comparePaths);

  if (resolution.status === 'not-found') {
    gaps.push(`No ${query.kind.toUpperCase()} matches "${query.query}" in the topology.`);
  } else if (resolution.status === 'ambiguous') {
    gaps.push(
      `Query "${query.query}" matched ${resolution.candidates.length} ${query.kind} nodes (${resolution.candidates.map(node => node.id).join(', ')}); all were traversed deterministically.`,
    );
  }
  if (resolution.status !== 'not-found' && collected.length === 0) {
    const envNote = query.environment ? ` under environment ${query.environment}` : '';
    gaps.push(
      `No ${[...targets].join('/')} reachable from ${resolution.candidates.map(node => node.id).join(', ')} within maxDepth=${maxDepth}${envNote}.`,
    );
  }
  if (collected.length >= maxPaths) {
    gaps.push(`Path enumeration stopped at maxPaths=${maxPaths}; further paths may exist.`);
  }
  if (states >= MAX_STATES) {
    gaps.push(`Path enumeration stopped at ${MAX_STATES} traversal states; further paths may exist.`);
  }

  const reachableTypes = collected.length === 0 && resolution.candidates.length
    ? reachableTypesFrom(index, resolution.candidates, edgeAllowed, maxDepth)
    : [];

  const status: PathQueryResult['status'] = resolution.status === 'not-found' || collected.length === 0
    ? 'unresolved'
    : (resolution.status === 'ambiguous' || collected.length > 1) ? 'ambiguous' : 'resolved';

  return {
    query: {
      kind: query.kind,
      query: query.query,
      to: [...targets],
      environment: query.environment ?? null,
      maxDepth,
    },
    resolution,
    status,
    paths: collected,
    gaps,
    reachableTypes,
  };
}

/** Convenience wrapper: build the index and answer a single query. */
export function findPaths(topology: TopologyGraph, query: PathQuery): PathQueryResult {
  return explorePaths(buildGraphIndex(topology), query);
}

function buildPath(steps: PathStep[]): TraversalPath {
  const edges = steps.map(step => step.edge);
  const confidence = edges.reduce<Confidence>(
    (weakest, edge) => (CONFIDENCE_RANK[edge.confidence] < CONFIDENCE_RANK[weakest] ? edge.confidence : weakest),
    'EXACT',
  );
  return {
    nodes: [steps[0].from, ...steps.map(step => step.to)],
    edges,
    steps,
    length: edges.length,
    confidence,
    weakEdges: edges.filter(edge => edge.confidence === confidence).map(edge => edge.id),
    environments: [...new Set(edges.map(edge => edge.environment))],
    unresolved: steps.some(step => step.from.status === 'unresolved' || step.from.status === 'external' || step.to.status === 'unresolved' || step.to.status === 'external')
      || edges.some(edge => edge.confidence === 'UNKNOWN'),
  };
}

function comparePaths(a: TraversalPath, b: TraversalPath): number {
  const byConfidence = CONFIDENCE_RANK[b.confidence] - CONFIDENCE_RANK[a.confidence];
  if (byConfidence) return byConfidence;
  if (a.length !== b.length) return a.length - b.length;
  return a.edges.map(edge => edge.id).join('>').localeCompare(b.edges.map(edge => edge.id).join('>'));
}

/** Undirected edge distance from every node to the nearest target node. */
function distancesToTargets(
  index: GraphIndex,
  targets: Set<NodeType>,
  edgeAllowed: (edge: TopologyEdge) => boolean,
): Map<string, number> {
  const distances = new Map<string, number>();
  let frontier: TopologyNode[] = [];
  for (const node of index.nodeById.values()) {
    if (targets.has(node.type)) {
      distances.set(node.id, 0);
      frontier.push(node);
    }
  }
  let depth = 0;
  while (frontier.length) {
    depth += 1;
    const next: TopologyNode[] = [];
    for (const node of frontier) {
      for (const step of index.adjacency.get(node.id) ?? []) {
        if (!edgeAllowed(step.edge) || distances.has(step.neighborId)) continue;
        const neighbor = index.nodeById.get(step.neighborId);
        if (!neighbor) continue;
        distances.set(step.neighborId, depth);
        next.push(neighbor);
      }
    }
    frontier = next;
  }
  return distances;
}

function reachableTypesFrom(
  index: GraphIndex,
  starts: TopologyNode[],
  edgeAllowed: (edge: TopologyEdge) => boolean,
  maxDepth: number,
): NodeType[] {
  const found = new Set<NodeType>();
  const visited = new Set<string>(starts.map(node => node.id));
  let frontier = starts.map(node => ({ node, depth: 0 }));
  let seen = 0;
  while (frontier.length && seen < REACHABLE_NODE_CAP) {
    const next: typeof frontier = [];
    for (const { node, depth } of frontier) {
      seen += 1;
      if (depth >= maxDepth) continue;
      for (const step of index.adjacency.get(node.id) ?? []) {
        if (!edgeAllowed(step.edge) || visited.has(step.neighborId)) continue;
        const neighbor = index.nodeById.get(step.neighborId);
        if (!neighbor) continue;
        visited.add(step.neighborId);
        found.add(neighbor.type);
        next.push({ node: neighbor, depth: depth + 1 });
      }
    }
    frontier = next;
  }
  const order: NodeType[] = ['DOMAIN', 'EIP', 'NAT_GATEWAY', 'DNAT_RULE', 'CLB', 'CLB_LISTENER', 'SERVER_GROUP', 'HOST', 'ENDPOINT', 'NGINX_ROUTE', 'UPSTREAM', 'APPLICATION', 'DEPLOYMENT', 'REPOSITORY'];
  return order.filter(type => found.has(type));
}

function lookupKeys(kind: NodeKind, query: string): [string, NodeResolution['matchedBy'][number]][] {
  const raw = query.trim();
  if (kind === 'domain') return [[normalizeDomain(raw), 'name']];
  if (kind === 'application') return [[raw.toLowerCase(), 'name'], [raw, 'id']];
  const keys: [string, NodeResolution['matchedBy'][number]][] = [];
  const ip = normalizeIp(raw);
  if (ip) keys.push([ip, 'ip']);
  const name = raw.toLowerCase();
  if (name !== ip) keys.push([name, 'name']);
  return keys;
}
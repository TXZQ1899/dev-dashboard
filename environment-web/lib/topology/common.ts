import type { Confidence, Evidence, TopologyEdge, TopologyEnvironment, TopologyGraph, TopologyNode, EdgeType, NodeType } from './types.ts';

export type SnapshotInput = {
  generatedAt?: string;
  devops: unknown;
  repositories: unknown;
  dns: unknown;
  eip: unknown;
  nat: unknown;
  ecs: unknown;
  clb: unknown;
  jumpserver: unknown;
};

export function asArray<T = Record<string, unknown>>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}

export function asRecord(value: unknown): Record<string, unknown> {
  return (value && typeof value === 'object' && !Array.isArray(value)) ? value as Record<string, unknown> : {};
}

export function text(value: unknown): string {
  if (typeof value === 'string') return value.trim();
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return '';
}

export function numberText(value: unknown): string {
  const raw = text(value);
  return /^\d+$/.test(raw) ? String(Number(raw)) : raw;
}

export function normalizeIp(value: unknown): string {
  const raw = text(value).replace(/^\[|\]$/g, '');
  if (/^(?:\d{1,3}\.){3}\d{1,3}$/.test(raw)) {
    return raw.split('.').map(part => String(Number(part))).join('.');
  }
  return raw.toLowerCase();
}

export function normalizeDomain(value: unknown): string {
  return text(value).replace(/\.$/, '').toLowerCase();
}

export function normalizeProtocol(value: unknown): string {
  const raw = text(value).toLowerCase();
  if (!raw) return 'unknown';
  if (raw === 'any' || raw === '*') return 'unknown';
  if (['http', 'https', 'tcp', 'udp', 'grpc', 'grpc-tls'].includes(raw)) return raw;
  return raw;
}

export function normalizePort(value: unknown): string {
  const raw = numberText(value).toLowerCase();
  if (!raw) return 'unknown';
  if (raw === 'any' || raw === '*' || raw === '0') return 'any';
  if (/^\d+$/.test(raw)) return raw;
  if (/^\d+-\d+$/.test(raw)) return raw;
  return raw;
}

export function endpointId(ip: string, port: string, protocol: string): string {
  return `endpoint:${normalizeIp(ip)}:${normalizePort(port)}:${normalizeProtocol(protocol)}`;
}

export function hostId(ip: string): string {
  return `host:${normalizeIp(ip)}`;
}

export function domainId(domain: string): string {
  return `domain:${normalizeDomain(domain)}`;
}

export function toIso(value: unknown): string | null {
  const raw = text(value);
  if (!raw) return null;
  const parsed = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(raw)
    ? Date.parse(raw.replace(' ', 'T') + '+08:00')
    : Date.parse(raw);
  if (!Number.isFinite(parsed)) return null;
  return new Date(parsed).toISOString();
}

export function latestObservedAt(evidence: Evidence[]): string | null {
  const values = evidence.map(item => item.observedAt).filter((item): item is string => Boolean(item)).map(item => Date.parse(item)).filter(Number.isFinite);
  return values.length ? new Date(Math.max(...values)).toISOString() : null;
}

export function sha256Short(value: string): string {
  // A stable hash is enough for topology IDs and avoids putting raw credentials/long URLs into IDs.
  let hash = 0x811c9dc5;
  for (let i = 0; i < value.length; i++) {
    hash ^= value.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash.toString(16).padStart(8, '0');
}

export function canonicalGitUrl(value: unknown): string {
  const raw = text(value);
  if (!raw) return '';
  const ssh = raw.match(/^git@([^:]+):(.+?)(?:\.git)?$/i);
  let url = ssh ? `https://${ssh[1].toLowerCase()}/${ssh[2].replace(/\.git$/i, '')}` : raw.replace(/\.git$/i, '');
  const http = url.match(/^https?:\/\/([^/]+)\/(.+)$/i);
  if (http) url = `https://${http[1].toLowerCase()}/${http[2]}`;
  return url.replace(/\/+$/, '');
}

export class GraphCollector {
  private nodes = new Map<string, TopologyNode>();
  private edges = new Map<string, TopologyEdge>();

  node(input: {
    id: string;
    type: NodeType;
    label: string;
    identity?: Record<string, string | number | string[]>;
    status?: TopologyNode['status'];
    environment?: TopologyEnvironment;
    evidence?: Evidence[];
    attributes?: Record<string, unknown>;
  }): TopologyNode {
    const existing = this.nodes.get(input.id);
    if (existing) {
      if (existing.type !== input.type) throw new Error(`node id collision with different types: ${input.id}`);
      existing.evidence = mergeEvidence(existing.evidence, input.evidence || []);
      existing.attributes = mergeAttributes(existing.attributes, input.attributes || {});
      existing.identity = mergeIdentity(existing.identity, input.identity || {});
      if (existing.status === 'unknown' && input.status) existing.status = input.status;
      return existing;
    }
    const node: TopologyNode = {
      id: input.id,
      type: input.type,
      label: input.label,
      identity: input.identity || {},
      status: input.status || 'unknown',
      environment: input.environment || 'GLOBAL',
      evidence: input.evidence || [],
      attributes: input.attributes || {},
    };
    this.nodes.set(node.id, node);
    return node;
  }

  edge(input: {
    id: string;
    from: string;
    to: string;
    type: EdgeType;
    environment?: TopologyEnvironment;
    evidence?: Evidence[];
    confidence?: Confidence;
  }): TopologyEdge {
    const existing = this.edges.get(input.id);
    if (existing) {
      existing.evidence = mergeEvidence(existing.evidence, input.evidence || []);
      return existing;
    }
    const evidence = input.evidence || [];
    const edge: TopologyEdge = {
      id: input.id,
      from: input.from,
      to: input.to,
      type: input.type,
      environment: input.environment || 'GLOBAL',
      evidence,
      confidence: input.confidence || (evidence.length ? 'INFERRED' : 'UNKNOWN'),
      observedAt: latestObservedAt(evidence),
    };
    this.edges.set(edge.id, edge);
    return edge;
  }

  hasNode(id: string): boolean {
    return this.nodes.has(id);
  }

  graph(generatedAt: string): TopologyGraph {
    const nodes = [...this.nodes.values()].sort((a, b) => a.id.localeCompare(b.id));
    const edges = [...this.edges.values()].sort((a, b) => a.id.localeCompare(b.id));
    return {
      generatedAt,
      nodes,
      edges,
      stats: {
        nodeCount: nodes.length,
        edgeCount: edges.length,
        ambiguousEdges: edges.filter(edge => edge.confidence === 'AMBIGUOUS').length,
        unresolvedNodes: nodes.filter(node => node.status === 'unresolved' || node.status === 'external').length,
      },
    };
  }
}

function mergeEvidence(current: Evidence[], additions: Evidence[]): Evidence[] {
  const byKey = new Map<string, Evidence>();
  for (const item of [...current, ...additions]) {
    const key = `${item.source}|${item.sourceId}|${item.detail}`;
    if (!byKey.has(key)) byKey.set(key, item);
  }
  return [...byKey.values()].sort((a, b) => `${a.source}|${a.sourceId}`.localeCompare(`${b.source}|${b.sourceId}`));
}

function mergeAttributes(current: Record<string, unknown>, additions: Record<string, unknown>): Record<string, unknown> {
  const merged = { ...current };
  for (const [key, value] of Object.entries(additions)) {
    const existing = merged[key];
    if (Array.isArray(existing) && Array.isArray(value)) merged[key] = [...new Set([...existing as unknown[], ...value])];
    else merged[key] = value;
  }
  return merged;
}

function mergeIdentity(current: Record<string, string | number | string[]>, additions: Record<string, string | number | string[]>): Record<string, string | number | string[]> {
  const merged: Record<string, string | number | string[]> = { ...current };
  for (const [key, value] of Object.entries(additions)) {
    if (!(key in merged) || merged[key] === '' || merged[key] === null) merged[key] = value;
    else if (Array.isArray(merged[key]) && Array.isArray(value)) {
      merged[key] = [...new Set([...merged[key] as string[], ...value as string[]])];
    }
  }
  return merged;
}

import type { TopologyEdge, TopologyGraph, TopologyNode } from './types.ts';

export type ValidationIssue = {
  code:
    | 'DUPLICATE_NODE_ID'
    | 'DUPLICATE_EDGE_ID'
    | 'MISSING_EDGE_NODE'
    | 'INVALID_ENDPOINT_FORMAT'
    | 'DEPLOYMENT_WITHOUT_ENDPOINT'
    | 'ENDPOINT_WITHOUT_EXPECTED_HOST'
    | 'EDGE_WITHOUT_EVIDENCE'
    | 'AMBIGUOUS_EDGE_COUNT_MISMATCH'
    | 'INVALID_CONFIDENCE';
  message: string;
  nodeId?: string;
  edgeId?: string;
};

export type TopologyValidation = {
  valid: boolean;
  errors: ValidationIssue[];
  warnings: ValidationIssue[];
  stats: {
    ambiguousEdges: number;
    endpointCount: number;
    unresolvedNodes: number;
  };
};

const IPv4 = /^(?:\d{1,3}\.){3}\d{1,3}$/;
const IPv6 = /^(?:[A-Fa-f0-9]{1,4}:){2,7}[A-Fa-f0-9]{1,4}$|^::1$|^(?:[A-Fa-f0-9]{1,4}:){1,7}:$/;
const Hostname = /^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/i;
const Port = /^(?:\d{1,5}|any|unknown|\d{1,5}-\d{1,5})$/;
const Protocol = /^[a-z][a-z0-9-]*$/;

export function validateTopology(topology: TopologyGraph): TopologyValidation {
  const errors: ValidationIssue[] = [];
  const warnings: ValidationIssue[] = [];
  const nodeIds = new Set<string>();
  const edgeIds = new Set<string>();
  const nodesById = new Map<string, TopologyNode>();
  const endpointsByIp = new Map<string, TopologyNode[]>();
  const hostsByIp = new Map<string, TopologyNode>();

  for (const node of topology.nodes) {
    if (nodeIds.has(node.id)) {
      errors.push({ code: 'DUPLICATE_NODE_ID', message: `Duplicate node id ${node.id}`, nodeId: node.id });
      continue;
    }
    nodeIds.add(node.id);
    nodesById.set(node.id, node);
    if (node.type === 'HOST') {
      const ip = String(node.identity.ip || '');
      if (IPv4.test(ip)) hostsByIp.set(ip, node);
    }
  }

  for (const edge of topology.edges) {
    if (edgeIds.has(edge.id)) {
      errors.push({ code: 'DUPLICATE_EDGE_ID', message: `Duplicate edge id ${edge.id}`, edgeId: edge.id });
    } else {
      edgeIds.add(edge.id);
    }
    if (!nodesById.has(edge.from)) errors.push({ code: 'MISSING_EDGE_NODE', message: `Edge ${edge.id} references missing from node ${edge.from}`, edgeId: edge.id });
    if (!nodesById.has(edge.to)) errors.push({ code: 'MISSING_EDGE_NODE', message: `Edge ${edge.id} references missing to node ${edge.to}`, edgeId: edge.id });
    if (!edge.evidence.length) errors.push({ code: 'EDGE_WITHOUT_EVIDENCE', message: `Edge ${edge.id} has no evidence`, edgeId: edge.id });
    if (!['EXACT', 'INFERRED', 'AMBIGUOUS', 'UNKNOWN'].includes(edge.confidence)) {
      errors.push({ code: 'INVALID_CONFIDENCE', message: `Edge ${edge.id} has invalid confidence ${edge.confidence}`, edgeId: edge.id });
    }
  }

  for (const node of topology.nodes) {
    if (node.type !== 'ENDPOINT') continue;
    const issue = validateEndpoint(node);
    if (issue) {
      errors.push(issue);
      continue;
    }
    const ip = String(node.identity.ip || '');
    if (IPv4.test(ip)) {
      const list = endpointsByIp.get(ip) || [];
      list.push(node);
      endpointsByIp.set(ip, list);
    }
  }

  const outgoing = new Map<string, TopologyEdge[]>();
  for (const edge of topology.edges) {
    const list = outgoing.get(edge.from) || [];
    list.push(edge);
    outgoing.set(edge.from, list);
  }

  for (const node of topology.nodes) {
    if (node.type !== 'DEPLOYMENT') continue;
    if (!(outgoing.get(node.id) || []).some(edge => edge.type === 'LISTENS_ON')) {
      const issue: ValidationIssue = { code: 'DEPLOYMENT_WITHOUT_ENDPOINT', message: `Deployment ${node.id} has no LISTENS_ON endpoint`, nodeId: node.id };
      // A source row without an IP cannot support an endpoint; retain the finding without inventing one.
      if (String(node.identity.ip || '')) errors.push(issue);
      else warnings.push(issue);
    }
  }

  for (const [ip, endpoints] of endpointsByIp) {
    const host = hostsByIp.get(ip);
    if (!host) continue;
    for (const endpoint of endpoints) {
      const hasHostEdge = (outgoing.get(endpoint.id) || []).some(edge => edge.type === 'ON_HOST' && edge.to === host.id);
      if (!hasHostEdge) {
        const shouldHaveHost = (endpoint.attributes.discoveredBy as string[] | undefined)?.includes('devops');
        const issue: ValidationIssue = {
          code: 'ENDPOINT_WITHOUT_EXPECTED_HOST',
          message: `Endpoint ${endpoint.id} has a matching host ${host.id} but no ON_HOST edge`,
          nodeId: endpoint.id,
        };
        if (shouldHaveHost) errors.push(issue);
        else warnings.push(issue);
      }
    }
  }

  const ambiguousEdges = topology.edges.filter(edge => edge.confidence === 'AMBIGUOUS').length;
  if (topology.stats.ambiguousEdges !== ambiguousEdges) {
    errors.push({
      code: 'AMBIGUOUS_EDGE_COUNT_MISMATCH',
      message: `Stats report ${topology.stats.ambiguousEdges} ambiguous edges but graph contains ${ambiguousEdges}`,
    });
  }

  return {
    valid: errors.length === 0,
    errors,
    warnings,
    stats: {
      ambiguousEdges,
      endpointCount: topology.nodes.filter(node => node.type === 'ENDPOINT').length,
      unresolvedNodes: topology.nodes.filter(node => node.status === 'unresolved' || node.status === 'external').length,
    },
  };
}

function validateEndpoint(node: TopologyNode): ValidationIssue | undefined {
  const ip = String(node.identity.ip ?? '');
  const port = String(node.identity.port ?? '');
  const protocol = String(node.identity.protocol ?? '');
  const expected = `endpoint:${ip}:${port}:${protocol}`;
  const validParts = (IPv4.test(ip) || IPv6.test(ip) || Hostname.test(ip)) && Port.test(port) && Protocol.test(protocol);
  if (node.id !== expected || !validParts) {
    return {
      code: 'INVALID_ENDPOINT_FORMAT',
      message: `Endpoint ${node.id} does not match identity ${expected} or has invalid ip/port/protocol`,
      nodeId: node.id,
    };
  }
  return undefined;
}

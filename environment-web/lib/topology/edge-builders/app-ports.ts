import type { GraphCollector } from '../common.ts';
import type { HostIndex } from '../node-builders/hosts.ts';
import type { AppPortEndpoint } from '../node-builders/app-ports.ts';

/**
 * Attaches `ON_HOST` edges for endpoints newly created from `appPorts` (those
 * with no prior `ip:port` endpoint). Enriched endpoints already carry their
 * `ON_HOST` edge from the DevOps/Nginx edge builders, so they are not touched
 * here. The edge id mirrors the other builders so a later DevOps/Nginx edge
 * for the same endpoint merges by id instead of duplicating.
 */
export function buildAppPortEdges(graph: GraphCollector, endpoints: AppPortEndpoint[], hosts: HostIndex): void {
  for (const endpoint of endpoints) {
    const host = hosts.byIp.get(endpoint.ip);
    if (!host) continue;
    graph.edge({
      id: `edge:ON_HOST:${endpoint.endpointId}:${host.id}`,
      from: endpoint.endpointId,
      to: host.id,
      type: 'ON_HOST',
      environment: 'GLOBAL',
      evidence: [...endpoint.evidence, ...host.evidence],
      confidence: 'EXACT',
    });
  }
}

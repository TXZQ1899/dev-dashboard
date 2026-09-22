import type { GraphCollector } from '../common.ts';
import type { HostIndex } from '../node-builders/hosts.ts';
import { domainId, normalizeIp } from '../common.ts';
import type { NginxBridge } from '../node-builders/nginx.ts';

export function buildNginxEdges(graph: GraphCollector, bridges: NginxBridge[], hosts: HostIndex): void {
  for (const bridge of bridges) {
    for (const domain of bridge.domains) {
      graph.edge({
        id: `edge:SERVED_BY:${domainId(domain)}:${bridge.routeNode.id}`,
        from: domainId(domain),
        to: bridge.routeNode.id,
        type: 'SERVED_BY',
        environment: 'GLOBAL',
        evidence: bridge.evidence,
        confidence: 'EXACT',
      });
    }
    graph.edge({
      id: `edge:USES_UPSTREAM:${bridge.routeNode.id}:${bridge.upstreamNode.id}`,
      from: bridge.routeNode.id,
      to: bridge.upstreamNode.id,
      type: 'USES_UPSTREAM',
      environment: 'GLOBAL',
      evidence: bridge.evidence,
      confidence: 'EXACT',
    });
    for (const endpointId of bridge.endpointIds) {
      graph.edge({
        id: `edge:FORWARDS_TO:${bridge.upstreamNode.id}:${endpointId}`,
        from: bridge.upstreamNode.id,
        to: endpointId,
        type: 'FORWARDS_TO',
        environment: 'GLOBAL',
        evidence: bridge.evidence,
        confidence: 'EXACT',
      });
      const host = normalizeIp(endpointId.slice('endpoint:'.length).split(':')[0]);
      const hostNode = hosts.byIp.get(host);
      if (hostNode) {
        graph.edge({
          id: `edge:ON_HOST:${endpointId}:${hostNode.id}`,
          from: endpointId,
          to: hostNode.id,
          type: 'ON_HOST',
          environment: 'GLOBAL',
          evidence: [...bridge.evidence, ...hostNode.evidence],
          confidence: 'EXACT',
        });
      }
    }
  }
}

import type { GraphCollector } from '../common.ts';
import type { HostIndex } from '../node-builders/hosts.ts';
import type { ApplicationBuildResult } from '../node-builders/applications.ts';

export function buildApplicationEdges(graph: GraphCollector, applications: ApplicationBuildResult, hosts: HostIndex): void {
  for (const deployment of applications.deployments) {
    graph.edge({
      id: `edge:HAS_DEPLOYMENT:${deployment.appId}:${deployment.node.id}`,
      from: `application:${deployment.appId}`,
      to: deployment.node.id,
      type: 'HAS_DEPLOYMENT',
      environment: deployment.env,
      evidence: deployment.evidence,
      confidence: 'EXACT',
    });

    if (!deployment.ip) continue;
    const endpointId = `endpoint:${deployment.ip}:${deployment.port || 'unknown'}:unknown`;
    const knownPortsOnHost = new Set(
      applications.deployments
        .filter(item => item.ip === deployment.ip && /^\d+$/.test(item.port))
        .map(item => item.port),
    );
    graph.edge({
      id: `edge:LISTENS_ON:${deployment.node.id}:${endpointId}`,
      from: deployment.node.id,
      to: endpointId,
      type: 'LISTENS_ON',
      environment: deployment.env,
      evidence: deployment.evidence,
      confidence: /^\d+$/.test(deployment.port)
        ? 'EXACT'
        // IP-only evidence cannot be assigned to one of several application ports on the same host.
        : knownPortsOnHost.size > 0 ? 'AMBIGUOUS' : 'INFERRED',
    });

    const host = hosts.byIp.get(deployment.ip);
    if (host) {
      graph.edge({
        id: `edge:ON_HOST:${endpointId}:${host.id}`,
        from: endpointId,
        to: host.id,
        type: 'ON_HOST',
        environment: deployment.env,
        evidence: [...deployment.evidence, ...host.evidence],
        confidence: 'EXACT',
      });
    }
  }

  for (const [appId, repository] of applications.repositoryByApp) {
    graph.edge({
      id: `edge:BUILT_FROM:application:${appId}:${repository.id}`,
      from: `application:${appId}`,
      to: repository.id,
      type: 'BUILT_FROM',
      environment: 'GLOBAL',
      evidence: repository.evidence,
      confidence: 'EXACT',
    });
  }
}

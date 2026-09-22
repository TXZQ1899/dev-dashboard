import type { GraphCollector, SnapshotInput } from '../common.ts';
import { asArray, asRecord, canonicalGitUrl, endpointId, normalizeIp, normalizePort, sha256Short, text, toIso } from '../common.ts';
import type { Evidence, TopologyEnvironment, TopologyNode } from '../types.ts';

export type DeploymentRecord = {
  appId: string;
  appName: string;
  env: TopologyEnvironment;
  deploymentId: string;
  node: TopologyNode;
  ip: string;
  port: string;
  repository: string;
  evidence: Evidence[];
};

export type ApplicationBuildResult = {
  deployments: DeploymentRecord[];
  endpoints: Map<string, TopologyNode>;
  repositoryByApp: Map<string, { id: string; url: string; evidence: Evidence[] }>;
};

export function buildApplicationNodes(graph: GraphCollector, input: SnapshotInput): ApplicationBuildResult {
  const devopsRoot = asRecord(input.devops);
  const devopsAt = toIso(devopsRoot.collectedAt);
  const repositoryRoot = asRecord(input.repositories);
  const repositories = asArray(repositoryRoot.repos);
  const repositoryByCanonical = new Map<string, Record<string, unknown>>();
  for (const repository of repositories) {
    const canonical = canonicalGitUrl(repository.url);
    if (canonical) repositoryByCanonical.set(canonical, repository);
  }

  const deployments: DeploymentRecord[] = [];
  const endpoints = new Map<string, TopologyNode>();
  const repositoryByApp = new Map<string, { id: string; url: string; evidence: Evidence[] }>();

  for (const appRecord of asArray(devopsRoot.apps)) {
    const appId = text(appRecord.id);
    const appName = text(appRecord.name);
    const appEvidence: Evidence = {
      source: 'devops',
      sourceId: appId,
      reference: 'environment-web/lib/snapshot.json',
      detail: `DevOps application ${appName} (HTTP ${text(appRecord.http)})`,
      observedAt: toIso(appRecord.capturedAt) ?? devopsAt,
    };
    graph.node({
      id: `application:${appId}`,
      type: 'APPLICATION',
      label: appName || appId,
      identity: { devopsAppId: appId },
      status: 'active',
      environment: 'GLOBAL',
      evidence: [appEvidence],
      attributes: {
        http: text(appRecord.http),
        declaredPort: normalizePort(appRecord.port),
        branch: text(appRecord.branch),
      },
    });

    const appRepository = canonicalGitUrl(appRecord.repository);
    const deploymentRepositories = new Set<string>();
    for (const [, rows] of Object.entries(asRecord(appRecord.envs))) {
      for (const deployment of asArray(rows)) {
        const url = canonicalGitUrl(deployment.repository);
        if (url) deploymentRepositories.add(url);
      }
    }
    const repositoryUrl = appRepository || [...deploymentRepositories][0] || '';
    if (repositoryUrl) {
      const matched = repositoryByCanonical.get(repositoryUrl);
      const repositoryId = `repository:${sha256Short(repositoryUrl)}`;
      const evidence: Evidence = matched ? {
        source: 'repository',
        sourceId: text(matched.id),
        reference: 'environment-web/lib/repositories.json',
        detail: `Repository ${text(matched.path)} (${text(matched.difference)})`,
        observedAt: toIso(repositoryRoot.codeupCollectedAt) ?? toIso(repositoryRoot.snapshotDate),
      } : {
        source: 'devops',
        sourceId: appId,
        reference: 'environment-web/lib/snapshot.json',
        detail: `DevOps repository declaration ${repositoryUrl}`,
        observedAt: appEvidence.observedAt,
      };
      graph.node({
        id: repositoryId,
        type: 'REPOSITORY',
        label: text(matched?.name) || repositoryUrl.split('/').pop() || repositoryUrl,
        identity: { url: repositoryUrl },
        status: matched ? 'active' : 'unresolved',
        environment: 'GLOBAL',
        evidence: [evidence],
        attributes: {
          url: repositoryUrl,
          difference: text(matched?.difference) || 'devops_only',
          groupName: text(matched?.groupName),
          match: text(matched?.match),
        },
      });
      repositoryByApp.set(appId, { id: repositoryId, url: repositoryUrl, evidence: [evidence] });
    }

    for (const [env, rows] of Object.entries(asRecord(appRecord.envs))) {
      for (const deploymentRecord of asArray(rows)) {
        const deployId = text(deploymentRecord.deploy);
        const ip = normalizeIp(deploymentRecord.ip);
        const port = normalizePort(deploymentRecord.port || appRecord.port);
        const deploymentNodeId = `deployment:${appId}:${env}:${deployId}`;
        const evidence: Evidence[] = [{
          source: 'devops',
          sourceId: `${appId}/${env}/${deployId}`,
          reference: 'environment-web/lib/snapshot.json',
          detail: `DevOps deployment ${appName} ${env} deploy ${deployId} on ${ip || 'unknown IP'}`,
          observedAt: devopsAt,
        }];
        const deploymentNode = graph.node({
          id: deploymentNodeId,
          type: 'DEPLOYMENT',
          label: `${appName} ${env} ${deployId}`,
          identity: { appId, env, deployId, ip, port },
          status: text(deploymentRecord.status) === '成功' ? 'active' : 'unknown',
          environment: env as TopologyEnvironment,
          evidence,
          attributes: {
            config: text(deploymentRecord.config),
            status: text(deploymentRecord.status),
            error: text(deploymentRecord.error),
            branch: text(deploymentRecord.branch),
            branchSource: text(deploymentRecord.branchSource),
            repository: canonicalGitUrl(deploymentRecord.repository),
            lastPublishedAt: text(deploymentRecord.lastPublishedAt),
            publishStatus: text(deploymentRecord.publishStatus),
          },
        });
        deployments.push({
          appId,
          appName,
          env: env as TopologyEnvironment,
          deploymentId: deployId,
          node: deploymentNode,
          ip,
          port,
          repository: canonicalGitUrl(deploymentRecord.repository),
          evidence,
        });
        if (ip) {
          const id = endpointId(ip, port || 'unknown', 'unknown');
          const endpoint = graph.node({
            id,
            type: 'ENDPOINT',
            label: `${ip}:${port || 'unknown'}:unknown`,
            identity: { ip, port: port || 'unknown', protocol: 'unknown' },
            status: 'active',
            environment: 'GLOBAL',
            evidence,
            attributes: { discoveredBy: ['devops'] },
          });
          endpoints.set(id, endpoint);
        }
      }
    }
  }

  return { deployments, endpoints, repositoryByApp };
}

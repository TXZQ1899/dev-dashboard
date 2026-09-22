import type { GraphCollector, SnapshotInput } from '../common.ts';
import { asArray, asRecord, hostId, normalizeIp, text, toIso } from '../common.ts';
import type { Evidence, TopologyNode } from '../types.ts';

export type HostIndex = {
  byIp: Map<string, TopologyNode>;
};

type IndexedHost = {
  node: TopologyNode;
  ips: Set<string>;
};

export function buildHostNodes(graph: GraphCollector, input: SnapshotInput): HostIndex {
  const hosts = new Map<string, IndexedHost>();
  const byIp = new Map<string, TopologyNode>();
  const ecsRoot = asRecord(input.ecs);
  const observedAt = toIso(ecsRoot.fetchedAt);

  const attach = (ip: string, evidence: Evidence, identity: Record<string, string | number | string[]>, attributes: Record<string, unknown>) => {
    const normalized = normalizeIp(ip);
    if (!normalized) return;
    const existingHost = byIp.get(normalized);
    if (existingHost) {
      existingHost.evidence = mergeUnique(existingHost.evidence, [evidence]);
      existingHost.identity = mergeHostIdentity(existingHost.identity, identity);
      existingHost.attributes = { ...existingHost.attributes, ...attributes };
      return;
    }
    const node = graph.node({
      id: hostId(normalized),
      type: 'HOST',
      label: normalized,
      identity: { ip: normalized, ...identity },
      status: 'active',
      evidence: [evidence],
      attributes,
    });
    byIp.set(normalized, node);
    const indexed = hosts.get(node.id);
    if (indexed) indexed.ips.add(normalized);
    else hosts.set(node.id, { node, ips: new Set([normalized]) });
  };

  for (const instance of asArray(ecsRoot.instances)) {
    const privateIps = asArray<string>(instance.privateIps).map(normalizeIp).filter(Boolean);
    const publicIps = asArray<string>(instance.publicIps).map(normalizeIp).filter(Boolean);
    const ips = [...new Set([...privateIps, ...publicIps])];
    const evidence: Evidence = {
      source: 'ecs',
      sourceId: text(instance.id),
      reference: 'environment-web/lib/ecs-snapshot.json',
      detail: `ECS instance ${text(instance.name)} (${ips.join(', ')})`,
      observedAt,
    };
    const identity: Record<string, string | number | string[]> = { cloudInstanceIds: [text(instance.id)] };
    const attributes: Record<string, unknown> = {
      ecs: {
        id: text(instance.id),
        name: text(instance.name),
        status: text(instance.status),
        region: text(instance.region),
        zone: text(instance.zone),
        instanceType: text(instance.instanceType),
        os: text(instance.os),
        privateIps,
        publicIps,
        tags: instance.tags ?? {},
      },
    };
    for (const ip of ips) attach(ip, evidence, identity, attributes);
  }

  const jumpRoot = asRecord(input.jumpserver);
  const jumpAt = toIso(jumpRoot.collectedAt);
  for (const asset of asArray(jumpRoot.assets)) {
    const ip = normalizeIp(asset.ip);
    if (!ip) continue;
    const evidence: Evidence = {
      source: 'jumpserver',
      sourceId: text(asset.id),
      reference: 'environment-web/lib/jumpserver-snapshot.json',
      detail: `JumpServer asset ${text(asset.hostname)} (${ip})`,
      observedAt: jumpAt,
    };
    attach(ip, evidence, { jumpserverAssetIds: [text(asset.id)] }, {
      jumpserver: { id: text(asset.id), hostname: text(asset.hostname), os: text(asset.os), platform: text(asset.platform) },
    });
  }

  const devopsRoot = asRecord(input.devops);
  const devopsAt = toIso(devopsRoot.collectedAt);
  for (const app of asArray(devopsRoot.apps)) {
    for (const [env, deployments] of Object.entries(asRecord(app.envs))) {
      for (const deployment of asArray(deployments)) {
        const ip = normalizeIp(deployment.ip);
        if (!ip) continue;
        const evidence: Evidence = {
          source: 'devops',
          sourceId: `${text(app.id)}/${env}/${text(deployment.deploy)}`,
          reference: 'environment-web/lib/snapshot.json',
          detail: `DevOps deployment ${text(app.name)} ${env} deploy ${text(deployment.deploy)}`,
          observedAt: devopsAt,
        };
        attach(ip, evidence, { devopsDeploymentIds: [`${text(app.id)}/${env}/${text(deployment.deploy)}`] }, {});
      }
    }
  }

  return { byIp };
}

function mergeUnique(current: Evidence[], additions: Evidence[]): Evidence[] {
  const map = new Map<string, Evidence>();
  for (const item of [...current, ...additions]) map.set(`${item.source}|${item.sourceId}|${item.detail}`, item);
  return [...map.values()];
}

function mergeHostIdentity(current: Record<string, string | number | string[]>, additions: Record<string, string | number | string[]>): Record<string, string | number | string[]> {
  const merged = { ...current };
  for (const [key, value] of Object.entries(additions)) {
    if (!(key in merged)) merged[key] = value;
    else if (Array.isArray(merged[key]) && Array.isArray(value)) merged[key] = [...new Set([...merged[key] as string[], ...value as string[]])];
  }
  return merged;
}

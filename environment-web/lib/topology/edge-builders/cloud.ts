import type { GraphCollector } from '../common.ts';
import type { HostIndex } from '../node-builders/hosts.ts';
import { asArray, endpointId, normalizeIp, normalizePort, normalizeProtocol, text } from '../common.ts';
import type { CloudBuildResult } from '../node-builders/cloud.ts';

export function buildCloudEdges(graph: GraphCollector, cloud: CloudBuildResult, hosts: HostIndex): void {
  const hostByInstanceId = new Map<string, string>();
  for (const host of hosts.byIp.values()) {
    for (const instanceId of (host.identity.cloudInstanceIds as string[] | undefined) || []) hostByInstanceId.set(instanceId, host.id);
  }
  const natById = new Map(cloud.dnatRules.map(item => [item.gateway.id, item]));
  const clbById = new Map([...cloud.clbByIp.values()].flat().map(node => [node.id, node]));

  for (const eip of cloud.eips) {
    const bindingId = text(eip.record.bindingId);
    const bindingType = text(eip.record.bindingType);
    const target = natById.get(`nat-gateway:${bindingId}`)?.gateway.id
      ?? clbById.get(`clb:${bindingId}`)?.id
      ?? hostByInstanceId.get(bindingId);
    if (target) {
      graph.edge({
        id: `edge:BOUND_TO:${eip.node.id}:${target}`,
        from: eip.node.id,
        to: target,
        type: 'BOUND_TO',
        environment: 'GLOBAL',
        evidence: [eip.evidence],
        confidence: 'EXACT',
      });
      continue;
    }
    // IP-only binding evidence is kept but never upgraded to a definite cloud instance.
    if (/ecs|实例|eni|网卡/i.test(bindingType)) {
      const ip = normalizeIp(eip.record.ip);
      const host = ip ? hosts.byIp.get(ip) : undefined;
      if (host) {
        graph.edge({
          id: `edge:BOUND_TO:${eip.node.id}:${host.id}`,
          from: eip.node.id,
          to: host.id,
          type: 'BOUND_TO',
          environment: 'GLOBAL',
          evidence: [eip.evidence, ...host.evidence],
          confidence: 'AMBIGUOUS',
        });
      }
    }
  }

  for (const rule of cloud.dnatRules) {
    graph.edge({
      id: `edge:HAS_DNAT_RULE:${rule.gateway.id}:${rule.rule.id}`,
      from: rule.gateway.id,
      to: rule.rule.id,
      type: 'HAS_DNAT_RULE',
      environment: 'GLOBAL',
      evidence: [rule.evidence],
      confidence: 'EXACT',
    });

    const externalIp = normalizeIp(rule.entry.externalIp);
    const externalPort = normalizePort(rule.entry.externalPort);
    const protocol = normalizeProtocol(rule.entry.protocol);
    if (externalIp) {
      const externalEndpoint = endpointId(externalIp, externalPort, protocol);
      graph.edge({
        id: `edge:EXPOSES:${rule.rule.id}:${externalEndpoint}`,
        from: rule.rule.id,
        to: externalEndpoint,
        type: 'EXPOSES',
        environment: 'GLOBAL',
        evidence: [rule.evidence],
        confidence: 'EXACT',
      });
      addEndpointHostEdge(graph, externalEndpoint, externalIp, hosts, [rule.evidence]);
    }

    const internalIp = normalizeIp(rule.entry.internalIp);
    const internalPort = normalizePort(rule.entry.internalPort);
    if (internalIp) {
      const internalEndpoint = endpointId(internalIp, internalPort, protocol);
      graph.edge({
        id: `edge:FORWARDS_TO:${rule.rule.id}:${internalEndpoint}`,
        from: rule.rule.id,
        to: internalEndpoint,
        type: 'FORWARDS_TO',
        environment: 'GLOBAL',
        evidence: [rule.evidence],
        confidence: 'EXACT',
      });
      addEndpointHostEdge(graph, internalEndpoint, internalIp, hosts, [rule.evidence]);
    }
  }

  for (const listener of cloud.listeners) {
    graph.edge({
      id: `edge:HAS_LISTENER:${listener.clb.id}:${listener.listener.id}`,
      from: listener.clb.id,
      to: listener.listener.id,
      type: 'HAS_LISTENER',
      environment: 'GLOBAL',
      evidence: [listener.evidence],
      confidence: 'EXACT',
    });
    const groupId = text(listener.record.groupId) || 'default';
    graph.edge({
      id: `edge:ROUTES_TO:${listener.listener.id}:clb-server-group:${listener.clb.id}:${groupId}`,
      from: listener.listener.id,
      to: `clb-server-group:${String(listener.clb.identity.clbId)}:${groupId}`,
      type: 'ROUTES_TO',
      environment: 'GLOBAL',
      evidence: [listener.evidence],
      confidence: 'EXACT',
    });
  }

  const protocolsByGroup = new Map<string, Set<string>>();
  for (const listener of cloud.listeners) {
    const groupId = text(listener.record.groupId) || 'default';
    const set = protocolsByGroup.get(`${String(listener.clb.identity.clbId)}/${groupId}`) || new Set<string>();
    set.add(String(listener.listener.identity.protocol || 'unknown'));
    protocolsByGroup.set(`${String(listener.clb.identity.clbId)}/${groupId}`, set);
  }

  for (const group of cloud.groups) {
    const groupId = text(group.record.id) || 'default';
    const protocols = protocolsByGroup.get(`${String(group.clb.identity.clbId)}/${groupId}`) || new Set(['unknown']);
    for (const server of asArray(group.record.servers)) {
      const ip = normalizeIp(server.ip);
      if (!ip) continue;
      const port = normalizePort(server.port);
      const protocol = protocols.size === 1 ? [...protocols][0] : 'unknown';
      const endpoint = endpointId(ip, port, protocol);
      graph.edge({
        id: `edge:FORWARDS_TO:${group.group.id}:${endpoint}`,
        from: group.group.id,
        to: endpoint,
        type: 'FORWARDS_TO',
        environment: 'GLOBAL',
        evidence: [group.evidence],
        confidence: 'EXACT',
      });
      addEndpointHostEdge(graph, endpoint, ip, hosts, [group.evidence]);
    }
  }
}

function addEndpointHostEdge(graph: GraphCollector, endpoint: string, ip: string, hosts: HostIndex, evidence: Parameters<GraphCollector['edge']>[0]['evidence']): void {
  const host = hosts.byIp.get(ip);
  if (!host) return;
  graph.edge({
    id: `edge:ON_HOST:${endpoint}:${host.id}`,
    from: endpoint,
    to: host.id,
    type: 'ON_HOST',
    environment: 'GLOBAL',
    evidence: [...(evidence || []), ...host.evidence],
    confidence: 'EXACT',
  });
}


import type { GraphCollector, SnapshotInput } from '../common.ts';
import { asArray, asRecord, endpointId, normalizeIp, normalizePort, normalizeProtocol, text, toIso } from '../common.ts';
import type { Evidence, TopologyNode } from '../types.ts';

export type EipInfo = { node: TopologyNode; record: Record<string, unknown>; evidence: Evidence };
export type DnatInfo = { gateway: TopologyNode; rule: TopologyNode; entry: Record<string, unknown>; evidence: Evidence };
export type ListenerInfo = { clb: TopologyNode; listener: TopologyNode; record: Record<string, unknown>; evidence: Evidence };
export type GroupInfo = { clb: TopologyNode; group: TopologyNode; record: Record<string, unknown>; evidence: Evidence };

export type CloudBuildResult = {
  eips: EipInfo[];
  dnatRules: DnatInfo[];
  listeners: ListenerInfo[];
  groups: GroupInfo[];
  eipByIp: Map<string, TopologyNode>;
  clbByIp: Map<string, TopologyNode[]>;
  endpoints: Map<string, TopologyNode>;
};

export function buildCloudNodes(graph: GraphCollector, input: SnapshotInput): CloudBuildResult {
  const endpoints = new Map<string, TopologyNode>();
  const eipByIp = new Map<string, TopologyNode>();
  const clbByIp = new Map<string, TopologyNode[]>();

  const eipRoot = asRecord(input.eip);
  const eipAt = toIso(eipRoot.snapshotDate);
  const eips: EipInfo[] = asArray(eipRoot.records).map(record => {
    const ip = normalizeIp(record.ip);
    const evidence: Evidence = {
      source: 'eip',
      sourceId: text(record.id),
      reference: `environment-web/lib/eip-snapshot.json (${text(record.source)} row ${text(record.row)})`,
      detail: `EIP ${text(record.name)} ${ip}, binding ${text(record.bindingType)} ${text(record.bindingId)}`,
      observedAt: eipAt,
    };
    const node = graph.node({
      id: `eip:${text(record.id)}`,
      type: 'EIP',
      label: text(record.name) || text(record.id),
      identity: { eipId: text(record.id), ip },
      status: 'active',
      environment: 'GLOBAL',
      evidence: [evidence],
      attributes: {
        protection: text(record.protection),
        bindingType: text(record.bindingType),
        bindingId: text(record.bindingId),
        bindingName: text(record.bindingName),
        status: text(record.status),
        bandwidth: text(record.bandwidth),
        network: text(record.network),
        owner: text(record.owner),
      },
    });
    if (ip) eipByIp.set(ip, node);
    return { node, record, evidence };
  });

  const natRoot = asRecord(input.nat);
  const dnatRules: DnatInfo[] = [];
  if (natRoot.available) {
    const natAt = toIso(natRoot.collectedAt);
    for (const gateway of asArray(natRoot.gateways)) {
      const gatewayEvidence: Evidence = {
        source: 'nat',
        sourceId: text(gateway.id),
        reference: 'environment-web/lib/nat-snapshot.json',
        detail: `NAT gateway ${text(gateway.name)} (${text(gateway.id)}), VPC ${text(gateway.vpcId)}`,
        observedAt: natAt,
      };
      const gatewayNode = graph.node({
        id: `nat-gateway:${text(gateway.id)}`,
        type: 'NAT_GATEWAY',
        label: text(gateway.name) || text(gateway.id),
        identity: { natGatewayId: text(gateway.id), vpcId: text(gateway.vpcId) },
        status: text(gateway.status) === 'Available' ? 'active' : 'unknown',
        environment: 'GLOBAL',
        evidence: [gatewayEvidence],
        attributes: { status: text(gateway.status), region: text(natRoot.region), vpcId: text(gateway.vpcId) },
      });
      for (const entry of asArray(gateway.entries)) {
        const evidence: Evidence = {
          source: 'nat',
          sourceId: `${text(gateway.id)}/${text(entry.id)}`,
          reference: 'environment-web/lib/nat-snapshot.json',
          detail: `DNAT ${normalizeIp(entry.externalIp)}:${normalizePort(entry.externalPort)} ${normalizeProtocol(entry.protocol)} -> ${normalizeIp(entry.internalIp)}:${normalizePort(entry.internalPort)}`,
          observedAt: natAt,
        };
        const rule = graph.node({
          id: `dnat-rule:${text(gateway.id)}:${text(entry.id)}`,
          type: 'DNAT_RULE',
          label: text(entry.name) || text(entry.id),
          identity: {
            dnatEntryId: text(entry.id),
            externalIp: normalizeIp(entry.externalIp),
            externalPort: normalizePort(entry.externalPort),
            internalIp: normalizeIp(entry.internalIp),
            internalPort: normalizePort(entry.internalPort),
            protocol: normalizeProtocol(entry.protocol),
          },
          status: text(entry.status) === 'Available' ? 'active' : 'unknown',
          environment: 'GLOBAL',
          evidence: [evidence],
          attributes: {
            tableId: text(entry.tableId),
            status: text(entry.status),
            externalIp: normalizeIp(entry.externalIp),
            externalPort: normalizePort(entry.externalPort),
            internalIp: normalizeIp(entry.internalIp),
            internalPort: normalizePort(entry.internalPort),
            protocol: normalizeProtocol(entry.protocol),
          },
        });
        const protocol = normalizeProtocol(entry.protocol);
        for (const [side, ip, port] of [
          ['external', normalizeIp(entry.externalIp), normalizePort(entry.externalPort)],
          ['internal', normalizeIp(entry.internalIp), normalizePort(entry.internalPort)],
        ] as const) {
          if (!ip) continue;
          const id = endpointId(ip, port, protocol);
          const endpoint = graph.node({
            id,
            type: 'ENDPOINT',
            label: `${ip}:${port}:${protocol}`,
            identity: { ip, port, protocol },
            status: 'active',
            environment: 'GLOBAL',
            evidence: [evidence],
            attributes: { discoveredBy: ['nat'], natSide: side },
          });
          endpoints.set(id, endpoint);
        }
        dnatRules.push({ gateway: gatewayNode, rule, entry, evidence });
      }
    }
  }

  const clbRoot = asRecord(input.clb);
  const listeners: ListenerInfo[] = [];
  const groups: GroupInfo[] = [];
  if (clbRoot.available) {
    const clbAt = toIso(clbRoot.collectedAt);
    for (const instance of asArray(clbRoot.instances)) {
      const ip = normalizeIp(instance.ip);
      const evidence: Evidence = {
        source: 'clb',
        sourceId: text(instance.id),
        reference: 'environment-web/lib/clb-snapshot.json',
        detail: `CLB ${text(instance.name)} (${text(instance.id)}) ${ip}`,
        observedAt: clbAt,
      };
      const clb = graph.node({
        id: `clb:${text(instance.id)}`,
        type: 'CLB',
        label: text(instance.name) || text(instance.id),
        identity: { clbId: text(instance.id), ip },
        status: text(instance.status) === 'active' ? 'active' : 'unknown',
        environment: 'GLOBAL',
        evidence: [evidence],
        attributes: { addressType: text(instance.addressType), status: text(instance.status), region: text(clbRoot.region) },
      });
      if (ip) {
        const list = clbByIp.get(ip) || [];
        list.push(clb);
        clbByIp.set(ip, list);
      }
      for (const listener of asArray(instance.listeners)) {
        const protocol = normalizeProtocol(listener.protocol);
        const port = normalizePort(listener.port);
        const listenerEvidence: Evidence = {
          source: 'clb',
          sourceId: `${text(instance.id)}/${protocol}/${port}`,
          reference: 'environment-web/lib/clb-snapshot.json',
          detail: `CLB listener ${protocol}/${port}, status ${text(listener.status)}`,
          observedAt: clbAt,
        };
        const listenerNode = graph.node({
          id: `clb-listener:${text(instance.id)}:${protocol}:${port}`,
          type: 'CLB_LISTENER',
          label: `${text(instance.name)} ${protocol}/${port}`,
          identity: { clbId: text(instance.id), protocol, port },
          status: text(listener.status) === 'running' ? 'active' : 'unknown',
          environment: 'GLOBAL',
          evidence: [listenerEvidence],
          attributes: {
            status: text(listener.status),
            description: text(listener.description),
            healthCheck: text(listener.healthCheck),
            backendPort: listener.backendPort ?? null,
            forwardPort: listener.forwardPort ?? null,
            rules: listener.rules ?? [],
            certificates: listener.certificates ?? [],
          },
        });
        listeners.push({ clb, listener: listenerNode, record: listener, evidence: listenerEvidence });
      }
      for (const group of asArray(instance.groups)) {
        const groupId = text(group.id) || 'default';
        const groupEvidence: Evidence = {
          source: 'clb',
          sourceId: `${text(instance.id)}/${groupId}`,
          reference: 'environment-web/lib/clb-snapshot.json',
          detail: `CLB server group ${text(group.name)} (${groupId}), ${asArray(group.servers).length} backends`,
          observedAt: clbAt,
        };
        const groupNode = graph.node({
          id: `clb-server-group:${text(instance.id)}:${groupId}`,
          type: 'SERVER_GROUP',
          label: text(group.name) || groupId,
          identity: { clbId: text(instance.id), serverGroupId: groupId, kind: text(group.kind) },
          status: 'active',
          environment: 'GLOBAL',
          evidence: [groupEvidence],
          attributes: { kind: text(group.kind), servers: group.servers ?? [] },
        });
        groups.push({ clb, group: groupNode, record: group, evidence: groupEvidence });
        for (const server of asArray(group.servers)) {
          const serverIp = normalizeIp(server.ip);
          const serverPort = normalizePort(server.port);
          if (!serverIp) continue;
          const endpointProtocol = normalizeProtocol(listenerProtocolFor(instance, groupId));
          const id = endpointId(serverIp, serverPort, endpointProtocol);
          const endpoint = graph.node({
            id,
            type: 'ENDPOINT',
            label: `${serverIp}:${serverPort}:${endpointProtocol}`,
            identity: { ip: serverIp, port: serverPort, protocol: endpointProtocol },
            status: 'active',
            environment: 'GLOBAL',
            evidence: [groupEvidence],
            attributes: { discoveredBy: ['clb'] },
          });
          endpoints.set(id, endpoint);
        }
      }
    }
  }

  return { eips, dnatRules, listeners, groups, eipByIp, clbByIp, endpoints };
}

function listenerProtocolFor(instance: Record<string, unknown>, groupId: string): string {
  const protocols = [...new Set(asArray(instance.listeners)
    .filter(listener => (text(listener.groupId) || 'default') === groupId)
    .map(listener => normalizeProtocol(listener.protocol)))];
  return protocols.length === 1 ? protocols[0] : 'unknown';
}

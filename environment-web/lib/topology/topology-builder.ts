import { GraphCollector, asArray, asRecord, normalizeIp } from './common.ts';
import type { SnapshotInput } from './common.ts';
import { buildApplicationEdges } from './edge-builders/applications.ts';
import { buildAppPortEdges } from './edge-builders/app-ports.ts';
import { buildCloudEdges } from './edge-builders/cloud.ts';
import { buildDnsEdges } from './edge-builders/dns.ts';
import { buildNginxEdges } from './edge-builders/nginx.ts';
import { buildApplicationNodes } from './node-builders/applications.ts';
import { buildAppPortNodes } from './node-builders/app-ports.ts';
import { buildCloudNodes } from './node-builders/cloud.ts';
import { buildDnsNodes } from './node-builders/dns.ts';
import { buildHostNodes } from './node-builders/hosts.ts';
import { buildNginxNodes } from './node-builders/nginx.ts';
import type { TopologyGraph } from './types.ts';
import { validateTopology } from './topology-validator.ts';

export function buildTopology(input: SnapshotInput, generatedAt = new Date().toISOString()): TopologyGraph {
  const graph = new GraphCollector();

  // Hosts are built first so every endpoint can be attached to the same merged logical host.
  const hosts = buildHostNodes(graph, input);
  const cloud = buildCloudNodes(graph, input);
  const applications = buildApplicationNodes(graph, input);

  const knownIps = new Set<string>([...cloud.eipByIp.keys(), ...cloud.clbByIp.keys()]);
  const natRoot = asRecord(input.nat);
  if (natRoot.available) {
    for (const gateway of asArray(natRoot.gateways)) {
      for (const entry of asArray(gateway.entries)) {
        for (const ip of [entry.externalIp, entry.internalIp]) {
          const normalized = normalizeIp(ip);
          if (normalized) knownIps.add(normalized);
        }
      }
    }
  }
  const dns = buildDnsNodes(graph, input, hosts, knownIps);
  const nginx = buildNginxNodes(graph, input);
  // appPorts runs after applications + nginx so it can enrich existing
  // ip:port endpoints with the runtime app identity before edges are built.
  const appPorts = buildAppPortNodes(graph, input);

  buildApplicationEdges(graph, applications, hosts);
  buildAppPortEdges(graph, appPorts, hosts);
  buildDnsEdges(graph, dns, cloud, hosts);
  buildCloudEdges(graph, cloud, hosts);
  buildNginxEdges(graph, nginx, hosts);

  return graph.graph(generatedAt);
}

export function buildValidatedTopology(input: SnapshotInput, generatedAt = new Date().toISOString()) {
  const topology = buildTopology(input, generatedAt);
  return { topology, validation: validateTopology(topology) };
}

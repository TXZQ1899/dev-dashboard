/* eslint-disable typescript/no-require-imports -- Node test harness uses CommonJS, matching the existing suite. */
const { test } = require('node:test');
const assert = require('node:assert/strict');

const OBSERVED_AT = '2026-09-23T00:00:00.000Z';

// Synthetic fixtures only (PROJECT_GUIDE §15); ids follow lib/topology/common.ts conventions.
const ev = (source, sourceId) => [{
  source,
  sourceId,
  reference: 'tests/request-resolver-dns-nat.test.cjs',
  detail: `${source}:${sourceId}`,
  observedAt: OBSERVED_AT,
}];

const epId = (ip, port, protocol) => `endpoint:${ip}:${port}:${protocol}`;

const node = (id, type, identity, options = {}) => ({
  id,
  type,
  label: options.label ?? id,
  identity,
  status: options.status ?? 'active',
  environment: 'GLOBAL',
  evidence: options.evidence ?? ev('dns', `node ${id}`),
  attributes: options.attributes ?? {},
});

const domain = (name, options = {}) => node(`domain:${name}`, 'DOMAIN', { name }, options);
const eip = (id, ip, options = {}) => node(`eip:${id}`, 'EIP', { eipId: id, ip }, { evidence: ev('eip', id), ...options });
const endpoint = (ip, port, protocol, natSide, options = {}) => {
  // DNAT rules record protocol `any`, which the topology builder normalizes to `unknown`.
  const normalizedProtocol = protocol === 'any' ? 'unknown' : protocol;
  return node(
    epId(ip, port, normalizedProtocol),
    'ENDPOINT',
    { ip, port, protocol: normalizedProtocol },
    { evidence: ev('nat', 'dnat-entry'), attributes: { natSide, discoveredBy: ['nat'] }, ...options },
  );
};
const natGateway = id => node(`nat-gateway:${id}`, 'NAT_GATEWAY', { natGatewayId: id }, { evidence: ev('nat', id) });
const dnatRule = (gatewayId, entryId, identity) => node(
  `dnat-rule:${gatewayId}:${entryId}`,
  'DNAT_RULE',
  { dnatEntryId: entryId, ...identity },
  { evidence: ev('nat', entryId) },
);

const edge = (id, from, to, type, options = {}) => ({
  id: `edge:${id}`,
  from,
  to,
  type,
  environment: options.environment ?? 'GLOBAL',
  evidence: options.evidence ?? ev('dns', `edge ${id}`),
  confidence: options.confidence ?? 'EXACT',
  observedAt: OBSERVED_AT,
});

const graph = (nodes, edges = []) => ({
  generatedAt: OBSERVED_AT,
  nodes,
  edges,
  stats: { nodeCount: nodes.length, edgeCount: edges.length, ambiguousEdges: 0, unresolvedNodes: 0 },
});

const framework = async () => await import('../lib/topology/request/index.ts');
const endpointUtils = async () => await import('../lib/topology/request/endpoint.ts');

const dnsRegistry = async () => {
  const { ResolverRegistry, DnsResolver } = await framework();
  return new ResolverRegistry().register(new DnsResolver());
};
const defaultRegistry = async () => {
  const { createDefaultResolverRegistry } = await framework();
  return createDefaultResolverRegistry();
};

// ---------------------------------------------------------------------------
// Step 2 — DNSResolver
// ---------------------------------------------------------------------------

test('dns: A record resolves DOMAIN -> EIP with edge evidence', async () => {
  const { resolveRequestPath, STOP_REASON } = await framework();
  const d = domain('api.example.com');
  const e = eip('eip-1', '47.1.1.1');
  const g = graph([d, e], [edge('r1', d.id, e.id, 'RESOLVES_TO')]);
  const trace = resolveRequestPath('https://api.example.com/', g, { registry: await dnsRegistry() });
  assert.equal(trace.paths.length, 1);
  const [path] = trace.paths;
  assert.deepEqual(path.nodes.map(item => item.id), ['domain:api.example.com', 'eip:eip-1']);
  assert.deepEqual(path.steps.map(step => step.rule), ['dns:entry', 'dns:records']);
  assert.equal(path.steps[0].confidence, 'EXACT');
  assert.equal(path.steps[1].confidence, 'EXACT');
  assert.ok(path.steps[1].evidence.length > 0);
  assert.equal(path.terminalNodeId, null);
  // With only the DNSResolver registered, no resolver continues from an EIP node.
  assert.equal(path.reason, STOP_REASON.NO_ENTRY);
  assert.equal(path.stoppedAt, 'eip:eip-1');
});

test('dns: CNAME -> A chain keeps every hop', async () => {
  const { resolveRequestPath } = await framework();
  const d1 = domain('api.example.com');
  const d2 = domain('api-gateway.example.net');
  const e = eip('eip-1', '47.1.1.1');
  const g = graph([d1, d2, e], [
    edge('c1', d1.id, d2.id, 'CNAME_TO'),
    edge('r1', d2.id, e.id, 'RESOLVES_TO'),
  ]);
  const trace = resolveRequestPath('https://api.example.com/', g, { registry: await dnsRegistry() });
  assert.equal(trace.paths.length, 1);
  assert.deepEqual(trace.paths[0].nodes.map(item => item.id), [d1.id, d2.id, e.id]);
  assert.deepEqual(trace.paths[0].steps.map(step => step.rule), ['dns:entry', 'dns:records', 'dns:records']);
});

test('dns: two-level CNAME chain resolves to the final A record', async () => {
  const { resolveRequestPath } = await framework();
  const d1 = domain('a.example.com');
  const d2 = domain('b.example.net');
  const d3 = domain('c.example.org');
  const e = eip('eip-1', '47.1.1.1');
  const g = graph([d1, d2, d3, e], [
    edge('c1', d1.id, d2.id, 'CNAME_TO'),
    edge('c2', d2.id, d3.id, 'CNAME_TO'),
    edge('r1', d3.id, e.id, 'RESOLVES_TO'),
  ]);
  const trace = resolveRequestPath('https://a.example.com/', g, { registry: await dnsRegistry() });
  assert.equal(trace.paths.length, 1);
  assert.deepEqual(trace.paths[0].nodes.map(item => item.id), [d1.id, d2.id, d3.id, e.id]);
});

test('dns: multiple A records fan out deterministically without a random pick', async () => {
  const { resolveRequestPath } = await framework();
  const d = domain('api.example.com');
  const e1 = eip('eip-1', '47.1.1.1');
  const e2 = eip('eip-2', '47.1.1.2');
  const g = graph([d, e1, e2], [
    edge('r1', d.id, e1.id, 'RESOLVES_TO'),
    edge('r2', d.id, e2.id, 'RESOLVES_TO'),
  ]);
  const trace = resolveRequestPath('https://api.example.com/', g, { registry: await dnsRegistry() });
  assert.equal(trace.paths.length, 2);
  assert.deepEqual(trace.paths.map(path => path.stoppedAt), ['eip:eip-1', 'eip:eip-2']);
  assert.deepEqual(trace.paths[0].steps[1].outputNodeIds, ['eip:eip-1', 'eip:eip-2']);
});

test('dns: paused record cannot carry the active traffic path', async () => {
  const { resolveRequestPath, STOP_REASON } = await framework();
  const d = domain('api.example.com', { status: 'paused' });
  const e = eip('eip-1', '47.1.1.1');
  const g = graph([d, e], [edge('r1', d.id, e.id, 'RESOLVES_TO')]);
  const trace = resolveRequestPath('https://api.example.com/', g, { registry: await dnsRegistry() });
  assert.equal(trace.paths.length, 1);
  const [path] = trace.paths;
  assert.deepEqual(path.nodes, []);
  assert.equal(path.steps.length, 0);
  assert.equal(path.reason, STOP_REASON.NO_CANDIDATE);
  assert.ok(trace.warnings.some(message => message.includes('paused')));
});

test('dns: paused CNAME target blocks continuation but keeps the queried domain', async () => {
  const { resolveRequestPath } = await framework();
  const d1 = domain('api.example.com');
  const d2 = domain('api-gateway.example.net', { status: 'paused' });
  const g = graph([d1, d2], [edge('c1', d1.id, d2.id, 'CNAME_TO')]);
  const trace = resolveRequestPath('https://api.example.com/', g, { registry: await dnsRegistry() });
  assert.equal(trace.paths.length, 1);
  assert.deepEqual(trace.paths[0].nodes.map(item => item.id), [d1.id]);
  assert.equal(trace.paths[0].stoppedAt, d1.id);
  assert.ok(trace.warnings.some(message => message.includes('paused')));
});

test('dns: external CNAME target is preserved as an unresolved branch', async () => {
  const { resolveRequestPath, STOP_REASON } = await framework();
  const d1 = domain('api.example.com');
  const d2 = domain('cdn.third-party.com', { status: 'external' });
  const g = graph([d1, d2], [edge('c1', d1.id, d2.id, 'CNAME_TO')]);
  const trace = resolveRequestPath('https://api.example.com/', g, { registry: await dnsRegistry() });
  assert.equal(trace.paths.length, 1);
  assert.deepEqual(trace.paths[0].nodes.map(item => item.id), [d1.id, d2.id]);
  assert.equal(trace.paths[0].stoppedAt, d2.id);
  assert.equal(trace.paths[0].reason, STOP_REASON.NO_CANDIDATE);
});

test('dns: CNAME cycle stays within the engine cycle guard', async () => {
  const { resolveRequestPath, STOP_REASON } = await framework();
  const d1 = domain('a.example.com');
  const d2 = domain('b.example.com');
  const g = graph([d1, d2], [
    edge('c1', d1.id, d2.id, 'CNAME_TO'),
    edge('c2', d2.id, d1.id, 'CNAME_TO'),
  ]);
  const trace = resolveRequestPath('https://a.example.com/', g, { registry: await dnsRegistry() });
  assert.equal(trace.paths.length, 1);
  assert.deepEqual(trace.paths[0].nodes.map(item => item.id), [d1.id, d2.id]);
  assert.equal(trace.paths[0].reason, STOP_REASON.CYCLE);
});

test('dns: environment filter skips env-specific edges', async () => {
  const { resolveRequestPath } = await framework();
  const d1 = domain('api.example.com');
  const d2 = domain('api-gateway.example.net');
  const g = graph([d1, d2], [edge('c1', d1.id, d2.id, 'CNAME_TO', { environment: 'TEST' })]);
  const blocked = resolveRequestPath('https://api.example.com/', g, {
    registry: await dnsRegistry(),
    environment: 'PRODUCT',
  });
  assert.deepEqual(blocked.paths[0].nodes.map(item => item.id), [d1.id]);
  assert.ok(blocked.warnings.some(message => message.includes('environment filter')));
  const allowed = resolveRequestPath('https://api.example.com/', g, {
    registry: await dnsRegistry(),
    environment: 'TEST',
  });
  assert.deepEqual(allowed.paths[0].nodes.map(item => item.id), [d1.id, d2.id]);
});

// ---------------------------------------------------------------------------
// Step 1 — Endpoint utilities
// ---------------------------------------------------------------------------

test('endpoint: makeEndpointId normalizes ip, port and protocol', async () => {
  const { makeEndpointId } = await endpointUtils();
  assert.equal(makeEndpointId('10.179.1.10', '8080', 'TCP'), 'endpoint:10.179.1.10:8080:tcp');
  assert.equal(makeEndpointId('10.179.1.10', 8080, 'tcp'), 'endpoint:10.179.1.10:8080:tcp');
});

test('endpoint: parsePortRange accepts ranges only', async () => {
  const { parsePortRange } = await endpointUtils();
  assert.deepEqual(parsePortRange('8440-8450'), [8440, 8450]);
  assert.equal(parsePortRange('443'), null);
  assert.equal(parsePortRange('any'), null);
});

test('endpoint: portMatches supports exact ports, Any and ranges', async () => {
  const { portMatches } = await endpointUtils();
  assert.equal(portMatches('443', 443), true);
  assert.equal(portMatches('443', 80), false);
  assert.equal(portMatches('any', 8080), true);
  assert.equal(portMatches('0', 443), true); // 0 normalizes to Any
  assert.equal(portMatches('8440-8450', 8443), true);
  assert.equal(portMatches('8440-8450', 8451), false);
  assert.equal(portMatches('unknown', 443), false);
});

test('endpoint: protocolMatches supports Any and transport families', async () => {
  const { protocolMatches } = await endpointUtils();
  assert.equal(protocolMatches('tcp', 'tcp'), true);
  assert.equal(protocolMatches('any', 'tcp'), true);
  assert.equal(protocolMatches('unknown', 'tcp'), true);
  assert.equal(protocolMatches('http', 'tcp'), true);
  assert.equal(protocolMatches('https', 'tcp'), true);
  assert.equal(protocolMatches('udp', 'tcp'), false);
  assert.equal(protocolMatches('sctp', 'tcp'), false);
});

test('endpoint: resolveEndpointNodes prefers exact id, then compatible labels, then synthesizes', async () => {
  const { buildGraphIndex } = await import('../lib/topology/path-explorer.ts');
  const { resolveEndpointNodes } = await endpointUtils();
  const exact = endpoint('10.0.0.1', '8080', 'tcp', 'external');
  const compatible = endpoint('10.0.0.3', '443', 'http', 'external');
  const index = buildGraphIndex(graph([exact, compatible]));

  const exactHit = resolveEndpointNodes(index, '10.0.0.1', 8080, 'tcp', ev('eip', 'eip-1'), 'external');
  assert.equal(exactHit.exact, true);
  assert.deepEqual(exactHit.nodes.map(item => item.id), [exact.id]);
  assert.equal(exactHit.synthesized, null);

  const compatibleHit = resolveEndpointNodes(index, '10.0.0.3', 443, 'tcp', [], 'external');
  assert.equal(compatibleHit.exact, false);
  assert.deepEqual(compatibleHit.nodes.map(item => item.id), [compatible.id]);
  assert.equal(compatibleHit.synthesized, null);

  const synthesized = resolveEndpointNodes(index, '10.0.0.2', 9999, 'tcp', [], 'external');
  assert.deepEqual(synthesized.nodes, []);
  assert.equal(synthesized.synthesized.id, 'endpoint:10.0.0.2:9999:tcp');
  assert.equal(synthesized.synthesized.identity.port, '9999');
  assert.equal(synthesized.synthesized.attributes.synthesized, true);
  assert.equal(synthesized.synthesized.attributes.natSide, 'external');
});

// ---------------------------------------------------------------------------
// Step 3/4 — external endpoint + NAT/DNAT resolution (default registry)
// ---------------------------------------------------------------------------

const dnatGraph = (ruleIdentity, externalEndpointId, internalEndpointId) => {
  const d = domain('api.example.com');
  const e = eip('eip-1', '47.1.1.1');
  const gw = natGateway('ngw-1');
  const rule = dnatRule('ngw-1', 'fwd-1', ruleIdentity);
  const nodes = [d, e, gw, rule];
  const edges = [
    edge('r1', d.id, e.id, 'RESOLVES_TO'),
    edge('h1', gw.id, rule.id, 'HAS_DNAT_RULE'),
  ];
  if (externalEndpointId) {
    nodes.push(endpoint('47.1.1.1', ruleIdentity.externalPort, ruleIdentity.protocol, 'external'));
    edges.push(edge('x1', rule.id, externalEndpointId, 'EXPOSES'));
  }
  if (internalEndpointId) {
    nodes.push(endpoint(ruleIdentity.internalIp, ruleIdentity.internalPort, ruleIdentity.protocol, 'internal'));
    edges.push(edge('f1', rule.id, internalEndpointId, 'FORWARDS_TO'));
  }
  return graph(nodes, edges);
};

test('dnat: exact port rule rewrites 443 to 8443 (full chain acceptance)', async () => {
  const { resolveRequestPath, STOP_REASON } = await framework();
  const internalId = epId('10.1.1.10', '8443', 'tcp');
  const g = dnatGraph(
    { externalIp: '47.1.1.1', externalPort: '443', internalIp: '10.1.1.10', internalPort: '8443', protocol: 'tcp' },
    epId('47.1.1.1', '443', 'tcp'),
    internalId,
  );
  const registry = await defaultRegistry();
  const trace = resolveRequestPath('https://api.example.com/order/1', g, { registry });
  assert.equal(trace.paths.length, 1);
  const [path] = trace.paths;
  assert.deepEqual(path.nodes.map(item => item.type), ['DOMAIN', 'EIP', 'ENDPOINT', 'ENDPOINT']);
  assert.deepEqual(path.nodes.map(item => item.id), [
    'domain:api.example.com',
    'eip:eip-1',
    epId('47.1.1.1', '443', 'tcp'),
    internalId,
  ]);
  assert.deepEqual(path.steps.map(step => step.rule), ['dns:entry', 'dns:records', 'nat:external-endpoint', 'nat:dnat']);
  assert.equal(path.steps[2].confidence, 'EXACT');
  assert.equal(path.steps[3].confidence, 'EXACT');
  assert.ok(path.steps[3].evidence.some(item => item.source === 'nat'));
  assert.equal(path.confidence, 'EXACT');
  // Terminal semantics (TASK-01 review point): internal endpoints must NOT be
  // terminal; only reaching an APPLICATION node claims RESOLVED (TASK-06).
  assert.equal(path.terminalNodeId, null);
  // TASK-06 semantics: the chain progressed (DNS → EIP → DNAT) but stopped at
  // the internal endpoint with no nginx/application data => PARTIAL.
  assert.equal(path.status, 'PARTIAL');
  // NginxResolver (TASK-04) now fires on the internal endpoint but finds no
  // associated host, so the branch stops with no-candidate (not no-resolver-entry).
  assert.equal(path.reason, STOP_REASON.NO_CANDIDATE);
  // Deterministic result.
  const again = resolveRequestPath('https://api.example.com/order/1', g, { registry });
  assert.deepEqual(JSON.parse(JSON.stringify(trace)), JSON.parse(JSON.stringify(again)));
});

test('dnat: Any external port matches the query port via a derived endpoint', async () => {
  const { resolveRequestPath } = await framework();
  const g = dnatGraph(
    { externalIp: '47.1.1.1', externalPort: 'any', internalIp: '10.1.1.10', internalPort: '8080', protocol: 'tcp' },
    epId('47.1.1.1', 'any', 'tcp'),
    epId('10.1.1.10', '8080', 'tcp'),
  );
  const trace = resolveRequestPath('http://api.example.com/', g, { registry: await defaultRegistry() });
  assert.equal(trace.paths.length, 1);
  const [path] = trace.paths;
  const external = path.nodes[2];
  assert.equal(external.id, epId('47.1.1.1', '80', 'tcp'));
  assert.equal(external.attributes.synthesized, true);
  assert.equal(path.steps[2].confidence, 'INFERRED');
  assert.equal(path.steps[3].confidence, 'INFERRED'); // loose (Any port) match
  assert.ok(path.steps[3].evidence.length > 0);
  assert.equal(path.nodes[3].id, epId('10.1.1.10', '8080', 'tcp'));
});

test('dnat: external port range matches only ports inside the range', async () => {
  const { resolveRequestPath } = await framework();
  const g = dnatGraph(
    { externalIp: '47.1.1.1', externalPort: '8440-8450', internalIp: '10.1.1.10', internalPort: '8443', protocol: 'tcp' },
    epId('47.1.1.1', '8440-8450', 'tcp'),
    epId('10.1.1.10', '8443', 'tcp'),
  );
  const inside = resolveRequestPath('https://api.example.com:8443/', g, { registry: await defaultRegistry() });
  assert.equal(inside.paths.length, 1);
  assert.equal(inside.paths[0].nodes[3].id, epId('10.1.1.10', '8443', 'tcp'));
  assert.equal(inside.paths[0].steps[3].confidence, 'INFERRED');

  const outside = resolveRequestPath('https://api.example.com:8451/', g, { registry: await defaultRegistry() });
  assert.equal(outside.paths.length, 1);
  assert.equal(outside.paths[0].nodes[2].id, epId('47.1.1.1', '8451', 'tcp'));
  assert.equal(outside.paths[0].nodes.length, 3);
  assert.ok(outside.warnings.some(message => message.includes('No DNAT rule matches')));
});

test('dnat: Any protocol matches the TCP request through a divergent endpoint label', async () => {
  const { resolveRequestPath } = await framework();
  const g = dnatGraph(
    { externalIp: '47.1.1.1', externalPort: '443', internalIp: '10.1.1.10', internalPort: '8443', protocol: 'any' },
    epId('47.1.1.1', '443', 'unknown'),
    epId('10.1.1.10', '8443', 'unknown'),
  );
  const trace = resolveRequestPath('https://api.example.com/', g, { registry: await defaultRegistry() });
  assert.equal(trace.paths.length, 1);
  const [path] = trace.paths;
  // The graph endpoint carries the rule's Any protocol label; the request still reaches it.
  assert.deepEqual(path.nodes.map(item => item.id), [
    'domain:api.example.com',
    'eip:eip-1',
    epId('47.1.1.1', '443', 'unknown'),
    epId('10.1.1.10', '8443', 'unknown'),
  ]);
  assert.equal(path.steps[2].confidence, 'INFERRED');
  assert.equal(path.steps[3].confidence, 'INFERRED');
  assert.ok(trace.warnings.some(message => message.includes('divergent protocol label')));
});

test('dnat: multiple matching rules fan out without random selection', async () => {
  const { resolveRequestPath } = await framework();
  const d = domain('api.example.com');
  const e = eip('eip-1', '47.1.1.1');
  const gw = natGateway('ngw-1');
  const rule1 = dnatRule('ngw-1', 'fwd-1', { externalIp: '47.1.1.1', externalPort: '443', internalIp: '10.1.1.10', internalPort: '8443', protocol: 'tcp' });
  const rule2 = dnatRule('ngw-1', 'fwd-2', { externalIp: '47.1.1.1', externalPort: '443', internalIp: '10.1.1.11', internalPort: '8443', protocol: 'tcp' });
  const int1 = endpoint('10.1.1.10', '8443', 'tcp', 'internal');
  const int2 = endpoint('10.1.1.11', '8443', 'tcp', 'internal');
  const g = graph([d, e, gw, rule1, rule2, int1, int2], [
    edge('r1', d.id, e.id, 'RESOLVES_TO'),
    edge('h1', gw.id, rule1.id, 'HAS_DNAT_RULE'),
    edge('h2', gw.id, rule2.id, 'HAS_DNAT_RULE'),
    edge('x1', rule1.id, epId('47.1.1.1', '443', 'tcp'), 'EXPOSES'),
    edge('x2', rule2.id, epId('47.1.1.1', '443', 'tcp'), 'EXPOSES'),
    edge('f1', rule1.id, int1.id, 'FORWARDS_TO'),
    edge('f2', rule2.id, int2.id, 'FORWARDS_TO'),
  ]);
  const trace = resolveRequestPath('https://api.example.com/', g, { registry: await defaultRegistry() });
  assert.equal(trace.paths.length, 2);
  assert.deepEqual(trace.paths.map(path => path.nodes[3].id), [int1.id, int2.id]);
  for (const path of trace.paths) {
    assert.equal(path.steps[3].confidence, 'AMBIGUOUS');
  }
  assert.ok(trace.warnings.some(message => message.includes('dnat-rule:ngw-1:fwd-1') && message.includes('dnat-rule:ngw-1:fwd-2')));
});

test('dnat: no matching rule leaves the external endpoint unresolved with a warning', async () => {
  const { resolveRequestPath, STOP_REASON } = await framework();
  const g = dnatGraph(
    { externalIp: '47.1.1.1', externalPort: '8443', internalIp: '10.1.1.10', internalPort: '8443', protocol: 'tcp' },
    epId('47.1.1.1', '8443', 'tcp'),
    epId('10.1.1.10', '8443', 'tcp'),
  );
  const trace = resolveRequestPath('https://api.example.com/', g, { registry: await defaultRegistry() });
  assert.equal(trace.paths.length, 1);
  const [path] = trace.paths;
  assert.equal(path.nodes.length, 3);
  assert.equal(path.stoppedAt, epId('47.1.1.1', '443', 'tcp'));
  assert.equal(path.reason, STOP_REASON.NO_CANDIDATE);
  assert.ok(trace.warnings.some(message => message.includes('No DNAT rule matches')));
});

// ---------------------------------------------------------------------------
// Registry
// ---------------------------------------------------------------------------

test('registry: default registry registers DNS before NAT under the reserved names', async () => {
  const { createDefaultResolverRegistry, RESERVED_RESOLVER_NAMES } = await framework();
  const registry = createDefaultResolverRegistry();
  assert.deepEqual(registry.list().map(resolver => resolver.name), [
    RESERVED_RESOLVER_NAMES.DNS,
    RESERVED_RESOLVER_NAMES.NAT,
    RESERVED_RESOLVER_NAMES.CLB,
    RESERVED_RESOLVER_NAMES.NGINX,
    RESERVED_RESOLVER_NAMES.DEPLOYMENT,
    RESERVED_RESOLVER_NAMES.REPOSITORY,
  ]);
  assert.deepEqual(registry.list().map(resolver => resolver.name), [
    'DNSResolver',
    'NatResolver',
    'ClbResolver',
    'NginxResolver',
    'DeploymentResolver',
    'RepositoryResolver',
  ]);
});

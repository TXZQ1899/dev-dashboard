/* eslint-disable typescript/no-require-imports -- Node test harness uses CommonJS, matching the existing suite. */
const { test } = require('node:test');
const assert = require('node:assert/strict');

const OBSERVED_AT = '2026-09-23T00:00:00.000Z';

// Synthetic fixtures only (PROJECT_GUIDE §15); ids follow lib/topology/common.ts conventions.
const ev = (source, sourceId) => [{
  source,
  sourceId,
  reference: 'tests/request-resolver-clb.test.cjs',
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
  evidence: options.evidence ?? ev('clb', `node ${id}`),
  attributes: options.attributes ?? {},
});

const domain = (name, options = {}) => node(`domain:${name}`, 'DOMAIN', { name }, { evidence: ev('dns', `domain ${name}`), ...options });
const eip = (id, ip, options = {}) => node(`eip:${id}`, 'EIP', { eipId: id, ip }, { evidence: ev('eip', id), ...options });
const endpoint = (ip, port, protocol, options = {}) => {
  const normalizedProtocol = protocol === 'any' ? 'unknown' : protocol;
  return node(epId(ip, port, normalizedProtocol), 'ENDPOINT', { ip, port, protocol: normalizedProtocol }, { evidence: ev('clb', `ep ${ip}:${port}`), ...options });
};
const natGateway = id => node(`nat-gateway:${id}`, 'NAT_GATEWAY', { natGatewayId: id }, { evidence: ev('nat', id) });
const dnatRule = (gatewayId, entryId, identity) => node(
  `dnat-rule:${gatewayId}:${entryId}`,
  'DNAT_RULE',
  { dnatEntryId: entryId, ...identity },
  { evidence: ev('nat', entryId) },
);
const clb = (id, ip, options = {}) => node(`clb:${id}`, 'CLB', { clbId: id, ip }, { evidence: ev('clb', id), ...options });
const listener = (clbId, protocol, port, options = {}) => {
  const attrs = { ...options.attributes };
  if (options.rules) attrs.rules = options.rules;
  if (options.groupId !== undefined) attrs.groupId = options.groupId;
  return node(
    `clb-listener:${clbId}:${protocol}:${port}`,
    'CLB_LISTENER',
    { clbId, protocol, port },
    { evidence: ev('clb', `listener ${protocol}/${port}`), ...options, attributes: attrs },
  );
};
const serverGroup = (clbId, groupId, options = {}) =>
  node(`clb-server-group:${clbId}:${groupId}`, 'SERVER_GROUP', { clbId, serverGroupId: groupId, kind: options.kind ?? 'virtual' }, { evidence: ev('clb', `group ${groupId}`), ...options });

const edge = (id, from, to, type, options = {}) => ({
  id: `edge:${id}`,
  from,
  to,
  type,
  environment: options.environment ?? 'GLOBAL',
  evidence: options.evidence ?? ev('clb', `edge ${id}`),
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

// DNS + CLB (no NAT): isolates the CLB chain without NAT dead-end branches.
const dnsClbRegistry = async () => {
  const { ResolverRegistry, DnsResolver, ClbResolver } = await framework();
  return new ResolverRegistry().register(new DnsResolver()).register(new ClbResolver());
};
const defaultRegistry = async () => {
  const { createDefaultResolverRegistry } = await framework();
  return createDefaultResolverRegistry();
};

// ---------------------------------------------------------------------------
// Full CLB chain helpers
// ---------------------------------------------------------------------------

/** Build a full CLB → Listener → ServerGroup → Backend endpoint topology. */
const clbTopology = (opts = {}) => {
  const clbId = opts.clbId ?? 'lb-1';
  const clbIp = opts.clbIp ?? '10.179.6.15';
  const protocol = opts.protocol ?? 'https';
  const port = opts.port ?? 443;
  const groupId = opts.groupId ?? 'rsp-1';
  const groupKind = opts.groupKind ?? 'virtual';
  const servers = opts.servers ?? [{ id: 'i-1', ip: '10.179.6.4', port: 80, weight: 100, type: 'ecs' }];
  const rules = opts.rules ?? [];
  const listenerGroupId = opts.listenerGroupId ?? groupId;

  const clbNode = clb(clbId, clbIp);
  const listenerNode = listener(clbId, protocol, port, { rules, groupId: listenerGroupId, attributes: { groupId: listenerGroupId, rules } });
  const groupNode = serverGroup(clbId, groupId, { kind: groupKind, attributes: { servers } });

  const nodes = [clbNode, listenerNode, groupNode];
  const edges = [
    edge(`hl:${clbId}:${protocol}:${port}`, clbNode.id, listenerNode.id, 'HAS_LISTENER'),
    edge(`rt:${listenerNode.id}:${groupNode.id}`, listenerNode.id, groupNode.id, 'ROUTES_TO'),
  ];
  for (const server of servers) {
    const ip = server.ip;
    const port = String(server.port);
    const epProtocol = opts.backendProtocol ?? protocol;
    const epNode = endpoint(ip, port, epProtocol);
    nodes.push(epNode);
    edges.push(edge(`ft:${groupNode.id}:${epNode.id}`, groupNode.id, epNode.id, 'FORWARDS_TO'));
  }
  return { clbNode, listenerNode, groupNode, nodes, edges };
};

/** Wire DNS → EIP → BOUND_TO → CLB topology. */
const dnsEipClb = (topo, opts = {}) => {
  const d = domain(opts.host ?? 'api.example.com');
  const e = eip('eip-1', '47.1.1.1');
  return {
    d, e,
    nodes: [d, e, ...topo.nodes],
    edges: [
      edge('r1', d.id, e.id, 'RESOLVES_TO'),
      edge('b1', e.id, topo.clbNode.id, 'BOUND_TO'),
      ...topo.edges,
    ],
  };
};

// ---------------------------------------------------------------------------
// Step 1 — CLB identification
// ---------------------------------------------------------------------------

test('clb: EIP → CLB via BOUND_TO edge resolves the CLB instance', async () => {
  const { resolveRequestPath } = await framework();
  const d = domain('api.example.com');
  const e = eip('eip-1', '47.1.1.1');
  const lb = clb('lb-1', '10.179.6.15');
  const g = graph([d, e, lb], [
    edge('r1', d.id, e.id, 'RESOLVES_TO'),
    edge('b1', e.id, lb.id, 'BOUND_TO'),
  ]);
  const trace = resolveRequestPath('https://api.example.com/', g, { registry: await dnsClbRegistry() });
  assert.equal(trace.paths.length, 1);
  const [path] = trace.paths;
  assert.deepEqual(path.nodes.map(n => n.id), [d.id, e.id, lb.id]);
  assert.deepEqual(path.steps.map(s => s.rule), ['dns:entry', 'dns:records', 'clb:eip-bound']);
  assert.equal(path.steps[2].confidence, 'EXACT');
});

test('clb: endpoint IP matching a CLB instance resolves the CLB (via DNAT internal endpoint)', async () => {
  const { resolveRequestPath } = await framework();
  // DNS → EIP → DNAT → internal endpoint (CLB IP) → CLB (by IP match)
  const d = domain('api.example.com');
  const e = eip('eip-1', '47.1.1.1');
  const gw = natGateway('ngw-1');
  const rule = dnatRule('ngw-1', 'fwd-1', {
    externalIp: '47.1.1.1', externalPort: '443', internalIp: '10.179.6.15', internalPort: '443', protocol: 'tcp',
  });
  const extEp = endpoint('47.1.1.1', '443', 'tcp', { attributes: { natSide: 'external', discoveredBy: ['nat'] } });
  const intEp = endpoint('10.179.6.15', '443', 'tcp', { attributes: { natSide: 'internal', discoveredBy: ['nat'] } });
  const lb = clb('lb-1', '10.179.6.15');
  const lst = listener('lb-1', 'https', '443', { groupId: 'rsp-1', attributes: { groupId: 'rsp-1', rules: [] } });
  const grp = serverGroup('lb-1', 'rsp-1', { attributes: { servers: [{ id: 'i-1', ip: '10.179.6.4', port: 80, weight: 100, type: 'ecs' }] } });
  const backend = endpoint('10.179.6.4', '80', 'https');
  const g = graph([d, e, gw, rule, extEp, intEp, lb, lst, grp, backend], [
    edge('r1', d.id, e.id, 'RESOLVES_TO'),
    edge('h1', gw.id, rule.id, 'HAS_DNAT_RULE'),
    edge('x1', rule.id, extEp.id, 'EXPOSES'),
    edge('f1', rule.id, intEp.id, 'FORWARDS_TO'),
    edge('hl', lb.id, lst.id, 'HAS_LISTENER'),
    edge('rt', lst.id, grp.id, 'ROUTES_TO'),
    edge('ft', grp.id, backend.id, 'FORWARDS_TO'),
  ]);
  const trace = resolveRequestPath('https://api.example.com/', g, { registry: await defaultRegistry() });
  // The internal endpoint (10.179.6.15) matches the CLB by IP.
  const clbPath = trace.paths.find(p => p.nodes.some(n => n.type === 'CLB'));
  assert.ok(clbPath, 'Expected a path reaching the CLB');
  assert.deepEqual(clbPath.nodes.map(n => n.type), ['DOMAIN', 'EIP', 'ENDPOINT', 'ENDPOINT', 'CLB', 'CLB_LISTENER', 'SERVER_GROUP', 'ENDPOINT']);
  assert.equal(clbPath.steps[4].rule, 'clb:endpoint-ip');
});

// ---------------------------------------------------------------------------
// Step 2 — Listener matching (HTTPS / HTTP / TCP)
// ---------------------------------------------------------------------------

test('clb: full chain — DNS → EIP → CLB → HTTPS listener → group → backend', async () => {
  const { resolveRequestPath } = await framework();
  const topo = clbTopology({ protocol: 'https', port: 443 });
  const wired = dnsEipClb(topo);
  const g = graph(wired.nodes, wired.edges);
  const trace = resolveRequestPath('https://api.example.com/', g, { registry: await dnsClbRegistry() });
  assert.equal(trace.paths.length, 1);
  const [path] = trace.paths;
  assert.deepEqual(path.nodes.map(n => n.type), ['DOMAIN', 'EIP', 'CLB', 'CLB_LISTENER', 'SERVER_GROUP', 'ENDPOINT']);
  assert.deepEqual(path.steps.map(s => s.rule), [
    'dns:entry', 'dns:records', 'clb:eip-bound', 'clb:listener', 'clb:default-group', 'clb:backend',
  ]);
  assert.equal(path.confidence, 'EXACT');
  // Terminal stays open for TASK-05 APPLICATION.
  assert.equal(path.terminalNodeId, null);
  // TASK-06 semantics: real progress but no APPLICATION reached => PARTIAL.
  assert.equal(path.status, 'PARTIAL');
  const backend = path.nodes[5];
  assert.equal(backend.identity.ip, '10.179.6.4');
  assert.equal(backend.identity.port, '80');
});

test('clb: HTTPS:443 listener matches https request on port 443', async () => {
  const { resolveRequestPath } = await framework();
  const topo = clbTopology({ protocol: 'https', port: 443 });
  const wired = dnsEipClb(topo);
  const trace = resolveRequestPath('https://api.example.com/', graph(wired.nodes, wired.edges), { registry: await dnsClbRegistry() });
  assert.equal(trace.paths.length, 1);
  assert.equal(trace.paths[0].nodes[3].identity.protocol, 'https');
  assert.equal(trace.paths[0].steps[3].confidence, 'EXACT');
});

test('clb: HTTP:80 listener matches http request on port 80', async () => {
  const { resolveRequestPath } = await framework();
  const topo = clbTopology({ protocol: 'http', port: 80, backendProtocol: 'http' });
  const wired = dnsEipClb(topo);
  const trace = resolveRequestPath('http://api.example.com/', graph(wired.nodes, wired.edges), { registry: await dnsClbRegistry() });
  assert.equal(trace.paths.length, 1);
  assert.equal(trace.paths[0].nodes[3].identity.protocol, 'http');
  assert.equal(trace.paths[0].steps[3].confidence, 'EXACT');
});

test('clb: TCP listener matches https request via transport family fallback', async () => {
  const { resolveRequestPath } = await framework();
  const topo = clbTopology({ protocol: 'tcp', port: 443, backendProtocol: 'tcp' });
  const wired = dnsEipClb(topo);
  const trace = resolveRequestPath('https://api.example.com/', graph(wired.nodes, wired.edges), { registry: await dnsClbRegistry() });
  assert.equal(trace.paths.length, 1);
  const [path] = trace.paths;
  assert.equal(path.nodes[3].identity.protocol, 'tcp');
  // TCP is a transport-family fallback, not an exact L7 match.
  assert.equal(path.steps[3].confidence, 'INFERRED');
});

test('clb: HTTPS:443 preferred over TCP:443 for an https request', async () => {
  const { resolveRequestPath } = await framework();
  const d = domain('api.example.com');
  const e = eip('eip-1', '47.1.1.1');
  const lb = clb('lb-1', '10.179.6.15');
  const httpsListener = listener('lb-1', 'https', '443', { groupId: 'rsp-https', attributes: { groupId: 'rsp-https', rules: [] } });
  const tcpListener = listener('lb-1', 'tcp', '443', { groupId: 'rsp-tcp', attributes: { groupId: 'rsp-tcp', rules: [] } });
  const httpsGroup = serverGroup('lb-1', 'rsp-https', { attributes: { servers: [{ id: 'i-1', ip: '10.179.6.4', port: 80, weight: 100, type: 'ecs' }] } });
  const tcpGroup = serverGroup('lb-1', 'rsp-tcp', { attributes: { servers: [{ id: 'i-2', ip: '10.179.6.5', port: 80, weight: 100, type: 'ecs' }] } });
  const httpsEp = endpoint('10.179.6.4', '80', 'https');
  const tcpEp = endpoint('10.179.6.5', '80', 'tcp');
  const g = graph([d, e, lb, httpsListener, tcpListener, httpsGroup, tcpGroup, httpsEp, tcpEp], [
    edge('r1', d.id, e.id, 'RESOLVES_TO'),
    edge('b1', e.id, lb.id, 'BOUND_TO'),
    edge('hl1', lb.id, httpsListener.id, 'HAS_LISTENER'),
    edge('hl2', lb.id, tcpListener.id, 'HAS_LISTENER'),
    edge('rt1', httpsListener.id, httpsGroup.id, 'ROUTES_TO'),
    edge('rt2', tcpListener.id, tcpGroup.id, 'ROUTES_TO'),
    edge('ft1', httpsGroup.id, httpsEp.id, 'FORWARDS_TO'),
    edge('ft2', tcpGroup.id, tcpEp.id, 'FORWARDS_TO'),
  ]);
  const trace = resolveRequestPath('https://api.example.com/', g, { registry: await dnsClbRegistry() });
  assert.equal(trace.paths.length, 1);
  const [path] = trace.paths;
  // HTTPS listener wins (exact L7 match) over TCP.
  assert.equal(path.nodes[3].id, httpsListener.id);
  assert.equal(path.nodes[5].id, httpsEp.id);
  assert.equal(path.steps[3].confidence, 'EXACT');
});

// ---------------------------------------------------------------------------
// Step 3 — HTTP/HTTPS Rule matching
// ---------------------------------------------------------------------------

test('clb: exact domain rule matches the request host', async () => {
  const { resolveRequestPath } = await framework();
  const topo = clbTopology({
    protocol: 'https', port: 443,
    rules: [{ id: 'rule-1', domain: 'api.example.com', path: '', groupId: 'rsp-1' }],
    listenerGroupId: 'rsp-default',
  });
  // Add the default group so the listener has a ROUTES_TO edge.
  const defaultGrp = serverGroup('lb-1', 'rsp-default', { attributes: { servers: [{ id: 'i-d', ip: '10.179.6.99', port: 80, weight: 100, type: 'ecs' }] } });
  const defaultEp = endpoint('10.179.6.99', '80', 'https');
  const wired = dnsEipClb(topo);
  const g = graph([...wired.nodes, defaultGrp, defaultEp], [
    ...wired.edges,
    edge('rt-d', topo.listenerNode.id, defaultGrp.id, 'ROUTES_TO'),
    edge('ft-d', defaultGrp.id, defaultEp.id, 'FORWARDS_TO'),
  ]);
  const trace = resolveRequestPath('https://api.example.com/order/123', g, { registry: await dnsClbRegistry() });
  assert.equal(trace.paths.length, 1);
  const [path] = trace.paths;
  // The rule's group (rsp-1) should be selected, not the default group.
  assert.equal(path.nodes[4].identity.serverGroupId, 'rsp-1');
  assert.equal(path.steps[4].rule, 'clb:rule:exact:default-path');
  assert.equal(path.steps[4].confidence, 'EXACT');
});

test('clb: wildcard domain rule (*.example.com) matches a subdomain', async () => {
  const { resolveRequestPath } = await framework();
  const topo = clbTopology({
    protocol: 'https', port: 443,
    rules: [{ id: 'rule-wild', domain: '*.example.com', path: '', groupId: 'rsp-1' }],
    listenerGroupId: 'rsp-default',
  });
  const wired = dnsEipClb(topo, { host: 'www.example.com' });
  const g = graph(wired.nodes, wired.edges);
  const trace = resolveRequestPath('https://www.example.com/', g, { registry: await dnsClbRegistry() });
  assert.equal(trace.paths.length, 1);
  assert.equal(trace.paths[0].nodes[4].identity.serverGroupId, 'rsp-1');
  assert.equal(trace.paths[0].steps[4].rule, 'clb:rule:wildcard:default-path');
});

test('clb: narrower wildcard domain outranks broader wildcard (CLB auto-specificity)', async () => {
  const { resolveRequestPath } = await framework();
  const d = domain('info.market.aliyun.com');
  const e = eip('eip-1', '47.1.1.1');
  const lb = clb('lb-1', '10.179.6.15');
  const lst = listener('lb-1', 'https', '443', {
    groupId: 'rsp-default',
    attributes: {
      groupId: 'rsp-default',
      rules: [
        { id: 'rule-broad', domain: '*.aliyun.com', path: '', groupId: 'rsp-broad' },
        { id: 'rule-narrow', domain: '*.market.aliyun.com', path: '', groupId: 'rsp-narrow' },
      ],
    },
  });
  const grpBroad = serverGroup('lb-1', 'rsp-broad', { attributes: { servers: [{ id: 'i-0', ip: '10.179.6.10', port: 8080, weight: 100, type: 'ecs' }] } });
  const grpNarrow = serverGroup('lb-1', 'rsp-narrow', { attributes: { servers: [{ id: 'i-1', ip: '10.179.6.4', port: 80, weight: 100, type: 'ecs' }] } });
  const epBroad = endpoint('10.179.6.10', '8080', 'https');
  const epNarrow = endpoint('10.179.6.4', '80', 'https');
  const g = graph([d, e, lb, lst, grpBroad, grpNarrow, epBroad, epNarrow], [
    edge('r1', d.id, e.id, 'RESOLVES_TO'),
    edge('b1', e.id, lb.id, 'BOUND_TO'),
    edge('hl', lb.id, lst.id, 'HAS_LISTENER'),
    edge('ft-b', grpBroad.id, epBroad.id, 'FORWARDS_TO'),
    edge('ft-n', grpNarrow.id, epNarrow.id, 'FORWARDS_TO'),
  ]);
  const trace = resolveRequestPath('https://info.market.aliyun.com/order/123', g, { registry: await dnsClbRegistry() });
  assert.equal(trace.paths.length, 1);
  const [path] = trace.paths;
  // *.market.aliyun.com must win over *.aliyun.com — no AMBIGUOUS fan-out.
  assert.equal(path.nodes[4].identity.serverGroupId, 'rsp-narrow');
  assert.equal(path.steps[4].rule, 'clb:rule:wildcard:default-path');
  assert.equal(path.steps[4].confidence, 'EXACT');
});

test('clb: exact domain rule outranks wildcard rules for the same host', async () => {
  const { resolveRequestPath } = await framework();
  const d = domain('api.aliyun.com');
  const e = eip('eip-1', '47.1.1.1');
  const lb = clb('lb-1', '10.179.6.15');
  const lst = listener('lb-1', 'https', '443', {
    groupId: 'rsp-default',
    attributes: {
      groupId: 'rsp-default',
      rules: [
        { id: 'rule-wild', domain: '*.aliyun.com', path: '', groupId: 'rsp-wild' },
        { id: 'rule-exact', domain: 'api.aliyun.com', path: '', groupId: 'rsp-exact' },
      ],
    },
  });
  const grpWild = serverGroup('lb-1', 'rsp-wild', { attributes: { servers: [{ id: 'i-0', ip: '10.179.6.10', port: 8080, weight: 100, type: 'ecs' }] } });
  const grpExact = serverGroup('lb-1', 'rsp-exact', { attributes: { servers: [{ id: 'i-1', ip: '10.179.6.4', port: 80, weight: 100, type: 'ecs' }] } });
  const epWild = endpoint('10.179.6.10', '8080', 'https');
  const epExact = endpoint('10.179.6.4', '80', 'https');
  const g = graph([d, e, lb, lst, grpWild, grpExact, epWild, epExact], [
    edge('r1', d.id, e.id, 'RESOLVES_TO'),
    edge('b1', e.id, lb.id, 'BOUND_TO'),
    edge('hl', lb.id, lst.id, 'HAS_LISTENER'),
    edge('ft-w', grpWild.id, epWild.id, 'FORWARDS_TO'),
    edge('ft-e', grpExact.id, epExact.id, 'FORWARDS_TO'),
  ]);
  const trace = resolveRequestPath('https://api.aliyun.com/order', g, { registry: await dnsClbRegistry() });
  assert.equal(trace.paths.length, 1);
  assert.equal(trace.paths[0].nodes[4].identity.serverGroupId, 'rsp-exact');
  assert.equal(trace.paths[0].steps[4].rule, 'clb:rule:exact:default-path');
});

test('clb: path prefix rule matches and outranks empty-path rule on same domain', async () => {
  const { resolveRequestPath } = await framework();
  const d = domain('api.example.com');
  const e = eip('eip-1', '47.1.1.1');
  const lb = clb('lb-1', '10.179.6.15');
  const lst = listener('lb-1', 'https', '443', {
    groupId: 'rsp-default',
    attributes: {
      groupId: 'rsp-default',
      rules: [
        { id: 'rule-empty', domain: 'api.example.com', path: '', groupId: 'rsp-empty' },
        { id: 'rule-path', domain: 'api.example.com', path: '/order', groupId: 'rsp-path' },
      ],
    },
  });
  const grpEmpty = serverGroup('lb-1', 'rsp-empty', { attributes: { servers: [{ id: 'i-0', ip: '10.179.6.10', port: 8080, weight: 100, type: 'ecs' }] } });
  const grpPath = serverGroup('lb-1', 'rsp-path', { attributes: { servers: [{ id: 'i-1', ip: '10.179.6.4', port: 80, weight: 100, type: 'ecs' }] } });
  const epEmpty = endpoint('10.179.6.10', '8080', 'https');
  const epPath = endpoint('10.179.6.4', '80', 'https');
  const g = graph([d, e, lb, lst, grpEmpty, grpPath, epEmpty, epPath], [
    edge('r1', d.id, e.id, 'RESOLVES_TO'),
    edge('b1', e.id, lb.id, 'BOUND_TO'),
    edge('hl', lb.id, lst.id, 'HAS_LISTENER'),
    edge('ft-e', grpEmpty.id, epEmpty.id, 'FORWARDS_TO'),
    edge('ft-p', grpPath.id, epPath.id, 'FORWARDS_TO'),
  ]);
  const trace = resolveRequestPath('https://api.example.com/order/123', g, { registry: await dnsClbRegistry() });
  assert.equal(trace.paths.length, 1);
  const [path] = trace.paths;
  // The /order path rule should win over the empty-path rule.
  assert.equal(path.nodes[4].identity.serverGroupId, 'rsp-path');
  assert.equal(path.steps[4].rule, 'clb:rule:exact:path-prefix');
});

// ---------------------------------------------------------------------------
// Step 4 — Server Group / Backend resolution
// ---------------------------------------------------------------------------

test('clb: default server group is used when no L7 rule matches', async () => {
  const { resolveRequestPath } = await framework();
  const topo = clbTopology({
    protocol: 'https', port: 443,
    rules: [{ id: 'rule-1', domain: 'api.example.com', path: '', groupId: 'rsp-1' }],
    listenerGroupId: 'rsp-default',
  });
  const defaultGrp = serverGroup('lb-1', 'rsp-default', { attributes: { servers: [{ id: 'i-d', ip: '10.179.6.99', port: 80, weight: 100, type: 'ecs' }] } });
  const defaultEp = endpoint('10.179.6.99', '80', 'https');
  const wired = dnsEipClb(topo, { host: 'other.example.com' });
  const g = graph([...wired.nodes, defaultGrp, defaultEp], [
    ...wired.edges,
    edge('rt-d', topo.listenerNode.id, defaultGrp.id, 'ROUTES_TO'),
    edge('ft-d', defaultGrp.id, defaultEp.id, 'FORWARDS_TO'),
  ]);
  const trace = resolveRequestPath('https://other.example.com/', g, { registry: await dnsClbRegistry() });
  assert.equal(trace.paths.length, 1);
  const [path] = trace.paths;
  assert.equal(path.nodes[4].identity.serverGroupId, 'rsp-default');
  assert.equal(path.steps[4].rule, 'clb:default-group');
  assert.ok(trace.warnings.some(w => w.includes('falling back to the default server group')));
});

test('clb: port-less default-group backend is re-anchored on the listener BackendServerPort', async () => {
  const { resolveRequestPath } = await framework();
  // Mirrors the real Alibaba CLB topology: the default group's BackendServer
  // entry omits Port (FORWARDS_TO endpoint ...:unknown:unknown), while both
  // listeners configure BackendServerPort=80 and a concrete ip:80 endpoint
  // exists in the graph (shared nginx host).
  const d = domain('sit01pay.example.com');
  const e = eip('eip-1', '47.1.1.1');
  const lb = clb('lb-1', '10.179.6.15');
  const lst = listener('lb-1', 'https', '443', {
    groupId: 'default',
    attributes: { groupId: 'default', backendPort: 80, rules: [{ id: 'rule-1', domain: '*.elsewhere.com', path: '', groupId: 'rsp-1' }] },
  });
  const defaultGrp = serverGroup('lb-1', 'default', {
    kind: 'default',
    attributes: { servers: [{ id: 'i-d', ip: '10.179.6.99', port: null, weight: 100, type: 'ecs' }] },
  });
  const portlessEp = endpoint('10.179.6.99', 'unknown', 'unknown');
  const realEp = endpoint('10.179.6.99', '80', 'unknown');
  const g = graph([d, e, lb, lst, defaultGrp, portlessEp, realEp], [
    edge('r1', d.id, e.id, 'RESOLVES_TO'),
    edge('b1', e.id, lb.id, 'BOUND_TO'),
    edge('hl', lb.id, lst.id, 'HAS_LISTENER'),
    edge('rt', lst.id, defaultGrp.id, 'ROUTES_TO'),
    edge('ft', defaultGrp.id, portlessEp.id, 'FORWARDS_TO'),
  ]);
  const trace = resolveRequestPath('https://sit01pay.example.com/api/cashier/query', g, { registry: await dnsClbRegistry() });
  assert.equal(trace.paths.length, 1);
  const [path] = trace.paths;
  // The group step resolves to the concrete :80 endpoint, not the port-less one.
  assert.equal(path.nodes[5].id, epId('10.179.6.99', '80', 'unknown'));
  assert.equal(path.steps[5].rule, 'clb:backend');
  assert.equal(path.steps[5].confidence, 'EXACT');
  assert.ok(trace.warnings.some(w => w.includes('BackendServerPort') && w.includes('80')));
  // No forwarding rule matched this host: default group is used explicitly.
  assert.equal(path.steps[4].rule, 'clb:default-group');
});

test('clb: port-less backend without any graph endpoint synthesizes an INFERRED :80 endpoint', async () => {
  const { resolveRequestPath } = await framework();
  const d = domain('sit01pay.example.com');
  const e = eip('eip-1', '47.1.1.1');
  const lb = clb('lb-1', '10.179.6.15');
  const lst = listener('lb-1', 'https', '443', {
    groupId: 'default',
    attributes: { groupId: 'default', backendPort: 80, rules: [] },
  });
  const defaultGrp = serverGroup('lb-1', 'default', {
    kind: 'default',
    attributes: { servers: [{ id: 'i-d', ip: '10.179.6.99', port: null, weight: 100, type: 'ecs' }] },
  });
  const portlessEp = endpoint('10.179.6.99', 'unknown', 'unknown');
  const g = graph([d, e, lb, lst, defaultGrp, portlessEp], [
    edge('r1', d.id, e.id, 'RESOLVES_TO'),
    edge('b1', e.id, lb.id, 'BOUND_TO'),
    edge('hl', lb.id, lst.id, 'HAS_LISTENER'),
    edge('rt', lst.id, defaultGrp.id, 'ROUTES_TO'),
    edge('ft', defaultGrp.id, portlessEp.id, 'FORWARDS_TO'),
  ]);
  const trace = resolveRequestPath('https://sit01pay.example.com/', g, { registry: await dnsClbRegistry() });
  assert.equal(trace.paths.length, 1);
  const backend = trace.paths[0].nodes[5];
  assert.equal(backend.identity.ip, '10.179.6.99');
  assert.equal(backend.identity.port, '80');
  assert.equal(trace.paths[0].steps[5].confidence, 'INFERRED');
});

test('clb: virtual server group with multiple backends fans out as load-balanced candidates', async () => {
  const { resolveRequestPath } = await framework();
  const topo = clbTopology({
    protocol: 'https', port: 443, groupKind: 'virtual',
    servers: [
      { id: 'i-1', ip: '10.179.6.4', port: 80, weight: 50, type: 'ecs' },
      { id: 'i-2', ip: '10.179.6.5', port: 80, weight: 50, type: 'ecs' },
      { id: 'i-3', ip: '10.179.6.6', port: 80, weight: 50, type: 'ecs' },
    ],
  });
  const wired = dnsEipClb(topo);
  const trace = resolveRequestPath('https://api.example.com/', graph(wired.nodes, wired.edges), { registry: await dnsClbRegistry() });
  // 3 backends → 3 paths (all non-terminal, all EXACT confidence).
  assert.equal(trace.paths.length, 3);
  const backendIps = trace.paths.map(p => p.nodes[5].identity.ip).sort((a, b) => a.localeCompare(b));
  assert.deepEqual(backendIps, ['10.179.6.4', '10.179.6.5', '10.179.6.6']);
  // Multiple backends are legitimate load-balanced candidates, not ambiguous.
  for (const path of trace.paths) {
    assert.equal(path.steps[5].confidence, 'EXACT');
    // TASK-06 semantics: progress without APPLICATION => PARTIAL (not UNRESOLVED).
    assert.equal(path.status, 'PARTIAL');
  }
});

// ---------------------------------------------------------------------------
// Step 5 — Unsupported / not-found cases
// ---------------------------------------------------------------------------

test('clb: listener not found leaves the CLB unresolved with a warning', async () => {
  const { resolveRequestPath, STOP_REASON } = await framework();
  const d = domain('api.example.com');
  const e = eip('eip-1', '47.1.1.1');
  const lb = clb('lb-1', '10.179.6.15');
  const g = graph([d, e, lb], [
    edge('r1', d.id, e.id, 'RESOLVES_TO'),
    edge('b1', e.id, lb.id, 'BOUND_TO'),
  ]);
  const trace = resolveRequestPath('https://api.example.com/', g, { registry: await dnsClbRegistry() });
  assert.equal(trace.paths.length, 1);
  const [path] = trace.paths;
  assert.equal(path.nodes.length, 3); // DOMAIN, EIP, CLB
  assert.equal(path.stoppedAt, lb.id);
  assert.equal(path.reason, STOP_REASON.NO_CANDIDATE);
  assert.ok(trace.warnings.some(w => w.includes('No CLB listener')));
});

test('clb: rule not found on L7 listener falls back to default group', async () => {
  const { resolveRequestPath } = await framework();
  const d = domain('nomatch.example.com');
  const e = eip('eip-1', '47.1.1.1');
  const lb = clb('lb-1', '10.179.6.15');
  const lst = listener('lb-1', 'https', '443', {
    groupId: 'rsp-default',
    attributes: {
      groupId: 'rsp-default',
      rules: [{ id: 'rule-1', domain: 'api.example.com', path: '/order', groupId: 'rsp-1' }],
    },
  });
  const defaultGrp = serverGroup('lb-1', 'rsp-default', { attributes: { servers: [{ id: 'i-d', ip: '10.179.6.99', port: 80, weight: 100, type: 'ecs' }] } });
  const defaultEp = endpoint('10.179.6.99', '80', 'https');
  const g = graph([d, e, lb, lst, defaultGrp, defaultEp], [
    edge('r1', d.id, e.id, 'RESOLVES_TO'),
    edge('b1', e.id, lb.id, 'BOUND_TO'),
    edge('hl', lb.id, lst.id, 'HAS_LISTENER'),
    edge('rt', lst.id, defaultGrp.id, 'ROUTES_TO'),
    edge('ft', defaultGrp.id, defaultEp.id, 'FORWARDS_TO'),
  ]);
  const trace = resolveRequestPath('https://nomatch.example.com/', g, { registry: await dnsClbRegistry() });
  assert.equal(trace.paths.length, 1);
  const [path] = trace.paths;
  assert.equal(path.nodes[4].identity.serverGroupId, 'rsp-default');
  assert.ok(trace.warnings.some(w => w.includes('falling back')));
});

test('clb: competing rules with equal specificity produce AMBIGUOUS paths', async () => {
  const { resolveRequestPath } = await framework();
  const d = domain('api.example.com');
  const e = eip('eip-1', '47.1.1.1');
  const lb = clb('lb-1', '10.179.6.15');
  const lst = listener('lb-1', 'https', '443', {
    groupId: 'rsp-default',
    attributes: {
      groupId: 'rsp-default',
      rules: [
        { id: 'rule-a', domain: 'api.example.com', path: '/order', groupId: 'rsp-a' },
        { id: 'rule-b', domain: 'api.example.com', path: '/order', groupId: 'rsp-b' },
      ],
    },
  });
  const grpA = serverGroup('lb-1', 'rsp-a', { attributes: { servers: [{ id: 'i-a', ip: '10.179.6.10', port: 80, weight: 100, type: 'ecs' }] } });
  const grpB = serverGroup('lb-1', 'rsp-b', { attributes: { servers: [{ id: 'i-b', ip: '10.179.6.11', port: 80, weight: 100, type: 'ecs' }] } });
  const epA = endpoint('10.179.6.10', '80', 'https');
  const epB = endpoint('10.179.6.11', '80', 'https');
  const g = graph([d, e, lb, lst, grpA, grpB, epA, epB], [
    edge('r1', d.id, e.id, 'RESOLVES_TO'),
    edge('b1', e.id, lb.id, 'BOUND_TO'),
    edge('hl', lb.id, lst.id, 'HAS_LISTENER'),
    edge('ft-a', grpA.id, epA.id, 'FORWARDS_TO'),
    edge('ft-b', grpB.id, epB.id, 'FORWARDS_TO'),
  ]);
  const trace = resolveRequestPath('https://api.example.com/order/123', g, { registry: await dnsClbRegistry() });
  // Two equally-specific rules → 2 paths, AMBIGUOUS confidence on the rule step.
  assert.equal(trace.paths.length, 2);
  for (const path of trace.paths) {
    assert.equal(path.steps[4].confidence, 'AMBIGUOUS');
  }
  assert.ok(trace.warnings.some(w => w.includes('equally match')));
});

test('clb: deterministic output — same input always produces the same trace', async () => {
  const { resolveRequestPath } = await framework();
  const topo = clbTopology({ protocol: 'https', port: 443 });
  const wired = dnsEipClb(topo);
  const g = graph(wired.nodes, wired.edges);
  const registry = await dnsClbRegistry();
  const trace1 = resolveRequestPath('https://api.example.com/order/123', g, { registry });
  const trace2 = resolveRequestPath('https://api.example.com/order/123', g, { registry });
  assert.deepEqual(JSON.parse(JSON.stringify(trace1)), JSON.parse(JSON.stringify(trace2)));
});

// ---------------------------------------------------------------------------
// Registry
// ---------------------------------------------------------------------------

test('registry: default registry registers DNS → NAT → CLB → NGINX → DEPLOYMENT → REPOSITORY in order', async () => {
  const { createDefaultResolverRegistry, RESERVED_RESOLVER_NAMES } = await framework();
  const registry = createDefaultResolverRegistry();
  assert.deepEqual(registry.list().map(r => r.name), [
    RESERVED_RESOLVER_NAMES.DNS,
    RESERVED_RESOLVER_NAMES.NAT,
    RESERVED_RESOLVER_NAMES.CLB,
    RESERVED_RESOLVER_NAMES.NGINX,
    RESERVED_RESOLVER_NAMES.DEPLOYMENT,
    RESERVED_RESOLVER_NAMES.REPOSITORY,
  ]);
});

/* eslint-disable typescript/no-require-imports -- Node test harness uses CommonJS, matching the existing suite. */
const { test } = require('node:test');
const assert = require('node:assert/strict');

// TASK-06 Step 6 — Synthetic end-to-end fixtures (Case A–G).
// Each case runs the full default resolver registry (DNS → NAT → CLB → NGINX →
// DEPLOYMENT → REPOSITORY) over a factual synthetic graph; no fixture may
// fabricate edges: unresolved chains must stop and say why.

const OBSERVED_AT = '2026-09-23T00:00:00.000Z';

const ev = (source, sourceId) => [{
  source,
  sourceId,
  reference: 'tests/request-resolver-e2e.test.cjs',
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
  environment: options.environment ?? 'GLOBAL',
  evidence: options.evidence ?? ev('jumpserver', `node ${id}`),
  attributes: options.attributes ?? {},
});

const domain = (name, options = {}) => node(`domain:${name}`, 'DOMAIN', { name }, { evidence: ev('dns', `domain ${name}`), ...options });
const eip = (id, ip, options = {}) => node(`eip:${id}`, 'EIP', { eipId: id, ip }, { evidence: ev('eip', id), ...options });
const endpoint = (ip, port, protocol, options = {}) =>
  node(epId(ip, port, protocol), 'ENDPOINT', { ip, port, protocol }, { evidence: ev('jumpserver', `ep ${ip}:${port}`), ...options });
const natEndpoint = (ip, port, side) => endpoint(ip, port, 'tcp', { attributes: { discoveredBy: ['nat'], natSide: side } });
const natGateway = id => node(`nat-gateway:${id}`, 'NAT_GATEWAY', { natGatewayId: id }, { evidence: ev('nat', id) });
const dnatRule = (gatewayId, entryId, identity) => node(
  `dnat-rule:${gatewayId}:${entryId}`,
  'DNAT_RULE',
  { dnatEntryId: entryId, ...identity },
  { evidence: ev('nat', entryId) },
);
const clb = (id, ip, options = {}) => node(`clb:${id}`, 'CLB', { clbId: id, ip }, { evidence: ev('clb', id), ...options });
const listener = (clbId, protocol, port, options = {}) => node(
  `clb-listener:${clbId}:${protocol}:${port}`,
  'CLB_LISTENER',
  { clbId, protocol, port },
  { evidence: ev('clb', `listener ${protocol}/${port}`), ...options },
);
const serverGroup = (clbId, groupId, servers, options = {}) =>
  node(`clb-server-group:${clbId}:${groupId}`, 'SERVER_GROUP', { clbId, serverGroupId: groupId, kind: options.kind ?? 'virtual' }, {
    evidence: ev('clb', `group ${groupId}`),
    ...options,
    attributes: { servers, ...options.attributes },
  });
const host = (ip, options = {}) => {
  const assetIds = options.assetIds ?? [`asset-${ip}`];
  return node(`host:${ip}`, 'HOST', { ip, jumpserverAssetIds: assetIds }, {
    evidence: ev('jumpserver', `host ${ip}`),
    ...options,
    attributes: { jumpserver: { id: assetIds[0], hostname: `srv-${ip}` }, ...options.attributes },
  });
};
const nginxRoute = (assetId, suffix, opts = {}) => node(
  `nginx-route:${assetId}:${suffix}`,
  'NGINX_ROUTE',
  {
    assetId,
    domains: opts.domains ?? [],
    uri: opts.uri ?? '/',
    directive: 'proxy_pass',
    target: opts.target ?? '',
    upstream: opts.upstreamName ?? '',
  },
  {
    evidence: ev('jumpserver', `route ${assetId}/${suffix}`),
    attributes: { listen: opts.listen ?? ['80'], context: 'http > server', nginxStatus: 'complete' },
  },
);
const upstream = (assetId, name, opts = {}) => node(
  `nginx-upstream:${assetId}:${name}`,
  'UPSTREAM',
  { assetId, name },
  { evidence: ev('jumpserver', `upstream ${name}`), attributes: { target: opts.target ?? `http://${name}/`, directive: 'proxy_pass' } },
);
const application = (appId, name) =>
  node(`application:${appId}`, 'APPLICATION', { devopsAppId: appId }, { label: name, evidence: ev('devops', `app ${appId}`) });
const deployment = (appId, env, deployId, ip, port) =>
  node(`deployment:${appId}:${env}:${deployId}`, 'DEPLOYMENT', { appId, env, deployId, ip, port }, {
    label: `${appId} ${env} ${deployId}`,
    environment: env,
    evidence: ev('devops', `${appId}/${env}/${deployId}`),
  });
const repository = appId => node(`repository:${appId}`, 'REPOSITORY', { url: `https://codeup.example.com/group/${appId}.git` }, {
  label: appId,
  evidence: ev('repository', `repo ${appId}`),
  attributes: { url: `https://codeup.example.com/group/${appId}.git`, match: 'exact' },
});

const edge = (id, from, to, type, options = {}) => ({
  id: `edge:${id}`,
  from,
  to,
  type,
  environment: options.environment ?? 'GLOBAL',
  evidence: options.evidence ?? ev('jumpserver', `edge ${id}`),
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
const defaultRegistry = async () => {
  const { createDefaultResolverRegistry } = await framework();
  return createDefaultResolverRegistry();
};

/** Wires an application (deployment + repository) listening on an endpoint. */
const wireApp = (appId, appName, env, ep, deployId = 'd1') => {
  const app = application(appId, appName);
  const dep = deployment(appId, env, deployId, ep.identity.ip, ep.identity.port);
  const repo = repository(appId);
  return {
    app, dep, repo,
    nodes: [app, dep, repo],
    edges: [
      edge(`hd-${appId}-${deployId}`, app.id, dep.id, 'HAS_DEPLOYMENT', { environment: env }),
      edge(`lo-${appId}-${deployId}`, dep.id, ep.id, 'LISTENS_ON', { environment: env }),
      edge(`bf-${appId}-${deployId}`, app.id, repo.id, 'BUILT_FROM'),
    ],
  };
};

// ---------------------------------------------------------------------------
// Case A — DNS → ECS(Nginx) → Application
// ---------------------------------------------------------------------------

const caseA = () => {
  const d = domain('api.example.com');
  const entry = endpoint('10.1.1.5', '443', 'https');
  const hostNode = host('10.1.1.5', { assetIds: ['asset-a'] });
  const route = nginxRoute('asset-a', 'main', {
    domains: ['api.example.com'], uri: '/order', listen: ['443 ssl'],
    target: 'http://order_backend/', upstreamName: 'order_backend',
  });
  const up = upstream('asset-a', 'order_backend');
  const backend = endpoint('10.1.1.9', '8080', 'http');
  const appParts = wireApp('app-a', 'Order Service', 'PRODUCT', backend);
  return {
    d, entry, hostNode, route, up, backend, ...appParts,
    nodes: [d, entry, hostNode, route, up, backend, ...appParts.nodes],
    edges: [
      edge('r1', d.id, entry.id, 'RESOLVES_TO'),
      edge('oh1', entry.id, hostNode.id, 'ON_HOST'),
      edge('uu1', route.id, up.id, 'USES_UPSTREAM'),
      edge('ft1', up.id, backend.id, 'FORWARDS_TO'),
      ...appParts.edges,
    ],
  };
};

test('Case A: DNS → ECS → Nginx → Application → Repository resolves end to end', async () => {
  const { resolveRequestPath } = await framework();
  const topo = caseA();
  const trace = resolveRequestPath('https://api.example.com/order/123', graph(topo.nodes, topo.edges), {
    registry: await defaultRegistry(),
    environment: 'PRODUCT',
  });
  assert.equal(trace.status, 'RESOLVED');
  assert.equal(trace.paths.length, 1);
  const [path] = trace.paths;
  assert.equal(path.terminalNodeId, topo.repo.id);
  assert.equal(path.confidence, 'EXACT');
  assert.deepEqual(path.nodes.map(n => n.type), [
    'DOMAIN', 'ENDPOINT', 'HOST', 'NGINX_ROUTE', 'UPSTREAM', 'ENDPOINT', 'DEPLOYMENT', 'APPLICATION', 'REPOSITORY',
  ]);
  assert.deepEqual(path.steps.map(s => s.rule), [
    'dns:entry', 'dns:records', 'nginx:host', 'nginx:route:exact:prefix', 'nginx:upstream', 'nginx:backend',
    'deployment:endpoint:ip-port', 'deployment:application', 'repository:built-from',
  ]);
  // Explainability (TASK-06 Step 3): every hop carries resolver/rule/confidence/evidence.
  for (const step of path.steps) {
    assert.ok(step.resolver.length > 0);
    assert.ok(step.rule.length > 0);
    assert.ok(['EXACT', 'INFERRED', 'AMBIGUOUS', 'UNKNOWN'].includes(step.confidence));
    assert.ok(step.evidence.length > 0);
    assert.ok(Array.isArray(step.warnings));
  }
});

test('Case A variant: application reached without repository stays RESOLVED with a stoppedAt', async () => {
  const { resolveRequestPath, STOP_REASON } = await framework();
  const topo = caseA();
  const nodes = topo.nodes.filter(n => n.id !== topo.repo.id);
  const edges = topo.edges.filter(e => e.type !== 'BUILT_FROM');
  const trace = resolveRequestPath('https://api.example.com/order/123', graph(nodes, edges), {
    registry: await defaultRegistry(),
    environment: 'PRODUCT',
  });
  assert.equal(trace.status, 'RESOLVED');
  assert.equal(trace.paths.length, 1);
  const [path] = trace.paths;
  // Missing repository is a warning, not a demotion.
  assert.equal(path.terminalNodeId, null);
  assert.equal(path.stoppedAt, topo.app.id);
  assert.equal(path.reason, STOP_REASON.NO_CANDIDATE);
  assert.ok(trace.warnings.some(w => w.includes('BUILT_FROM')));
  assert.ok(path.nodes.some(n => n.type === 'APPLICATION'));
});

// ---------------------------------------------------------------------------
// Case B — DNS → NAT(DNAT 443→8443) → ECS(Nginx) → Application
// ---------------------------------------------------------------------------

const caseB = () => {
  const d = domain('api.example.com');
  const e = eip('eip-1', '47.1.1.1');
  const gw = natGateway('ngw-1');
  const rule = dnatRule('ngw-1', 'fwd-1', {
    externalIp: '47.1.1.1', externalPort: '443', internalIp: '10.1.1.10', internalPort: '8443', protocol: 'tcp',
  });
  const extEp = natEndpoint('47.1.1.1', '443', 'external');
  const intEp = natEndpoint('10.1.1.10', '8443', 'internal');
  const hostNode = host('10.1.1.10', { assetIds: ['asset-b'] });
  const route = nginxRoute('asset-b', 'main', {
    domains: ['api.example.com'], uri: '/', listen: ['8443 ssl'],
    target: 'http://order_backend/', upstreamName: 'order_backend',
  });
  const up = upstream('asset-b', 'order_backend');
  const backend = endpoint('10.179.1.10', '8080', 'http');
  const appParts = wireApp('app-b', 'Order Service B', 'PRODUCT', backend);
  return {
    d, e, gw, rule, extEp, intEp, hostNode, route, up, backend, ...appParts,
    nodes: [d, e, gw, rule, extEp, intEp, hostNode, route, up, backend, ...appParts.nodes],
    edges: [
      edge('r1', d.id, e.id, 'RESOLVES_TO'),
      edge('h1', gw.id, rule.id, 'HAS_DNAT_RULE'),
      edge('x1', rule.id, extEp.id, 'EXPOSES'),
      edge('f1', rule.id, intEp.id, 'FORWARDS_TO'),
      edge('oh1', intEp.id, hostNode.id, 'ON_HOST'),
      edge('uu1', route.id, up.id, 'USES_UPSTREAM'),
      edge('ft1', up.id, backend.id, 'FORWARDS_TO'),
      ...appParts.edges,
    ],
  };
};

test('Case B: DNS → NAT → ECS → Nginx → Application resolves with the DNAT port rewrite', async () => {
  const { resolveRequestPath } = await framework();
  const topo = caseB();
  const trace = resolveRequestPath('https://api.example.com/order/123', graph(topo.nodes, topo.edges), {
    registry: await defaultRegistry(),
    environment: 'PRODUCT',
  });
  assert.equal(trace.status, 'RESOLVED');
  assert.equal(trace.paths.length, 1);
  const [path] = trace.paths;
  assert.equal(path.terminalNodeId, topo.repo.id);
  // The nginx listen (8443) must be matched via the arrival port after DNAT,
  // not the requested port (443).
  assert.deepEqual(path.steps.map(s => s.rule), [
    'dns:entry', 'dns:records', 'nat:external-endpoint', 'nat:dnat',
    'nginx:host', 'nginx:route:exact:prefix', 'nginx:upstream', 'nginx:backend',
    'deployment:endpoint:ip-port', 'deployment:application', 'repository:built-from',
  ]);
  assert.deepEqual(path.nodes.map(n => n.type), [
    'DOMAIN', 'EIP', 'ENDPOINT', 'ENDPOINT', 'HOST', 'NGINX_ROUTE', 'UPSTREAM', 'ENDPOINT', 'DEPLOYMENT', 'APPLICATION', 'REPOSITORY',
  ]);
  assert.equal(path.confidence, 'EXACT');
});

// ---------------------------------------------------------------------------
// Case C — DNS → NAT → CLB → Application
// ---------------------------------------------------------------------------

const caseC = () => {
  const d = domain('api.example.com');
  const e = eip('eip-1', '47.1.1.1');
  const gw = natGateway('ngw-1');
  // DNAT forwards to the CLB VIP.
  const rule = dnatRule('ngw-1', 'fwd-1', {
    externalIp: '47.1.1.1', externalPort: '443', internalIp: '10.179.6.15', internalPort: '443', protocol: 'tcp',
  });
  const extEp = natEndpoint('47.1.1.1', '443', 'external');
  const vipEp = natEndpoint('10.179.6.15', '443', 'internal');
  const lb = clb('lb-1', '10.179.6.15');
  const lis = listener('lb-1', 'https', '443');
  const grp = serverGroup('lb-1', 'rsp-1', [{ id: 'i-1', ip: '10.179.1.20', port: 8080, weight: 100, type: 'ecs' }]);
  const backend = endpoint('10.179.1.20', '8080', 'http');
  const appParts = wireApp('app-c', 'Order Service C', 'PRODUCT', backend);
  return {
    d, e, gw, rule, extEp, vipEp, lb, lis, grp, backend, ...appParts,
    nodes: [d, e, gw, rule, extEp, vipEp, lb, lis, grp, backend, ...appParts.nodes],
    edges: [
      edge('r1', d.id, e.id, 'RESOLVES_TO'),
      edge('h1', gw.id, rule.id, 'HAS_DNAT_RULE'),
      edge('x1', rule.id, extEp.id, 'EXPOSES'),
      edge('f1', rule.id, vipEp.id, 'FORWARDS_TO'),
      edge('hl1', lb.id, lis.id, 'HAS_LISTENER'),
      edge('rt1', lis.id, grp.id, 'ROUTES_TO'),
      edge('ft1', grp.id, backend.id, 'FORWARDS_TO'),
      ...appParts.edges,
    ],
  };
};

test('Case C: DNS → NAT → CLB → Application resolves through the VIP endpoint', async () => {
  const { resolveRequestPath } = await framework();
  const topo = caseC();
  const trace = resolveRequestPath('https://api.example.com/order/123', graph(topo.nodes, topo.edges), {
    registry: await defaultRegistry(),
    environment: 'PRODUCT',
  });
  assert.equal(trace.status, 'RESOLVED');
  assert.equal(trace.paths.length, 1);
  const [path] = trace.paths;
  assert.equal(path.terminalNodeId, topo.repo.id);
  assert.deepEqual(path.steps.map(s => s.rule), [
    'dns:entry', 'dns:records', 'nat:external-endpoint', 'nat:dnat',
    'clb:endpoint-ip', 'clb:listener', 'clb:default-group', 'clb:backend',
    'deployment:endpoint:ip-port', 'deployment:application', 'repository:built-from',
  ]);
  assert.deepEqual(path.nodes.map(n => n.type), [
    'DOMAIN', 'EIP', 'ENDPOINT', 'ENDPOINT', 'CLB', 'CLB_LISTENER', 'SERVER_GROUP', 'ENDPOINT', 'DEPLOYMENT', 'APPLICATION', 'REPOSITORY',
  ]);
});

// ---------------------------------------------------------------------------
// Case D — DNS → CLB → Nginx → Application (CLB backend port drives nginx listen)
// ---------------------------------------------------------------------------

const caseD = () => {
  const d = domain('api.example.com');
  const e = eip('eip-1', '47.1.1.1');
  const lb = clb('lb-1', '10.179.6.15');
  const lis = listener('lb-1', 'https', '443');
  const grp = serverGroup('lb-1', 'rsp-1', [{ id: 'i-1', ip: '10.1.1.5', port: 8443, weight: 100, type: 'ecs' }]);
  const nginxEp = endpoint('10.1.1.5', '8443', 'https');
  const hostNode = host('10.1.1.5', { assetIds: ['asset-d'] });
  const route = nginxRoute('asset-d', 'main', {
    domains: ['api.example.com'], uri: '/', listen: ['8443 ssl'],
    target: 'http://order_backend/', upstreamName: 'order_backend',
  });
  const up = upstream('asset-d', 'order_backend');
  const backend = endpoint('10.179.1.30', '8080', 'http');
  const appParts = wireApp('app-d', 'Order Service D', 'PRODUCT', backend);
  return {
    d, e, lb, lis, grp, nginxEp, hostNode, route, up, backend, ...appParts,
    nodes: [d, e, lb, lis, grp, nginxEp, hostNode, route, up, backend, ...appParts.nodes],
    edges: [
      edge('r1', d.id, e.id, 'RESOLVES_TO'),
      edge('b1', e.id, lb.id, 'BOUND_TO'),
      edge('hl1', lb.id, lis.id, 'HAS_LISTENER'),
      edge('rt1', lis.id, grp.id, 'ROUTES_TO'),
      edge('ft1', grp.id, nginxEp.id, 'FORWARDS_TO'),
      edge('oh1', nginxEp.id, hostNode.id, 'ON_HOST'),
      edge('uu1', route.id, up.id, 'USES_UPSTREAM'),
      edge('ft2', up.id, backend.id, 'FORWARDS_TO'),
      ...appParts.edges,
    ],
  };
};

test('Case D: DNS → CLB → Nginx → Application matches nginx listen on the backend port', async () => {
  const { resolveRequestPath } = await framework();
  const topo = caseD();
  const trace = resolveRequestPath('https://api.example.com/order/123', graph(topo.nodes, topo.edges), {
    registry: await defaultRegistry(),
    environment: 'PRODUCT',
  });
  assert.equal(trace.status, 'RESOLVED');
  // Main chain + one retained dead-end branch: the EIP has no external
  // ENDPOINT node (real EIP→CLB bindings carry none), so NatResolver
  // speculatively synthesizes one that no DNAT rule claims. The dead branch
  // is a diagnostic, not a demotion.
  assert.equal(trace.paths.length, 2);
  const [path] = trace.paths;
  assert.equal(path.terminalNodeId, topo.repo.id);
  assert.deepEqual(path.steps.map(s => s.rule), [
    'dns:entry', 'dns:records', 'clb:eip-bound', 'clb:listener', 'clb:default-group', 'clb:backend',
    'nginx:host', 'nginx:route:exact:prefix', 'nginx:upstream', 'nginx:backend',
    'deployment:endpoint:ip-port', 'deployment:application', 'repository:built-from',
  ]);
  // The CLB forwards to 8443; the route listens on 8443 — the requested port
  // (443) must not be the only candidate port for the listen match.
  assert.equal(path.steps[6].rule, 'nginx:host');
  assert.equal(path.confidence, 'EXACT');
  const deadEnd = trace.paths[1];
  assert.equal(deadEnd.status, 'PARTIAL');
  assert.equal(deadEnd.stoppedAt, 'endpoint:47.1.1.1:443:tcp');
  assert.ok(trace.warnings.some(w => w.includes('was derived from the request scheme/port')));
});

// ---------------------------------------------------------------------------
// Case E — DNS → CLB → multiple backends
// ---------------------------------------------------------------------------

const caseE = (appIds) => {
  const d = domain('api.example.com');
  const e = eip('eip-1', '47.1.1.1');
  const lb = clb('lb-1', '10.179.6.15');
  const lis = listener('lb-1', 'https', '443');
  const backends = appIds.map((appId, i) => endpoint(`10.179.1.${40 + i}`, '8080', 'http'));
  const grp = serverGroup('lb-1', 'rsp-1', backends.map((b, i) => ({ id: `i-${i}`, ip: b.identity.ip, port: 8080, weight: 50, type: 'ecs' })));
  // One application + one repository + one BUILT_FROM per unique appId (as the
  // topology builder emits); one deployment instance per backend.
  const apps = new Map();
  const appParts = appIds.map((appId, i) => {
    if (!apps.has(appId)) {
      const app = application(appId);
      const repo = repository(appId);
      apps.set(appId, { app, repo });
    }
    const { app, repo } = apps.get(appId);
    const dep = deployment(appId, 'PRODUCT', `d${i + 1}`, backends[i].identity.ip, backends[i].identity.port);
    return {
      app, dep, repo,
      nodes: [app, dep, repo],
      edges: [
        edge(`hd-${appId}-d${i + 1}`, app.id, dep.id, 'HAS_DEPLOYMENT', { environment: 'PRODUCT' }),
        edge(`lo-${appId}-d${i + 1}`, dep.id, backends[i].id, 'LISTENS_ON', { environment: 'PRODUCT' }),
        edge(`bf-${appId}`, app.id, repo.id, 'BUILT_FROM'),
      ],
    };
  });
  const nodes = [d, e, lb, lis, grp, ...backends, ...appParts.flatMap(p => p.nodes)];
  const edges = [
    edge('r1', d.id, e.id, 'RESOLVES_TO'),
    edge('b1', e.id, lb.id, 'BOUND_TO'),
    edge('hl1', lb.id, lis.id, 'HAS_LISTENER'),
    edge('rt1', lis.id, grp.id, 'ROUTES_TO'),
    ...backends.map((b, i) => edge(`ft${i}`, grp.id, b.id, 'FORWARDS_TO')),
    ...appParts.flatMap(p => p.edges),
  ];
  return {
    d, e, lb, lis, grp, backends, appParts,
    nodes: [...new Map(nodes.map(n => [n.id, n])).values()],
    edges: [...new Map(edges.map(x => [x.id, x])).values()],
  };
};

test('Case E: two CLB backends serving the SAME application resolve without AMBIGUOUS', async () => {
  const { resolveRequestPath } = await framework();
  // Two backend entries, both mapped to the same appId (deduped to one
  // application/repository with two deployment instances).
  const topo = caseE(['app-e', 'app-e']);
  const trace = resolveRequestPath('https://api.example.com/order/1', graph(topo.nodes, topo.edges), {
    registry: await defaultRegistry(),
    environment: 'PRODUCT',
  });
  // Legitimate load-balanced candidates are not ambiguous: both backends reach
  // the same application/repository.
  assert.equal(trace.status, 'RESOLVED');
  const resolved = trace.paths.filter(path => path.terminalNodeId);
  assert.equal(resolved.length, 2);
  for (const path of resolved) {
    assert.equal(path.status, 'RESOLVED');
    assert.equal(path.terminalNodeId, topo.appParts[0].repo.id);
    assert.equal(path.confidence, 'EXACT');
  }
  const backendIps = resolved.map(p => p.nodes[5].identity.ip).sort((a, b) => a.localeCompare(b));
  assert.deepEqual(backendIps, ['10.179.1.40', '10.179.1.41']);
});

test('Case E: two CLB backends serving DIFFERENT applications are AMBIGUOUS (all kept)', async () => {
  const { resolveRequestPath } = await framework();
  const topo = caseE(['app-e1', 'app-e2']);
  const trace = resolveRequestPath('https://api.example.com/order/1', graph(topo.nodes, topo.edges), {
    registry: await defaultRegistry(),
    environment: 'PRODUCT',
  });
  // Each backend resolves its own chain (EXACT per path), but the request as a
  // whole has two distinct application interpretations => AMBIGUOUS.
  assert.equal(trace.status, 'AMBIGUOUS');
  const terminals = trace.paths.filter(p => p.terminalNodeId);
  assert.equal(terminals.length, 2);
  assert.deepEqual(terminals.map(p => p.terminalNodeId).sort((a, b) => a.localeCompare(b)), [topo.appParts[0].repo.id, topo.appParts[1].repo.id].sort((a, b) => a.localeCompare(b)));
  for (const path of terminals) {
    assert.equal(path.status, 'RESOLVED');
    assert.ok(path.nodes.some(n => n.type === 'APPLICATION'));
  }
});

// ---------------------------------------------------------------------------
// Case F — DNS → external target that cannot be resolved further
// ---------------------------------------------------------------------------

test('Case F: CNAME to an external domain stops honestly as PARTIAL (no fabricated paths)', async () => {
  const { resolveRequestPath, STOP_REASON } = await framework();
  const d = domain('www.example.com');
  const external = domain('cdn.external.net');
  const g = graph([d, external], [edge('c1', d.id, external.id, 'CNAME_TO')]);
  const trace = resolveRequestPath('https://www.example.com/', g, { registry: await defaultRegistry() });
  assert.equal(trace.status, 'PARTIAL');
  assert.equal(trace.paths.length, 1);
  const [path] = trace.paths;
  assert.equal(path.terminalNodeId, null);
  assert.equal(path.stoppedAt, external.id);
  assert.equal(path.reason, STOP_REASON.NO_CANDIDATE);
  // No invented nodes/edges beyond the factual DNS chain.
  assert.deepEqual(path.nodes.map(n => n.id), [d.id, external.id]);
  assert.deepEqual(path.steps.map(s => s.rule), ['dns:entry', 'dns:records']);
});

test('Case F: a domain absent from DNS stays UNRESOLVED with zero steps', async () => {
  const { resolveRequestPath, STOP_REASON } = await framework();
  const unrelated = domain('other.example.com');
  const trace = resolveRequestPath('https://missing.example.com/', graph([unrelated]), { registry: await defaultRegistry() });
  assert.equal(trace.status, 'UNRESOLVED');
  assert.equal(trace.paths.length, 1);
  assert.equal(trace.paths[0].steps.length, 0);
  assert.equal(trace.paths[0].terminalNodeId, null);
  // DnsResolver fires at the entry but finds no record for the host.
  assert.equal(trace.paths[0].reason, STOP_REASON.NO_CANDIDATE);
});

// ---------------------------------------------------------------------------
// Case G — Endpoint with multiple possible Applications (AMBIGUOUS, all kept)
// ---------------------------------------------------------------------------

test('Case G: one endpoint claimed by two applications is AMBIGUOUS with both paths kept', async () => {
  const { resolveRequestPath } = await framework();
  const d = domain('api.example.com');
  const ep = endpoint('10.179.1.50', '8080', 'http');
  const partsA = wireApp('app-g1', 'Service G1', 'PRODUCT', ep, 'd1');
  const partsB = wireApp('app-g2', 'Service G2', 'PRODUCT', ep, 'd1');
  const g = graph([d, ep, ...partsA.nodes, ...partsB.nodes], [
    edge('r1', d.id, ep.id, 'RESOLVES_TO'),
    ...partsA.edges,
    ...partsB.edges,
  ]);
  const trace = resolveRequestPath('https://api.example.com/order/1', g, {
    registry: await defaultRegistry(),
    environment: 'PRODUCT',
  });
  assert.equal(trace.status, 'AMBIGUOUS');
  assert.equal(trace.paths.length, 2);
  const appIds = trace.paths.map(p => p.nodes.find(n => n.type === 'APPLICATION')?.identity.devopsAppId).filter(Boolean).sort((a, b) => a.localeCompare(b));
  assert.deepEqual(appIds, ['app-g1', 'app-g2']);
  // Neither candidate was randomly dropped; both chains reach their repository.
  for (const path of trace.paths) assert.equal(path.status, 'AMBIGUOUS');
  assert.deepEqual(trace.paths.map(p => p.terminalNodeId).sort((a, b) => a.localeCompare(b)), [partsA.repo.id, partsB.repo.id].sort((a, b) => a.localeCompare(b)));
});

// ---------------------------------------------------------------------------
// Step 4 — domain-only query on path-dependent routing
// ---------------------------------------------------------------------------

test('domain-only query on path-specific nginx locations reports "URI required to continue routing"', async () => {
  const { resolveRequestPath } = await framework();
  const topo = caseA();
  // Query without a meaningful path: https://api.example.com/ (domain-only).
  const trace = resolveRequestPath('https://api.example.com/', graph(topo.nodes, topo.edges), {
    registry: await defaultRegistry(),
    environment: 'PRODUCT',
  });
  assert.equal(trace.query.domainOnly, true);
  // The chain progressed (host reached) but routing depends on the URI path.
  assert.equal(trace.status, 'PARTIAL');
  assert.equal(trace.paths.length, 1);
  const [path] = trace.paths;
  assert.equal(path.stoppedAt, topo.hostNode.id);
  assert.ok(trace.warnings.some(w => w.includes('URI required to continue routing')));
  assert.ok(trace.warnings.some(w => w.includes('https://api.example.com/<path>')));
});

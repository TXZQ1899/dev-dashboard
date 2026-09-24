/* eslint-disable typescript/no-require-imports -- Node test harness uses CommonJS, matching the existing suite. */
const { test } = require('node:test');
const assert = require('node:assert/strict');

const OBSERVED_AT = '2026-09-23T00:00:00.000Z';

// Synthetic fixtures only (PROJECT_GUIDE §15); ids follow lib/topology/common.ts conventions.
const ev = (source, sourceId) => [{
  source,
  sourceId,
  reference: 'tests/request-resolver-nginx.test.cjs',
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
  evidence: options.evidence ?? ev('jumpserver', `node ${id}`),
  attributes: options.attributes ?? {},
});

const domain = (name, options = {}) => node(`domain:${name}`, 'DOMAIN', { name }, { evidence: ev('dns', `domain ${name}`), ...options });
const endpoint = (ip, port, protocol, options = {}) => node(epId(ip, port, protocol), 'ENDPOINT', { ip, port, protocol }, { evidence: ev('jumpserver', `ep ${ip}:${port}`), ...options });
const host = (ip, options = {}) => {
  const assetIds = options.assetIds ?? [`asset-${ip}`];
  return node(`host:${ip}`, 'HOST', { ip, jumpserverAssetIds: assetIds }, {
    evidence: ev('jumpserver', `host ${ip}`),
    ...options,
    attributes: { jumpserver: { id: assetIds[0], hostname: `srv-${ip}` }, ...options.attributes },
  });
};
// Mirrors lib/topology/node-builders/nginx.ts identity/attributes shape.
const nginxRoute = (assetId, suffix, opts = {}) => node(
  `nginx-route:${assetId}:${suffix}`,
  'NGINX_ROUTE',
  {
    assetId,
    domains: opts.domains ?? [],
    uri: opts.uri ?? '/',
    directive: opts.directive ?? 'proxy_pass',
    target: opts.target ?? '',
    upstream: opts.upstreamName ?? (opts.target ? opts.target.replace(/^\w+:\/\//, '').replace(/\/$/, '') : ''),
  },
  {
    status: opts.status ?? 'active',
    evidence: ev('jumpserver', `route ${assetId}/${suffix}`),
    attributes: {
      listen: opts.listen ?? ['80'],
      context: opts.context ?? 'http > server',
      nginxStatus: opts.nginxStatus ?? 'complete',
      configurationVersion: '20260922-1',
    },
  },
);
const upstream = (assetId, name, opts = {}) => node(
  `nginx-upstream:${assetId}:${name}`,
  'UPSTREAM',
  { assetId, name },
  { evidence: ev('jumpserver', `upstream ${name}`), attributes: { target: opts.target ?? '', directive: opts.directive ?? 'proxy_pass' } },
);

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

// DNS + Nginx only: isolates the nginx chain without NAT/CLB branches.
const dnsNginxRegistry = async () => {
  const { ResolverRegistry, DnsResolver, NginxResolver } = await framework();
  return new ResolverRegistry().register(new DnsResolver()).register(new NginxResolver());
};

/**
 * Standard nginx topology: DNS A record → nginx host endpoint → ON_HOST → host
 * → route → upstream → backend endpoint(s). Returns everything plus `wire()`
 * helpers so tests can add extra routes/upstreams on the same host.
 */
const nginxTopology = (opts = {}) => {
  const assetId = 'asset-1';
  const hostIp = opts.hostIp ?? '10.1.1.5';
  const hostPort = opts.hostPort ?? '443';
  const hostProtocol = opts.hostProtocol ?? 'https';
  const route = nginxRoute(assetId, 'main', {
    domains: opts.domains ?? ['api.example.com'],
    uri: opts.uri ?? '/order',
    listen: opts.listen ?? ['443 ssl'],
    target: opts.target ?? 'http://order_backend/',
    upstreamName: opts.upstreamName ?? 'order_backend',
  });
  const up = upstream(assetId, opts.upstreamName ?? 'order_backend', { target: opts.target ?? 'http://order_backend/' });
  const backendIps = opts.backendIps ?? ['10.1.1.9'];
  const backendPort = opts.backendPort ?? '8080';
  const d = domain(opts.queryHost ?? 'api.example.com');
  const entryEp = endpoint(hostIp, hostPort, hostProtocol);
  const hostNode = host(hostIp, { assetIds: [assetId] });
  const backends = backendIps.map(ip => endpoint(ip, backendPort, opts.backendProtocol ?? 'http'));
  const nodes = [d, entryEp, hostNode, route, up, ...backends];
  const edges = [
    edge('r1', d.id, entryEp.id, 'RESOLVES_TO'),
    edge('oh1', entryEp.id, hostNode.id, 'ON_HOST'),
    edge('uu1', route.id, up.id, 'USES_UPSTREAM'),
    ...backends.map((b, i) => edge(`ft${i}`, up.id, b.id, 'FORWARDS_TO')),
  ];
  return { d, entryEp, hostNode, route, up, backends, nodes, edges, assetId };
};

// ---------------------------------------------------------------------------
// Full chain + server_name matching
// ---------------------------------------------------------------------------

test('nginx: full chain — DNS → endpoint → host → route (exact server_name) → upstream → backend', async () => {
  const { resolveRequestPath } = await framework();
  const topo = nginxTopology();
  const trace = resolveRequestPath('https://api.example.com/order/123', graph(topo.nodes, topo.edges), { registry: await dnsNginxRegistry() });
  assert.equal(trace.paths.length, 1);
  const [path] = trace.paths;
  assert.deepEqual(path.nodes.map(n => n.type), ['DOMAIN', 'ENDPOINT', 'HOST', 'NGINX_ROUTE', 'UPSTREAM', 'ENDPOINT']);
  assert.deepEqual(path.steps.map(s => s.rule), [
    'dns:entry', 'dns:records', 'nginx:host', 'nginx:route:exact:prefix', 'nginx:upstream', 'nginx:backend',
  ]);
  assert.equal(path.confidence, 'EXACT');
  assert.equal(path.steps[3].rule, 'nginx:route:exact:prefix');
  // Terminal stays open for TASK-05 APPLICATION.
  assert.equal(path.terminalNodeId, null);
  const backend = path.nodes[5];
  assert.equal(backend.identity.ip, '10.1.1.9');
  assert.equal(backend.identity.port, '8080');
});

test('nginx: exact server_name wins over unrelated server_name on the same host', async () => {
  const { resolveRequestPath } = await framework();
  const topo = nginxTopology();
  const otherRoute = nginxRoute(topo.assetId, 'other', {
    domains: ['other.example.com'],
    uri: '/',
    listen: ['443 ssl'],
    target: 'http://other_backend/',
    upstreamName: 'other_backend',
  });
  const otherUp = upstream(topo.assetId, 'other_backend');
  const g = graph([...topo.nodes, otherRoute, otherUp], [
    ...topo.edges,
    edge('uu2', otherRoute.id, otherUp.id, 'USES_UPSTREAM'),
  ]);
  const trace = resolveRequestPath('https://api.example.com/order/123', g, { registry: await dnsNginxRegistry() });
  assert.equal(trace.paths.length, 1);
  assert.equal(trace.paths[0].nodes[3].id, topo.route.id);
});

test('nginx: wildcard server_name (*.example.com) matches a subdomain', async () => {
  const { resolveRequestPath } = await framework();
  const topo = nginxTopology({ domains: ['*.example.com'], queryHost: 'www.example.com' });
  const trace = resolveRequestPath('https://www.example.com/order/123', graph(topo.nodes, topo.edges), { registry: await dnsNginxRegistry() });
  assert.equal(trace.paths.length, 1);
  const [path] = trace.paths;
  assert.equal(path.nodes[3].id, topo.route.id);
  assert.equal(path.steps[3].rule, 'nginx:route:wildcard:prefix');
  assert.equal(path.steps[3].confidence, 'EXACT');
});

test('nginx: unmatched server_name falls back to the default_server block with INFERRED', async () => {
  const { resolveRequestPath } = await framework();
  const topo = nginxTopology({ domains: [], listen: ['443 ssl default_server'], queryHost: 'nomatch.example.com' });
  const trace = resolveRequestPath('https://nomatch.example.com/order/123', graph(topo.nodes, topo.edges), { registry: await dnsNginxRegistry() });
  assert.equal(trace.paths.length, 1);
  const [path] = trace.paths;
  assert.equal(path.nodes[3].id, topo.route.id);
  assert.equal(path.steps[3].rule, 'nginx:route:default:prefix');
  assert.equal(path.steps[3].confidence, 'INFERRED');
  assert.ok(trace.warnings.some(w => w.includes('default/fallback server block')));
});

// ---------------------------------------------------------------------------
// listen matching
// ---------------------------------------------------------------------------

test('nginx: listen 80 matches an http request on port 80', async () => {
  const { resolveRequestPath } = await framework();
  const topo = nginxTopology({ listen: ['80'], hostPort: '80', hostProtocol: 'http', backendProtocol: 'http' });
  const trace = resolveRequestPath('http://api.example.com/order/123', graph(topo.nodes, topo.edges), { registry: await dnsNginxRegistry() });
  assert.equal(trace.paths.length, 1);
  assert.deepEqual(trace.paths[0].nodes.map(n => n.type), ['DOMAIN', 'ENDPOINT', 'HOST', 'NGINX_ROUTE', 'UPSTREAM', 'ENDPOINT']);
});

test('nginx: listen 443 matches an https request while a listen-80-only route is skipped', async () => {
  const { resolveRequestPath } = await framework();
  const topo = nginxTopology();
  const httpOnlyRoute = nginxRoute(topo.assetId, 'http-only', {
    domains: ['api.example.com'],
    uri: '/order',
    listen: ['80'],
    target: 'http://http_backend/',
    upstreamName: 'http_backend',
  });
  const g = graph([...topo.nodes, httpOnlyRoute], [...topo.edges]);
  const trace = resolveRequestPath('https://api.example.com/order/123', g, { registry: await dnsNginxRegistry() });
  assert.equal(trace.paths.length, 1);
  assert.equal(trace.paths[0].nodes[3].id, topo.route.id);
  assert.equal(trace.paths[0].steps[3].rule, 'nginx:route:exact:prefix');
});

test('nginx: no route listens on the request port falls back to port-agnostic matching (INFERRED)', async () => {
  const { resolveRequestPath } = await framework();
  const topo = nginxTopology({ listen: ['8080'] });
  const trace = resolveRequestPath('https://api.example.com/order/123', graph(topo.nodes, topo.edges), { registry: await dnsNginxRegistry() });
  assert.equal(trace.paths.length, 1);
  const [path] = trace.paths;
  // Port fallback: the chain continues past the host to the route → upstream → backend.
  assert.deepEqual(path.nodes.map(n => n.type), ['DOMAIN', 'ENDPOINT', 'HOST', 'NGINX_ROUTE', 'UPSTREAM', 'ENDPOINT']);
  assert.equal(path.steps[2].confidence, 'EXACT'); // nginx:host via ON_HOST edge
  assert.equal(path.steps[3].confidence, 'INFERRED'); // nginx:route port-inferred fallback
  assert.ok(trace.warnings.some(w => w.includes('falling back to port-agnostic matching')));
  assert.ok(trace.warnings.some(w => w.includes('Nginx listens on: 8080')));
});

// ---------------------------------------------------------------------------
// location matching
// ---------------------------------------------------------------------------

test('nginx: exact location (= /health) beats the / prefix route', async () => {
  const { resolveRequestPath } = await framework();
  const topo = nginxTopology({ uri: '/', target: 'http://root_backend/', upstreamName: 'root_backend' });
  const healthRoute = nginxRoute(topo.assetId, 'health', {
    domains: ['api.example.com'],
    uri: '= /health',
    listen: ['443 ssl'],
    target: 'http://health_backend/',
    upstreamName: 'health_backend',
  });
  const healthUp = upstream(topo.assetId, 'health_backend');
  const healthBackend = endpoint('10.1.1.20', '8080', 'http');
  const g = graph([...topo.nodes, healthRoute, healthUp, healthBackend], [
    ...topo.edges,
    edge('uu-h', healthRoute.id, healthUp.id, 'USES_UPSTREAM'),
    edge('ft-h', healthUp.id, healthBackend.id, 'FORWARDS_TO'),
  ]);
  const trace = resolveRequestPath('https://api.example.com/health', g, { registry: await dnsNginxRegistry() });
  assert.equal(trace.paths.length, 1);
  assert.equal(trace.paths[0].nodes[3].id, healthRoute.id);
  assert.equal(trace.paths[0].steps[3].rule, 'nginx:route:exact:exact');
});

test('nginx: longest prefix wins over shorter prefix and root fallback', async () => {
  const { resolveRequestPath } = await framework();
  const topo = nginxTopology({ uri: '/order', target: 'http://order_backend/', upstreamName: 'order_backend' });
  const rootRoute = nginxRoute(topo.assetId, 'root', {
    domains: ['api.example.com'],
    uri: '/',
    listen: ['443 ssl'],
    target: 'http://root_backend/',
    upstreamName: 'root_backend',
  });
  const g = graph([...topo.nodes, rootRoute], [...topo.edges]);
  // /order/123 → /order route (longest prefix), not the / root fallback.
  const ordered = resolveRequestPath('https://api.example.com/order/123', g, { registry: await dnsNginxRegistry() });
  assert.equal(ordered.paths.length, 1);
  assert.equal(ordered.paths[0].nodes[3].id, topo.route.id);
  // /other/x → root fallback route.
  const other = resolveRequestPath('https://api.example.com/other/x', g, { registry: await dnsNginxRegistry() });
  assert.equal(other.paths.length, 1);
  assert.equal(other.paths[0].nodes[3].id, rootRoute.id);
  assert.equal(other.paths[0].steps[3].rule, 'nginx:route:exact:prefix');
});

test('nginx: root fallback route serves any path when it is the only match', async () => {
  const { resolveRequestPath } = await framework();
  const topo = nginxTopology({ uri: '/' });
  const trace = resolveRequestPath('https://api.example.com/anything/else', graph(topo.nodes, topo.edges), { registry: await dnsNginxRegistry() });
  assert.equal(trace.paths.length, 1);
  assert.equal(trace.paths[0].nodes[3].id, topo.route.id);
  assert.equal(trace.paths[0].steps[3].rule, 'nginx:route:exact:prefix');
});

test('nginx: regex location is unsupported in V1 and excluded with a warning', async () => {
  const { resolveRequestPath } = await framework();
  const topo = nginxTopology({ uri: '~ \\.php$', listen: ['443 ssl'] });
  const trace = resolveRequestPath('https://api.example.com/order/123', graph(topo.nodes, topo.edges), { registry: await dnsNginxRegistry() });
  assert.equal(trace.paths.length, 1);
  const [path] = trace.paths;
  assert.deepEqual(path.nodes.map(n => n.type), ['DOMAIN', 'ENDPOINT', 'HOST']);
  assert.equal(path.stoppedAt, topo.hostNode.id);
  assert.ok(trace.warnings.some(w => w.includes('regex')));
});

test('nginx: equally specific routes (same server_name + location) produce AMBIGUOUS paths', async () => {
  const { resolveRequestPath } = await framework();
  const topo = nginxTopology();
  const twinRoute = nginxRoute(topo.assetId, 'twin', {
    domains: ['api.example.com'],
    uri: '/order',
    listen: ['443 ssl'],
    target: 'http://twin_backend/',
    upstreamName: 'twin_backend',
  });
  const twinUp = upstream(topo.assetId, 'twin_backend');
  const twinBackend = endpoint('10.1.1.30', '8080', 'http');
  const g = graph([...topo.nodes, twinRoute, twinUp, twinBackend], [
    ...topo.edges,
    edge('uu-t', twinRoute.id, twinUp.id, 'USES_UPSTREAM'),
    edge('ft-t', twinUp.id, twinBackend.id, 'FORWARDS_TO'),
  ]);
  const trace = resolveRequestPath('https://api.example.com/order/123', g, { registry: await dnsNginxRegistry() });
  assert.equal(trace.paths.length, 2);
  for (const path of trace.paths) {
    assert.equal(path.steps[3].confidence, 'AMBIGUOUS');
  }
  assert.ok(trace.warnings.some(w => w.includes('equally match')));
});

// ---------------------------------------------------------------------------
// Upstream / backend resolution
// ---------------------------------------------------------------------------

test('nginx: named upstream resolves through FORWARDS_TO edges', async () => {
  const { resolveRequestPath } = await framework();
  const topo = nginxTopology({ upstreamName: 'order_backend', target: 'http://order_backend/' });
  const trace = resolveRequestPath('https://api.example.com/order/123', graph(topo.nodes, topo.edges), { registry: await dnsNginxRegistry() });
  assert.equal(trace.paths.length, 1);
  const [path] = trace.paths;
  assert.equal(path.nodes[4].id, topo.up.id);
  assert.equal(path.steps[5].rule, 'nginx:backend');
  assert.equal(path.steps[5].confidence, 'EXACT');
});

test('nginx: direct proxy_pass to an IP resolves the backend endpoint', async () => {
  const { resolveRequestPath } = await framework();
  const topo = nginxTopology({ target: 'http://10.1.1.9:8080', upstreamName: '10.1.1.9:8080' });
  const trace = resolveRequestPath('https://api.example.com/order/123', graph(topo.nodes, topo.edges), { registry: await dnsNginxRegistry() });
  assert.equal(trace.paths.length, 1);
  const [path] = trace.paths;
  assert.equal(path.nodes[4].id, topo.up.id);
  assert.equal(path.nodes[5].identity.ip, '10.1.1.9');
  assert.equal(path.nodes[5].identity.port, '8080');
  assert.equal(path.steps[5].rule, 'nginx:backend');
});

test('nginx: multiple upstream backends fan out as load-balanced candidates', async () => {
  const { resolveRequestPath } = await framework();
  const topo = nginxTopology({ backendIps: ['10.1.1.9', '10.1.1.10', '10.1.1.11'] });
  const trace = resolveRequestPath('https://api.example.com/order/123', graph(topo.nodes, topo.edges), { registry: await dnsNginxRegistry() });
  assert.equal(trace.paths.length, 3);
  const backendIps = trace.paths.map(p => p.nodes[5].identity.ip).sort((a, b) => a.localeCompare(b));
  assert.deepEqual(backendIps, ['10.1.1.10', '10.1.1.11', '10.1.1.9']);
  for (const path of trace.paths) {
    assert.equal(path.steps[5].confidence, 'EXACT');
    assert.equal(path.steps[5].rule, 'nginx:backend');
  }
});

test('nginx: IP backend missing from the graph is derived from the proxy_pass target', async () => {
  const { resolveRequestPath } = await framework();
  const topo = nginxTopology({ target: 'http://10.1.1.99:8080', upstreamName: '10.1.1.99:8080' });
  // Drop the FORWARDS_TO edge + backend node: only the route/upstream identity remains.
  const nodes = topo.nodes.filter(n => n.id !== topo.backends[0].id);
  const edges = topo.edges.filter(e => e.type !== 'FORWARDS_TO');
  const trace = resolveRequestPath('https://api.example.com/order/123', graph(nodes, edges), { registry: await dnsNginxRegistry() });
  assert.equal(trace.paths.length, 1);
  const [path] = trace.paths;
  assert.equal(path.steps[5].rule, 'nginx:backend-derived');
  assert.equal(path.steps[5].confidence, 'INFERRED');
  assert.equal(path.nodes[5].identity.ip, '10.1.1.99');
  assert.equal(path.nodes[5].attributes.synthesized, true);
  assert.ok(trace.warnings.some(w => w.includes('logical endpoint was derived')));
});

test('nginx: unresolved hostname backend stops the branch without DNS speculation', async () => {
  const { resolveRequestPath, STOP_REASON } = await framework();
  const topo = nginxTopology({ target: 'http://api.internal:8080', upstreamName: 'api.internal:8080' });
  const nodes = topo.nodes.filter(n => n.id !== topo.backends[0].id);
  const edges = topo.edges.filter(e => e.type !== 'FORWARDS_TO');
  const trace = resolveRequestPath('https://api.example.com/order/123', graph(nodes, edges), { registry: await dnsNginxRegistry() });
  assert.equal(trace.paths.length, 1);
  const [path] = trace.paths;
  assert.deepEqual(path.nodes.map(n => n.type), ['DOMAIN', 'ENDPOINT', 'HOST', 'NGINX_ROUTE', 'UPSTREAM']);
  assert.equal(path.stoppedAt, topo.up.id);
  assert.equal(path.reason, STOP_REASON.NO_CANDIDATE);
  assert.ok(trace.warnings.some(w => w.includes('api.internal') && w.includes('without DNS speculation')));
});

test('nginx: hostname backend continues through an existing DOMAIN node', async () => {
  const { resolveRequestPath } = await framework();
  const topo = nginxTopology({ target: 'http://uaa.tcc.cn:80', upstreamName: 'uaa.tcc.cn:80' });
  const nodes = topo.nodes.filter(n => n.id !== topo.backends[0].id);
  const edges = topo.edges.filter(e => e.type !== 'FORWARDS_TO');
  // Existing DNS data for the backend hostname (not speculation).
  const backendDomain = domain('uaa.tcc.cn');
  const resolvedEp = endpoint('10.2.2.2', '80', 'http');
  nodes.push(backendDomain, resolvedEp);
  edges.push(edge('r2', backendDomain.id, resolvedEp.id, 'RESOLVES_TO'));
  const trace = resolveRequestPath('https://api.example.com/order/123', graph(nodes, edges), { registry: await dnsNginxRegistry() });
  assert.equal(trace.paths.length, 1);
  const [path] = trace.paths;
  assert.deepEqual(path.nodes.map(n => n.type), ['DOMAIN', 'ENDPOINT', 'HOST', 'NGINX_ROUTE', 'UPSTREAM', 'DOMAIN', 'ENDPOINT']);
  assert.equal(path.steps[5].rule, 'nginx:backend-domain');
  assert.equal(path.steps[5].confidence, 'INFERRED');
  assert.ok(trace.warnings.some(w => w.includes('uaa.tcc.cn') && w.includes('DOMAIN node')));
});

test('nginx: dynamic and unix backend targets stop with unsupported warnings', async () => {
  const { resolveRequestPath, STOP_REASON } = await framework();
  for (const [upstreamName, target, expectedWord] of [
    ['$backend_host', 'http://$backend_host/', 'variable'],
    ['unix:/tmp/app.sock', 'unix:/tmp/app.sock', 'unix socket'],
  ]) {
    const topo = nginxTopology({ target, upstreamName });
    const nodes = topo.nodes.filter(n => n.id !== topo.backends[0].id);
    const edges = topo.edges.filter(e => e.type !== 'FORWARDS_TO');
    const trace = resolveRequestPath('https://api.example.com/order/123', graph(nodes, edges), { registry: await dnsNginxRegistry() });
    assert.equal(trace.paths.length, 1, upstreamName);
    const [path] = trace.paths;
    assert.equal(path.stoppedAt, topo.up.id, upstreamName);
    assert.equal(path.reason, STOP_REASON.NO_CANDIDATE, upstreamName);
    assert.ok(trace.warnings.some(w => w.includes(expectedWord)), `${upstreamName} → ${expectedWord}`);
  }
});

// ---------------------------------------------------------------------------
// Host location / no-route cases
// ---------------------------------------------------------------------------

test('nginx: no route found for host/server_name leaves the branch unresolved with a warning', async () => {
  const { resolveRequestPath, STOP_REASON } = await framework();
  const topo = nginxTopology({ domains: ['other.example.com'] });
  const trace = resolveRequestPath('https://api.example.com/order/123', graph(topo.nodes, topo.edges), { registry: await dnsNginxRegistry() });
  assert.equal(trace.paths.length, 1);
  const [path] = trace.paths;
  assert.deepEqual(path.nodes.map(n => n.type), ['DOMAIN', 'ENDPOINT', 'HOST']);
  assert.equal(path.stoppedAt, topo.hostNode.id);
  assert.equal(path.reason, STOP_REASON.NO_CANDIDATE);
  assert.ok(trace.warnings.some(w => w.includes('No nginx server_name on host')));
});

test('nginx: host without collected routes reports nginx presence as unknown (cannot infer "no nginx")', async () => {
  const { resolveRequestPath, STOP_REASON } = await framework();
  const d = domain('api.example.com');
  const ep = endpoint('10.1.1.5', '443', 'https');
  const hostNode = host('10.1.1.5', { assetIds: ['asset-empty'] });
  const g = graph([d, ep, hostNode], [
    edge('r1', d.id, ep.id, 'RESOLVES_TO'),
    edge('oh1', ep.id, hostNode.id, 'ON_HOST'),
  ]);
  const trace = resolveRequestPath('https://api.example.com/order/123', g, { registry: await dnsNginxRegistry() });
  assert.equal(trace.paths.length, 1);
  const [path] = trace.paths;
  assert.deepEqual(path.nodes.map(n => n.type), ['DOMAIN', 'ENDPOINT', 'HOST']);
  assert.equal(path.stoppedAt, hostNode.id);
  assert.equal(path.reason, STOP_REASON.NO_CANDIDATE);
  assert.ok(trace.warnings.some(w => w.includes('nginx presence is unknown')));
});

test('nginx: endpoint without ON_HOST edge falls back to an exact IP host match (INFERRED)', async () => {
  const { resolveRequestPath } = await framework();
  const topo = nginxTopology();
  // Remove the ON_HOST edge; the host is still in the inventory at the same IP.
  const edges = topo.edges.filter(e => e.type !== 'ON_HOST');
  const trace = resolveRequestPath('https://api.example.com/order/123', graph(topo.nodes, edges), { registry: await dnsNginxRegistry() });
  assert.equal(trace.paths.length, 1);
  const [path] = trace.paths;
  assert.equal(path.steps[2].rule, 'nginx:host');
  assert.equal(path.steps[2].confidence, 'INFERRED');
  assert.deepEqual(path.nodes.map(n => n.type), ['DOMAIN', 'ENDPOINT', 'HOST', 'NGINX_ROUTE', 'UPSTREAM', 'ENDPOINT']);
});

// ---------------------------------------------------------------------------
// Determinism & registry
// ---------------------------------------------------------------------------

test('nginx: deterministic output — same input always produces the same trace', async () => {
  const { resolveRequestPath } = await framework();
  const topo = nginxTopology();
  const g = graph(topo.nodes, topo.edges);
  const registry = await dnsNginxRegistry();
  const trace1 = resolveRequestPath('https://api.example.com/order/123', g, { registry });
  const trace2 = resolveRequestPath('https://api.example.com/order/123', g, { registry });
  assert.deepEqual(JSON.parse(JSON.stringify(trace1)), JSON.parse(JSON.stringify(trace2)));
});

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

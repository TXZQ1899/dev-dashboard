/* eslint-disable typescript/no-require-imports -- Node test harness uses CommonJS, matching the existing suite. */
const { test } = require('node:test');
const assert = require('node:assert/strict');

// Nginx location reverse lookup: URI 关键字 -> 命中的 NGINX_ROUTE（含来源配置文件）
// -> UPSTREAM -> 后端 IP+端口 -> DevOps 应用。Fixtures are factual synthetic graphs;
// unresolved chains must stay honest (no fabricated matches).

const OBSERVED_AT = '2026-10-13T00:00:00.000Z';

const ev = (source, sourceId) => [{
  source,
  sourceId,
  reference: 'tests/nginx-location.test.cjs',
  detail: `${source}:${sourceId}`,
  observedAt: OBSERVED_AT,
}];

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

const endpoint = (ip, port, protocol, options = {}) =>
  node(`endpoint:${ip}:${port}:${protocol}`, 'ENDPOINT', { ip, port, protocol }, { evidence: ev('jumpserver', `ep ${ip}:${port}`), ...options });

const host = (ip, assetIds, options = {}) =>
  node(`host:${ip}`, 'HOST', { ip, jumpserverAssetIds: assetIds }, {
    evidence: ev('jumpserver', `host ${ip}`),
    ...options,
    attributes: { jumpserver: { id: assetIds[0], hostname: `srv-${ip}` }, ...options.attributes },
  });

const nginxRoute = (assetId, suffix, opts = {}) => node(
  `nginx-route:${assetId}:${suffix}`,
  'NGINX_ROUTE',
  {
    assetId,
    domains: opts.domains ?? [],
    uri: opts.uri ?? '/',
    directive: opts.directive ?? 'proxy_pass',
    target: opts.target ?? '',
    upstream: opts.upstreamName ?? '',
  },
  {
    evidence: ev('jumpserver', `route ${assetId}/${suffix}`),
    attributes: { listen: opts.listen ?? ['80'], context: 'http > server', nginxStatus: 'complete', ...(opts.file ? { file: opts.file } : {}) },
  },
);

const upstream = (assetId, name) => node(
  `nginx-upstream:${assetId}:${name}`,
  'UPSTREAM',
  { assetId, name },
  { evidence: ev('jumpserver', `upstream ${name}`), attributes: { target: `http://${name}/`, directive: 'proxy_pass' } },
);

const application = (appId, name) =>
  node(`application:${appId}`, 'APPLICATION', { devopsAppId: appId }, { label: name, evidence: ev('devops', `app ${appId}`) });

const deployment = (appId, env, deployId, ip, port) =>
  node(`deployment:${appId}:${env}:${deployId}`, 'DEPLOYMENT', { appId, env, deployId, ip, port }, {
    label: `${appId} ${env} ${deployId}`,
    environment: env,
    evidence: ev('devops', `${appId}/${env}/${deployId}`),
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

/** Wires an application (deployment + repository) listening on an endpoint. */
const wireApp = (appId, appName, env, ep, deployId = 'd1') => {
  const app = application(appId, appName);
  const dep = deployment(appId, env, deployId, ep.identity.ip, ep.identity.port);
  return {
    app, dep,
    nodes: [app, dep],
    edges: [
      edge(`hd-${appId}-${deployId}`, app.id, dep.id, 'HAS_DEPLOYMENT', { environment: env }),
      edge(`lo-${appId}-${deployId}`, dep.id, ep.id, 'LISTENS_ON', { environment: env }),
    ],
  };
};

/** Two routes sharing one upstream behind host 10.1.1.5, plus an unrelated route. */
const baseFixture = ({ withFile = true } = {}) => {
  const hostNode = host('10.1.1.5', ['asset-a']);
  const routeA = nginxRoute('asset-a', 'r1', {
    domains: ['fostay.example.com'], uri: '/tc-fostay', listen: ['443 ssl'],
    target: 'http://fostay_backend/', upstreamName: 'fostay_backend',
    ...(withFile ? { file: '/etc/nginx/conf.d/fostay.conf' } : {}),
  });
  const routeB = nginxRoute('asset-a', 'r2', {
    domains: ['fostay.example.com'], uri: '/tc-fostay/api', listen: ['443 ssl'],
    target: 'http://fostay_backend/', upstreamName: 'fostay_backend',
    ...(withFile ? { file: '/etc/nginx/conf.d/fostay.conf' } : {}),
  });
  const routeC = nginxRoute('asset-a', 'r3', {
    domains: ['other.example.com'], uri: '/other', listen: ['80'],
    target: 'http://other_backend/', upstreamName: 'other_backend',
    ...(withFile ? { file: '/etc/nginx/conf.d/other.conf' } : {}),
  });
  const upA = upstream('asset-a', 'fostay_backend');
  const upC = upstream('asset-a', 'other_backend');
  const backendA = endpoint('10.1.1.9', '8080', 'http');
  const appParts = wireApp('app-a', 'FoStay Service', 'PRODUCT', backendA);
  return {
    hostNode, routeA, routeB, routeC, upA, upC, backendA, ...appParts,
    nodes: [hostNode, routeA, routeB, routeC, upA, upC, backendA, ...appParts.nodes],
    edges: [
      edge('uu1', routeA.id, upA.id, 'USES_UPSTREAM'),
      edge('uu2', routeB.id, upA.id, 'USES_UPSTREAM'),
      edge('uu3', routeC.id, upC.id, 'USES_UPSTREAM'),
      edge('ft1', upA.id, backendA.id, 'FORWARDS_TO'),
      ...appParts.edges,
    ],
  };
};

const framework = async () => await import('../lib/topology/nginx-location.ts');

test('contains match: exact location first, file/host/upstream/app resolved', async () => {
  const { findNginxLocations } = await framework();
  const { buildGraphIndex } = await import('../lib/topology/path-explorer.ts');
  const topo = baseFixture();
  const result = findNginxLocations(buildGraphIndex(graph(topo.nodes, topo.edges)), { uri: '/tc-fostay', environment: null });
  assert.equal(result.status, 'found');
  assert.equal(result.matches.length, 2);
  // Exact hit lists first, the more specific prefix second; unrelated route excluded.
  assert.equal(result.matches[0].uri, '/tc-fostay');
  assert.equal(result.matches[1].uri, '/tc-fostay/api');
  assert.ok(result.matches.every(match => match.route.id !== topo.routeC.id));
  assert.equal(result.matches[0].file, '/etc/nginx/conf.d/fostay.conf');
  assert.deepEqual(result.matches[0].domains, ['fostay.example.com']);
  assert.deepEqual(result.matches[0].listen, ['443 ssl']);
  assert.equal(result.matches[0].directive, 'proxy_pass');
  assert.equal(result.matches[0].host?.id, 'host:10.1.1.5');
  // Upstream -> backend -> application (IP+port exact match).
  const [group] = result.matches[0].upstreams;
  assert.equal(group.upstream.identity.name, 'fostay_backend');
  assert.equal(group.backends.length, 1);
  assert.equal(group.backends[0].status, 'exact');
  assert.equal(group.backends[0].matches[0].application.label, 'FoStay Service');
  assert.equal(group.backends[0].matches[0].environment, 'PRODUCT');
  assert.equal(result.stats.routeCount, 2);
  assert.equal(result.stats.exactBackendCount, 2);
  assert.equal(result.stats.unresolvedBackendCount, 0);
  assert.deepEqual(result.stats.applicationIds, ['application:app-a']);
  // No missing-file warning when the attribute is present.
  assert.ok(!result.warnings.some(w => w.includes('配置文件')));
});

test('matching is a case-insensitive substring hit without a leading slash', async () => {
  const { findNginxLocations } = await framework();
  const { buildGraphIndex } = await import('../lib/topology/path-explorer.ts');
  const topo = baseFixture();
  const result = findNginxLocations(buildGraphIndex(graph(topo.nodes, topo.edges)), { uri: 'TC-FOSTAY', environment: null });
  assert.equal(result.status, 'found');
  assert.deepEqual(result.matches.map(match => match.uri).sort(), ['/tc-fostay', '/tc-fostay/api']);
});

test('unmatched URI stays honestly not-found', async () => {
  const { findNginxLocations } = await framework();
  const { buildGraphIndex } = await import('../lib/topology/path-explorer.ts');
  const topo = baseFixture();
  const result = findNginxLocations(buildGraphIndex(graph(topo.nodes, topo.edges)), { uri: '/nope', environment: null });
  assert.equal(result.status, 'not-found');
  assert.deepEqual(result.matches, []);
  assert.deepEqual(result.stats.applicationIds, []);
});

test('environment filter restricts application matching without hiding routes', async () => {
  const { findNginxLocations } = await framework();
  const { buildGraphIndex } = await import('../lib/topology/path-explorer.ts');
  const topo = baseFixture();
  const index = buildGraphIndex(graph(topo.nodes, topo.edges));

  const product = findNginxLocations(index, { uri: '/tc-fostay', environment: 'PRODUCT' });
  assert.equal(product.status, 'found');
  assert.equal(product.matches[0].upstreams[0].backends[0].status, 'exact');

  // App only deployed as PRODUCT: TEST view keeps the route but the backend stays unresolved.
  const testView = findNginxLocations(index, { uri: '/tc-fostay', environment: 'TEST' });
  assert.equal(testView.status, 'found');
  assert.equal(testView.matches.length, 2);
  assert.equal(testView.matches[0].upstreams[0].backends[0].status, 'unresolved');
  assert.deepEqual(testView.stats.applicationIds, []);
});

test('topology generated before the file attribute reports the gap instead of failing', async () => {
  const { findNginxLocations } = await framework();
  const { buildGraphIndex } = await import('../lib/topology/path-explorer.ts');
  const topo = baseFixture({ withFile: false });
  const result = findNginxLocations(buildGraphIndex(graph(topo.nodes, topo.edges)), { uri: '/tc-fostay', environment: null });
  assert.equal(result.status, 'found');
  assert.ok(result.matches.every(match => match.file === ''));
  assert.ok(result.warnings.some(w => w.includes('配置文件') && w.includes('重新生成拓扑')));
});

test('asset without a HOST node reports the unlocated machine', async () => {
  const { findNginxLocations } = await framework();
  const { buildGraphIndex } = await import('../lib/topology/path-explorer.ts');
  const route = nginxRoute('asset-x', 'r1', {
    domains: ['fostay.example.com'], uri: '/tc-fostay', target: 'http://fostay_backend/', upstreamName: 'fostay_backend',
    file: '/etc/nginx/conf.d/fostay.conf',
  });
  const up = upstream('asset-x', 'fostay_backend');
  const backend = endpoint('10.1.1.9', '8080', 'http');
  const appParts = wireApp('app-a', 'FoStay Service', 'PRODUCT', backend);
  const result = findNginxLocations(
    buildGraphIndex(graph([route, up, backend, ...appParts.nodes], [edge('uu1', route.id, up.id, 'USES_UPSTREAM'), edge('ft1', up.id, backend.id, 'FORWARDS_TO'), ...appParts.edges])),
    { uri: '/tc-fostay', environment: null },
  );
  assert.equal(result.status, 'found');
  assert.equal(result.matches[0].host, null);
  assert.ok(result.warnings.some(w => w.includes('asset-x') && w.includes('不在主机清单')));
});

test('empty query is rejected with a warning instead of matching everything', async () => {
  const { findNginxLocations } = await framework();
  const { buildGraphIndex } = await import('../lib/topology/path-explorer.ts');
  const topo = baseFixture();
  const result = findNginxLocations(buildGraphIndex(graph(topo.nodes, topo.edges)), { uri: '   ', environment: null });
  assert.equal(result.status, 'not-found');
  assert.deepEqual(result.matches, []);
  assert.ok(result.warnings.some(w => w.includes('查询为空')));
});

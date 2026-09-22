/* eslint-disable typescript/no-require-imports -- Node test harness uses CommonJS, matching the existing suite. */
const { test } = require('node:test');
const assert = require('node:assert/strict');

const OBSERVED_AT = '2026-09-20T00:00:00.000Z';
const evidence = detail => [{ source: 'nginx-config', sourceId: detail, reference: 'tests/domain-chain.test.cjs', detail, observedAt: OBSERVED_AT }];

const node = (id, type, label, extra = {}) => ({
  id,
  type,
  label,
  identity: extra.identity || {},
  status: extra.status || 'active',
  environment: extra.environment || 'GLOBAL',
  evidence: evidence(`node ${id}`),
  attributes: extra.attributes || {},
});

const edge = (id, from, to, type, extra = {}) => ({
  id,
  from,
  to,
  type,
  environment: extra.environment || 'GLOBAL',
  evidence: evidence(`edge ${id}`),
  confidence: extra.confidence || 'EXACT',
  observedAt: OBSERVED_AT,
});

/**
 * Mirrors the real apis.folidaymall.com shape, including the bug this fixes:
 * nginx forwards to 10.0.2.146:7006, while a DIFFERENT app listens on :6001 of
 * the same host. The staged projection must match 7006 -> app 1374 and never
 * join "through the host" to app 1376. The forwarded endpoint is tagged
 * protocol=http while the deployment endpoint is tagged unknown.
 */
const mainFixture = () => {
  const eip = 'eip:1.1.1.1';
  const frontHost = 'host:10.0.1.10';
  const backHost = 'host:10.0.2.146';
  const route = 'nginx-route:asset-1:cashier';
  const upstream = 'nginx-upstream:asset-1:cashierserver';
  const epForward = 'endpoint:10.0.2.146:7006:http';
  const epListener = 'endpoint:10.0.2.146:7006:unknown';
  const epOther = 'endpoint:10.0.2.146:6001:unknown';
  const dep1374 = 'deployment:1374:PRODUCT:d1';
  const dep1376 = 'deployment:1376:PRODUCT:d2';
  return {
    generatedAt: OBSERVED_AT,
    nodes: [
      node('domain:api.example.com', 'DOMAIN', 'api.example.com', { identity: { name: 'api.example.com' } }),
      node(eip, 'EIP', '1.1.1.1', { identity: { ip: '1.1.1.1', allocationId: 'eip-1' } }),
      node(frontHost, 'HOST', '10.0.1.10', {
        identity: { ip: '10.0.1.10', jumpserverAssetIds: ['asset-1'] },
        attributes: { ecs: { name: 'nginx-box', id: 'i-front' } },
      }),
      node(route, 'NGINX_ROUTE', 'api.example.com/cashier-api/', {
        identity: { assetId: 'asset-1', domains: ['api.example.com'], uri: '/cashier-api/', directive: 'proxy_pass', target: 'http://cashierserver/' },
        attributes: { nginxStatus: 'complete', configurationVersion: '20260920-1', listen: ['80'], context: 'http > server' },
      }),
      node(upstream, 'UPSTREAM', 'cashierserver', { identity: { assetId: 'asset-1', name: 'cashierserver' } }),
      node(epForward, 'ENDPOINT', '10.0.2.146:7006:http', { identity: { ip: '10.0.2.146', port: '7006', protocol: 'http' } }),
      node(epListener, 'ENDPOINT', '10.0.2.146:7006:unknown', { identity: { ip: '10.0.2.146', port: '7006', protocol: 'unknown' } }),
      node(epOther, 'ENDPOINT', '10.0.2.146:6001:unknown', { identity: { ip: '10.0.2.146', port: '6001', protocol: 'unknown' } }),
      node(backHost, 'HOST', '10.0.2.146', { identity: { ip: '10.0.2.146' }, attributes: { ecs: { name: 'app-box' } } }),
      node(dep1374, 'DEPLOYMENT', 'cashier-api PRODUCT', { identity: { appId: '1374', env: 'PRODUCT', deployId: 'd1', ip: '10.0.2.146', port: '7006' }, environment: 'PRODUCT' }),
      node(dep1376, 'DEPLOYMENT', 'other-app PRODUCT', { identity: { appId: '1376', env: 'PRODUCT', deployId: 'd2', ip: '10.0.2.146', port: '6001' }, environment: 'PRODUCT' }),
      node('application:1374', 'APPLICATION', 'cashier-api', { identity: { devopsAppId: '1374' } }),
      node('application:1376', 'APPLICATION', 'other-app', { identity: { devopsAppId: '1376' } }),
    ],
    edges: [
      edge('e-resolve', 'domain:api.example.com', eip, 'RESOLVES_TO'),
      edge('e-bind', eip, frontHost, 'BOUND_TO'),
      edge('e-served', 'domain:api.example.com', route, 'SERVED_BY'),
      edge('e-upstream', route, upstream, 'USES_UPSTREAM'),
      edge('e-fwd', upstream, epForward, 'FORWARDS_TO'),
      edge('e-onhost-fwd', epForward, backHost, 'ON_HOST'),
      edge('e-listen-1374', dep1374, epListener, 'LISTENS_ON', { environment: 'PRODUCT' }),
      edge('e-listen-1376', dep1376, epOther, 'LISTENS_ON', { environment: 'PRODUCT' }),
      edge('e-has-1374', 'application:1374', dep1374, 'HAS_DEPLOYMENT', { environment: 'PRODUCT' }),
      edge('e-has-1376', 'application:1376', dep1376, 'HAS_DEPLOYMENT', { environment: 'PRODUCT' }),
    ],
    stats: {},
  };
};

const runChain = async (topology, query) => {
  const { findDomainChain } = await import('../lib/topology/domain-chain.ts');
  return findDomainChain(topology, query);
};

test('staged chain resolves DNS -> EIP/ECS -> nginx -> upstream -> app by IP+port', async () => {
  const result = await runChain(mainFixture(), { domain: 'api.example.com', environment: 'PRODUCT' });
  assert.equal(result.resolution.status, 'found');
  assert.equal(result.status, 'resolved');

  // Stage 1+2.
  assert.equal(result.entries.length, 1);
  const entry = result.entries[0];
  assert.equal(entry.targetKind, 'eip');
  assert.equal(entry.target.id, 'eip:1.1.1.1');
  assert.equal(entry.binding?.kind, 'ecs-host');
  assert.equal(entry.binding?.host?.id, 'host:10.0.1.10');
  assert.deepEqual(entry.dnat, []);

  // Stage 3: asset folded into the owning HOST.
  assert.equal(result.nginxHosts.length, 1);
  const nh = result.nginxHosts[0];
  assert.equal(nh.assetId, 'asset-1');
  assert.equal(nh.host?.id, 'host:10.0.1.10');
  assert.equal(nh.nginxStatus, 'complete');
  assert.deepEqual(nh.listen, ['80']);
  assert.equal(nh.routeCount, 1);
  assert.equal(nh.totalRouteCount, 1);

  // Stage 4+5: one group, one backend, exact match.
  assert.equal(result.groups.length, 1);
  const group = result.groups[0];
  assert.equal(group.kind, 'proxy');
  assert.equal((group.upstream.identity).name, 'cashierserver');
  assert.deepEqual(group.uris, ['/cashier-api/']);
  assert.equal(group.uriCount, 1);
  assert.equal(group.backends.length, 1);
  const backend = group.backends[0];
  assert.equal(backend.status, 'exact');
  assert.equal((backend.endpoint.identity).port, '7006');
  assert.equal(backend.matches.length, 1);
  assert.equal(backend.matches[0].application.id, 'application:1374');
  assert.equal(backend.matches[0].application.label, 'cashier-api');
  assert.equal(backend.matches[0].environment, 'PRODUCT');
  // The forwarded endpoint says http, the listener endpoint says unknown.
  assert.equal(backend.matches[0].protocolMismatch, true);
  // The same-host :6001 app is never the answer and is not even hinted.
  assert.deepEqual(backend.sameHostCandidates, []);
  assert.ok(!result.stats.applicationIds.includes('application:1376'));

  // The EIP binds directly to the nginx box, so entry/nginx layers are connected.
  assert.equal(result.connected, true);
  assert.deepEqual(result.stats, {
    routeCount: 1,
    upstreamCount: 1,
    backendCount: 1,
    exactBackendCount: 1,
    ambiguousBackendCount: 0,
    unresolvedBackendCount: 0,
    applicationIds: ['application:1374'],
  });
  assert.ok(result.gaps.some(gap => gap.includes('bound directly to ECS host')));
});

test('unknown domain is reported unresolved without fabrication', async () => {
  const result = await runChain(mainFixture(), { domain: 'nope.example.com' });
  assert.equal(result.resolution.status, 'not-found');
  assert.equal(result.status, 'unresolved');
  assert.deepEqual(result.entries, []);
  assert.deepEqual(result.groups, []);
  assert.ok(result.gaps.some(gap => gap.includes('No DOMAIN matches')));
});

test('routes for other domains on the same nginx asset are excluded by identity.domains filter', async () => {
  // Two domains on the same nginx asset. api.example.com has 1 route;
  // other.example.com has its own route with a different upstream/backend.
  // Even if the topology builder erroneously creates a SERVED_BY edge from
  // domain:api.example.com to the other-domain route, the domains filter
  // must exclude it.
  const topology = {
    generatedAt: OBSERVED_AT,
    nodes: [
      node('domain:api.example.com', 'DOMAIN', 'api.example.com', { identity: { name: 'api.example.com' } }),
      node('domain:other.example.com', 'DOMAIN', 'other.example.com', { identity: { name: 'other.example.com' } }),
      node('eip:8.8.8.8', 'EIP', '8.8.8.8', { identity: { ip: '8.8.8.8' } }),
      node('host:10.0.1.10', 'HOST', 'h', { identity: { ip: '10.0.1.10', jumpserverAssetIds: ['asset-9'] } }),
      // Route for api.example.com
      node('nginx-route:asset-9:api', 'NGINX_ROUTE', 'api.example.com/api', {
        identity: { assetId: 'asset-9', domains: ['api.example.com'], uri: '/api/', directive: 'proxy_pass', target: 'http://api_backend/' },
        attributes: { nginxStatus: 'complete', listen: ['80'] },
      }),
      // Route for other.example.com — different domain, different upstream
      node('nginx-route:asset-9:other', 'NGINX_ROUTE', 'other.example.com/other', {
        identity: { assetId: 'asset-9', domains: ['other.example.com'], uri: '/other/', directive: 'proxy_pass', target: 'http://other_backend/' },
        attributes: { nginxStatus: 'complete', listen: ['80'] },
      }),
      node('nginx-upstream:asset-9:api_backend', 'UPSTREAM', 'api_backend', { identity: { assetId: 'asset-9', name: 'api_backend' } }),
      node('nginx-upstream:asset-9:other_backend', 'UPSTREAM', 'other_backend', { identity: { assetId: 'asset-9', name: 'other_backend' } }),
      node('endpoint:10.0.5.1:7000:http', 'ENDPOINT', '10.0.5.1:7000', { identity: { ip: '10.0.5.1', port: '7000', protocol: 'http' } }),
      node('endpoint:10.0.5.2:8000:http', 'ENDPOINT', '10.0.5.2:8000', { identity: { ip: '10.0.5.2', port: '8000', protocol: 'http' } }),
      node('host:10.0.5.1', 'HOST', '10.0.5.1', { identity: { ip: '10.0.5.1' } }),
      node('host:10.0.5.2', 'HOST', '10.0.5.2', { identity: { ip: '10.0.5.2' } }),
      node('deployment:1:PRODUCT:d', 'DEPLOYMENT', 'app1', { identity: { appId: '1' }, environment: 'PRODUCT' }),
      node('application:1', 'APPLICATION', 'app1', { identity: { devopsAppId: '1' } }),
    ],
    edges: [
      edge('r-res', 'domain:api.example.com', 'eip:8.8.8.8', 'RESOLVES_TO'),
      edge('r-bind', 'eip:8.8.8.8', 'host:10.0.1.10', 'BOUND_TO'),
      // SERVED_BY from api.example.com to its own route (correct)
      edge('r-served-api', 'domain:api.example.com', 'nginx-route:asset-9:api', 'SERVED_BY'),
      // Spurious SERVED_BY from api.example.com to the other-domain route (builder bug)
      edge('r-served-bug', 'domain:api.example.com', 'nginx-route:asset-9:other', 'SERVED_BY'),
      // SERVED_BY from other.example.com to its own route (correct, but irrelevant)
      edge('r-served-other', 'domain:other.example.com', 'nginx-route:asset-9:other', 'SERVED_BY'),
      edge('r-up-api', 'nginx-route:asset-9:api', 'nginx-upstream:asset-9:api_backend', 'USES_UPSTREAM'),
      edge('r-up-other', 'nginx-route:asset-9:other', 'nginx-upstream:asset-9:other_backend', 'USES_UPSTREAM'),
      edge('r-fwd-api', 'nginx-upstream:asset-9:api_backend', 'endpoint:10.0.5.1:7000:http', 'FORWARDS_TO'),
      edge('r-fwd-other', 'nginx-upstream:asset-9:other_backend', 'endpoint:10.0.5.2:8000:http', 'FORWARDS_TO'),
      edge('r-oh-api', 'endpoint:10.0.5.1:7000:http', 'host:10.0.5.1', 'ON_HOST'),
      edge('r-oh-other', 'endpoint:10.0.5.2:8000:http', 'host:10.0.5.2', 'ON_HOST'),
      edge('r-listen', 'deployment:1:PRODUCT:d', 'endpoint:10.0.5.1:7000:http', 'LISTENS_ON', { environment: 'PRODUCT' }),
      edge('r-has', 'application:1', 'deployment:1:PRODUCT:d', 'HAS_DEPLOYMENT', { environment: 'PRODUCT' }),
    ],
    stats: {},
  };
  const result = await runChain(topology, { domain: 'api.example.com', environment: 'PRODUCT' });
  // Only the api route should be collected; the other-domain route is filtered out.
  assert.equal(result.stats.routeCount, 1);
  assert.equal(result.nginxHosts[0].routeCount, 1);
  assert.equal(result.nginxHosts[0].totalRouteCount, 2);
  // Only the api_backend group and its 10.0.5.1:7000 endpoint should appear.
  assert.equal(result.groups.length, 1);
  assert.equal((result.groups[0].upstream.identity).name, 'api_backend');
  assert.equal(result.stats.backendCount, 1);
  assert.equal((result.groups[0].backends[0].endpoint.identity).ip, '10.0.5.1');
  // The other-domain endpoint 10.0.5.2:8000 must NOT appear anywhere.
  assert.ok(!result.gaps.some(gap => gap.includes('10.0.5.2')));
});

test('unresolved backend keeps same-host other-port deployments as hints only and flags the entry/nginx gap', async () => {
  const topology = {
    generatedAt: OBSERVED_AT,
    nodes: [
      node('domain:broken.example.com', 'DOMAIN', 'broken.example.com', { identity: { name: 'broken.example.com' } }),
      node('eip:2.2.2.2', 'EIP', '2.2.2.2', { identity: { ip: '2.2.2.2' } }),
      node('host:10.0.9.9', 'HOST', 'front', { identity: { ip: '10.0.9.9' } }),
      node('host:10.0.9.8', 'HOST', 'nginx8', { identity: { ip: '10.0.9.8', jumpserverAssetIds: ['asset-2'] } }),
      node('host:10.0.8.8', 'HOST', 'mid', { identity: { ip: '10.0.8.8' } }),
      node('nginx-route:asset-2:r2', 'NGINX_ROUTE', 'r2', {
        identity: { assetId: 'asset-2', uri: '/kong/', directive: 'proxy_pass', target: 'http://kong_servers/' },
        attributes: { nginxStatus: 'complete', listen: ['80'] },
      }),
      node('nginx-upstream:asset-2:kong', 'UPSTREAM', 'kong_servers', { identity: { assetId: 'asset-2', name: 'kong_servers' } }),
      node('endpoint:10.0.8.8:8000:http', 'ENDPOINT', '10.0.8.8:8000', { identity: { ip: '10.0.8.8', port: '8000', protocol: 'http' } }),
      node('endpoint:10.0.8.8:9000:unknown', 'ENDPOINT', '10.0.8.8:9000', { identity: { ip: '10.0.8.8', port: '9000', protocol: 'unknown' } }),
      node('deployment:55:PRODUCT:x', 'DEPLOYMENT', 'hint-app', { identity: { appId: '55', env: 'PRODUCT', deployId: 'x', ip: '10.0.8.8', port: '9000' }, environment: 'PRODUCT' }),
      node('application:55', 'APPLICATION', 'hint-app', { identity: { devopsAppId: '55' } }),
    ],
    edges: [
      edge('e-resolve', 'domain:broken.example.com', 'eip:2.2.2.2', 'RESOLVES_TO'),
      edge('e-bind', 'eip:2.2.2.2', 'host:10.0.9.9', 'BOUND_TO'),
      edge('e-served', 'domain:broken.example.com', 'nginx-route:asset-2:r2', 'SERVED_BY'),
      edge('e-upstream', 'nginx-route:asset-2:r2', 'nginx-upstream:asset-2:kong', 'USES_UPSTREAM'),
      edge('e-fwd', 'nginx-upstream:asset-2:kong', 'endpoint:10.0.8.8:8000:http', 'FORWARDS_TO'),
      edge('e-onhost', 'endpoint:10.0.8.8:8000:http', 'host:10.0.8.8', 'ON_HOST'),
      edge('e-listen', 'deployment:55:PRODUCT:x', 'endpoint:10.0.8.8:9000:unknown', 'LISTENS_ON', { environment: 'PRODUCT' }),
      edge('e-has', 'application:55', 'deployment:55:PRODUCT:x', 'HAS_DEPLOYMENT', { environment: 'PRODUCT' }),
    ],
    stats: {},
  };
  const result = await runChain(topology, { domain: 'broken.example.com', environment: 'PRODUCT' });
  assert.equal(result.connected, false);
  assert.ok(result.gaps.some(gap => gap.includes('not connected') && gap.includes('10.0.9.9') && gap.includes('10.0.9.8')));

  const backend = result.groups[0].backends[0];
  assert.equal(backend.status, 'unresolved');
  assert.deepEqual(backend.matches, []);
  assert.equal(backend.sameHostCandidates.length, 1);
  assert.equal(backend.sameHostCandidates[0].port, '9000');
  assert.equal(backend.sameHostCandidates[0].application.id, 'application:55');
  // Hints do not make the chain "resolved".
  assert.equal(result.status, 'ambiguous');
  assert.ok(result.gaps.some(gap => gap.includes('Backend 10.0.8.8:8000')));
});

test('two applications on one IP+port is AMBIGUOUS, not a silent pick', async () => {
  const ep = 'endpoint:10.0.4.4:8080:http';
  const topology = {
    generatedAt: OBSERVED_AT,
    nodes: [
      node('domain:dual.example.com', 'DOMAIN', 'dual.example.com', { identity: { name: 'dual.example.com' } }),
      node('eip:3.3.3.3', 'EIP', '3.3.3.3', { identity: { ip: '3.3.3.3' } }),
      node('host:10.0.1.10', 'HOST', 'h', { identity: { ip: '10.0.1.10', jumpserverAssetIds: ['asset-a'] } }),
      node('nginx-route:asset-a:r', 'NGINX_ROUTE', 'r', {
        identity: { assetId: 'asset-a', uri: '/x/', directive: 'proxy_pass', target: 'http://dual/' },
        attributes: { nginxStatus: 'complete', listen: ['80'] },
      }),
      node('nginx-upstream:asset-a:dual', 'UPSTREAM', 'dual', { identity: { assetId: 'asset-a', name: 'dual' } }),
      node(ep, 'ENDPOINT', '10.0.4.4:8080', { identity: { ip: '10.0.4.4', port: '8080', protocol: 'http' } }),
      node('host:10.0.4.4', 'HOST', '10.0.4.4', { identity: { ip: '10.0.4.4' } }),
      node('deployment:1:PRODUCT:a', 'DEPLOYMENT', 'app1', { identity: { appId: '1' }, environment: 'PRODUCT' }),
      node('deployment:2:PRODUCT:b', 'DEPLOYMENT', 'app2', { identity: { appId: '2' }, environment: 'PRODUCT' }),
      node('application:1', 'APPLICATION', 'app1', { identity: { devopsAppId: '1' } }),
      node('application:2', 'APPLICATION', 'app2', { identity: { devopsAppId: '2' } }),
    ],
    edges: [
      edge('r1', 'domain:dual.example.com', 'eip:3.3.3.3', 'RESOLVES_TO'),
      edge('r2', 'eip:3.3.3.3', 'host:10.0.1.10', 'BOUND_TO'),
      edge('r3', 'domain:dual.example.com', 'nginx-route:asset-a:r', 'SERVED_BY'),
      edge('r4', 'nginx-route:asset-a:r', 'nginx-upstream:asset-a:dual', 'USES_UPSTREAM'),
      edge('r5', 'nginx-upstream:asset-a:dual', ep, 'FORWARDS_TO'),
      edge('r6', ep, 'host:10.0.4.4', 'ON_HOST'),
      edge('r7', 'deployment:1:PRODUCT:a', ep, 'LISTENS_ON', { environment: 'PRODUCT' }),
      edge('r8', 'deployment:2:PRODUCT:b', ep, 'LISTENS_ON', { environment: 'PRODUCT' }),
      edge('r9', 'application:1', 'deployment:1:PRODUCT:a', 'HAS_DEPLOYMENT', { environment: 'PRODUCT' }),
      edge('r10', 'application:2', 'deployment:2:PRODUCT:b', 'HAS_DEPLOYMENT', { environment: 'PRODUCT' }),
    ],
    stats: {},
  };
  const result = await runChain(topology, { domain: 'dual.example.com', environment: 'PRODUCT' });
  assert.equal(result.status, 'ambiguous');
  const backend = result.groups[0].backends[0];
  assert.equal(backend.status, 'ambiguous');
  assert.deepEqual(backend.matches.map(m => m.application.id).sort(), ['application:1', 'application:2']);
});

test('routes fold into proxy/external/static groups and sort proxy first', async () => {
  const nodes = [
    node('domain:mix.example.com', 'DOMAIN', 'mix.example.com', { identity: { name: 'mix.example.com' } }),
    node('eip:4.4.4.4', 'EIP', '4.4.4.4', { identity: { ip: '4.4.4.4' } }),
    node('host:10.0.1.10', 'HOST', 'h', { identity: { ip: '10.0.1.10', jumpserverAssetIds: ['asset-3'] } }),
  ];
  const edges = [
    edge('m-res', 'domain:mix.example.com', 'eip:4.4.4.4', 'RESOLVES_TO'),
    edge('m-bind', 'eip:4.4.4.4', 'host:10.0.1.10', 'BOUND_TO'),
  ];
  const routes = [
    { id: 'nginx-route:asset-3:proxy', uri: '/api/', directive: 'proxy_pass', target: 'http://svc/', upstream: 'nginx-upstream:asset-3:svc' },
    { id: 'nginx-route:asset-3:ext', uri: '/sys/uaa', directive: 'proxy_pass', target: 'http://uaa.tcc.cn:80', upstream: 'nginx-upstream:asset-3:uaa' },
    { id: 'nginx-route:asset-3:static-1', uri: '/', directive: 'static', target: '/var/www', upstream: 'nginx-upstream:asset-3:static' },
    { id: 'nginx-route:asset-3:static-2', uri: '/health.txt', directive: 'other', target: '-', upstream: 'nginx-upstream:asset-3:static' },
  ];
  for (const r of routes) {
    nodes.push(node(r.id, 'NGINX_ROUTE', r.uri, {
      identity: { assetId: 'asset-3', uri: r.uri, directive: r.directive, target: r.target },
      attributes: { nginxStatus: 'complete', listen: ['80'] },
    }));
    nodes.push(node(r.upstream, 'UPSTREAM', r.upstream, { identity: { assetId: 'asset-3', name: r.upstream.split(':').pop() } }));
    edges.push(edge(`e-s-${r.uri}`, 'domain:mix.example.com', r.id, 'SERVED_BY'));
    edges.push(edge(`e-u-${r.uri}`, r.id, r.upstream, 'USES_UPSTREAM'));
  }
  // Only the proxy group has a concrete IP backend.
  nodes.push(node('endpoint:10.0.5.5:8080:http', 'ENDPOINT', '10.0.5.5:8080', { identity: { ip: '10.0.5.5', port: '8080', protocol: 'http' } }));
  nodes.push(node('host:10.0.5.5', 'HOST', '10.0.5.5', { identity: { ip: '10.0.5.5' } }));
  edges.push(edge('e-fwd', 'nginx-upstream:asset-3:svc', 'endpoint:10.0.5.5:8080:http', 'FORWARDS_TO'));
  edges.push(edge('e-oh', 'endpoint:10.0.5.5:8080:http', 'host:10.0.5.5', 'ON_HOST'));

  const result = await runChain({ generatedAt: OBSERVED_AT, nodes, edges, stats: {} }, { domain: 'mix.example.com' });
  assert.equal(result.stats.routeCount, 4);
  assert.equal(result.nginxHosts[0].routeCount, 4);
  assert.deepEqual(result.groups.map(g => g.kind), ['proxy', 'external', 'static']);
  const [proxy, ext, stat] = result.groups;
  assert.equal(proxy.backends.length, 1);
  assert.equal(proxy.backends[0].status, 'unresolved');
  assert.equal(ext.backends.length, 0);
  assert.equal(ext.targets[0], 'http://uaa.tcc.cn:80');
  assert.equal(stat.backends.length, 0);
  assert.equal(stat.uriCount, 2);
  assert.deepEqual([...stat.uris].sort(), ['/', '/health.txt']);
});

test('CNAME through an external domain reaches the EIP and keeps the external gap', async () => {
  const topology = {
    generatedAt: OBSERVED_AT,
    nodes: [
      node('domain:alias.example.com', 'DOMAIN', 'alias.example.com', { identity: { name: 'alias.example.com' } }),
      node('domain:cdn.external.net', 'DOMAIN', 'cdn.external.net', { identity: { name: 'cdn.external.net' }, status: 'external' }),
      node('eip:5.5.5.5', 'EIP', '5.5.5.5', { identity: { ip: '5.5.5.5' } }),
    ],
    edges: [
      edge('c1', 'domain:alias.example.com', 'domain:cdn.external.net', 'CNAME_TO'),
      edge('c2', 'domain:cdn.external.net', 'eip:5.5.5.5', 'RESOLVES_TO'),
    ],
    stats: {},
  };
  const result = await runChain(topology, { domain: 'alias.example.com' });
  assert.equal(result.cnameChain.length, 1);
  assert.equal(result.cnameChain[0].domain.id, 'domain:cdn.external.net');
  assert.equal(result.entries[0].target.id, 'eip:5.5.5.5');
  assert.ok(result.gaps.some(gap => gap.includes('CNAME target') && gap.includes('external')));
});

test('EIP -> NAT gateway -> DNAT exposes external and internal IP:port', async () => {
  const topology = {
    generatedAt: OBSERVED_AT,
    nodes: [
      node('domain:nat.example.com', 'DOMAIN', 'nat.example.com', { identity: { name: 'nat.example.com' } }),
      node('eip:6.6.6.6', 'EIP', '6.6.6.6', { identity: { ip: '6.6.6.6' } }),
      node('nat-gateway:ngw-1', 'NAT_GATEWAY', 'ngw-1', { identity: { gatewayId: 'ngw-1' } }),
      node('dnat-rule:1', 'DNAT_RULE', '6.6.6.6:443 -> 10.0.5.5:80', { identity: { ruleId: 'dnat-1' } }),
      node('endpoint:6.6.6.6:443:https', 'ENDPOINT', '6.6.6.6:443', { identity: { ip: '6.6.6.6', port: '443', protocol: 'https' } }),
      node('endpoint:10.0.5.5:80:http', 'ENDPOINT', '10.0.5.5:80', { identity: { ip: '10.0.5.5', port: '80', protocol: 'http' } }),
      node('host:10.0.5.5', 'HOST', 'nginx-via-nat', { identity: { ip: '10.0.5.5', jumpserverAssetIds: ['asset-4'] } }),
      node('nginx-route:asset-4:r', 'NGINX_ROUTE', 'r', {
        identity: { assetId: 'asset-4', uri: '/', directive: 'proxy_pass', target: 'http://backend/' },
        attributes: { nginxStatus: 'complete', listen: ['80'] },
      }),
      node('nginx-upstream:asset-4:backend', 'UPSTREAM', 'backend', { identity: { assetId: 'asset-4', name: 'backend' } }),
      node('endpoint:10.0.6.6:7000:http', 'ENDPOINT', '10.0.6.6:7000', { identity: { ip: '10.0.6.6', port: '7000', protocol: 'http' } }),
      node('endpoint:10.0.6.6:7000:unknown', 'ENDPOINT', '10.0.6.6:7000u', { identity: { ip: '10.0.6.6', port: '7000', protocol: 'unknown' } }),
      node('host:10.0.6.6', 'HOST', '10.0.6.6', { identity: { ip: '10.0.6.6' } }),
      node('deployment:900:PRODUCT:p', 'DEPLOYMENT', 'svc PRODUCT', { identity: { appId: '900' }, environment: 'PRODUCT' }),
      node('deployment:900:TEST:t', 'DEPLOYMENT', 'svc TEST', { identity: { appId: '900' }, environment: 'TEST' }),
      node('endpoint:10.0.6.7:7000:unknown', 'ENDPOINT', '10.0.6.7:7000u', { identity: { ip: '10.0.6.7', port: '7000', protocol: 'unknown' } }),
      node('host:10.0.6.7', 'HOST', '10.0.6.7', { identity: { ip: '10.0.6.7' } }),
      node('application:900', 'APPLICATION', 'svc', { identity: { devopsAppId: '900' } }),
    ],
    edges: [
      edge('n-res', 'domain:nat.example.com', 'eip:6.6.6.6', 'RESOLVES_TO'),
      edge('n-bind', 'eip:6.6.6.6', 'nat-gateway:ngw-1', 'BOUND_TO'),
      edge('n-rule', 'nat-gateway:ngw-1', 'dnat-rule:1', 'HAS_DNAT_RULE'),
      edge('n-ext', 'dnat-rule:1', 'endpoint:6.6.6.6:443:https', 'EXPOSES'),
      edge('n-int', 'dnat-rule:1', 'endpoint:10.0.5.5:80:http', 'FORWARDS_TO'),
      edge('n-oh', 'endpoint:10.0.5.5:80:http', 'host:10.0.5.5', 'ON_HOST'),
      edge('n-served', 'domain:nat.example.com', 'nginx-route:asset-4:r', 'SERVED_BY'),
      edge('n-up', 'nginx-route:asset-4:r', 'nginx-upstream:asset-4:backend', 'USES_UPSTREAM'),
      edge('n-fwd-p', 'nginx-upstream:asset-4:backend', 'endpoint:10.0.6.6:7000:http', 'FORWARDS_TO'),
      edge('n-oh-p', 'endpoint:10.0.6.6:7000:http', 'host:10.0.6.6', 'ON_HOST'),
      edge('n-listen-p', 'deployment:900:PRODUCT:p', 'endpoint:10.0.6.6:7000:unknown', 'LISTENS_ON', { environment: 'PRODUCT' }),
      edge('n-has-p', 'application:900', 'deployment:900:PRODUCT:p', 'HAS_DEPLOYMENT', { environment: 'PRODUCT' }),
      edge('n-fwd-t', 'nginx-upstream:asset-4:backend', 'endpoint:10.0.6.7:7000:unknown', 'FORWARDS_TO', { environment: 'TEST' }),
      edge('n-oh-t', 'endpoint:10.0.6.7:7000:unknown', 'host:10.0.6.7', 'ON_HOST'),
      edge('n-listen-t', 'deployment:900:TEST:t', 'endpoint:10.0.6.7:7000:unknown', 'LISTENS_ON', { environment: 'TEST' }),
      edge('n-has-t', 'application:900', 'deployment:900:TEST:t', 'HAS_DEPLOYMENT', { environment: 'TEST' }),
    ],
    stats: {},
  };

  const all = await runChain(topology, { domain: 'nat.example.com' });
  const entry = all.entries[0];
  assert.equal(entry.binding?.kind, 'nat-gateway');
  assert.equal(entry.dnat.length, 1);
  assert.equal((entry.dnat[0].external.identity).port, '443');
  assert.equal((entry.dnat[0].internal.identity).ip, '10.0.5.5');
  assert.equal(entry.dnat[0].internalHost?.id, 'host:10.0.5.5');
  assert.equal(all.connected, true);
  assert.equal(all.stats.exactBackendCount, 2);

  // DNAT rules for a different EIP IP must not attach to this entry.
  const otherDnat = {
    generatedAt: OBSERVED_AT,
    nodes: [
      node('domain:x.example.com', 'DOMAIN', 'x', { identity: { name: 'x.example.com' } }),
      ...topology.nodes,
      node('dnat-rule:2', 'DNAT_RULE', '7.7.7.7:22 -> 10.0.0.1:22', { identity: { ruleId: 'dnat-2' } }),
      node('endpoint:7.7.7.7:22:tcp', 'ENDPOINT', '7.7.7.7:22', { identity: { ip: '7.7.7.7', port: '22', protocol: 'tcp' } }),
      node('endpoint:10.0.0.1:22:tcp', 'ENDPOINT', '10.0.0.1:22', { identity: { ip: '10.0.0.1', port: '22', protocol: 'tcp' } }),
    ],
    edges: [
      edge('x-res', 'domain:x.example.com', 'eip:6.6.6.6', 'RESOLVES_TO'),
      ...topology.edges,
      edge('x-rule', 'nat-gateway:ngw-1', 'dnat-rule:2', 'HAS_DNAT_RULE'),
      edge('x-ext', 'dnat-rule:2', 'endpoint:7.7.7.7:22:tcp', 'EXPOSES'),
      edge('x-int', 'dnat-rule:2', 'endpoint:10.0.0.1:22:tcp', 'FORWARDS_TO'),
    ],
    stats: {},
  };
  const filtered = await runChain(otherDnat, { domain: 'x.example.com' });
  assert.deepEqual(filtered.entries[0].dnat.map(d => d.rule.id), ['dnat-rule:1']);

  // Environment filter keeps GLOBAL stages but restricts deployment matches.
  const product = await runChain(topology, { domain: 'nat.example.com', environment: 'PRODUCT' });
  assert.equal(product.stats.exactBackendCount, 1);
  assert.equal(product.stats.unresolvedBackendCount, 1);
  assert.equal(product.groups[0].backends.find(b => (b.endpoint.identity).ip === '10.0.6.6').status, 'exact');
  const test = await runChain(topology, { domain: 'nat.example.com', environment: 'TEST' });
  assert.equal(test.stats.exactBackendCount, 1);
  assert.equal(test.groups[0].backends.find(b => (b.endpoint.identity).ip === '10.0.6.7').matches[0].environment, 'TEST');
});

test('EIP bound to a CLB lists listener/server-group backends', async () => {
  const topology = {
    generatedAt: OBSERVED_AT,
    nodes: [
      node('domain:clb.example.com', 'DOMAIN', 'clb.example.com', { identity: { name: 'clb.example.com' } }),
      node('eip:7.7.7.7', 'EIP', '7.7.7.7', { identity: { ip: '7.7.7.7' } }),
      node('clb:lb-1', 'CLB', 'lb-1', { identity: { loadBalancerId: 'lb-1', ip: '7.7.7.7' } }),
      node('clb-listener:lb-1:http', 'CLB_LISTENER', 'http:80', { identity: { listenerId: 'l1' } }),
      node('server-group:sg-1', 'SERVER_GROUP', 'sg-1', { identity: { groupId: 'sg-1' } }),
      node('endpoint:10.0.7.7:8080:http', 'ENDPOINT', '10.0.7.7:8080', { identity: { ip: '10.0.7.7', port: '8080', protocol: 'http' } }),
      node('host:10.0.7.7', 'HOST', '10.0.7.7', { identity: { ip: '10.0.7.7' } }),
    ],
    edges: [
      edge('l-res', 'domain:clb.example.com', 'eip:7.7.7.7', 'RESOLVES_TO'),
      edge('l-bind', 'eip:7.7.7.7', 'clb:lb-1', 'BOUND_TO'),
      edge('l-listener', 'clb:lb-1', 'clb-listener:lb-1:http', 'HAS_LISTENER'),
      edge('l-routes', 'clb-listener:lb-1:http', 'server-group:sg-1', 'ROUTES_TO'),
      edge('l-fwd', 'server-group:sg-1', 'endpoint:10.0.7.7:8080:http', 'FORWARDS_TO'),
      edge('l-oh', 'endpoint:10.0.7.7:8080:http', 'host:10.0.7.7', 'ON_HOST'),
    ],
    stats: {},
  };
  const result = await runChain(topology, { domain: 'clb.example.com' });
  const entry = result.entries[0];
  assert.equal(entry.targetKind, 'eip');
  assert.equal(entry.binding?.kind, 'clb');
  assert.equal(entry.binding?.clb?.id, 'clb:lb-1');
  assert.equal(entry.clbBackends.length, 1);
  assert.equal(entry.clbBackends[0].listener.id, 'clb-listener:lb-1:http');
  assert.equal(entry.clbBackends[0].group.id, 'server-group:sg-1');
  assert.equal((entry.clbBackends[0].endpoint.identity).port, '8080');
  assert.equal(entry.clbBackends[0].host?.id, 'host:10.0.7.7');
});

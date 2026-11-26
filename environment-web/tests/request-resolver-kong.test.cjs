const { test } = require('node:test');
const assert = require('node:assert/strict');
const { buildGraphIndex } = require('../lib/topology/path-explorer.ts');
const { KongResolver, KONG_GATEWAYS, KONG_NO_TARGET, kongEnvironmentWarnings, loadKongData } = require('../lib/topology/request/kong.ts');
const graph = { nodes: [], edges: [], stats: {}, generatedAt: '' };
const index = buildGraphIndex(graph);
const gateway = KONG_GATEWAYS.find(item => item.ip === '10.179.4.220');
const query = { scheme: 'http', host: 'upstream', port: 80, path: '/prod/orders/1' };
const context = { current: { type: 'ENDPOINT', identity: { ip: gateway.ip, port: 8000 } }, index, query };
const route = { id: 'r1', name: 'route', paths: ['/prod/'], protocols: ['http'], service: { id: 's1' } };
const service = { id: 's1', name: 'service', host: 'up', protocol: 'http' };
const data = () => ({ gateway, observedAt: '2026-11-13T00:00:00Z', routes: [route], services: { s1: service }, targets: { up: [{ id: 't1', target: '10.1.2.3:8001', weight: 100 }, { target: '10.1.2.4:8001', weight: 0 }] } });

test('gateway mapping and environment mismatch diagnostics use the proxy endpoint', () => {
  const testGateway = KONG_GATEWAYS.find(item => item.environment === 'TEST');
  const paths = [{ nodes: [{ type: 'ENDPOINT', identity: { ip: testGateway.ip, port: testGateway.proxyPort } }] }];
  assert.match(kongEnvironmentWarnings(paths, 'PRODUCT')[0], /TEST/);
  assert.match(kongEnvironmentWarnings(paths, null)[0], /未指定/);
  assert.deepEqual(kongEnvironmentWarnings(paths, 'TEST'), []);
  paths[0].nodes[0].identity.port = 8001;
  assert.deepEqual(kongEnvironmentWarnings(paths, 'PRODUCT'), []);
});

test('Kong follows a unique supported route to positive-weight endpoint with evidence', () => {
  const resolver = new KongResolver(data());
  assert.equal(resolver.canResolve(context), true);
  assert.equal(resolver.canResolve({ ...context, current: { ...context.current, identity: { ip: gateway.ip, port: 8001 } } }), false);
  const result = resolver.resolve(context);
  assert.equal(result.moves.length, 1);
  assert.equal(result.moves[0].node.identity.ip, '10.1.2.3');
  assert.equal(result.confidence, 'INFERRED');
  assert.equal(result.evidence[1].source, 'kong');
});

test('two gateways in one environment resolve from the observation for the reached proxy endpoint', () => {
  const second = KONG_GATEWAYS.find(item => item.ip === '10.179.4.73');
  const resolver = new KongResolver([data(), { ...data(), gateway: second,
    targets: { up: [{ target: '192.0.2.73:8070', weight: 100 }] } }]);
  const secondContext = { ...context, current: { ...context.current, identity: { ip: second.ip, port: second.proxyPort } } };
  const result = resolver.resolve(secondContext);
  assert.equal(result.moves[0].node.identity.ip, '192.0.2.73');
  assert.equal(result.evidence[0].reference, `http://${second.ip}:${second.adminPort}`);
  assert.equal(resolver.resolve(context).moves[0].node.identity.ip, '10.1.2.3');
});

test('Kong leaves competing or request-dependent routes unresolved', () => {
  const ambiguous = data(); ambiguous.routes = [route, { ...route, id: 'r2' }];
  assert.equal(new KongResolver(ambiguous).resolve(context).moves.length, 0);
  const headers = data(); headers.routes = [{ ...route, headers: { token: ['x'] } }];
  assert.equal(new KongResolver(headers).resolve(context).moves.length, 0);
  const regex = data(); regex.routes = [{ ...route, paths: ['~/prod/.*'] }];
  assert.equal(new KongResolver(regex).resolve(context).moves.length, 0);
});

test('only a successful empty Target list reports that Kong has no Target', () => {
  const empty = new KongResolver({ ...data(), targets: { up: [] } }).resolve(context);
  assert.equal(empty.stopReason, KONG_NO_TARGET);
  assert.equal(empty.evidence[0].sourceId, route.id);
  for (const targets of [{}, { up: [{ target: '10.1.2.3:8001', weight: 0 }] },
    { up: [{ target: 'example.test:8001', weight: 100 }] }]) {
    const stopped = new KongResolver({ ...data(), targets }).resolve(context);
    assert.equal(stopped.moves.length, 0);
    assert.equal(stopped.stopReason, undefined);
  }
});

test('Admin failures and mismatched environments do not fabricate targets', async () => {
  const bad = data(); bad.error = 'timeout';
  assert.match(new KongResolver(bad).resolve(context).warnings[0], /timeout/);
  assert.equal(await loadKongData(undefined, [query]), null);
  assert.equal(await loadKongData(gateway, []), null);
});

test('Admin query follows pagination and loads only matching service targets', async () => {
  const originalFetch = global.fetch;
  const calls = [];
  global.fetch = async url => {
    const u = new URL(url); calls.push(u.pathname + u.search);
    if (u.pathname === '/routes' && !u.searchParams.has('offset')) return Response.json({ data: [], next: '/routes?offset=second' });
    if (u.pathname === '/routes') return Response.json({ data: [route], next: null });
    if (u.pathname === '/services/s1') return Response.json(service);
    if (u.pathname === '/upstreams/up/targets') return Response.json({ data: data().targets.up, next: null });
    throw new Error('unexpected request');
  };
  try {
    const observed = await loadKongData(gateway, [query]);
    assert.equal(observed.error, undefined);
    assert.equal(observed.targets.up.length, 2);
    assert.equal(calls.length, 4);
    assert.ok(calls.every(c => !c.startsWith('http')));
  } finally { global.fetch = originalFetch; }
});

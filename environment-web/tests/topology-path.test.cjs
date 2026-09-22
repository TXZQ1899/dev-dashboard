/* eslint-disable typescript/no-require-imports -- Node test harness uses CommonJS, matching the existing suite. */
const { test } = require('node:test');
const assert = require('node:assert/strict');

const OBSERVED_AT = '2026-09-20T00:00:00.000Z';
const evidence = detail => [{ source: 'dns', sourceId: detail, reference: 'tests/topology-path.test.cjs', detail, observedAt: OBSERVED_AT }];

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
 * Small hand-built fixture:
 *   api.example.com --SERVED_BY--> nginx-route --USES_UPSTREAM--> upstream
 *   upstream --FORWARDS_TO--> endpoint 10.0.0.10 (PRODUCT) / 10.0.0.11 (TEST)
 *   endpoints --ON_HOST--> host 10.0.0.10 / 10.0.0.11
 *   deployments --LISTENS_ON--> endpoints, applications --HAS_DEPLOYMENT--> deployments
 * A CNAME cycle (api <-> loop) and an unresolved DNS target are included on purpose.
 */
const fixture = () => {
  const api = 'domain:api.example.com';
  const loop = 'domain:loop.example.com';
  const cdn = 'domain:cdn.example.com';
  const route = 'nginx-route:asset-1:route1';
  const upstream = 'nginx-upstream:asset-1:up1';
  const ep10 = 'endpoint:10.0.0.10:8080:http';
  const ep11 = 'endpoint:10.0.0.11:8080:http';
  const ep20 = 'endpoint:10.0.0.20:9090:http';
  const host10 = 'host:10.0.0.10';
  const host11 = 'host:10.0.0.11';
  const host20 = 'host:10.0.0.20';
  const hostUnresolved = 'host:10.0.0.99';
  const depProd = 'deployment:100:PRODUCT:1';
  const depTest = 'deployment:100:TEST:2';
  const depOther = 'deployment:200:PRODUCT:3';

  return {
    generatedAt: OBSERVED_AT,
    nodes: [
      node(api, 'DOMAIN', 'api.example.com', { identity: { name: 'api.example.com' } }),
      node(loop, 'DOMAIN', 'loop.example.com', { identity: { name: 'loop.example.com' } }),
      node(cdn, 'DOMAIN', 'cdn.example.com', { identity: { name: 'cdn.example.com' }, status: 'external' }),
      node(route, 'NGINX_ROUTE', 'api.example.com/api'),
      node(upstream, 'UPSTREAM', 'order_backend'),
      node(ep10, 'ENDPOINT', '10.0.0.10:8080:http', { identity: { ip: '10.0.0.10', port: '8080', protocol: 'http' } }),
      node(ep11, 'ENDPOINT', '10.0.0.11:8080:http', { identity: { ip: '10.0.0.11', port: '8080', protocol: 'http' } }),
      node(ep20, 'ENDPOINT', '10.0.0.20:9090:http', { identity: { ip: '10.0.0.20', port: '9090', protocol: 'http' } }),
      node(host10, 'HOST', '10.0.0.10', { identity: { ip: '10.0.0.10' }, attributes: { ecs: { name: 'order-node-a' } } }),
      node(host11, 'HOST', '10.0.0.11', { identity: { ip: '10.0.0.11' }, attributes: { ecs: { name: 'order-node-b' } } }),
      node(host20, 'HOST', '10.0.0.20', { identity: { ip: '10.0.0.20' } }),
      node(hostUnresolved, 'HOST', '10.0.0.99', { identity: { ip: '10.0.0.99' }, status: 'unresolved' }),
      node(depProd, 'DEPLOYMENT', 'order-service PRODUCT 1', { identity: { appId: '100', env: 'PRODUCT', deployId: '1', ip: '10.0.0.10', port: '8080' }, environment: 'PRODUCT' }),
      node(depTest, 'DEPLOYMENT', 'order-service TEST 2', { identity: { appId: '100', env: 'TEST', deployId: '2', ip: '10.0.0.11', port: '8080' }, environment: 'TEST' }),
      node(depOther, 'DEPLOYMENT', 'order-service PRODUCT 3', { identity: { appId: '200', env: 'PRODUCT', deployId: '3', ip: '10.0.0.20', port: '9090' }, environment: 'PRODUCT' }),
      node('application:100', 'APPLICATION', 'order-service', { identity: { devopsAppId: '100' } }),
      node('application:200', 'APPLICATION', 'order-service', { identity: { devopsAppId: '200' } }),
    ],
    edges: [
      edge('e-served', api, route, 'SERVED_BY'),
      edge('e-cname-out', api, loop, 'CNAME_TO'),
      edge('e-cname-back', loop, api, 'CNAME_TO'),
      edge('e-resolves', cdn, hostUnresolved, 'RESOLVES_TO'),
      edge('e-upstream', route, upstream, 'USES_UPSTREAM'),
      edge('e-fwd-10', upstream, ep10, 'FORWARDS_TO'),
      edge('e-fwd-11', upstream, ep11, 'FORWARDS_TO'),
      edge('e-onhost-10', ep10, host10, 'ON_HOST'),
      edge('e-onhost-11', ep11, host11, 'ON_HOST'),
      edge('e-listen-prod', depProd, ep10, 'LISTENS_ON', { environment: 'PRODUCT' }),
      edge('e-listen-test', depTest, ep11, 'LISTENS_ON', { environment: 'TEST' }),
      edge('e-listen-other', depOther, ep20, 'LISTENS_ON', { environment: 'PRODUCT', confidence: 'AMBIGUOUS' }),
      edge('e-onhost-20', ep20, host20, 'ON_HOST'),
      edge('e-has-prod', 'application:100', depProd, 'HAS_DEPLOYMENT', { environment: 'PRODUCT' }),
      edge('e-has-test', 'application:100', depTest, 'HAS_DEPLOYMENT', { environment: 'TEST' }),
      edge('e-has-other', 'application:200', depOther, 'HAS_DEPLOYMENT', { environment: 'PRODUCT' }),
    ],
    stats: { nodeCount: 15, edgeCount: 16, ambiguousEdges: 1, unresolvedNodes: 2 },
  };
};

const explore = async (query) => {
  const { buildGraphIndex, explorePaths } = await import('../lib/topology/path-explorer.ts');
  const index = buildGraphIndex(fixture());
  return explorePaths(index, query);
};

/** Shortest paths only: longer graph-valid detours are allowed but not asserted on. */
const shortest = result => {
  const min = Math.min(...result.paths.map(path => path.length));
  return result.paths.filter(path => path.length === min);
};
const terminals = paths => [...new Set(paths.map(path => path.nodes[path.nodes.length - 1].id))].sort();

test('domain -> application returns the complete path with evidence and derived confidence', async () => {
  const result = await explore({ kind: 'domain', query: 'api.example.com' });
  assert.equal(result.resolution.status, 'found');
  assert.equal(result.status, 'ambiguous');
  assert.equal(result.paths.length, 2);

  for (const path of result.paths) {
    // Full path, not just the terminal node.
    assert.equal(path.nodes.length, path.length + 1);
    assert.equal(path.nodes[0].id, 'domain:api.example.com');
    assert.equal(path.nodes[path.nodes.length - 1].type, 'APPLICATION');
    assert.deepEqual(path.edges.map(item => item.type), ['SERVED_BY', 'USES_UPSTREAM', 'FORWARDS_TO', 'LISTENS_ON', 'HAS_DEPLOYMENT']);
    assert.ok(path.edges.every(item => item.evidence.length > 0));
    assert.equal(path.confidence, 'EXACT');
    assert.equal(path.unresolved, false);
  }
  assert.deepEqual(terminals(result.paths), ['application:100']);
  // One path per backend deployment (PRODUCT on .10, TEST on .11).
  assert.deepEqual([...new Set(result.paths.map(path => path.nodes[3].id))].sort(), ['endpoint:10.0.0.10:8080:http', 'endpoint:10.0.0.11:8080:http']);
});

test('domain -> endpoint fans out over multiple upstream backends', async () => {
  const result = await explore({ kind: 'domain', query: 'api.example.com', to: ['ENDPOINT'] });
  assert.equal(result.paths.length, 2);
  assert.deepEqual(terminals(result.paths), ['endpoint:10.0.0.10:8080:http', 'endpoint:10.0.0.11:8080:http']);
  assert.ok(shortest(result).every(path => path.length === 3));
});

test('application -> host resolves every deployment host, and host -> application walks back', async () => {
  const forward = await explore({ kind: 'application', query: '100' });
  assert.equal(forward.resolution.status, 'found');
  assert.deepEqual(terminals(shortest(forward)), ['host:10.0.0.10', 'host:10.0.0.11']);
  assert.ok(shortest(forward).every(path => path.length === 3 && path.nodes[path.nodes.length - 1].type === 'HOST'));

  const backward = await explore({ kind: 'host', query: '10.0.0.11' });
  assert.equal(backward.resolution.status, 'found');
  assert.deepEqual(terminals(backward.paths), ['application:100']);
  assert.deepEqual(backward.paths[0].edges.map(item => item.type), ['ON_HOST', 'LISTENS_ON', 'HAS_DEPLOYMENT']);
  assert.deepEqual(backward.paths[0].edges.map(item => item.id), ['e-onhost-11', 'e-listen-test', 'e-has-test']);
});

test('a name matching several nodes is reported as ambiguous instead of picking one', async () => {
  const result = await explore({ kind: 'application', query: 'order-service' });
  assert.equal(result.resolution.status, 'ambiguous');
  assert.deepEqual(result.resolution.candidates.map(item => item.id), ['application:100', 'application:200']);
  assert.equal(result.status, 'ambiguous');
  assert.ok(result.gaps.some(gap => gap.includes('matched 2')));
  // Every candidate is traversed; the AMBIGUOUS LISTENS_ON edge downgrades only that path.
  assert.ok(result.paths.some(path => path.nodes[path.nodes.length - 1].id === 'host:10.0.0.20'));
  const other = result.paths.find(path => path.nodes[path.nodes.length - 1].id === 'host:10.0.0.20');
  assert.equal(other.confidence, 'AMBIGUOUS');
  assert.deepEqual(other.weakEdges, ['e-listen-other']);
  assert.ok(result.paths.filter(path => path.nodes[path.nodes.length - 1].id !== 'host:10.0.0.20').every(path => path.confidence === 'EXACT'));
});

test('unresolved queries and unresolved data both surface as diagnosable results', async () => {
  const missing = await explore({ kind: 'domain', query: 'missing.example.com' });
  assert.equal(missing.resolution.status, 'not-found');
  assert.equal(missing.status, 'unresolved');
  assert.equal(missing.paths.length, 0);
  assert.ok(missing.gaps.some(gap => gap.includes('No DOMAIN matches')));

  const deadEnd = await explore({ kind: 'host', query: '10.0.0.99' });
  assert.equal(deadEnd.resolution.status, 'found');
  assert.equal(deadEnd.status, 'unresolved');
  assert.ok(deadEnd.gaps.some(gap => gap.includes('No APPLICATION reachable')));
  // The index walks both directions, so the DNS referrer is reachable even from a dead end.
  assert.deepEqual(deadEnd.reachableTypes, ['DOMAIN']);

  // A path that exists but crosses an unresolved node is returned and flagged.
  const external = await explore({ kind: 'domain', query: 'cdn.example.com', to: ['HOST'] });
  assert.equal(external.paths.length, 1);
  assert.equal(external.paths[0].unresolved, true);
  assert.equal(external.paths[0].nodes[1].status, 'unresolved');
});

test('cycles terminate and every returned path stays simple, within maxDepth', async () => {
  const result = await explore({ kind: 'domain', query: 'loop.example.com' });
  assert.ok(result.paths.length >= 1);
  for (const path of result.paths) {
    assert.equal(new Set(path.nodes.map(item => item.id)).size, path.nodes.length);
    assert.ok(path.length <= 8);
  }
  assert.ok(result.paths.some(path => path.nodes[path.nodes.length - 1].id === 'application:100'));

  const shallow = await explore({ kind: 'domain', query: 'api.example.com', maxDepth: 4 });
  assert.equal(shallow.status, 'unresolved');
  assert.equal(shallow.paths.length, 0);
  assert.ok(shallow.gaps.some(gap => gap.includes('maxDepth=4')));
});

test('environment filter restricts env-specific edges while keeping GLOBAL links traversable', async () => {
  const product = await explore({ kind: 'application', query: '100', environment: 'PRODUCT' });
  assert.deepEqual(terminals(shortest(product)), ['host:10.0.0.10']);
  assert.ok(product.paths.every(path => path.environments.every(env => env === 'GLOBAL' || env === 'PRODUCT')));
  assert.ok(product.paths.some(path => path.edges.some(item => item.id === 'e-listen-prod')));

  const test = await explore({ kind: 'application', query: '100', environment: 'TEST' });
  assert.deepEqual(terminals(shortest(test)), ['host:10.0.0.11']);
  assert.ok(test.paths.every(path => path.environments.every(env => env === 'GLOBAL' || env === 'TEST')));
  assert.ok(test.paths.some(path => path.edges.some(item => item.id === 'e-listen-test')));

  const productOnly = await explore({ kind: 'application', query: '200', environment: 'TEST' });
  assert.equal(productOnly.status, 'unresolved');
  assert.equal(productOnly.paths.length, 0);
});

test('graph index is built once and reused across queries without rescanning edges', async () => {
  const { buildGraphIndex, explorePaths, resolveNodes } = await import('../lib/topology/path-explorer.ts');
  const harness = fixture();
  const index = buildGraphIndex(harness);
  assert.equal(index.nodeById.size, harness.nodes.length);
  assert.ok(index.adjacency.size > 0);
  // Neighbour lists are precomputed for both directions of every edge.
  assert.equal([...index.adjacency.values()].reduce((total, list) => total + list.length, 0), harness.edges.length * 2);
  assert.deepEqual(resolveNodes(index, 'host', 'order-node-a').candidates.map(item => item.id), ['host:10.0.0.10']);
  assert.equal(explorePaths(index, { kind: 'domain', query: 'api.example.com', to: ['ENDPOINT'] }).paths.length, 2);
  assert.equal(explorePaths(index, { kind: 'host', query: '10.0.0.11' }).paths[0].nodes[0].id, 'host:10.0.0.11');
});

// The runtime service reads this CLI's stdout through a pipe. A payload larger
// than the 64KB pipe buffer used to arrive truncated because the script called
// process.exit() while stdout was still buffered.
test('CLI --json stays parseable when output exceeds the pipe buffer', () => {
  const { spawnSync } = require('node:child_process');
  const fs = require('node:fs');
  const os = require('node:os');
  const path = require('node:path');

  const host = '10.0.0.1';
  const nodes = [node(`host:${host}`, 'HOST', '10.0.0.1', { identity: { ip: host } })];
  const edges = [];
  // 300 deployments on one host: 300 distinct host -> application paths, each
  // carrying node and edge evidence well past 64KB once serialized.
  for (let index = 0; index < 300; index += 1) {
    const ip = `10.1.${Math.floor(index / 250)}.${index % 250}`;
    const endpoint = `endpoint:${ip}:8080:http`;
    nodes.push(node(endpoint, 'ENDPOINT', `${ip}:8080`, { identity: { ip, port: '8080', protocol: 'http' } }));
    nodes.push(node(`deployment:${index}`, 'DEPLOYMENT', `app-${index}`, { identity: { appId: String(index) } }));
    nodes.push(node(`application:${index}`, 'APPLICATION', `app-${index}`, { identity: { devopsAppId: String(index) } }));
    edges.push(edge(`e-${index}-onhost`, endpoint, `host:${host}`, 'ON_HOST'));
    edges.push(edge(`e-${index}-listen`, `deployment:${index}`, endpoint, 'LISTENS_ON'));
    edges.push(edge(`e-${index}-has`, `application:${index}`, `deployment:${index}`, 'HAS_DEPLOYMENT'));
  }
  const file = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'envscope-path-')), 'topology.json');
  fs.writeFileSync(file, JSON.stringify({ generatedAt: OBSERVED_AT, nodes, edges, stats: {} }));

  const run = spawnSync(process.execPath, [
    'scripts/topology-path.mjs', 'host', host, '--json',
    '--file', file, '--max-paths', '300', '--max-depth', '4',
  ], { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 });
  assert.equal(run.status, 0, run.stderr);
  assert.ok(run.stdout.length > 65536, `payload should exceed the pipe buffer, got ${run.stdout.length} bytes`);

  const payload = JSON.parse(run.stdout);
  assert.equal(payload.paths.length, 300);
  assert.equal(payload.paths.at(-1).nodes.length, payload.paths.at(-1).length + 1);
  assert.ok(payload.paths.every(found => found.edges.every(item => item.evidence.length > 0)));
});

// In the container the CLI must read the same active version as the runtime
// service (the data volume), never the repo's checked-in snapshot.
test('target-directed pruning ignores large target-free fan-out from a hub start', async () => {
  const { buildGraphIndex, explorePaths } = await import('../lib/topology/path-explorer.ts');
  const topology = { generatedAt: OBSERVED_AT, nodes: [], edges: [], stats: {} };
  const hub = 'domain:hub.example.com';
  topology.nodes.push(node(hub, 'DOMAIN', 'hub.example.com', { identity: { name: 'hub.example.com' } }));

  // 800 dead-end CNAME leaves: connected to the hub but never to an application.
  for (let i = 0; i < 800; i += 1) {
    const id = `domain:leaf-${i}.example.com`;
    topology.nodes.push(node(id, 'DOMAIN', `leaf-${i}.example.com`, { identity: { name: `leaf-${i}.example.com` } }));
    topology.edges.push(edge(`e-leaf-${i}`, hub, id, 'CNAME_TO'));
  }
  // A cloud dead-end: hub -> eip -> host without any deployment.
  topology.nodes.push(node('eip:1.2.3.4', 'EIP', '1.2.3.4', { identity: { publicIp: '1.2.3.4' } }));
  topology.nodes.push(node('host:10.9.9.9', 'HOST', '10.9.9.9', { identity: { ip: '10.9.9.9' } }));
  topology.edges.push(edge('e-hub-eip', hub, 'eip:1.2.3.4', 'RESOLVES_TO'));
  topology.edges.push(edge('e-eip-host', 'eip:1.2.3.4', 'host:10.9.9.9', 'BINDS_EIP'));

  // One correct branch of length 5 to a single application.
  const chainNodes = [
    ['nginx-route:a:r', 'NGINX_ROUTE', 'route'],
    ['nginx-upstream:a:u', 'UPSTREAM', 'up'],
    ['endpoint:10.9.0.1:8080:http', 'ENDPOINT', '10.9.0.1:8080'],
    ['deployment:1:PRODUCT:1', 'DEPLOYMENT', 'dep'],
    ['application:1', 'APPLICATION', 'svc'],
  ];
  for (const [id, type, label] of chainNodes) topology.nodes.push(node(id, type, label));
  topology.edges.push(edge('e-chain-1', hub, 'nginx-route:a:r', 'SERVED_BY'));
  topology.edges.push(edge('e-chain-2', 'nginx-route:a:r', 'nginx-upstream:a:u', 'USES_UPSTREAM'));
  topology.edges.push(edge('e-chain-3', 'nginx-upstream:a:u', 'endpoint:10.9.0.1:8080:http', 'FORWARDS_TO'));
  topology.edges.push(edge('e-chain-4', 'deployment:1:PRODUCT:1', 'endpoint:10.9.0.1:8080:http', 'LISTENS_ON', { environment: 'PRODUCT' }));
  topology.edges.push(edge('e-chain-5', 'application:1', 'deployment:1:PRODUCT:1', 'HAS_DEPLOYMENT', { environment: 'PRODUCT' }));

  const index = buildGraphIndex(topology);
  const result = explorePaths(index, { kind: 'domain', query: 'hub.example.com', maxDepth: 8, maxPaths: 10 });
  assert.equal(result.status, 'resolved');
  assert.equal(result.paths.length, 1);
  const only = result.paths[0];
  assert.equal(only.nodes[only.nodes.length - 1].id, 'application:1');
  assert.equal(only.length, 5);
  assert.ok(!only.nodes.some(item => item.id.startsWith('domain:leaf-')), 'decoy leaves must not appear on the answer');
  assert.deepEqual(only.edges.map(item => item.type), ['SERVED_BY', 'USES_UPSTREAM', 'FORWARDS_TO', 'LISTENS_ON', 'HAS_DEPLOYMENT']);
  assert.deepEqual(result.gaps, []);

  // The only HOST on the graph is the cloud dead-end branch; pruning still finds it.
  const hosts = explorePaths(index, { kind: 'domain', query: 'hub.example.com', to: ['HOST'], maxDepth: 8, maxPaths: 10 });
  assert.deepEqual(terminals(hosts.paths), ['host:10.9.9.9']);

  // Env filtering changes connectivity: the PRODUCT branch disappears under TEST.
  const testOnly = explorePaths(index, { kind: 'domain', query: 'hub.example.com', environment: 'TEST' });
  assert.equal(testOnly.status, 'unresolved');
  assert.equal(testOnly.paths.length, 0);
});

test('CLI resolves the active topology from ENVSCOPE_DATA when no --file is given', () => {
  const { spawnSync } = require('node:child_process');
  const fs = require('node:fs');
  const os = require('node:os');
  const path = require('node:path');

  const harness = fixture();
  const data = fs.mkdtempSync(path.join(os.tmpdir(), 'envscope-data-'));
  // A stale copy in the repo-style location must not win over the active version.
  fs.mkdirSync(path.join(data, 'topology', 'topology-e2e'), { recursive: true });
  fs.writeFileSync(path.join(data, 'topology', 'topology-e2e', 'topology.json'), JSON.stringify(harness));
  fs.writeFileSync(path.join(data, 'topology', 'latest.json'), JSON.stringify({ id: 'topology-e2e' }));

  const run = spawnSync(process.execPath, ['scripts/topology-path.mjs', 'domain', 'api.example.com', '--json'], {
    encoding: 'utf8',
    env: { ...process.env, ENVSCOPE_DATA: data },
    maxBuffer: 64 * 1024 * 1024,
  });
  assert.equal(run.status, 0, run.stderr);
  const payload = JSON.parse(run.stdout);
  assert.equal(payload.paths.length, 2);
  assert.equal(payload.paths[0].nodes[0].id, 'domain:api.example.com');

  // Without a generated Topology the CLI must say so instead of guessing.
  fs.rmSync(path.join(data, 'topology', 'latest.json'));
  const missing = spawnSync(process.execPath, ['scripts/topology-path.mjs', 'domain', 'api.example.com'], {
    encoding: 'utf8',
    env: { ...process.env, ENVSCOPE_DATA: data },
  });
  assert.equal(missing.status, 2);
  assert.match(missing.stderr, /latest\.json/);
  assert.match(missing.stderr, /Generate a Topology first or pass --file/);
});

test('CLI and runtime service agree on which file answers a query', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const service = fs.readFileSync('runtime/service.py', 'utf8');
  const cli = fs.readFileSync('scripts/topology-path.mjs', 'utf8');
  for (const [name, source] of [['service.py', service], ['topology-path.mjs', cli]]) {
    assert.match(source, /latest\.json/, `${name} 应读取 latest.json 激活指针`);
  }
  assert.match(service, /ENVSCOPE_DATA/);
  assert.match(cli, /process\.env\.ENVSCOPE_DATA/);
  assert.match(service, /'--file'/, 'service.py 应显式把激活版本文件传给 CLI');
});
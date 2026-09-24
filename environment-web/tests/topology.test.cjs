/* eslint-disable typescript/no-require-imports -- Node test harness uses CommonJS, matching the existing suite. */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const readJson = file => JSON.parse(fs.readFileSync(path.join(root, 'lib', file), 'utf8'));
const snapshotInput = () => ({
  devops: readJson('snapshot.json'),
  repositories: readJson('repositories.json'),
  dns: readJson('dns-snapshot.json'),
  eip: readJson('eip-snapshot.json'),
  nat: readJson('nat-snapshot.json'),
  ecs: readJson('ecs-snapshot.json'),
  clb: readJson('clb-snapshot.json'),
  jumpserver: readJson('jumpserver-snapshot.json'),
});

test('real snapshots build a validated topology with evidence on every edge', async () => {
  const { buildValidatedTopology } = await import('../lib/topology/topology-builder.ts');
  const { topology, validation } = buildValidatedTopology(snapshotInput(), '2026-09-20T00:00:00.000Z');
  assert.equal(validation.valid, true);
  assert.ok(topology.nodes.length > 1000);
  assert.ok(topology.edges.length > 1000);
  assert.ok(topology.edges.every(edge => edge.evidence.length > 0));
  assert.ok(topology.nodes.every(node => node.evidence.length > 0));
  assert.equal(new Set(topology.nodes.map(node => node.id)).size, topology.nodes.length);
  assert.equal(new Set(topology.edges.map(edge => edge.id)).size, topology.edges.length);
  assert.equal(topology.stats.nodeCount, topology.nodes.length);
  assert.equal(topology.stats.edgeCount, topology.edges.length);
  assert.equal(topology.stats.ambiguousEdges, topology.edges.filter(edge => edge.confidence === 'AMBIGUOUS').length);
  assert.ok(topology.stats.ambiguousEdges > 0);
});

test('ECS, JumpServer and DevOps IPs merge into one host with all evidence', async () => {
  const { buildTopology } = await import('../lib/topology/topology-builder.ts');
  const input = snapshotInput();
  input.ecs = { fetchedAt: '2026-09-07T00:00:00Z', instances: [{ id: 'i-1', name: 'merged', privateIps: ['10.179.1.10'], publicIps: ['8.8.8.8'], status: 'Running', region: 'cn-shanghai', zone: 'a', tags: {} }] };
  input.jumpserver = { collectedAt: '2026-09-08T00:00:00Z', assets: [{ id: 'j-1', hostname: 'jump-host', ip: '10.179.1.10' }] };
  input.devops = { collectedAt: '2026-09-07T00:00:00Z', apps: [{ id: 'app', name: 'App', http: '/app', port: '', repository: 'https://example.com/group/app.git', envs: { TEST: [{ ip: '10.179.1.10', deploy: 'd1', status: '成功', port: '' }, { ip: '10.179.1.10', deploy: 'd2', status: '成功', port: '9090' }] } }] };
  const topology = buildTopology(input, '2026-09-20T00:00:00.000Z');
  const host = topology.nodes.find(node => node.id === 'host:10.179.1.10');
  assert.ok(host);
  assert.deepEqual([...new Set(host.evidence.map(evidence => evidence.source))].sort(), ['devops', 'ecs', 'jumpserver']);
  assert.ok(topology.nodes.some(node => node.id === 'host:8.8.8.8' && node.evidence.some(evidence => evidence.source === 'ecs')));
  const deployment = topology.edges.find(edge => edge.from === 'deployment:app:TEST:d1' && edge.type === 'LISTENS_ON');
  assert.equal(deployment.confidence, 'AMBIGUOUS');
});

test('DNS retains CNAME targets and unresolved address targets', async () => {
  const { buildTopology } = await import('../lib/topology/topology-builder.ts');
  const input = snapshotInput();
  input.dns = { records: [
    { id: 'z:1', zone: 'folidaymall.com', name: 'alias.folidaymall.com', type: 'CNAME', value: 'external.example.com.', status: '启用', line: '默认', ttl: 60, policy: '', remark: '', source: 'fixture.xlsx', row: 1 },
    { id: 'z:2', zone: 'folidaymall.com', name: 'direct.folidaymall.com', type: 'A', value: '203.0.113.10', status: '启用', line: '默认', ttl: 60, policy: '', remark: '', source: 'fixture.xlsx', row: 2 },
  ] };
  const topology = buildTopology(input, '2026-09-20T00:00:00.000Z');
  assert.ok(topology.nodes.some(node => node.id === 'domain:external.example.com' && node.status === 'external'));
  assert.ok(topology.nodes.some(node => node.id === 'host:203.0.113.10' && node.status === 'unresolved'));
  assert.ok(topology.edges.some(edge => edge.type === 'CNAME_TO' && edge.from === 'domain:alias.folidaymall.com' && edge.to === 'domain:external.example.com'));
  assert.ok(topology.edges.some(edge => edge.type === 'RESOLVES_TO' && edge.to === 'host:203.0.113.10'));
});

test('NAT, CLB and Nginx preserve endpoint-level relations and source fields', async () => {
  const { buildTopology } = await import('../lib/topology/topology-builder.ts');
  const input = snapshotInput();
  input.eip = { snapshotDate: '2026-06-26', records: [{ id: 'eip-1', name: 'nat-eip', ip: '47.117.144.217', bindingType: 'NAT网关', bindingId: 'ngw-1', bindingName: 'nat', status: '已分配', bandwidth: '10 Mbps', network: 'BGP', source: 'fixture.csv', row: 1 }] };
  input.nat = { available: true, collectedAt: '2026-09-14T00:00:00Z', region: 'cn-shanghai', gateways: [{ id: 'ngw-1', name: 'nat', status: 'Available', vpcId: 'vpc-1', entries: [{ id: 'fwd-1', tableId: 'ftb-1', name: 'web', externalIp: '47.117.144.217', externalPort: '443', internalIp: '10.179.1.10', internalPort: '8443', protocol: 'TCP', status: 'Available' }] }] };
  input.clb = { available: true, collectedAt: '2026-09-14T00:00:00Z', region: 'cn-shanghai', instances: [{ id: 'lb-1', name: 'clb', ip: '10.179.1.20', addressType: 'intranet', status: 'active', listeners: [
    { protocol: 'TCP', port: 443, status: 'running', description: '', groupId: 'sg-1', backendPort: 80, forwardPort: null, healthCheck: 'on', certificates: [], rules: [] },
    { protocol: 'TCP', port: 8443, status: 'running', description: '', groupId: 'sg-1', backendPort: 80, forwardPort: null, healthCheck: 'on', certificates: [], rules: [] },
  ], groups: [{ id: 'sg-1', name: 'group', kind: 'virtual', servers: [{ id: 'i-1', ip: '10.179.1.10', port: 8080, weight: 100, type: 'ecs' }] }] }] };
  input.jumpserver = { collectedAt: '2026-09-20T00:00:00Z', assets: [{ id: 'asset-1', hostname: 'nginx-host', ip: '10.179.1.20', inspection: { checkedAt: '2026-09-20T00:00:00Z', loginStatus: 'can_login', reason: '', processStatus: 'complete', processes: [], nginxStatus: 'complete', warnings: [], nginxRoutes: [{ domains: ['api.example.com'], listen: ['443 ssl'], uri: '/api', directive: 'proxy_pass', target: 'http://upstream', upstream: 'upstream', backends: [{ host: '10.179.1.10', port: '8080', resolution: 'ip' }, { host: '$dynamic', port: null, resolution: 'dynamic' }, { host: 'localhost', port: '8081', resolution: 'hostname' }] }] } }] };
  input.devops = { collectedAt: '2026-09-07T00:00:00Z', apps: [] };
  input.ecs = { fetchedAt: '2026-09-07T00:00:00Z', instances: [] };

  const topology = buildTopology(input, '2026-09-20T00:00:00.000Z');
  const dnat = topology.nodes.find(node => node.type === 'DNAT_RULE');
  assert.deepEqual(dnat.identity, { dnatEntryId: 'fwd-1', externalIp: '47.117.144.217', externalPort: '443', internalIp: '10.179.1.10', internalPort: '8443', protocol: 'tcp' });
  assert.ok(topology.edges.some(edge => edge.type === 'EXPOSES' && edge.from === dnat.id && edge.to === 'endpoint:47.117.144.217:443:tcp'));
  assert.ok(topology.edges.some(edge => edge.type === 'FORWARDS_TO' && edge.from === dnat.id && edge.to === 'endpoint:10.179.1.10:8443:tcp'));
  assert.ok(topology.edges.some(edge => edge.type === 'BOUND_TO' && edge.from === 'eip:eip-1' && edge.to === 'nat-gateway:ngw-1' && edge.confidence === 'EXACT'));
  assert.ok(topology.edges.some(edge => edge.type === 'HAS_LISTENER' && edge.from === 'clb:lb-1' && edge.to === 'clb-listener:lb-1:tcp:443'));
  assert.ok(topology.edges.some(edge => edge.type === 'ROUTES_TO' && edge.from === 'clb-listener:lb-1:tcp:443' && edge.to === 'clb-server-group:lb-1:sg-1'));
  assert.ok(topology.edges.some(edge => edge.type === 'FORWARDS_TO' && edge.from === 'clb-server-group:lb-1:sg-1' && edge.to === 'endpoint:10.179.1.10:8080:tcp'));
  assert.ok(topology.edges.some(edge => edge.type === 'SERVED_BY' && edge.from === 'domain:api.example.com' && edge.to.startsWith('nginx-route:asset-1:')));
  assert.ok(topology.edges.some(edge => edge.type === 'USES_UPSTREAM'));
  assert.ok(topology.edges.some(edge => edge.type === 'FORWARDS_TO' && edge.from.startsWith('nginx-upstream:asset-1:') && edge.to === 'endpoint:10.179.1.10:8080:http'));
  assert.ok(!topology.nodes.some(node => node.id === 'endpoint:$dynamic:unknown:http' || node.id === 'endpoint:localhost:8081:http'));
});

test('validator catches broken graph integrity and ambiguous count drift', async () => {
  const { validateTopology } = await import('../lib/topology/topology-validator.ts');
  const graph = {
    generatedAt: '2026-09-20T00:00:00.000Z',
    nodes: [
      { id: 'host:10.0.0.1', type: 'HOST', label: '10.0.0.1', identity: { ip: '10.0.0.1' }, status: 'active', environment: 'GLOBAL', evidence: [], attributes: {} },
      { id: 'host:10.0.0.1', type: 'HOST', label: '10.0.0.1', identity: { ip: '10.0.0.1' }, status: 'active', environment: 'GLOBAL', evidence: [], attributes: {} },
      { id: 'deployment:a:TEST:1', type: 'DEPLOYMENT', label: 'a', identity: { appId: 'a', env: 'TEST', deployId: '1', ip: '10.0.0.1', port: '8080' }, status: 'active', environment: 'TEST', evidence: [], attributes: {} },
      { id: 'endpoint:10.0.0.1:8080', type: 'ENDPOINT', label: 'bad', identity: { ip: '10.0.0.1', port: '8080' }, status: 'active', environment: 'GLOBAL', evidence: [], attributes: { discoveredBy: ['devops'] } },
    ],
    edges: [
      { id: 'e1', from: 'deployment:a:TEST:1', to: 'missing', type: 'HAS_DEPLOYMENT', environment: 'TEST', evidence: [], confidence: 'EXACT', observedAt: null },
      { id: 'e1', from: 'missing', to: 'host:10.0.0.1', type: 'ON_HOST', environment: 'GLOBAL', evidence: [], confidence: 'AMBIGUOUS', observedAt: null },
    ],
    stats: { nodeCount: 4, edgeCount: 2, ambiguousEdges: 0, unresolvedNodes: 0 },
  };
  const validation = validateTopology(graph);
  const codes = new Set(validation.errors.map(issue => issue.code));
  for (const code of ['DUPLICATE_NODE_ID', 'DUPLICATE_EDGE_ID', 'MISSING_EDGE_NODE', 'INVALID_ENDPOINT_FORMAT', 'DEPLOYMENT_WITHOUT_ENDPOINT', 'EDGE_WITHOUT_EVIDENCE', 'AMBIGUOUS_EDGE_COUNT_MISMATCH']) assert.ok(codes.has(code), code);
  assert.equal(validation.valid, false);
});

test('appPorts enrich existing endpoints and create tcp endpoints with ON_HOST', async () => {
  const { buildTopology } = await import('../lib/topology/topology-builder.ts');
  const input = snapshotInput();
  input.ecs = { fetchedAt: '2026-09-07T00:00:00Z', instances: [{ id: 'i-1', name: 'h', privateIps: ['10.58.9.217'], publicIps: [], status: 'Running', region: 'cn', zone: 'a', tags: {} }] };
  input.jumpserver = {
    collectedAt: '2026-09-08T00:00:00Z',
    assets: [{
      id: 'j-1', hostname: 'app-host', ip: '10.58.9.217',
      inspection: { checkedAt: '2026-09-08T00:00:00Z', appPorts: [
        { app: 'fosun_cashier', kind: 'Java', port: 7084, addresses: ['::'], pids: [384] },
        { app: 'Nginx', kind: 'Nginx', port: 8080, addresses: ['0.0.0.0'], pids: [10] },
      ] },
    }],
  };
  // DevOps deployment on the same ip:port -> endpoint:10.58.9.217:7084:unknown exists to enrich.
  input.devops = { collectedAt: '2026-09-07T00:00:00Z', apps: [{ id: 'cashier', name: 'fosun-cashier', http: '/x', port: '7084', repository: 'https://example.com/g/c.git', envs: { TEST: [{ ip: '10.58.9.217', deploy: 'd1', status: '成功', port: '7084' }] } }] };
  const topology = buildTopology(input, '2026-09-20T00:00:00.000Z');

  // Enrichment: the devops :unknown endpoint carries the runtime app identity + jumpserver evidence.
  const enriched = topology.nodes.find(n => n.id === 'endpoint:10.58.9.217:7084:unknown');
  assert.ok(enriched, 'devops endpoint exists at ip:port');
  assert.equal(enriched.attributes.runtimeApp, 'fosun_cashier');
  assert.equal(enriched.attributes.runtimeKind, 'Java');
  assert.deepEqual(enriched.attributes.runtimePids, [384]);
  assert.ok(enriched.attributes.discoveredBy.includes('jumpserver'));
  assert.ok(enriched.evidence.some(e => e.source === 'jumpserver' && e.detail.includes('fosun_cashier')));

  // Creation: no prior endpoint at :8080 -> a concrete :tcp endpoint is created.
  const tcp = topology.nodes.find(n => n.id === 'endpoint:10.58.9.217:8080:tcp');
  assert.ok(tcp, 'new tcp endpoint created when no existing endpoint at ip:port');
  assert.equal(tcp.attributes.runtimeApp, 'Nginx');
  assert.equal(tcp.identity.protocol, 'tcp');
  const onHost = topology.edges.find(e => e.type === 'ON_HOST' && e.from === tcp.id && e.to === 'host:10.58.9.217');
  assert.ok(onHost, 'ON_HOST edge created for the new tcp endpoint');
  assert.equal(onHost.confidence, 'EXACT');
  assert.ok(onHost.evidence.some(e => e.source === 'jumpserver'));

  // The enriched endpoint keeps its existing ON_HOST edge (no duplicate).
  const onHostEnriched = topology.edges.filter(e => e.type === 'ON_HOST' && e.from === enriched.id && e.to === 'host:10.58.9.217');
  assert.equal(onHostEnriched.length, 1);
});

test('appPorts skips assets without a valid IP and leaves foreign ip:port untouched', async () => {
  const { buildTopology } = await import('../lib/topology/topology-builder.ts');
  const input = snapshotInput();
  input.ecs = { fetchedAt: '2026-09-07T00:00:00Z', instances: [{ id: 'i-1', name: 'h', privateIps: ['10.58.9.217'], publicIps: [], status: 'Running', region: 'cn', zone: 'a', tags: {} }] };
  input.devops = { collectedAt: '2026-09-07T00:00:00Z', apps: [] };
  input.jumpserver = {
    collectedAt: '2026-09-08T00:00:00Z',
    assets: [
      { id: 'j-bad', hostname: 'noip', ip: 'hostname-only', inspection: { checkedAt: '2026-09-08T00:00:00Z', appPorts: [{ app: 'X', kind: 'Java', port: 1, addresses: ['*'], pids: [1] }] } },
      { id: 'j-1', hostname: 'app-host', ip: '10.58.9.217', inspection: { checkedAt: '2026-09-08T00:00:00Z', appPorts: [{ app: 'fosun_cashier', kind: 'Java', port: 7084, addresses: ['::'], pids: [384] }] } },
    ],
  };
  const topology = buildTopology(input, '2026-09-20T00:00:00.000Z');
  assert.ok(!topology.nodes.some(n => n.id === 'endpoint:hostname-only:1:tcp'));
  assert.ok(topology.nodes.some(n => n.id === 'endpoint:10.58.9.217:7084:tcp'));
});

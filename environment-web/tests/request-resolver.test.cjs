/* eslint-disable typescript/no-require-imports -- Node test harness uses CommonJS, matching the existing suite. */
const { test } = require('node:test');
const assert = require('node:assert/strict');

const OBSERVED_AT = '2026-09-22T00:00:00.000Z';
const evidence = detail => [{ source: 'ecs', sourceId: detail, reference: 'tests/request-resolver.test.cjs', detail, observedAt: OBSERVED_AT }];

const node = (id, type, label) => ({
  id,
  type,
  label: label ?? id,
  identity: {},
  status: 'active',
  environment: 'GLOBAL',
  evidence: evidence(`node ${id}`),
  attributes: {},
});

const topologyOf = (...nodes) => ({ generatedAt: OBSERVED_AT, nodes, edges: [], stats: { nodeCount: nodes.length, edgeCount: 0 } });

const framework = async () => await import('../lib/topology/request/index.ts');

// ---------------------------------------------------------------------------
// PathQuery normalization
// ---------------------------------------------------------------------------

test('normalizeRequestQuery: HTTP URL defaults port to 80 and path to /', async () => {
  const { normalizeRequestQuery } = await framework();
  const query = normalizeRequestQuery('http://api.example.com');
  assert.deepEqual(query, { scheme: 'http', host: 'api.example.com', port: 80, path: '/', raw: 'http://api.example.com', domainOnly: true });
});

test('normalizeRequestQuery: HTTPS URL defaults port to 443', async () => {
  const { normalizeRequestQuery } = await framework();
  const query = normalizeRequestQuery('https://api.example.com/');
  assert.equal(query.scheme, 'https');
  assert.equal(query.port, 443);
  assert.equal(query.path, '/');
});

test('normalizeRequestQuery: custom explicit port is preserved', async () => {
  const { normalizeRequestQuery } = await framework();
  const query = normalizeRequestQuery('https://api.example.com:8443/health');
  assert.equal(query.port, 8443);
  assert.equal(query.path, '/health');
});

test('normalizeRequestQuery: path is preserved without its query string', async () => {
  const { normalizeRequestQuery } = await framework();
  const query = normalizeRequestQuery('https://api.example.com/order/1?x=1&y=2');
  assert.equal(query.scheme, 'https');
  assert.equal(query.host, 'api.example.com');
  assert.equal(query.port, 443);
  assert.equal(query.path, '/order/1');
  assert.ok(!query.path.includes('?'));
});

test('normalizeRequestQuery: fragment is stripped as well', async () => {
  const { normalizeRequestQuery } = await framework();
  const query = normalizeRequestQuery('https://api.example.com/order/1#section');
  assert.equal(query.path, '/order/1');
});

test('normalizeRequestQuery: hostname is lowercased', async () => {
  const { normalizeRequestQuery } = await framework();
  const query = normalizeRequestQuery('https://API.Example.COM/Order');
  assert.equal(query.host, 'api.example.com');
  assert.equal(query.path, '/Order');
});

test('normalizeRequestQuery: trailing root dot is removed', async () => {
  const { normalizeRequestQuery } = await framework();
  const query = normalizeRequestQuery('https://api.example.com./v1/');
  assert.equal(query.host, 'api.example.com');
});

test('normalizeRequestQuery: domain-only input resolves with http defaults', async () => {
  const { normalizeRequestQuery } = await framework();
  const query = normalizeRequestQuery('api.example.com');
  assert.equal(query.scheme, 'http');
  assert.equal(query.host, 'api.example.com');
  assert.equal(query.port, 80);
  assert.equal(query.path, '/');
  assert.equal(query.raw, 'api.example.com');
});

test('normalizeRequestQuery: host plus path without scheme keeps the path', async () => {
  const { normalizeRequestQuery } = await framework();
  const query = normalizeRequestQuery('api.example.com/order/1?x=1');
  assert.equal(query.host, 'api.example.com');
  assert.equal(query.port, 80);
  assert.equal(query.path, '/order/1');
});

test('normalizeRequestQuery: structured fields are normalized consistently', async () => {
  const { normalizeRequestQuery } = await framework();
  const query = normalizeRequestQuery({
    scheme: 'https',
    host: 'API.Example.COM.',
    port: 9443,
    path: 'order/list',
    method: 'get',
    environment: 'product',
  });
  assert.equal(query.scheme, 'https');
  assert.equal(query.host, 'api.example.com');
  assert.equal(query.port, 9443);
  assert.equal(query.path, '/order/list');
  assert.equal(query.method, 'GET');
  assert.equal(query.environment, 'PRODUCT');
  assert.equal(query.raw, undefined);
});

test('normalizeRequestQuery: invalid input is rejected deterministically', async () => {
  const { normalizeRequestQuery } = await framework();
  assert.throws(() => normalizeRequestQuery({ host: '' }), /requires a host/);
  assert.throws(() => normalizeRequestQuery('ftp://api.example.com/'), /Unsupported PathQuery scheme/);
  assert.throws(() => normalizeRequestQuery({ host: 'api.example.com', port: 70000 }), /Invalid PathQuery port/);
  assert.throws(() => normalizeRequestQuery({ host: 'api.example.com', port: 0 }), /Invalid PathQuery port/);
});

// ---------------------------------------------------------------------------
// Resolver framework
// ---------------------------------------------------------------------------

test('framework: empty registry returns a structurally valid UNRESOLVED trace', async () => {
  const { resolveRequestPath, STOP_REASON } = await framework();
  const trace = resolveRequestPath('https://api.example.com/order', topologyOf(node('domain:api.example.com', 'DOMAIN')));
  assert.equal(trace.status, 'UNRESOLVED');
  assert.equal(trace.paths.length, 1);
  assert.equal(trace.paths[0].status, 'UNRESOLVED');
  assert.equal(trace.paths[0].reason, STOP_REASON.NO_ENTRY);
  assert.equal(trace.paths[0].terminalNodeId, null);
  assert.equal(trace.paths[0].steps.length, 0);
  assert.ok(trace.warnings.some(message => message.includes('api.example.com')));
  assert.equal(trace.query.host, 'api.example.com');
});

test('framework: resolver that fires but yields no candidates is UNRESOLVED', async () => {
  const { resolveRequestPath, ResolverRegistry, STOP_REASON } = await framework();
  const registry = new ResolverRegistry().register({
    name: 'StubResolver',
    canResolve: () => true,
    resolve: () => ({ rule: 'stub:none', moves: [], warnings: ['nothing to do'] }),
  });
  const trace = resolveRequestPath('https://api.example.com/', topologyOf(), { registry });
  assert.equal(trace.status, 'UNRESOLVED');
  assert.equal(trace.paths[0].reason, STOP_REASON.NO_CANDIDATE);
  assert.ok(trace.warnings.includes('nothing to do'));
});

test('framework: a terminal candidate produces a RESOLVED path with step evidence', async () => {
  const { resolveRequestPath, ResolverRegistry } = await framework();
  const app = node('application:1', 'APPLICATION', 'order-service');
  const registry = new ResolverRegistry().register({
    name: 'StubResolver',
    canResolve: () => true,
    resolve: () => ({ rule: 'stub:terminal', confidence: 'EXACT', evidence: evidence('exact-match'), moves: [{ node: app, terminal: true }] }),
  });
  const trace = resolveRequestPath('https://api.example.com/', topologyOf(app), { registry });
  assert.equal(trace.status, 'RESOLVED');
  assert.equal(trace.paths.length, 1);
  const [path] = trace.paths;
  assert.equal(path.terminalNodeId, 'application:1');
  assert.equal(path.confidence, 'EXACT');
  assert.equal(path.steps.length, 1);
  assert.equal(path.steps[0].resolver, 'StubResolver');
  assert.equal(path.steps[0].rule, 'stub:terminal');
  assert.deepEqual(path.steps[0].inputNodeIds, []);
  assert.deepEqual(path.steps[0].outputNodeIds, ['application:1']);
  assert.equal(path.steps[0].evidence[0].sourceId, 'exact-match');
});

test('framework: maxDepth stops long chains and records the stop reason', async () => {
  const { resolveRequestPath, ResolverRegistry, STOP_REASON } = await framework();
  const ids = ['n0', 'n1', 'n2', 'n3', 'n4'];
  const byId = new Map(ids.map(id => [id, node(id, 'HOST', id)]));
  const nextOf = current => (current === null ? 'n0' : ids[ids.indexOf(current.id) + 1]);
  const registry = new ResolverRegistry().register({
    name: 'ChainResolver',
    canResolve: () => true,
    resolve: context => {
      const nextId = nextOf(context.current);
      return nextId
        ? { rule: 'chain:next', confidence: 'EXACT', moves: [{ node: byId.get(nextId) }] }
        : { rule: 'chain:end', moves: [] };
    },
  });
  const trace = resolveRequestPath('https://api.example.com/', topologyOf(...byId.values()), { registry, maxDepth: 2 });
  // TASK-06 semantics: the chain progressed two hops before maxDepth => PARTIAL.
  assert.equal(trace.status, 'PARTIAL');
  assert.equal(trace.paths.length, 1);
  assert.equal(trace.paths[0].reason, STOP_REASON.MAX_DEPTH);
  assert.equal(trace.paths[0].steps.length, 2);
  assert.deepEqual(trace.paths[0].nodes.map(item => item.id), ['n0', 'n1']);
  assert.ok(trace.warnings.some(message => message.includes('maxDepth=2')));
});

test('framework: cycle guard prevents revisiting a node on the same branch', async () => {
  const { resolveRequestPath, ResolverRegistry, STOP_REASON } = await framework();
  const a = node('a', 'HOST', 'a');
  const b = node('b', 'HOST', 'b');
  const byId = new Map([['a', a], ['b', b]]);
  const registry = new ResolverRegistry().register({
    name: 'LoopResolver',
    canResolve: () => true,
    resolve: context => {
      const targetId = context.current === null ? 'a' : context.current.id === 'a' ? 'b' : 'a';
      return { rule: 'loop:next', confidence: 'INFERRED', moves: [{ node: byId.get(targetId) }] };
    },
  });
  const trace = resolveRequestPath('https://api.example.com/', topologyOf(a, b), { registry });
  // TASK-06 semantics: the branch progressed (a → b) before the cycle guard => PARTIAL.
  assert.equal(trace.status, 'PARTIAL');
  assert.equal(trace.paths.length, 1);
  assert.equal(trace.paths[0].reason, STOP_REASON.CYCLE);
  assert.equal(trace.paths[0].stoppedAt, 'b');
  assert.deepEqual(trace.paths[0].nodes.map(item => item.id), ['a', 'b']);
  // Every path stays a simple path.
  assert.equal(new Set(trace.paths[0].nodes.map(item => item.id)).size, trace.paths[0].nodes.length);
  assert.ok(trace.warnings.some(message => /Cycle guard.*already on this branch/.test(message)));
});

test('framework: candidates are ordered deterministically across runs', async () => {
  const { resolveRequestPath, ResolverRegistry } = await framework();
  const terminals = new Map(['z', 'a', 'm'].map(id => [id, node(id, 'APPLICATION', id)]));
  const registry = new ResolverRegistry().register({
    name: 'UnorderedResolver',
    canResolve: context => context.current === null,
    // Deliberately non-sorted; includes a duplicate which must collapse.
    resolve: () => ({
      rule: 'unordered:fanout',
      confidence: 'EXACT',
      moves: [
        { node: terminals.get('z'), terminal: true },
        { node: terminals.get('a'), terminal: true },
        { node: terminals.get('m'), terminal: true },
        { node: terminals.get('a'), terminal: true },
      ],
    }),
  });
  const graph = topologyOf(...terminals.values());
  const traceA = resolveRequestPath('https://api.example.com/', graph, { registry });
  const traceB = resolveRequestPath('https://api.example.com/', graph, { registry });
  assert.equal(traceA.status, 'AMBIGUOUS');
  assert.deepEqual(traceA.paths.map(path => path.terminalNodeId), ['a', 'm', 'z']);
  // Every invocation sees all candidates; duplicates collapse within a step.
  assert.deepEqual(traceA.paths[0].steps[0].outputNodeIds, ['a', 'm', 'z']);
  assert.deepEqual(JSON.parse(JSON.stringify(traceA)), JSON.parse(JSON.stringify(traceB)));
});

test('framework: multiple terminal candidates are AMBIGUOUS; dead branches make PARTIAL', async () => {
  const { resolveRequestPath, ResolverRegistry } = await framework();
  const app1 = node('application:1', 'APPLICATION', 'svc-1');
  const app2 = node('application:2', 'APPLICATION', 'svc-2');
  const deadEnd = node('endpoint:10.0.0.9:9000:tcp', 'ENDPOINT', 'dead-end');
  const registry = new ResolverRegistry().register({
    name: 'BranchResolver',
    canResolve: context => context.current === null,
    resolve: () => ({
      rule: 'branch:fanout',
      confidence: 'INFERRED',
      moves: [{ node: app1, terminal: true }, { node: app2, terminal: true }, { node: deadEnd }],
    }),
  });
  const trace = resolveRequestPath('https://api.example.com/', topologyOf(app1, app2, deadEnd), { registry });
  // Three distinct terminals => AMBIGUOUS even though one branch later dies.
  assert.equal(trace.status, 'AMBIGUOUS');
  // TASK-06 semantics: the two terminal branches are RESOLVED; the dead branch
  // progressed one step (endpoint reached) and stops as PARTIAL, not UNRESOLVED.
  assert.equal(trace.paths.filter(path => path.status === 'RESOLVED').length, 2);
  assert.ok(trace.paths.some(path => path.status === 'PARTIAL' && path.stoppedAt === deadEnd.id));
});

test('framework: environment filter and graph index are exposed via ResolverContext', async () => {
  const { buildGraphIndex } = await import('../lib/topology/path-explorer.ts');
  const { resolveRequestPathWithIndex, ResolverRegistry } = await framework();
  const seen = [];
  const app = node('application:1', 'APPLICATION', 'svc');
  const registry = new ResolverRegistry().register({
    name: 'ProbeResolver',
    canResolve: () => true,
    resolve: context => {
      seen.push(context);
      return { rule: 'probe:terminal', confidence: 'EXACT', moves: [{ node: app, terminal: true }] };
    },
  });
  const index = buildGraphIndex(topologyOf(app));
  const trace = resolveRequestPathWithIndex(index, { host: 'api.example.com', environment: 'PRODUCT' }, { registry });
  assert.equal(trace.status, 'RESOLVED');
  const [context] = seen;
  assert.equal(context.environment, 'PRODUCT');
  assert.equal(context.index, index);
  assert.equal(context.edgeAllowed({ environment: 'PRODUCT' }), true);
  assert.equal(context.edgeAllowed({ environment: 'GLOBAL' }), true);
  assert.equal(context.edgeAllowed({ environment: 'TEST' }), false);
  // The same built index is reused on a second request — edges are never rescanned per request.
  resolveRequestPathWithIndex(index, 'https://api.example.com/', { registry });
  assert.ok(seen.every(context => context.index === index));
});

test('framework: reserved resolver names exist but ship no implementations', async () => {
  const { ResolverRegistry, RESERVED_RESOLVER_NAMES } = await framework();
  assert.deepEqual(Object.values(RESERVED_RESOLVER_NAMES).sort(), [
    'ClbResolver',
    'DNSResolver',
    'DeploymentResolver',
    'NatResolver',
    'NginxResolver',
    'RepositoryResolver',
  ]);
  const registry = new ResolverRegistry();
  for (const name of Object.values(RESERVED_RESOLVER_NAMES)) {
    assert.equal(registry.get(name), undefined);
  }
  assert.throws(() => registry.register({ name: 'Dup', canResolve: () => false, resolve: () => ({ rule: 'x', moves: [] }) }).register({ name: 'Dup', canResolve: () => false, resolve: () => ({ rule: 'x', moves: [] }) }), /already registered/);
});

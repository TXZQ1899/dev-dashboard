/* eslint-disable typescript/no-require-imports -- Node test harness uses CommonJS, matching the existing suite. */
const { test } = require('node:test');
const assert = require('node:assert/strict');

const OBSERVED_AT = '2026-09-23T00:00:00.000Z';

const ev = (source, sourceId) => [{
  source,
  sourceId,
  reference: 'tests/request-resolver-deployment.test.cjs',
  detail: `${source}:${sourceId}`,
  observedAt: OBSERVED_AT,
}];

const epId = (ip, port, protocol = 'unknown') => `endpoint:${ip}:${port}:${protocol}`;

const node = (id, type, identity, options = {}) => ({
  id,
  type,
  label: options.label ?? id,
  identity,
  status: options.status ?? 'active',
  environment: options.environment ?? 'GLOBAL',
  evidence: options.evidence ?? ev('devops', `node ${id}`),
  attributes: options.attributes ?? {},
});

const domain = (name) => node(`domain:${name}`, 'DOMAIN', { name }, { evidence: ev('dns', `domain ${name}`) });
const endpoint = (ip, port, protocol = 'unknown') =>
  node(epId(ip, port, protocol), 'ENDPOINT', { ip, port, protocol }, { evidence: ev('devops', `ep ${ip}:${port}`) });

const application = (appId, name) =>
  node(`application:${appId}`, 'APPLICATION', { devopsAppId: appId }, {
    label: name,
    evidence: ev('devops', `app ${appId}`),
    attributes: { http: 'true', declaredPort: '8080', branch: 'main' },
  });

const deployment = (appId, env, deployId, ip, port, options = {}) =>
  node(`deployment:${appId}:${env}:${deployId}`, 'DEPLOYMENT', { appId, env, deployId, ip, port }, {
    label: `${appId} ${env} ${deployId}`,
    environment: env,
    evidence: ev('devops', `${appId}/${env}/${deployId}`),
    attributes: {
      branch: 'main',
      repository: options.repository ?? `https://codeup.example.com/group/${appId}.git`,
      lastPublishedAt: '2026-09-20T10:00:00.000Z',
      publishStatus: 'success',
    },
  });

const repository = (url, options = {}) => {
  const hash = simpleHash(url);
  return node(`repository:${hash}`, 'REPOSITORY', { url }, {
    label: url.split('/').pop(),
    status: options.status ?? 'active',
    evidence: options.evidence ?? ev('repository', `repo ${url}`),
    attributes: { url, difference: options.difference ?? 'matched', match: 'exact' },
  });
};

function simpleHash(value) {
  let hash = 0x811c9dc5;
  for (let i = 0; i < value.length; i++) {
    hash ^= value.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash.toString(16).padStart(8, '0');
}

const edge = (id, from, to, type, options = {}) => ({
  id: `edge:${id}`,
  from,
  to,
  type,
  environment: options.environment ?? 'GLOBAL',
  evidence: options.evidence ?? ev('devops', `edge ${id}`),
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

// DNS + Deployment + Repository: domain -> endpoint -> deployment -> app -> repo.
const fullRegistry = async () => {
  const { ResolverRegistry, DnsResolver, DeploymentResolver, RepositoryResolver } = await framework();
  return new ResolverRegistry()
    .register(new DnsResolver())
    .register(new DeploymentResolver())
    .register(new RepositoryResolver());
};

// Deployment + Repository only (entry via a direct endpoint is not possible; tests
// that need entry use fullRegistry).
const deploymentRepoRegistry = async () => {
  const { ResolverRegistry, DeploymentResolver, RepositoryResolver } = await framework();
  return new ResolverRegistry()
    .register(new DeploymentResolver())
    .register(new RepositoryResolver());
};

/**
 * Builds a minimal application topology: DOMAIN -> ENDPOINT -> DEPLOYMENT ->
 * APPLICATION -> REPOSITORY. The deployment listens on the endpoint.
 */
const appTopology = (opts = {}) => {
  const appId = opts.appId ?? 'app-a';
  const appName = opts.appName ?? 'App A';
  const env = opts.env ?? 'TEST';
  const ip = opts.ip ?? '10.179.1.10';
  const port = opts.port ?? '8080';
  const deployId = opts.deployId ?? 'd1';
  const repoUrl = opts.repoUrl ?? `https://codeup.example.com/group/${appId}.git`;
  const repoStatus = opts.repoStatus ?? 'active';
  const hostName = opts.hostName ?? 'app-a.example.com';

  const d = domain(hostName);
  const ep = endpoint(ip, port);
  const app = application(appId, appName);
  const dep = deployment(appId, env, deployId, ip, port);
  const repo = repository(repoUrl, { status: repoStatus });
  const nodes = [d, ep, app, dep, repo];
  const edges = [
    edge('r1', d.id, ep.id, 'RESOLVES_TO'),
    edge('hd1', app.id, dep.id, 'HAS_DEPLOYMENT', { environment: env }),
    edge('lo1', dep.id, ep.id, 'LISTENS_ON', { environment: env }),
    edge('bf1', app.id, repo.id, 'BUILT_FROM'),
  ];
  return { d, ep, app, dep, repo, nodes, edges };
};

// ---------------------------------------------------------------------------
// 1. exact IP+port+env
// ---------------------------------------------------------------------------

test('deployment: exact IP+port+env resolves DEPLOYMENT -> APPLICATION -> REPOSITORY (EXACT)', async () => {
  const { resolveRequestPath } = await framework();
  const topo = appTopology({ env: 'TEST', port: '8080' });
  const trace = resolveRequestPath(
    { host: 'app-a.example.com', port: 8080, environment: 'TEST' },
    graph(topo.nodes, topo.edges),
    { registry: await fullRegistry() },
  );
  assert.equal(trace.paths.length, 1);
  const [path] = trace.paths;
  assert.deepEqual(path.nodes.map(n => n.type), ['DOMAIN', 'ENDPOINT', 'DEPLOYMENT', 'APPLICATION', 'REPOSITORY']);
  assert.equal(path.confidence, 'EXACT');
  assert.equal(path.terminalNodeId, topo.repo.id);
  assert.equal(path.nodes[2].identity.appId, 'app-a');
  assert.equal(path.nodes[3].identity.devopsAppId, 'app-a');
});

// ---------------------------------------------------------------------------
// 2. exact IP+port (no environment in query)
// ---------------------------------------------------------------------------

test('deployment: exact IP+port without query environment still resolves EXACT', async () => {
  const { resolveRequestPath } = await framework();
  const topo = appTopology({ env: 'TEST', port: '8080' });
  const trace = resolveRequestPath(
    { host: 'app-a.example.com', port: 8080 },
    graph(topo.nodes, topo.edges),
    { registry: await fullRegistry() },
  );
  assert.equal(trace.paths.length, 1);
  const [path] = trace.paths;
  assert.equal(path.confidence, 'EXACT');
  assert.equal(path.steps.find(s => s.rule.startsWith('deployment:endpoint')).confidence, 'EXACT');
});

// ---------------------------------------------------------------------------
// 3. same host multiple ports — port distinguishes applications
// ---------------------------------------------------------------------------

test('deployment: same host multiple ports resolves the port-matching application only', async () => {
  const { resolveRequestPath } = await framework();
  const base = appTopology({ appId: 'app-a', port: '8080', env: 'TEST', hostName: 'multi.example.com' });
  // Second application on the same host, different port.
  const appB = application('app-b', 'App B');
  const depB = deployment('app-b', 'TEST', 'd1', '10.179.1.10', '8081');
  const epB = endpoint('10.179.1.10', '8081');
  const repoB = repository('https://codeup.example.com/group/app-b.git');
  const nodes = [...base.nodes, appB, depB, epB, repoB];
  const edges = [
    ...base.edges,
    edge('hd-b', appB.id, depB.id, 'HAS_DEPLOYMENT', { environment: 'TEST' }),
    edge('lo-b', depB.id, epB.id, 'LISTENS_ON', { environment: 'TEST' }),
    edge('bf-b', appB.id, repoB.id, 'BUILT_FROM'),
    edge('r-b', base.d.id, epB.id, 'RESOLVES_TO'),
  ];

  const trace8080 = resolveRequestPath(
    { host: 'multi.example.com', port: 8080 },
    graph(nodes, edges),
    { registry: await fullRegistry() },
  );
  // DNS fans out to both port endpoints; each endpoint's port selects its app.
  const appAByPort = trace8080.paths.find(p => p.nodes[1].identity.port === '8080');
  assert.ok(appAByPort);
  assert.equal(appAByPort.nodes[2].identity.appId, 'app-a');
  const appBByPort = trace8080.paths.find(p => p.nodes[1].identity.port === '8081');
  assert.ok(appBByPort);
  assert.equal(appBByPort.nodes[2].identity.appId, 'app-b');

  const trace8081 = resolveRequestPath(
    { host: 'multi.example.com', port: 8081 },
    graph(nodes, edges),
    { registry: await fullRegistry() },
  );
  assert.equal(trace8081.paths.find(p => p.nodes[1].identity.port === '8081').nodes[2].identity.appId, 'app-b');
});

// ---------------------------------------------------------------------------
// 4. same host multiple applications — port distinguishes, no random pick
// ---------------------------------------------------------------------------

test('deployment: same host multiple applications on the SAME port are AMBIGUOUS (all kept)', async () => {
  const { resolveRequestPath } = await framework();
  const base = appTopology({ appId: 'app-a', port: '8080', env: 'TEST', hostName: 'amb.example.com' });
  // Second app on same host AND same port (realistic misconfiguration).
  const appB = application('app-b', 'App B');
  const depB = deployment('app-b', 'TEST', 'd1', '10.179.1.10', '8080');
  const repoB = repository('https://codeup.example.com/group/app-b.git');
  const nodes = [...base.nodes, appB, depB, repoB];
  const edges = [
    ...base.edges,
    edge('hd-b', appB.id, depB.id, 'HAS_DEPLOYMENT', { environment: 'TEST' }),
    edge('lo-b', depB.id, base.ep.id, 'LISTENS_ON', { environment: 'TEST' }),
    edge('bf-b', appB.id, repoB.id, 'BUILT_FROM'),
  ];

  const trace = resolveRequestPath(
    { host: 'amb.example.com', port: 8080 },
    graph(nodes, edges),
    { registry: await fullRegistry() },
  );
  // Two ambiguous paths (app-a and app-b), neither randomly dropped.
  const appIds = trace.paths.map(p => p.nodes[2].identity.appId).sort();
  assert.deepEqual(appIds, ['app-a', 'app-b']);
  assert.equal(trace.status, 'AMBIGUOUS');
  for (const p of trace.paths) assert.equal(p.confidence, 'AMBIGUOUS');
});

// ---------------------------------------------------------------------------
// 5. environment filtering
// ---------------------------------------------------------------------------

test('deployment: environment filter returns only the matching env deployment', async () => {
  const { resolveRequestPath } = await framework();
  const testTopo = appTopology({ appId: 'app-a', env: 'TEST', port: '8080', hostName: 'env.example.com' });
  // Same app deployed to PRODUCT on the same port.
  const prodDep = deployment('app-a', 'PRODUCT', 'd1', '10.179.1.10', '8080');
  const nodes = [...testTopo.nodes, prodDep];
  const edges = [
    ...testTopo.edges,
    edge('hd-prod', testTopo.app.id, prodDep.id, 'HAS_DEPLOYMENT', { environment: 'PRODUCT' }),
    edge('lo-prod', prodDep.id, testTopo.ep.id, 'LISTENS_ON', { environment: 'PRODUCT' }),
  ];

  const trace = resolveRequestPath(
    { host: 'env.example.com', port: 8080, environment: 'PRODUCT' },
    graph(nodes, edges),
    { registry: await fullRegistry() },
  );
  assert.equal(trace.paths.length, 1);
  assert.equal(trace.paths[0].nodes[2].environment, 'PRODUCT');
  assert.equal(trace.paths[0].confidence, 'EXACT');
});

test('deployment: environment filter with no matching env returns unresolved (no cross-env leak)', async () => {
  const { resolveRequestPath } = await framework();
  const topo = appTopology({ env: 'TEST', port: '8080', hostName: 'noleak.example.com' });
  const trace = resolveRequestPath(
    { host: 'noleak.example.com', port: 8080, environment: 'PRODUCT' },
    graph(topo.nodes, topo.edges),
    { registry: await fullRegistry() },
  );
  // TASK-06 semantics: DNS progressed to the endpoint but no eligible
  // deployment exists, so the chain is PARTIAL (not fully UNRESOLVED).
  assert.equal(trace.status, 'PARTIAL');
  assert.ok(trace.warnings.some(w => w.includes('PRODUCT')));
});

// ---------------------------------------------------------------------------
// 6. ambiguous match — IP only with multiple deployments
// ---------------------------------------------------------------------------

test('deployment: IP-only endpoint with multiple host deployments is AMBIGUOUS', async () => {
  const { resolveRequestPath } = await framework();
  // Endpoint with unknown port (e.g. reached without a concrete backend port).
  const d = domain('iponly.example.com');
  const ep = endpoint('10.179.1.10', 'unknown');
  const appA = application('app-a', 'App A');
  const appB = application('app-b', 'App B');
  const depA = deployment('app-a', 'TEST', 'd1', '10.179.1.10', '8080');
  const depB = deployment('app-b', 'TEST', 'd1', '10.179.1.10', '8081');
  const repoA = repository('https://codeup.example.com/group/app-a.git');
  const repoB = repository('https://codeup.example.com/group/app-b.git');
  const nodes = [d, ep, appA, appB, depA, depB, repoA, repoB];
  const edges = [
    edge('r1', d.id, ep.id, 'RESOLVES_TO'),
    edge('hd-a', appA.id, depA.id, 'HAS_DEPLOYMENT', { environment: 'TEST' }),
    edge('hd-b', appB.id, depB.id, 'HAS_DEPLOYMENT', { environment: 'TEST' }),
    edge('lo-a', depA.id, ep.id, 'LISTENS_ON', { environment: 'TEST', confidence: 'AMBIGUOUS' }),
    edge('lo-b', depB.id, ep.id, 'LISTENS_ON', { environment: 'TEST', confidence: 'AMBIGUOUS' }),
    edge('bf-a', appA.id, repoA.id, 'BUILT_FROM'),
    edge('bf-b', appB.id, repoB.id, 'BUILT_FROM'),
  ];

  const trace = resolveRequestPath(
    { host: 'iponly.example.com', port: 80 },
    graph(nodes, edges),
    { registry: await fullRegistry() },
  );
  const appIds = trace.paths.map(p => p.nodes[2]?.identity.appId).filter(Boolean).sort();
  assert.deepEqual(appIds, ['app-a', 'app-b']);
  assert.equal(trace.status, 'AMBIGUOUS');
});

// ---------------------------------------------------------------------------
// 7. no deployment
// ---------------------------------------------------------------------------

test('deployment: endpoint with no deployment stops the chain (PARTIAL)', async () => {
  const { resolveRequestPath } = await framework();
  const d = domain('nodeploy.example.com');
  const ep = endpoint('10.179.1.99', '8080');
  const trace = resolveRequestPath(
    { host: 'nodeploy.example.com', port: 8080 },
    graph([d, ep], [edge('r1', d.id, ep.id, 'RESOLVES_TO')]),
    { registry: await fullRegistry() },
  );
  // TASK-06 semantics: DNS progressed to the endpoint; missing deployment data
  // stops the chain as PARTIAL, never a fabricated match.
  assert.equal(trace.status, 'PARTIAL');
  assert.ok(trace.warnings.some(w => w.includes('No DevOps deployment')));
});

test('deployment: numeric port with no matching deployment stops instead of host-wide fan-out', async () => {
  const { resolveRequestPath } = await framework();
  // Shared host: nginx owns :80 (no DevOps deployment on 80), many Java
  // deployments live on other / unknown ports. A concrete :80 endpoint must not
  // fan out to every host deployment (the 50-paths explosion).
  const d = domain('shared.example.com');
  const ep80 = endpoint('10.179.1.10', '80');
  const appA = application('app-a', 'App A');
  const depA = deployment('app-a', 'TEST', 'd1', '10.179.1.10', '7002');
  const appB = application('app-b', 'App B');
  const depB = deployment('app-b', 'TEST', 'd1', '10.179.1.10', 'unknown');
  const nodes = [d, ep80, appA, depA, appB, depB];
  const edges = [
    edge('r1', d.id, ep80.id, 'RESOLVES_TO'),
    edge('hd-a', appA.id, depA.id, 'HAS_DEPLOYMENT', { environment: 'TEST' }),
    edge('hd-b', appB.id, depB.id, 'HAS_DEPLOYMENT', { environment: 'TEST' }),
  ];
  const trace = resolveRequestPath(
    { host: 'shared.example.com', port: 80, environment: 'TEST' },
    graph(nodes, edges),
    { registry: await fullRegistry() },
  );
  // The deployment branch is dead; no AMBIGUOUS fan-out paths are produced.
  assert.equal(trace.status, 'PARTIAL');
  assert.ok(!trace.paths.some(p => p.nodes.some(n => n.type === 'DEPLOYMENT')));
  assert.ok(trace.warnings.some(w => w.includes('listens on port 80')));
});

// ---------------------------------------------------------------------------
// 8. repository exact (Codeup-matched repository)
// ---------------------------------------------------------------------------

test('repository: Codeup-matched repository keeps EXACT confidence', async () => {
  const { resolveRequestPath } = await framework();
  const topo = appTopology({ repoStatus: 'active' });
  const trace = resolveRequestPath(
    { host: 'app-a.example.com', port: 8080 },
    graph(topo.nodes, topo.edges),
    { registry: await fullRegistry() },
  );
  const [path] = trace.paths;
  const repoStep = path.steps.find(s => s.resolver === 'RepositoryResolver');
  assert.equal(repoStep.confidence, 'EXACT');
  assert.equal(path.confidence, 'EXACT');
  assert.equal(path.terminalNodeId, topo.repo.id);
});

// ---------------------------------------------------------------------------
// 9. repository uncertain (devops-only, no Codeup match)
// ---------------------------------------------------------------------------

test('repository: devops-only (unresolved) repository downgrades to INFERRED', async () => {
  const { resolveRequestPath } = await framework();
  const topo = appTopology({ repoStatus: 'unresolved' });
  const trace = resolveRequestPath(
    { host: 'app-a.example.com', port: 8080 },
    graph(topo.nodes, topo.edges),
    { registry: await fullRegistry() },
  );
  const [path] = trace.paths;
  const repoStep = path.steps.find(s => s.resolver === 'RepositoryResolver');
  assert.equal(repoStep.confidence, 'INFERRED');
  // Path confidence = weakest step = INFERRED.
  assert.equal(path.confidence, 'INFERRED');
});

test('repository: application without BUILT_FROM edge stays RESOLVED at APPLICATION', async () => {
  const { resolveRequestPath } = await framework();
  const topo = appTopology();
  // Remove the repository and its BUILT_FROM edge.
  const nodes = topo.nodes.filter(n => n.type !== 'REPOSITORY');
  const edges = topo.edges.filter(e => e.type !== 'BUILT_FROM');
  const trace = resolveRequestPath(
    { host: 'app-a.example.com', port: 8080 },
    graph(nodes, edges),
    { registry: await fullRegistry() },
  );
  // TASK-06 semantics: the branch reliably reached APPLICATION => RESOLVED;
  // the missing repository hop is kept as stoppedAt/reason + a trace warning.
  const resolved = trace.paths.find(p => p.status === 'RESOLVED');
  assert.ok(resolved);
  assert.equal(resolved.stoppedAt, topo.app.id);
  assert.equal(trace.status, 'RESOLVED');
  // The no-repository warning is recorded at the trace level (no step is emitted
  // when a resolver produces no moves).
  assert.ok(trace.warnings.some(w => w.includes('BUILT_FROM')));
});

// ---------------------------------------------------------------------------
// 10. deterministic output
// ---------------------------------------------------------------------------

test('deployment: multiple ambiguous deployments produce deterministic ordering', async () => {
  const { resolveRequestPath } = await framework();
  const d = domain('det.example.com');
  const ep = endpoint('10.179.1.10', '8080');
  const apps = ['app-c', 'app-a', 'app-b'].map(id => application(id, id));
  const deps = apps.map((app, i) => deployment(app.identity.devopsAppId, 'TEST', `d${i}`, '10.179.1.10', '8080'));
  const repos = apps.map(app => repository(`https://codeup.example.com/group/${app.identity.devopsAppId}.git`));
  const nodes = [d, ep, ...apps, ...deps, ...repos];
  const edges = [edge('r1', d.id, ep.id, 'RESOLVES_TO')];
  apps.forEach((app, i) => {
    edges.push(edge(`hd-${i}`, app.id, deps[i].id, 'HAS_DEPLOYMENT', { environment: 'TEST' }));
    edges.push(edge(`lo-${i}`, deps[i].id, ep.id, 'LISTENS_ON', { environment: 'TEST' }));
    edges.push(edge(`bf-${i}`, app.id, repos[i].id, 'BUILT_FROM'));
  });

  const run = async () => resolveRequestPath(
    { host: 'det.example.com', port: 8080 },
    graph(nodes, edges),
    { registry: await fullRegistry() },
  );
  const first = await run();
  const second = await run();
  const order1 = first.paths.map(p => p.nodes[2].identity.appId);
  const order2 = second.paths.map(p => p.nodes[2].identity.appId);
  // Output is deterministic: identical ordering across runs.
  assert.deepEqual(order1, order2);
  // The deployment step emits candidates sorted by deployment node id.
  const depStep = first.paths[0].steps.find(s => s.resolver === 'DeploymentResolver' && s.rule.startsWith('deployment:endpoint'));
  const sortedOutputIds = [...depStep.outputNodeIds].sort();
  assert.deepEqual(depStep.outputNodeIds, sortedOutputIds);
});

// ---------------------------------------------------------------------------
// Metadata preservation
// ---------------------------------------------------------------------------

test('deployment: resolved nodes retain deployment/application metadata', async () => {
  const { resolveRequestPath } = await framework();
  const topo = appTopology({ port: '8080', env: 'TEST' });
  const trace = resolveRequestPath(
    { host: 'app-a.example.com', port: 8080, environment: 'TEST' },
    graph(topo.nodes, topo.edges),
    { registry: await fullRegistry() },
  );
  const dep = trace.paths[0].nodes[2];
  const app = trace.paths[0].nodes[3];
  assert.equal(dep.identity.env, 'TEST');
  assert.equal(dep.identity.ip, '10.179.1.10');
  assert.equal(dep.identity.port, '8080');
  assert.equal(dep.attributes.lastPublishedAt, '2026-09-20T10:00:00.000Z');
  assert.equal(app.attributes.declaredPort, '8080');
});

// ---------------------------------------------------------------------------
// IP-only single deployment -> INFERRED (Level 3)
// ---------------------------------------------------------------------------

test('deployment: IP-only endpoint with a single host deployment resolves INFERRED', async () => {
  const { resolveRequestPath } = await framework();
  const d = domain('single.example.com');
  const ep = endpoint('10.179.1.10', 'unknown');
  const app = application('app-a', 'App A');
  const dep = deployment('app-a', 'TEST', 'd1', '10.179.1.10', '8080');
  const repo = repository('https://codeup.example.com/group/app-a.git');
  const nodes = [d, ep, app, dep, repo];
  const edges = [
    edge('r1', d.id, ep.id, 'RESOLVES_TO'),
    edge('hd1', app.id, dep.id, 'HAS_DEPLOYMENT', { environment: 'TEST' }),
    edge('lo1', dep.id, ep.id, 'LISTENS_ON', { environment: 'TEST', confidence: 'INFERRED' }),
    edge('bf1', app.id, repo.id, 'BUILT_FROM'),
  ];
  const trace = resolveRequestPath(
    { host: 'single.example.com', port: 80 },
    graph(nodes, edges),
    { registry: await fullRegistry() },
  );
  assert.equal(trace.paths.length, 1);
  const step = trace.paths[0].steps.find(s => s.rule.startsWith('deployment:endpoint'));
  assert.equal(step.rule, 'deployment:endpoint:ip-only');
  assert.equal(step.confidence, 'INFERRED');
});

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { frontendPath, frontendRows, routeRows, routeHeaders, csv, matchEndpointProcess, createEndpointProcessMatcher } = require('../lib/nginx-export.mjs');
const snapshot = { assets: [{ ip: '10.0.0.1', hostname: 'nginx', inspection: { nginxRoutes: [
  { domains: ['a.test', 'b.test'], listen: ['80'], uri: '/app', directive: 'static/other', target: '/srv/app', backends: [] },
  { domains: ['a.test'], listen: ['443 ssl'], uri: '/api', directive: 'proxy_pass', target: 'http://svc', upstream: 'svc', backends: [{ host: '10.0.0.2', port: '8080', resolution: 'ip' }, { host: 'service.test', port: '80', resolution: 'dns' }] },
  { domains: ['a.test'], listen: ['80'], uri: '/robots.txt', directive: 'static/other', target: '/srv/robots.txt' },
] } }] };
test('exports normalized routes and all backends', () => {
  const rows = routeRows(snapshot);
  assert.equal(rows.length, 4);
  assert.deepEqual(rows[0], ['10.0.0.1', 'nginx', 'a.test|b.test', '80', '/app', 'static/other', '', '/srv/app', '', '', '', '']);
  assert.deepEqual(rows[2].slice(-3), ['service.test', '80', 'dns']);
  assert.equal(routeHeaders.length, 12);
});
test('frontend candidates exclude files, regex and dynamic locations', () => {
  for (const value of ['/robots.txt', '/.*\\(txt)', '~ ^.+\\.txt$', '@internal', '/$host']) assert.equal(frontendPath(value), null);
  assert.equal(frontendPath('^~ /app'), '/app');
  assert.deepEqual(frontendRows(snapshot).map(row => row[5]), ['a.test/app', 'b.test/app']);
});
test('application sheets omit the four internal Nginx URIs and retain the root route', async () => {
  const { backendRows } = require('../lib/nginx-backend-export.mjs');
  const uris = ['/', '@unbuffered', '@unbuffered_request', '@unbuffered_response', '=  /kong_buffered_http'];
  const routes = uris.map(uri => ({ uri, context: 'http', domains: ['entry.example.test'], listen: ['80'],
    directive: 'static/other', target: '/var/www/site' }));
  const makeSnapshot = nginxRoutes => ({ assets: [{ id: 'entry', ip: '192.0.2.1', inspection: { nginxRoutes } }] });
  assert.deepEqual(frontendRows(makeSnapshot(routes)).map(row => row[4]), ['/']);
  const proxied = makeSnapshot(routes.map(route => ({ ...route, directive: 'proxy_pass', target: 'http://192.0.2.2',
    backends: [{ host: '192.0.2.2', port: 80, resolution: 'ip' }] })));
  const { rows } = await backendRows({ nodes: [], edges: [], stats: {} }, proxied, {}, 'fixture');
  assert.deepEqual(rows.map(row => row[4]), ['/']);
  assert.deepEqual(routeRows(proxied).map(row => row[4]), uris);
});
test('CSV quoting preserves commas, quotes and newlines', () => {
  assert.equal(csv([['a,b', 'c"d', 'e\nf']]), '\ufeff"a,b","c""d","e\nf"\r\n');
});
test('application IDs require a unique matching listener, PID and deployment port', () => {
  const jump = { assets: [{ ip: '10.0.0.2', inspection: { processes: [{ pid: 42 }], appPorts: [{ app: 'order', port: 8080, addresses: ['0.0.0.0'], pids: [42] }] } }] };
  const apps = { apps: [{ id: '1', name: 'order', port: '8080', envs: { TEST: [{ ip: '10.0.0.2' }] } }] };
  assert.equal(matchEndpointProcess(jump, apps, '10.0.0.2', 8080).id, '1');
  const noApplication = matchEndpointProcess(jump, { apps: [] }, '10.0.0.2', 8080);
  assert.equal(noApplication.matchExplanation, '未能匹配应用');
  assert.match(noApplication.evidence, /PID 42/);
  const noListener = matchEndpointProcess(jump, apps, '10.0.0.2', 8081);
  assert.equal(noListener.status, 'UNKNOWN');
  assert.equal(noListener.matchExplanation, undefined);
  assert.equal(matchEndpointProcess({ assets: [...jump.assets, ...jump.assets] }, apps, '10.0.0.2', 8080).status, 'AMBIGUOUS');
  const ambiguous = matchEndpointProcess(jump, { apps: [...apps.apps, { ...apps.apps[0], id: '2' }] }, '10.0.0.2', 8080);
  assert.equal(ambiguous.status, 'AMBIGUOUS');
  assert.equal(ambiguous.matchExplanation, undefined);
  assert.equal(matchEndpointProcess({ assets: [{ ...jump.assets[0], inspection: { ...jump.assets[0].inspection, processes: [] } }] }, apps, '10.0.0.2', 8080).status, 'UNKNOWN');
});

test('Tomcat instance suffix and Java package version require a matching declared port', () => {
  const app = { id: 'app-1', name: 'booking-api', envs: { TEST: [{ ip: '192.0.2.10', port: '8181' }] } };
  for (const [label, command] of [
    ['Tomcat', 'java -Dcatalina.base=/opt/apache-tomcat_booking-api_38470 -Dcatalina.home=/opt/tomcat-other org.apache.catalina.startup.Bootstrap'],
    ['booking-api-1.2.0.jar', 'java -jar /opt/booking-api-1.2.0.jar'],
  ]) {
    const jump = { assets: [{ ip: '192.0.2.10', inspection: {
      processes: [{ pid: 42, command }], appPorts: [{ app: label, port: 8181, addresses: ['::'], pids: [42] }],
    } }] };
    assert.equal(matchEndpointProcess(jump, { apps: [app] }, '192.0.2.10', 8181).id, app.id);
    const undeclared = { ...app, envs: { TEST: [{ ip: '192.0.2.10' }] } };
    assert.equal(matchEndpointProcess(jump, { apps: [undeclared] }, '192.0.2.10', 8181).status, 'UNKNOWN');
  }
});

test('port not listening requires a complete TCP observation and no listener on the target address', () => {
  const ip = '192.0.2.10';
  const match = (portStatus, appPorts) => matchEndpointProcess({ assets: [{ ip, inspection: { portStatus, appPorts } }] }, { apps: [] }, ip, 8023);
  assert.equal(match('complete', []).matchExplanation, '端口未监听');
  assert.equal(match('complete', [{ port: 8023, addresses: ['127.0.0.1'], pids: [] }]).matchExplanation, '端口未监听');
  assert.equal(match('complete', [{ port: 8023, addresses: ['::'], pids: [] }]).matchExplanation, undefined);
  assert.equal(match('not_collected', []).matchExplanation, undefined);
  assert.equal(match('failed', []).matchExplanation, undefined);
});

test('asset explanations distinguish inventory absence from explicit SSH denial', () => {
  const denied = '没有授权 SSH 账号';
  const cannotLogin = (ip, reason) => ({ ip, inspection: { loginStatus: 'cannot_login', reason, portStatus: 'not_collected' } });
  const jump = { assets: [
    cannotLogin('192.0.2.3', denied),
    cannotLogin('192.0.2.4', '堡垒机连接资产失败'),
    cannotLogin('192.0.2.5', '读取授权账号失败：HTTPError'),
    cannotLogin('192.0.2.6,192.0.2.7', denied),
    { ip: '192.0.2.8', inspection: { loginStatus: 'can_login', sudoStatus: 'denied', portStatus: 'complete' } },
  ] };
  const ecs = { schemaVersion: 2, tenants: [{ complete: true }], instances: [
    { privateIps: ['192.0.2.3', '192.0.2.5', '192.0.2.7'], publicIps: ['192.0.2.1'] },
  ] };
  const match = createEndpointProcessMatcher(jump, { apps: [] }, ecs);
  assert.equal(match('192.0.2.1', 8080).assetStatus, 'JumpServer未收录');
  assert.equal(match('192.0.2.2', 8080).assetStatus, '资产已下线');
  assert.equal(match('192.0.2.3', 8080).assetStatus, '无权限访问');
  assert.equal(match('192.0.2.4', 8080).assetStatus, '资产已下线');
  assert.equal(match('192.0.2.5', 8080).assetStatus, undefined);
  assert.equal(match('192.0.2.6', 8080).assetStatus, '无权限访问');
  assert.equal(match('192.0.2.8', 8080).assetStatus, undefined);
  const incomplete = createEndpointProcessMatcher(jump, { apps: [] }, { ...ecs, tenants: [{ complete: false }] });
  assert.equal(incomplete('192.0.2.2', 8080).assetStatus, undefined);
  assert.equal(incomplete('192.0.2.4', 8080).assetStatus, undefined);
  const incompleteOverall = createEndpointProcessMatcher(jump, { apps: [] }, { ...ecs, complete: false });
  assert.equal(incompleteOverall('192.0.2.2', 8080).assetStatus, undefined);
});

test('dual-inventory absence applies to low-confidence final business endpoints and excludes a known CLB', async () => {
  const { backendRows, backendHeaders } = require('../lib/nginx-backend-export.mjs');
  const { buildValidatedTopology } = require('../lib/topology/topology-builder.ts');
  const route = (uri, host, port, resolution) => ({ domains: ['entry.example.test'], listen: ['80'],
    uri, context: 'http', directive: 'proxy_pass', target: `http://${host}:${port}`, backends: [{ host, port, resolution }] });
  const jumpserver = { assets: [{ id: 'entry', ip: '192.0.2.1', inspection: { nginxRoutes: [
    route('/service/', 'backend.example.test', 8080, 'hostname'), route('/balancer/', '198.51.100.32', 9388, 'ip'),
  ] } }] };
  const ecs = { schemaVersion: 2, available: true, complete: true, tenants: [{ complete: true }], instances: [] };
  const dns = { records: [{ id: 'address', name: 'backend.example.test', type: 'A', value: '198.51.100.31', status: '启用' }] };
  const clb = { available: true, instances: [{ id: 'lb-test', name: 'load balancer', ip: '198.51.100.32', status: 'active',
    listeners: [{ protocol: 'TCP', port: 20881, status: 'running' }], groups: [] }] };
  const { topology } = buildValidatedTopology({ jumpserver, ecs, dns, clb }, '2026-11-24T03:00:00Z');
  for (const edge of topology.edges) if (edge.type === 'RESOLVES_TO') edge.confidence = 'AMBIGUOUS';
  const { rows } = await backendRows(topology, jumpserver, {}, 'fixture', { ecs });
  const cell = (row, header) => row[backendHeaders.indexOf(header)];
  assert.deepEqual(rows[0].slice(6, 10), ['198.51.100.31:8080', '资产已下线', '', '']);
  assert.equal(cell(rows[0], '链路置信度'), 'AMBIGUOUS');
  assert.equal(cell(rows[0], '应用匹配置信度'), 'AMBIGUOUS');
  assert.equal(cell(rows[1], '应用匹配说明'), '');
  assert.match(cell(rows[1], '停止原因与说明'), /No CLB listener/);
});

test('invalid proxy configuration requires an absent CLB port or an exact confirmed backend', async () => {
  const { backendRows, backendHeaders } = require('../lib/nginx-backend-export.mjs');
  const { buildValidatedTopology } = require('../lib/topology/topology-builder.ts');
  const route = (uri, host, port) => ({ domains: ['entry.example.test'], listen: ['80'], uri,
    context: 'http', directive: 'proxy_pass', target: `http://${host}:${port}`, backends: [{ host, port, resolution: 'ip' }] });
  const jumpserver = { assets: [
    { id: 'entry', ip: '192.0.2.1', inspection: { nginxRoutes: [
      route('/missing-clb-port/', '192.0.2.2', 9388), route('/different-protocol/', '192.0.2.2', 20881),
      route('/confirmed/', '192.0.2.3', 9092), route('/other-port/', '192.0.2.3', 9093),
    ] } },
    { id: 'backend', ip: '192.0.2.3', inspection: { loginStatus: 'cannot_login' } },
  ] };
  const region = 'cn-shanghai';
  const clb = { available: true, complete: true, schemaVersion: 2,
    tenants: [{ tenantId: 'tenant', region, complete: true }],
    instances: [{ id: 'lb-test', name: 'load balancer', ip: '192.0.2.2', status: 'active', tenantId: 'tenant', region,
      listeners: [{ protocol: 'UDP', port: 20881, status: 'running' }], groups: [] }],
  };
  const { topology } = buildValidatedTopology({ jumpserver, clb }, '2026-11-24T03:00:00Z');
  const invalidProxyEndpoints = [{ ip: '192.0.2.3', port: 9092 }];
  const label = row => row[backendHeaders.indexOf('应用匹配说明')];
  const reason = row => row[backendHeaders.indexOf('停止原因与说明')];
  const { rows } = await backendRows(topology, jumpserver, {}, 'fixture', { clb, invalidProxyEndpoints });
  assert.deepEqual(rows.map(label), ['Proxy Pass后端配置失效', '', 'Proxy Pass后端配置失效', '进程证据不足']);
  assert.match(reason(rows[0]), /未配置 9388 监听端口/);
  assert.match(reason(rows[2]), /用户确认后端 192\.0\.2\.3:9092 未监听/);
  assert.equal(rows[2][backendHeaders.indexOf('服务IP+端口')], '192.0.2.3:9092');
  const incomplete = await backendRows(topology, jumpserver, {}, 'fixture', { clb: { ...clb, complete: false }, invalidProxyEndpoints });
  assert.equal(label(incomplete.rows[0]), '');
  const listener = { assets: [jumpserver.assets[0], { ...jumpserver.assets[1], inspection: { appPorts: [{ port: 9092 }] } }] };
  const observed = await backendRows(topology, listener, {}, 'fixture', { clb, invalidProxyEndpoints });
  assert.equal(label(observed.rows[2]), '进程证据不足');
});

test('an EIP with a missing bound ECS stops direct, DNS and Kong paths only with matching inventory coverage', async () => {
  const { backendRows, backendHeaders } = require('../lib/nginx-backend-export.mjs');
  const { buildValidatedTopology } = require('../lib/topology/topology-builder.ts');
  const removed = '198.51.100.31';
  const route = (uri, host, port = 8077, resolution = 'ip') => ({ domains: ['entry.example.test'], listen: ['80'],
    uri, context: 'http', directive: 'proxy_pass', target: `http://${host}:${port}`, backends: [{ host, port, resolution }] });
  const jumpserver = { assets: [
    { id: 'entry', ip: '192.0.2.1', inspection: { nginxRoutes: [route('/direct/', removed),
      route('/dns/', 'old.example.test', 8077, 'hostname'), route('/kong/', '10.179.4.220', 8000),
      route('/different-source/', '198.51.100.32'), route('/reassigned/', '198.51.100.33')] } },
    { id: 'old-asset', ip: removed, inspection: { loginStatus: 'cannot_login' } },
  ] };
  const region = 'cn-shanghai';
  const ecs = { schemaVersion: 2, available: true, tenants: [{ tenantId: 'tenant-a', region, complete: true }], instances: [
    { id: 'i-anchor', tenantId: 'tenant-a', region, publicIps: ['198.51.100.35'], privateIps: ['192.0.2.5'] },
    { id: 'i-current', tenantId: 'tenant-a', region, publicIps: ['198.51.100.33'], privateIps: ['192.0.2.6'] },
  ] };
  const eip = { region, records: [
    ['198.51.100.35', 'i-anchor', 'a.csv'], [removed, 'i-gone', 'a.csv'], ['198.51.100.32', 'i-unverified', 'b.csv'],
    ['198.51.100.33', 'i-oldbinding', 'a.csv'],
  ].map(([ip, bindingId, source], index) => ({ id: `eip-${index}`, ip, bindingId, source, bindingType: 'ECS实例' })) };
  const dns = { records: [{ id: 'address', name: 'old.example.test', type: 'A', value: removed, status: '启用' }] };
  const { topology } = buildValidatedTopology({ jumpserver, ecs, eip, dns }, '2026-11-24T03:00:00Z');
  const loadKong = async gateway => ({ gateway,
    routes: [{ id: 'route', paths: ['/kong/'], service: { id: 'service' } }],
    services: { service: { id: 'service', protocol: 'http', host: 'upstream' } },
    targets: { upstream: [{ target: `${removed}:8077`, weight: 100 }] },
  });
  const cell = (row, header) => row[backendHeaders.indexOf(header)];
  const { rows } = await backendRows(topology, jumpserver, {}, 'fixture', { ecs, eip, loadKong });
  for (const uri of ['/direct/', '/dns/', '/kong/']) {
    const row = rows.find(row => cell(row, 'URI') === uri);
    assert.deepEqual(row.slice(6, 10), ['', '资产已下线', '', '']);
    assert.match(cell(row, '完整代理链'), /198\.51\.100\.31/);
    assert.match(cell(row, '停止原因与说明'), /历史绑定 ECS i-gone/);
  }
  for (const uri of ['/different-source/', '/reassigned/']) {
    assert.doesNotMatch(cell(rows.find(row => cell(row, 'URI') === uri), '停止原因与说明'), /历史绑定 ECS/);
  }
  const incomplete = await backendRows(topology, jumpserver, {}, 'fixture', {
    ecs: { ...ecs, tenants: [{ ...ecs.tenants[0], complete: false }] }, eip, loadKong,
  });
  assert.doesNotMatch(cell(incomplete.rows[0], '停止原因与说明'), /历史绑定 ECS/);
});

test('a DNS and Nginx chain merges identical Kong Targets, preserving distinct endpoints and gateway evidence', async () => {
  const { backendRows, backendHeaders } = require('../lib/nginx-backend-export.mjs');
  const { buildValidatedTopology } = require('../lib/topology/topology-builder.ts');
  const route = (domain, target, host, port, resolution, listen = ['80']) => ({
    domains: [domain], listen, uri: '/api', context: 'http', directive: 'proxy_pass',
    target, upstream: host, backends: [{ host, port, resolution }],
  });
  const jumpserver = { assets: [
    { id: 'entry', ip: '192.0.2.1', inspection: { nginxRoutes: [
      route('entry.example.test', 'http://hop.example.test:8080', 'hop.example.test', 8080, 'hostname'),
      { ...route('entry.example.test', 'http://hop.example.test:8080', 'hop.example.test', 8080, 'hostname'), uri: '/api/doc.html' },
      { ...route('entry.example.test', 'http://hop.example.test:8080', 'hop.example.test', 8080, 'hostname'), uri: '/api/actuator/heapdump' },
      route('entry.example.test', 'http://missing.example.test', 'missing.example.test', 80, 'hostname'),
      { ...route('entry.example.test', 'http://dangling.example.test', 'dangling.example.test', 80, 'hostname'), uri: '/dns-alias' },
      { ...route('entry.example.test', 'http://missing.example.test', 'missing.example.test', '', 'hostname'), uri: '/no-port' },
      { ...route('entry.example.test', 'http://hop.example.test:8080', 'hop.example.test', 8080, 'hostname'), uri: '/dns-hop' },
    ] } },
    { id: 'hop', ip: '192.0.2.2', inspection: { nginxRoutes: [
      { ...route('hop.example.test', 'http://prod_kong/v1', 'prod_kong', 8000, 'ip', ['8080']),
        backends: ['10.179.4.220', '10.179.4.73'].map(host => ({ host, port: 8000, resolution: 'ip' })) },
      { domains: ['hop.example.test'], listen: ['8080'], uri: '~ /.*/doc.html.*', context: 'http',
        directive: 'static/other', target: '', backends: [] },
      { domains: ['hop.example.test'], listen: ['8080'], uri: '~ /.*/actuator/(.*)', context: 'http',
        directive: 'access_control', target: 'deny all', backends: [] },
      { ...route('hop.example.test', 'http://private.example.test', 'private.example.test', 80, 'hostname', ['8080']), uri: '= /dns-hop' },
    ] } },
    ...['192.0.2.3', '192.0.2.4'].map(ip => ({ id: `svc-${ip}`, ip, inspection: {
      processes: [{ pid: 42, command: '/opt/java -jar /opt/order-service.jar' }],
      appPorts: [{ app: 'order-service.jar', port: 9080, addresses: ['0.0.0.0'], pids: [42] }],
    } })),
  ] };
  const deployments = ['192.0.2.3', '192.0.2.4'].map(ip => ({ ip, port: '9080' }));
  const devops = { apps: [{ id: 'order-1', name: 'order_service', envs: { PRODUCT: deployments } }] };
  const dns = { records: [
    { id: 'cname', name: 'hop.example.test', type: 'CNAME', value: 'alias.example.test', status: '启用' },
    { id: 'answer', name: 'alias.example.test', type: 'A', value: '192.0.2.2', status: '启用' },
    { id: 'dangling', name: 'dangling.example.test', type: 'CNAME', value: 'no-answer.example.test', status: '启用' },
  ] };
  const cloudDns = { available: true, zones: [{ name: 'example.test', visibility: 'private', records: [
    { id: 'private', name: 'private.example.test', type: 'A', value: '192.0.2.3', status: '启用' },
  ] }] };
  const { topology } = buildValidatedTopology({ jumpserver, devops, dns, cloudDns }, '2026-11-24T03:00:00Z');
  assert.ok(topology.nodes.some(node => node.id === 'endpoint:192.0.2.3:9080:unknown'));
  const calls = [];
  const loadKong = async (gateway, queries) => {
    calls.push(gateway.ip);
    assert.ok(queries.some(query => query.path === '/v1'));
    return {
      gateway,
      observedAt: '2026-11-24T03:00:00Z',
      routes: [{ id: 'route-1', paths: ['/v1'], service: { id: 'service-1' } }],
      services: { 'service-1': { id: 'service-1', name: `order@${gateway.ip}`, protocol: 'http', host: 'order-upstream' } },
      targets: { 'order-upstream': deployments.map(({ ip, port }) => ({ target: `${ip}:${port}`, weight: 100 })) },
    };
  };
  const { rows, counts } = await backendRows(topology, jumpserver, devops, 'fixture', { loadKong });
  const cell = (row, header) => row[backendHeaders.indexOf(header)];
  assert.deepEqual(calls.sort(), ['10.179.4.220', '10.179.4.73']);
  const viaDns = rows.filter(row => cell(row, 'Nginx转发后端IP') === 'hop.example.test' && cell(row, 'URI') === '/api');
  assert.deepEqual(viaDns.map(row => row.slice(6, 10)), [
    ['192.0.2.3:9080', '进程匹配成功', 'order-1', 'order_service'],
    ['192.0.2.4:9080', '进程匹配成功', 'order-1', 'order_service'],
  ]);
  assert.deepEqual(backendHeaders.slice(6, 9), ['服务IP+端口', '应用匹配说明', '应用ID']);
  const incoming = viaDns[0];
  assert.equal(incoming.length, backendHeaders.length);
  assert.equal(cell(incoming, 'Kong最终Target'), '192.0.2.3:9080');
  assert.deepEqual(cell(incoming, 'Kong Admin').split(' | ').sort(), ['10.179.4.220:8001', '10.179.4.73:8001']);
  assert.deepEqual(cell(incoming, 'Kong Service名').split(' | ').sort(), ['order@10.179.4.220', 'order@10.179.4.73']);
  assert.match(cell(incoming, '完整代理链'), /10\.179\.4\.220:8000 → 192\.0\.2\.3:9080/);
  assert.match(cell(incoming, '完整代理链'), /10\.179\.4\.73:8000 → 192\.0\.2\.3:9080/);
  assert.equal(cell(incoming, 'DNS解析IP'), '192.0.2.2');
  assert.equal(cell(incoming, 'CNAME链'), 'hop.example.test → alias.example.test');
  assert.equal(incoming.at(-1), '2026-11-24T03:00:00Z');
  for (const uri of ['/api/doc.html', '/api/actuator/heapdump']) {
    const regexCase = rows.find(row => cell(row, 'URI') === uri);
    assert.equal(cell(regexCase, '服务IP+端口'), '');
    assert.equal(cell(regexCase, '应用匹配说明'), 'Nginx正则或重写待核实');
    assert.match(cell(regexCase, '停止原因与说明'), /regex location may override/);
  }
  for (const [uri, backendHost, missingDnsHost] of [
    ['/api', 'missing.example.test', 'missing.example.test'],
    ['/dns-alias', 'dangling.example.test', 'no-answer.example.test'],
    ['/dns-hop', 'hop.example.test', 'private.example.test'],
    ['= /dns-hop', 'private.example.test', 'private.example.test'],
  ]) {
    const row = rows.find(row => cell(row, 'URI') === uri && cell(row, 'Nginx转发后端IP') === backendHost);
    assert.deepEqual(row.slice(6, 10), ['', 'DNS待核实', '', '']);
    assert.ok(cell(row, '停止原因与说明').includes(missingDnsHost));
  }
  assert.equal(cell(rows.find(row => cell(row, 'URI') === '/no-port'), '应用匹配说明'), '');
  const direct = rows.filter(row => row[0] === '192.0.2.2' && cell(row, 'URI') === '/api');
  assert.equal(direct.length, 2);
  for (const row of direct) assert.deepEqual(cell(row, 'Nginx转发后端IP').split(' | ').sort(), ['10.179.4.220', '10.179.4.73']);
  assert.equal(counts.kongTargetRows, 4);
  assert.equal(counts.matchedRows, 4);
});

test('confirmed PrivateZone DNS applies only to the source asset and follows its downstream Nginx location', async () => {
  const { backendRows, backendHeaders } = require('../lib/nginx-backend-export.mjs');
  const { buildValidatedTopology } = require('../lib/topology/topology-builder.ts');
  const proxy = (domain, uri, target, backend) => ({
    domains: [domain], listen: ['80'], uri, context: 'http', directive: 'proxy_pass', target,
    backends: [backend],
  });
  const hostname = 'private.example.test';
  const entry = proxy('entry.example.test', '/sys/supplier/', `http://${hostname}`,
    { host: hostname, port: 80, resolution: 'hostname' });
  const hop = proxy(hostname, '/sys/', 'http://service.lan/sys/',
    { host: '192.0.2.4', port: 8181, resolution: 'ip' });
  const jumpserver = { assets: [
    { id: 'confirmed', ip: '192.0.2.1', inspection: { nginxRoutes: [entry] } },
    { id: 'other', ip: '192.0.2.2', inspection: { nginxRoutes: [entry] } },
    { id: 'hop', ip: '192.0.2.3', inspection: { nginxRoutes: [hop, {
      domains: [hostname], listen: ['80'], uri: '~ /druid/', context: 'http', directive: 'access_control', target: 'deny all',
    }] } },
    { id: 'service', ip: '192.0.2.4', inspection: {
      processes: [{ pid: 42, command: '/opt/java -jar /opt/order-service.jar' }],
      appPorts: [{ app: 'order-service.jar', port: 8181, addresses: ['0.0.0.0'], pids: [42] }],
    } },
  ] };
  const devops = { apps: [{ id: 'order-1', name: 'order_service', envs: { TEST: [{ ip: '192.0.2.4', port: '8181' }] } }] };
  const cloudDns = { available: true, zones: [{ id: 'zone-1', name: 'example.test', visibility: 'private', records: [
    { id: 'record-1', name: hostname, type: 'A', value: '192.0.2.3', status: '启用' },
  ] }] };
  const confirmations = [{ sourceAssetId: 'confirmed', zoneId: 'zone-1', hostname, ip: '192.0.2.3' }];
  const { topology } = buildValidatedTopology({ jumpserver, devops, cloudDns }, '2026-11-24T03:00:00Z');
  const { rows } = await backendRows(topology, jumpserver, devops, 'fixture', { cloudDns, privateDnsConfirmations: confirmations });
  const cell = (row, header) => row[backendHeaders.indexOf(header)];
  assert.deepEqual(rows[0].slice(6, 10), ['192.0.2.4:8181', '进程匹配成功', 'order-1', 'order_service']);
  assert.equal(cell(rows[0], 'DNS解析IP'), '192.0.2.3');
  assert.equal(cell(rows[0], '链路置信度'), 'INFERRED');
  assert.match(cell(rows[0], '停止原因与说明'), /来源 Nginx 192\.0\.2\.1 已核实 PrivateZone 记录/);
  assert.deepEqual(rows[1].slice(6, 10), ['', 'DNS待核实', '', '']);

  const stale = await backendRows(topology, jumpserver, devops, 'fixture', {
    cloudDns, privateDnsConfirmations: [{ ...confirmations[0], ip: '192.0.2.5' }],
  });
  assert.deepEqual(stale.rows[0].slice(6, 10), ['', 'DNS待核实', '', '']);
});

test('archived implicit Nginx fallbacks use the forwarded URI and preserve configured static locations', async t => {
  const { backendRows, backendHeaders } = require('../lib/nginx-backend-export.mjs');
  const { buildValidatedTopology } = require('../lib/topology/topology-builder.ts');
  const { execFileSync } = require('node:child_process');
  const { createHash } = require('node:crypto');
  const { mkdir, mkdtemp, rm, writeFile } = require('node:fs/promises');
  const { tmpdir } = require('node:os');
  const { join } = require('node:path');
  const source = await mkdtemp(join(tmpdir(), 'nginx-route-fixture-'));
  t.after(() => rm(source, { recursive: true, force: true }));
  const archive = join(source, 'fixture', 'nginx-configs');
  await mkdir(archive, { recursive: true });
  const checkedAt = '2026-11-24T03:00:00Z';
  const instance = '/proc/12/exe';
  const main = '/etc/nginx/nginx.conf';
  const conf = '/etc/nginx/conf.d/downstream.conf';
  const staticRoute = (host, uri) => ({ domains: [host], listen: ['80'], uri, context: 'http', directive: 'static/other',
    target: '', file: conf, instance, backends: [] });
  const requests = [['implicit.example.test', '/hotel/'], ['explicit.example.test', '/hotel/'],
    ['implicit.example.test', '/'], ['implicit.example.test', '/assets/'],
    ['implicit.example.test', '/legacy/', '/'], ['homepage.example.test', '/legacy/', '/'],
    ['logged.example.test', '/hotel-cms/'], ['implicit.example.test', '^~/distributionorder/', '/'],
    ['implicit.example.test', '^~ /distributionorder']];
  const entryRoutes = requests.map(([host, uri, replacement = ''], i) => ({ ...staticRoute(`entry${i}.example.test`, uri),
    directive: 'proxy_pass', target: `http://${host}${replacement}`, backends: [{ host, port: 80, resolution: 'hostname' }] }));
  const jumpserver = { assets: [
    { id: 'entry', ip: '192.0.2.1', inspection: { nginxRoutes: entryRoutes } },
    { id: 'downstream', ip: '192.0.2.2', inspection: { checkedAt, nginxStatus: 'complete', nginxRoutes: [
      staticRoute('implicit.example.test', '/'), staticRoute('implicit.example.test', '/assets/'), staticRoute('explicit.example.test', '/'),
      staticRoute('homepage.example.test', '/'), staticRoute('logged.example.test', '/'),
    ] } },
  ] };
  const files = [
    { path: main, instance, content: 'http { include conf.d/*.conf; }' },
    { path: conf, instance, content: `
      server { listen 80; server_name implicit.example.test; location /assets/ {}
        location /distributionorder/ { proxy_pass http://192.0.2.5; }
      }
      server { listen 80; server_name explicit.example.test; include explicit.conf; }
      server { listen 80; server_name homepage.example.test; index welcome.html; }
      server { listen 80; server_name logged.example.test;
        location / { if ($logged) { access_log /var/log/nginx/conditional.log; } }
        location /hotel/cms/ { proxy_pass http://192.0.2.5; }
      }
    ` },
    { path: '/etc/nginx/explicit.conf', instance, content: 'location / { if ($blocked) { return 403; } return 301 https://example.test/; }' },
  ];
  // The explicit location is recorded under the include that defines it.
  jumpserver.assets[1].inspection.nginxRoutes[2].file = files[2].path;
  const archiveId = createHash('sha256').update('downstream').digest('hex');
  await writeFile(join(archive, `${archiveId}.json`), JSON.stringify({ assetId: 'downstream', collectedAt: checkedAt, files }));
  const dns = { records: ['implicit', 'explicit', 'homepage', 'logged'].map(name => ({ id: name, name: `${name}.example.test`,
    type: 'A', value: '192.0.2.2', status: '启用' })) };
  const { topology } = buildValidatedTopology({ jumpserver, dns }, checkedAt);
  const checkArchive = (kind, candidates) => JSON.parse(execFileSync('python3', [join(__dirname, '../runtime/nginx_export_archive.py'), source, kind],
    { input: JSON.stringify(candidates), encoding: 'utf8', env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' } }));
  const { rows } = await backendRows(topology, jumpserver, {}, 'fixture', {
    loadServerFallbacks: candidates => checkArchive('fallbacks', candidates),
    loadNginxStops: candidates => checkArchive('stops', candidates),
  });
  const cell = (row, header) => row[backendHeaders.indexOf(header)];
  assert.deepEqual(rows.map(row => cell(row, '应用匹配说明')), [
    '后端路由不存在', '重定向或条件响应', '后端路由不存在', '', '后端路由不存在', '',
    'URI配置错误', 'URI配置错误', '重定向或条件响应',
  ]);
  assert.equal(cell(rows[0], '链路状态'), 'PARTIAL');
  assert.deepEqual(rows[0].slice(6, 10), ['', '后端路由不存在', '', '']);
  assert.match(cell(rows[0], '停止原因与说明'), /未找到匹配 \/hotel\/ 的 location/);
  assert.match(cell(rows[1], '停止原因与说明'), /HTTP 403 条件直接响应、HTTP 301 重定向/);
  assert.match(cell(rows[4], '停止原因与说明'), /未找到匹配 \/ 的 location/);
  assert.doesNotMatch(cell(rows[4], '停止原因与说明'), /下游 Nginx 路由没有 proxy_pass/);
  assert.match(cell(rows[6], '停止原因与说明'), /URI \/hotel-cms\/ 与下游代理路径 \/hotel\/cms\//);
  assert.match(cell(rows[7], '停止原因与说明'), /URI \/ 与下游代理路径 \/distributionorder\//);
  assert.match(cell(rows[8], '停止原因与说明'), /HTTP 301，补全末尾斜杠/);
});

test('archived variable proxies expand only if default and conditional upstream endpoints agree', async t => {
  const { backendRows, backendHeaders } = require('../lib/nginx-backend-export.mjs');
  const { execFileSync } = require('node:child_process');
  const { createHash } = require('node:crypto');
  const { mkdir, mkdtemp, rm, writeFile } = require('node:fs/promises');
  const { tmpdir } = require('node:os');
  const { join } = require('node:path');
  const source = await mkdtemp(join(tmpdir(), 'nginx-variable-proxy-'));
  t.after(() => rm(source, { recursive: true, force: true }));
  const archive = join(source, 'fixture', 'nginx-configs');
  await mkdir(archive, { recursive: true });
  const checkedAt = '2026-11-24T03:00:00Z';
  const file = '/etc/nginx/nginx.conf';
  const instance = '/proc/12/exe';
  const domains = ['entry.example.test'];
  const listen = ['80'];
  const target = 'http://$selected';
  const archiveId = createHash('sha256').update('entry').digest('hex');
  const route = uri => ({ domains, listen, uri, file, instance, target, directive: 'proxy_pass', context: 'http', backends: [] });
  await writeFile(join(archive, `${archiveId}.json`), JSON.stringify({ assetId: 'entry', collectedAt: checkedAt, files: [{
    path: file, instance, content: `http {
      upstream primary { server 192.0.2.2:8080; server 192.0.2.3:8080; }
      upstream same { server 192.0.2.3:8080; server 192.0.2.2:8080; }
      upstream different { server 192.0.2.3:8081; }
      server { listen 80; server_name entry.example.test;
        set $selected primary;
        location /safe/ { if ($http_cookie ~ gray) { set $selected same; } proxy_pass ${target}; }
        location /gray/ { if ($http_cookie ~ gray) { set $selected different; } proxy_pass ${target}; }
      }
    }`,
  }] }));
  const jumpserver = { assets: [{ id: 'entry', ip: '192.0.2.1', inspection: {
    checkedAt, nginxRoutes: [route('/safe/'), route('/gray/')],
  } }] };
  const { rows } = await backendRows({ nodes: [], edges: [], stats: {} }, jumpserver, {}, 'fixture', {
    loadRequestHints: candidates => JSON.parse(execFileSync('python3', [join(__dirname, '../runtime/nginx_export_archive.py'), source, 'requests'],
      { input: JSON.stringify(candidates), encoding: 'utf8', env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' } })),
  });
  const cell = (row, header) => row[backendHeaders.indexOf(header)];
  assert.deepEqual(rows.filter(row => cell(row, 'URI') === '/safe/').map(row => cell(row, '服务IP+端口')),
    ['192.0.2.2:8080', '192.0.2.3:8080']);
  assert.ok(rows.slice(0, 2).every(row => cell(row, '链路置信度') === 'INFERRED'));
  assert.equal(cell(rows[2], '服务IP+端口'), '');
  assert.equal(cell(rows[2], '应用匹配说明'), '运行时变量代理待核实');
  assert.match(cell(rows[2], '停止原因与说明'), /\$http_cookie/);
});

test('proxy traffic to a verified static alias moves to frontend while other upstream branches are retained', async t => {
  const { backendRows, backendHeaders } = require('../lib/nginx-backend-export.mjs');
  const { buildValidatedTopology } = require('../lib/topology/topology-builder.ts');
  const { execFileSync } = require('node:child_process');
  const { createHash } = require('node:crypto');
  const { mkdir, mkdtemp, rm, writeFile } = require('node:fs/promises');
  const { tmpdir } = require('node:os');
  const { join } = require('node:path');
  const source = await mkdtemp(join(tmpdir(), 'nginx-static-proxy-'));
  t.after(() => rm(source, { recursive: true, force: true }));
  const archive = join(source, 'fixture', 'nginx-configs');
  await mkdir(archive, { recursive: true });
  const checkedAt = '2026-11-24T03:00:00Z';
  const main = '/etc/openresty/nginx.conf';
  const staticRoute = (uri, target) => ({ domains: ['localhost'], listen: ['80'], uri, context: 'http',
    directive: 'static/other', target, file: main, instance: 'folidev', backends: [] });
  const backend = host => ({ host, port: 80, resolution: 'ip' });
  const entry = (uri, backends) => ({ ...staticRoute(uri, 'http://files_upstream'), domains: ['assets.example.test'],
    listen: ['443 ssl'], directive: 'proxy_pass', backends });
  const jumpserver = { assets: [
    { id: 'entry', ip: '192.0.2.1', hostname: 'entry', inspection: { nginxRoutes: [
      entry('/file/M00', [backend('192.0.2.2'), backend('192.0.2.3')]),
      entry('/redirect/', [backend('192.0.2.2')]),
    ] } },
    { id: 'files', ip: '192.0.2.2', inspection: { checkedAt, nginxStatus: 'partial', portStatus: 'complete',
      processes: [{ pid: 2, command: `nginx: master process /usr/sbin/nginx -c ${main}` }],
      appPorts: [{ port: 80, addresses: ['*'], pids: [] }], nginxRoutes: [
        staticRoute('/file/M00', '/var/files/cache'), staticRoute('/redirect/', '/var/files/redirect'),
      ] } },
  ] };
  const files = [{ path: main, instance: 'folidev', content: `http {
    include mime.types;
    server { listen 80; server_name localhost;
      location /file/M00 { alias /var/files/cache; if (!-f $file) { content_by_lua_file fetch.lua; } }
      location /redirect/ { alias /var/files/redirect; return 301 /; }
    }
  }` }];
  const archiveId = createHash('sha256').update('files').digest('hex');
  await writeFile(join(archive, `${archiveId}.json`), JSON.stringify({ assetId: 'files', collectedAt: checkedAt, files }));
  const { topology } = buildValidatedTopology({ jumpserver }, checkedAt);
  const { rows, counts, frontend } = await backendRows(topology, jumpserver, {}, 'fixture', {
    loadStaticLocations: candidates => JSON.parse(execFileSync('python3', [join(__dirname, '../runtime/nginx_export_archive.py'), source, 'static'],
      { input: JSON.stringify(candidates), encoding: 'utf8', env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' } })),
  });
  assert.deepEqual(frontend, [['192.0.2.1', 'entry', 'assets.example.test', '443 ssl', '/file/M00',
    'assets.example.test/file/M00', '/var/files/cache', '192.0.2.2:80']]);
  assert.deepEqual(rows.map(row => row[backendHeaders.indexOf('URI')]), ['/file/M00', '/redirect/']);
  assert.equal(rows[0][backendHeaders.indexOf('服务IP+端口')], '192.0.2.3:80');
  assert.equal(counts.backendRows, 2);
});

test('exports different Kong labels for an empty Target list and a URI without route candidates', async () => {
  const { backendRows, backendHeaders } = require('../lib/nginx-backend-export.mjs');
  const jumpserver = { assets: [{ id: 'entry', ip: '192.0.2.1', inspection: { nginxRoutes:
    ['empty', 'query-failed', 'no-route'].map(name => ({
      domains: ['entry.example.test'], listen: ['80'], uri: `/${name}/`, context: 'http', directive: 'proxy_pass',
      target: 'http://kong', upstream: 'kong', backends: [{ host: '10.179.4.73', port: 8000, resolution: 'ip' }],
    })),
  } }] };
  const loadKong = async gateway => ({ gateway, observedAt: '2026-11-24T03:00:00Z',
    routes: ['empty', 'query-failed'].map(name => ({ id: name, paths: [`/${name}/`], service: { id: name } })),
    services: { empty: { id: 'empty', name: 'empty', protocol: 'http', host: 'empty-upstream' } },
    serviceErrors: { 'query-failed': 'timeout' }, targets: { 'empty-upstream': [] },
  });
  const { rows } = await backendRows({ nodes: [], edges: [], stats: {} }, jumpserver, { apps: [] }, 'fixture', { loadKong });
  const note = backendHeaders.indexOf('应用匹配说明');
  assert.deepEqual(rows.map(row => [row[4], row[note]]), [
    ['/empty/', 'Kong网关无Target'], ['/query-failed/', ''], ['/no-route/', 'Kong网关无此路由'],
  ]);
  assert.match(rows[0][backendHeaders.indexOf('停止原因与说明')], /empty-upstream.*Target 清单/);
});

test('exports loopback on the Nginx server that issued the proxy_pass and verifies the local bind', async () => {
  const { backendRows, backendHeaders } = require('../lib/nginx-backend-export.mjs');
  const { buildValidatedTopology } = require('../lib/topology/topology-builder.ts');
  const route = (uri, host, port, resolution = 'ip') => ({
    domains: ['app.example.test'], listen: ['80'], uri, context: 'http',
    directive: 'proxy_pass', target: 'http://backend', upstream: 'backend',
    backends: [{ host, port, resolution }],
  });
  const jumpserver = { assets: [
    { id: 'entry', ip: '192.0.2.1', inspection: {
      nginxRoutes: [route('/api', '192.0.2.2', 80), route('/local', '127.0.0.1', 9080)],
    } },
    { id: 'hop', ip: '192.0.2.2', inspection: {
      nginxRoutes: [route('/api', 'localhost', 9080, 'hostname'), route('/other', '::1', 9081)],
      processes: [{ pid: 42, command: 'java -jar order.jar' }],
      portStatus: 'complete',
      appPorts: [9080, 9081].map(port => ({ app: 'order', port, addresses: ['127.0.0.1'], pids: [42] })),
    } },
  ] };
  const devops = { apps: [{ id: 'order-id', name: 'order', envs: { TEST: [{ ip: '192.0.2.2', port: '9080' }] } }] };
  const { topology } = buildValidatedTopology({ jumpserver, devops });
  const { rows } = await backendRows(topology, jumpserver, devops, 'fixture');
  const cell = (row, column) => row[backendHeaders.indexOf(column)];
  const fromEntry = rows.find(row => row[0] === '192.0.2.1' && row[4] === '/api');
  assert.equal(cell(fromEntry, '服务IP+端口'), '192.0.2.2:9080');
  assert.equal(cell(fromEntry, '应用ID'), 'order-id');
  assert.match(cell(fromEntry, '停止原因与说明'), /本机回环 localhost:9080/);
  assert.equal(cell(rows.find(row => row[4] === '/local'), '服务IP+端口'), '192.0.2.1:9080');
  const ipv6 = rows.find(row => row[4] === '/other');
  assert.equal(cell(ipv6, '服务IP+端口'), '192.0.2.2:9081');
  assert.equal(cell(ipv6, '应用匹配说明'), '端口未监听');
  assert.equal(cell(rows.find(row => row[0] === '192.0.2.2' && row[4] === '/api'), 'Hostname链最终后端IP'), '');
});

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { frontendPath, frontendRows, routeRows, routeHeaders, csv, matchEndpointProcess } = require('../lib/nginx-export.mjs');
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
test('CSV quoting preserves commas, quotes and newlines', () => {
  assert.equal(csv([['a,b', 'c"d', 'e\nf']]), '\ufeff"a,b","c""d","e\nf"\r\n');
});
test('application IDs require a unique matching listener, PID and deployment port', () => {
  const jump = { assets: [{ ip: '10.0.0.2', inspection: { processes: [{ pid: 42 }], appPorts: [{ app: 'order', port: 8080, pids: [42] }] } }] };
  const apps = { apps: [{ id: '1', name: 'order', port: '8080', envs: { TEST: [{ ip: '10.0.0.2' }] } }] };
  assert.equal(matchEndpointProcess(jump, apps, '10.0.0.2', 8080).id, '1');
  assert.equal(matchEndpointProcess(jump, apps, '10.0.0.2', 8081).status, 'UNKNOWN');
  assert.equal(matchEndpointProcess({ assets: [...jump.assets, ...jump.assets] }, apps, '10.0.0.2', 8080).status, 'AMBIGUOUS');
  assert.equal(matchEndpointProcess(jump, { apps: [...apps.apps, { ...apps.apps[0], id: '2' }] }, '10.0.0.2', 8080).status, 'AMBIGUOUS');
  assert.equal(matchEndpointProcess({ assets: [{ ...jump.assets[0], inspection: { ...jump.assets[0].inspection, processes: [] } }] }, apps, '10.0.0.2', 8080).status, 'UNKNOWN');
});

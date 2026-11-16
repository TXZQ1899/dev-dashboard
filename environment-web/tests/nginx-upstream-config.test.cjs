const { test } = require('node:test');
const assert = require('node:assert/strict');

test('finds all same-instance upstream definitions and preserves effective directives', async () => {
  const { findUpstreamDefinitions } = await import('../lib/nginx-upstream-config.ts');
  const files = [
    { path: '/etc/nginx/upstreams.conf', instance: 'master-1', content: `# upstream orders { server ignored; }
upstream orders {
  # server 10.0.0.1:80;
  zone orders 64k; # shared zone
  server 10.0.0.2:80 weight=2;
  server "host#literal:80";
  include upstream-servers.conf;
}` },
    { path: '/etc/nginx/other.conf', instance: 'master-2', content: 'upstream orders { server 10.0.0.3; }' },
  ];
  const definitions = findUpstreamDefinitions(files, 'master-1', 'orders');
  assert.equal(definitions.length, 1);
  assert.equal(definitions[0].path, '/etc/nginx/upstreams.conf');
  assert.equal(definitions[0].content, `upstream orders {
  zone orders 64k; 
  server 10.0.0.2:80 weight=2;
  server "host#literal:80";
  include upstream-servers.conf;
}`);
  assert.deepEqual(findUpstreamDefinitions(files, '', 'orders'), []);
  assert.deepEqual(findUpstreamDefinitions(files, 'master-1', 'missing'), []);
});

test('retains multiple candidates and ignores incomplete definitions', async () => {
  const { findUpstreamDefinitions } = await import('../lib/nginx-upstream-config.ts');
  const files = [
    { path: 'one', instance: 'i', content: 'upstream u { server a; }' },
    { path: 'two', instance: 'i', content: 'upstream u { server b; }' },
    { path: 'broken', instance: 'i', content: 'upstream u { server c;' },
  ];
  assert.deepEqual(findUpstreamDefinitions(files, 'i', 'u').map(x => x.path), ['one', 'two']);
});

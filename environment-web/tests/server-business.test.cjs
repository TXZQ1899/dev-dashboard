const { test } = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const fs = require('node:fs');
const vm = require('node:vm');
const code = ts.transpileModule(fs.readFileSync('lib/server-business.ts', 'utf8'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText;
const sandbox = { exports: {}, require: () => ({ resourceIdentity: r => r.resourceKey }) };
vm.runInNewContext(code, sandbox);
const { classifyServers, businessCategories, businessLabel } = sandbox.exports;
const server = (id, ip, publicIps = [], name = '') => ({ resourceKey: id, name, privateIps: [ip], publicIps });
const evidence = (ip, domainName) => ({ ip, domainName, environment: 'PRODUCT', unitType: 'APPLICATION' });
test('shared categories use the approved priority and retain all associations', () => {
  const rows = [server('a', '10.0.0.1', ['203.0.113.1']), server('b', '10.0.0.2')];
  const result = classifyServers(rows, [
    evidence('10.0.0.1', '商旅'), evidence('203.0.113.1', 'group-domain'),
    evidence('10.0.0.1', null), evidence('10.0.0.2', '商旅'),
  ]);
  assert.equal(result.get('a').category, 'group');
  assert.deepEqual(Array.from(result.get('a').categories), ['group', 'travel', 'other']);
  assert.equal(result.get('b').category, 'travel');
});
test('unmatched and ambiguous addresses do not invent associations', () => {
  const rows = [server('a', '10.0.0.1'), server('b', '10.0.0.1'), server('c', '10.0.0.3')];
  const result = classifyServers(rows, [evidence('10.0.0.1', '商旅')]);
  assert.equal(result.get('a').category, 'unknown');
  assert.equal(result.get('b').category, 'unknown');
  assert.equal(result.get('a').ambiguous, true);
  assert.equal(result.get('c').category, 'unknown');
  assert.equal(result.get('c').ambiguous, false);
});
test('instance names containing 商旅 add a travel association alongside catalog evidence', () => {
  const rows = [server('a', '10.0.0.1', [], '生产-商旅-01'), server('b', '10.0.0.2', [], '其它')];
  const result = classifyServers(rows, [evidence('10.0.0.1', '集团')]);
  assert.equal(result.get('a').category, 'group');
  assert.deepEqual(Array.from(result.get('a').categories), ['group', 'travel']);
  assert.equal(result.get('b').category, 'unknown');
  assert.equal(classifyServers([rows[0]], []).get('a').category, 'travel');
});
test('project is used only for unclassified instances and excludes placeholders', () => {
  const rows = ['大数据', '默认资源组', '未指定项目', '', '商旅', '集团', '大数据'].map((project, i) => ({ ...server(String(i), `10.0.0.${i}`, [], i === 4 ? '商旅服务器' : ''), project }));
  const result = classifyServers(rows, [evidence('10.0.0.5', '集团'), evidence('10.0.0.6', null)], row => row.project);
  assert.equal(result.get('0').category, 'project:大数据');
  assert.deepEqual(Array.from(result.get('0').categories), ['project:大数据']);
  for (const id of ['1', '2', '3']) assert.equal(result.get(id).category, 'unknown');
  assert.equal(result.get('4').category, 'travel');
  assert.equal(result.get('5').category, 'group');
  assert.equal(result.get('6').category, 'other');
  assert.equal(businessLabel(result.get('0').category), '大数据');
  assert.deepEqual(Array.from(businessCategories(rows, result)), ['group', 'travel', 'other', 'project:大数据', 'unknown']);
  assert.equal(classifyServers([rows[4]], [], () => '商旅').get('4').category, 'travel');
  assert.equal(classifyServers([rows[0]], [], () => '商旅').get('0').category, 'travel');
});

const { test } = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const fs = require('node:fs');
const vm = require('node:vm');
const code = ts.transpileModule(fs.readFileSync('lib/ecs-apps.ts', 'utf8'), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
const sandbox = { exports: {}, require: () => ({ resourceIdentity: r => r.resourceKey }) };
vm.runInNewContext(code, sandbox);
const { associateServerApps, csvCell, sortServerRows } = sandbox.exports;
test('应用跨环境及多个地址去重，共享 IP 保留待确认', () => {
  const rows = [ { resourceKey: 'a', privateIps: ['1', 'shared'], publicIps: ['2'] }, { resourceKey: 'b', privateIps: ['shared'], publicIps: [] } ];
  const apps = [{ id: '42', name: '应用', envs: { TEST: [{ ip: '1' }, { ip: 'shared' }], PRODUCT: [{ ip: '2' }] } }];
  const evidence = ['1', '2', 'shared'].map(ip => ({ ip, appId: '42', unitType: 'APPLICATION' }));
  const result = associateServerApps(rows, apps, evidence, () => [{ name: '应用' }], (app, process) => app.name === process.name);
  assert.equal(result.get('a').apps.length, 1);
  assert.equal(result.get('a').apps[0].id, '42');
  assert.equal(result.get('b').apps.length, 0);
  assert.equal(result.get('a').ambiguous, true);
  assert.equal(result.get('b').ambiguous, true);
});
test('仅目录和进程同时匹配且归属唯一时计入', () => {
  const rows = [{ resourceKey: 'a', privateIps: ['1'], publicIps: [] }];
  const apps = [{ id: '1', name: 'one' }, { id: '2', name: 'two' }];
  const item = id => ({ ip: '1', appId: id, unitType: 'APPLICATION' });
  const run = (evidence, processes, match) => associateServerApps(rows, apps, evidence, () => processes, match).get('a');
  assert.equal(run([item('1')], [], () => true).apps.length, 0);
  assert.equal(run([], [{}], () => true).apps.length, 0);
  assert.equal(run([item('1'), item('2')], [{}], () => true).ambiguous, true);
  assert.equal(run([item('1'), item('2')], [{}], () => true).apps.length, 0);
  assert.equal(run([item('1')], [{}], () => true).apps.length, 1);
});
test('CSV 转义引号、换行和公式', () => {
  assert.equal(csvCell('a,"b"'), '"a,""b"""');
  assert.equal(csvCell('=1+1'), "'=1+1");
  assert.equal(csvCell('a\nb'), '"a\nb"');
});
test('服务器数值和到期时间排序，缺失时间始终排最后', () => {
  const rows = [
    { resourceKey: 'a', cpu: 4, memoryGiB: 16, chargeType: 'PrePaid', expiredAt: '2027-01-01T00:00:00Z' },
    { resourceKey: 'b', cpu: 8, memoryGiB: 8, chargeType: 'PostPaid', expiredAt: '2026-01-01T00:00:00Z' },
    { resourceKey: 'c', cpu: 2, memoryGiB: 32, chargeType: 'PrePaid', expiredAt: '2026-12-01T00:00:00Z' },
    { resourceKey: 'd', cpu: 1, memoryGiB: 4, chargeType: 'PrePaid', expiredAt: 'invalid' },
  ];
  const associations = new Map([['a', { apps: [{}, {}] }], ['b', { apps: [{}] }]]);
  const order = (key, direction) => Array.from(sortServerRows(rows, associations, { key, direction }), row => row.resourceKey).join('');
  assert.equal(order('cpu', 'asc'), 'dcab');
  assert.equal(order('memory', 'desc'), 'cabd');
  assert.equal(order('apps', 'desc'), 'abcd');
  assert.equal(order('expiry', 'asc'), 'cabd');
  assert.equal(order('expiry', 'desc'), 'acbd');
  assert.equal(rows[0].resourceKey, 'a');
});

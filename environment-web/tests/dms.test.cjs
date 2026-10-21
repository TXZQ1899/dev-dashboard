/* eslint-disable @typescript-eslint/no-require-imports -- Project test suites use CommonJS. */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const fs = require('node:fs');
const vm = require('node:vm');
const output = ts.transpileModule(fs.readFileSync('lib/dms.ts', 'utf8'), {
  compilerOptions: {
    target: ts.ScriptTarget.ES2022,
    module: ts.ModuleKind.CommonJS,
    esModuleInterop: true,
  },
}).outputText;
const fixture = {
  available: true,
  collectedAt: '2026-10-20T06:00:00.000Z',
  instances: [
    {
      实例名称: 'Prod-UserCenter',
      实例ID: 'rm-1',
      实例地址: 'rm-1.mysql.rds.aliyuncs.com:3306',
      类型: 'MySQL',
      实例地区: '华东2（上海）',
      实例IP: '10.179.0.182',
      登录状态: '免登录',
      dmsInstanceIds: ['11', '12'],
      重复记录: 1,
    },
    {
      实例名称: 'FosunScrm-Product',
      实例ID: 'r-2',
      实例地址: 'r-2.redis.rds.aliyuncs.com:6379',
      类型: 'Redis',
      实例地区: '华东2（上海）',
      实例IP: '',
      登录状态: '未登录',
    },
    {
      实例名称: 'Prod, Finance',
      实例ID: 'pgm-3',
      实例地址: 'pgm-3.pg.rds.aliyuncs.com:1921',
      类型: 'PostgreSQL',
      实例地区: '华南1（深圳）',
      实例IP: '10.58.9.11',
      登录状态: '未登录',
    },
    {
      实例名称: 'prod-mbp-hotel',
      实例ID: 'mg-4',
      实例地址: 'mg-4.mongodb.rds.aliyuncs.com:3717',
      类型: 'MongoDB',
      实例地区: '华东1（杭州）',
      实例IP: '10.179.4.7',
      登录状态: '免登录',
    },
  ],
};
const sandbox = { exports: {}, require: () => fixture };
vm.runInNewContext(output, sandbox);
const d = sandbox.exports;
// The sandbox returns objects from another realm, so every array assertion is
// copied locally before comparing.
const ids = (rows) => [...rows.map((r) => r.id)];
test('snapshot rows normalize to readable fields and keep merge evidence', () => {
  assert.equal(d.dms.available, true);
  assert.equal(d.instances.length, 4);
  assert.deepEqual(
    [...d.instances.map((r) => r.name)],
    ['Prod-UserCenter', 'FosunScrm-Product', 'Prod, Finance', 'prod-mbp-hotel'],
  );
  const first = d.instances[0];
  assert.equal(first.dbType, 'MySQL');
  assert.equal(first.region, '华东2（上海）');
  assert.equal(first.ip, '10.179.0.182');
  assert.deepEqual([...first.dmsIds], ['11', '12']);
  assert.equal(first.duplicated, 1);
  assert.equal(d.instances[1].dmsIds.length, 0);
  assert.equal(d.instances[1].duplicated, 0);
});
test('summarize splits free-login and unresolved addresses', () => {
  const s = d.summarize(d.instances);
  assert.equal(s.count, 4);
  assert.equal(s.free, 2);
  assert.equal(s.needLogin, 2);
  assert.equal(s.types, 4);
  assert.equal(s.resolved, 3);
  assert.equal(s.unresolved, 1);
});
test('filters combine query, login status, type and region', () => {
  assert.equal(d.filterInstances(d.instances, d.emptyFilters).length, 4);
  assert.deepEqual(
    ids(d.filterInstances(d.instances, { ...d.emptyFilters, loginStatus: '免登录' })),
    ['rm-1', 'mg-4'],
  );
  assert.deepEqual(
    ids(d.filterInstances(d.instances, { ...d.emptyFilters, dbType: 'Redis' })),
    ['r-2'],
  );
  assert.deepEqual(
    ids(d.filterInstances(d.instances, { ...d.emptyFilters, region: '华南1（深圳）' })),
    ['pgm-3'],
  );
  assert.deepEqual(
    ids(d.filterInstances(d.instances, { ...d.emptyFilters, query: '10.179.' })),
    ['rm-1', 'mg-4'],
  );
  assert.deepEqual(
    ids(d.filterInstances(d.instances, { ...d.emptyFilters, query: 'RDS.ALIYUNCS' })),
    ['rm-1', 'r-2', 'pgm-3', 'mg-4'],
  );
  assert.deepEqual(
    ids(d.filterInstances(d.instances, { ...d.emptyFilters, loginStatus: '未登录', dbType: 'Redis' })),
    ['r-2'],
  );
  assert.equal(d.filterInstances(d.instances, { ...d.emptyFilters, query: 'nope' }).length, 0);
});
test('distinct values sort without locale drift', () => {
  assert.deepEqual([...d.distinctValues(d.instances, 'dbType')], ['MongoDB', 'MySQL', 'PostgreSQL', 'Redis']);
  assert.deepEqual([...d.distinctValues(d.instances, 'region')], ['华东1（杭州）', '华东2（上海）', '华南1（深圳）']);
});
test('csv export keeps header order, BOM and quoting', () => {
  const csv = d.instancesCsv(d.instances);
  const lines = csv.replace('\uFEFF', '').trim().split('\r\n');
  assert.ok(csv.startsWith('\uFEFF'));
  assert.equal(lines[0], '实例名称,实例地址,类型,实例地区,实例IP,登录状态,实例ID');
  assert.equal(
    lines[1],
    'Prod-UserCenter,rm-1.mysql.rds.aliyuncs.com:3306,MySQL,华东2（上海）,10.179.0.182,免登录,rm-1',
  );
  assert.equal(
    lines[2],
    'FosunScrm-Product,r-2.redis.rds.aliyuncs.com:6379,Redis,华东2（上海）,,未登录,r-2',
  );
  assert.equal(
    lines[3],
    '"Prod, Finance",pgm-3.pg.rds.aliyuncs.com:1921,PostgreSQL,华南1（深圳）,10.58.9.11,未登录,pgm-3',
  );
  assert.equal(d.instancesCsv([]), '\uFEFF' + '实例名称,实例地址,类型,实例地区,实例IP,登录状态,实例ID\r\n');
});

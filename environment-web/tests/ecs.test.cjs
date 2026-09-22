/* eslint-disable @typescript-eslint/no-require-imports -- Project test suites use CommonJS. */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const fs = require('node:fs');
const vm = require('node:vm');
const output = ts.transpileModule(fs.readFileSync('lib/ecs.ts', 'utf8'), {
  compilerOptions: {
    target: ts.ScriptTarget.ES2022,
    module: ts.ModuleKind.CommonJS,
    esModuleInterop: true,
  },
}).outputText;
const sandbox = {
  exports: {},
  require: () => require('../lib/ecs-snapshot.json'),
  URLSearchParams,
};
vm.runInNewContext(output, sandbox);
const e = sandbox.exports;
const raw = JSON.parse(
  fs.readFileSync('../ecs-export-cn-shanghai/ecs-list-all.json', 'utf8'),
).Instances.Instance;
test('import preserves every instance and exact configuration totals', () => {
  assert.equal(e.instances.length, 204);
  assert.equal(new Set(e.instances.map((r) => r.id)).size, 204);
  const s = e.summarize(e.instances);
  assert.equal(s.cpu, 992);
  assert.equal(s.memory, 3865.5);
  for (const r of e.instances) {
    const original = raw.find((i) => i.InstanceId === r.id);
    assert.equal(r.cpu, original.Cpu);
    assert.equal(r.memoryGiB, original.Memory / 1024);
    assert.equal(r.os, original.OSName);
    for (const ip of original.PublicIpAddress.IpAddress)
      assert.ok(r.publicIps.includes(ip));
    const eip = original.EipAddress?.IpAddress;
    if (eip) assert.ok(r.publicIps.includes(eip));
  }
});
test('missing project remains separate from the default project', () => {
  assert.equal(
    e.filterInstances(e.instances, { ...e.emptyFilters, project: '""' }).length,
    88,
  );
  assert.equal(
    e.filterInstances(e.instances, {
      ...e.emptyFilters,
      project: JSON.stringify('rg-acfmwvwagoicztq'),
    }).length,
    10,
  );
  assert.equal(e.projectName(''), '未指定项目');
});
test('all levels partition instances without duplicating IDs or configuration totals', () => {
  const tree = e.groupInstances(e.instances, [
    'project',
    'tag:env',
    'tag:monitorcpu',
  ]);
  const leaves = [];
  function visit(groups) {
    for (const g of groups) {
      if (g.children.length) visit(g.children);
      else leaves.push(...g.rows);
    }
  }
  visit(tree);
  assert.equal(leaves.length, 204);
  assert.equal(new Set(leaves.map((r) => r.id)).size, 204);
  assert.equal(e.summarize(leaves).cpu, 992);
});
test('tag filters intersect, preserve absence and exact raw values', () => {
  assert.equal(
    e.filterInstances(e.instances, {
      ...e.emptyFilters,
      tags: [{ key: 'env', value: '"prod"' }],
    }).length,
    132,
  );
  assert.equal(
    e.filterInstances(e.instances, {
      ...e.emptyFilters,
      tags: [{ key: 'env', value: 'null' }],
    }).length,
    49,
  );
  const f = {
    ...e.emptyFilters,
    tags: [
      { key: 'env', value: '"prod"' },
      { key: 'monitorcpu', value: 'null' },
    ],
  };
  assert.equal(
    e.filterInstances(e.instances, f).length,
    e.instances.filter(
      (r) => r.tags.env === 'prod' && !Object.hasOwn(r.tags, 'monitorcpu'),
    ).length,
  );
  const r = { ...e.instances[0], tags: { env: '' } };
  assert.equal(e.tagToken(r, 'env'), '""');
  assert.equal(e.tagToken(r, 'absent'), 'null');
});
test('project, IP, status and search compose and survive cross-page links', () => {
  const r = e.instances.find(
    (r) => r.publicIps.length && r.tags.env === 'prod',
  );
  const f = {
    query: r.publicIps[0],
    project: JSON.stringify(r.projectId),
    status: r.status,
    publicIp: 'yes',
    tags: [{ key: 'env', value: '"prod"' }],
  };
  assert.ok(e.filterInstances(e.instances, f).some((i) => i.id === r.id));
  assert.equal(
    JSON.stringify(e.filtersFromQuery('?' + e.filtersQuery(f))),
    JSON.stringify(f),
  );
  assert.equal(
    e.filterInstances(e.instances, { ...f, publicIp: 'no' }).length,
    0,
  );
  assert.equal(e.filtersFromQuery('?tag=bad').tags.length, 0);
});

test('sorting does not depend on the SSR or browser default locale', () => {
  const other = {
    exports: {},
    require: () => require('../lib/ecs-snapshot.json'),
    URLSearchParams,
  };
  vm.createContext(other);
  vm.runInContext(
    "String.prototype.localeCompare = function () { throw new Error('Runtime-dependent sorting'); };",
    other,
  );
  vm.runInContext(output, other);
  assert.equal(
    JSON.stringify(other.exports.tagKeys),
    JSON.stringify(e.tagKeys),
  );
  assert.equal(
    JSON.stringify(
      other.exports.groupInstances(other.exports.instances, [
        'project',
        'tag:env',
      ]),
    ),
    JSON.stringify(e.groupInstances(e.instances, ['project', 'tag:env'])),
  );
});

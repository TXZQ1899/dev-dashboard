const { test } = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const fs = require('node:fs');
const vm = require('node:vm');
const js = ts.transpileModule(fs.readFileSync('lib/server-resources.ts', 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const context = { exports: {} }; vm.runInNewContext(js, context);
const { partitionCloud, capacity } = context.exports;
test('partitions by supported tenant names and preserves unknown or conflicting identities', () => {
  const rows = [
    { tenantId: 'a', cpu: 2, memoryGiB: 4 },
    { tenantId: 'b', tenantName: 'Thomascookjv', cpu: 4, memoryGiB: 8 },
    { cpu: 8, memoryGiB: 16 },
    { tenantId: 'c', tenantName: '修平', cpu: 1, memoryGiB: 2 },
  ];
  const scopes = [{ tenantId: 'a', tenantName: '上海修平' }, { tenantId: 'c', tenantName: 'Thomascookjv' }];
  const groups = partitionCloud(rows, scopes);
  assert.equal(groups.xiuping.length, 1); assert.equal(groups.thomascookjv.length, 1); assert.equal(groups.unknown.length, 2);
  assert.deepEqual(JSON.parse(JSON.stringify(capacity(groups.unknown))), { count: 2, cpu: 9, memory: 18 });
});

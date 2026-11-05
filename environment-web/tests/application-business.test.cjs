const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');

const moduleContext = { exports: {} };
vm.runInNewContext(ts.transpileModule(fs.readFileSync('lib/application-business.ts', 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS },
}).outputText, moduleContext);

test('business domains map to the three application tabs', () => {
  const { businessTab } = moduleContext.exports;
  assert.equal(businessTab('商旅'), 'travel');
  assert.equal(businessTab('地中海'), 'group');
  assert.equal(businessTab('其他业务领域'), 'group');
  assert.equal(businessTab(null), 'other');
  assert.equal(businessTab('未分类'), 'other');
});

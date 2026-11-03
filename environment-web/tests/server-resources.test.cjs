const { test } = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const fs = require('node:fs');
const vm = require('node:vm');
const js = ts.transpileModule(fs.readFileSync('lib/server-resources.ts', 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const context = { exports: {} }; vm.runInNewContext(js, context);
const { partitionCloud, capacity, siteTenantMap, cloudSiteLabel } = context.exports;
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
test('keeps tenant site assignment after tenants are renamed', () => {
  const scopes = [
    { tenantId: 'legacy-aliyun', tenantName: '珠海横琴旅文' },
    { tenantId: 'tenant-2', tenantName: 'CM酷怡国际' },
  ];
  const rows = [
    { tenantId: 'legacy-aliyun', cpu: 2, memoryGiB: 4 },
    { tenantId: 'tenant-2', cpu: 4, memoryGiB: 8 },
    { tenantId: 'tenant-2', cpu: 1, memoryGiB: 2 },
  ];
  assert.deepEqual(JSON.parse(JSON.stringify(siteTenantMap(scopes))),
    { xiuping: 'legacy-aliyun', thomascookjv: 'tenant-2' });
  const groups = partitionCloud(rows, scopes);
  assert.equal(groups.xiuping.length, 1);
  assert.equal(groups.thomascookjv.length, 2);
  assert.equal(groups.unknown.length, 0);
});

test('uses a loading or error label until the configured tenant name is known', () => {
  const mapping = siteTenantMap([{ tenantId: 'legacy-aliyun', tenantName: '上海修平' }]);
  assert.equal(cloudSiteLabel('xiuping', mapping, null), '阿里云-加载中');
  assert.equal(cloudSiteLabel('xiuping', mapping, null, true), '阿里云-租户信息不可用');
  assert.equal(cloudSiteLabel('xiuping', mapping, [{ id: 'legacy-aliyun', name: '珠海横琴旅文' }]), '阿里云-珠海横琴旅文');
  assert.equal(cloudSiteLabel('xiuping', mapping, [{ id: 'other', name: '其他租户' }]), '阿里云-租户未确认');
});

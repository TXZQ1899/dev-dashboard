const { test } = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const fs = require('node:fs');
const vm = require('node:vm');

const js = ts.transpileModule(fs.readFileSync('lib/aliyun-tenant-cache.ts', 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
function setup(cached, fetchAliyunTenants) {
  const saved = new Map(cached === undefined ? [] : [['envscope:aliyun-tenant-names:v1', cached]]);
  const window = { localStorage: { getItem: (key) => saved.get(key) ?? null, setItem: (key, value) => saved.set(key, value) }, addEventListener() {}, removeEventListener() {} };
  const context = { exports: {}, window, Date, JSON, Set, require: (name) => name === 'react'
    ? { useSyncExternalStore: (subscribe, snapshot) => ({ subscribe, snapshot }) }
    : { fetchAliyunTenants } };
  vm.runInNewContext(js, context);
  return { hook: context.exports.useAliyunTenants(), saved };
}
const tick = () => new Promise((resolve) => setImmediate(resolve));

test('shows cached names, merges requests and updates cache after revalidation', async () => {
  let finish; let calls = 0;
  const pending = new Promise((resolve) => { finish = resolve; });
  const { hook, saved } = setup(JSON.stringify([{ id: 'a', name: '旧名称' }]), () => { calls++; return pending; });
  assert.equal(hook.snapshot().tenants[0].name, '旧名称');
  const stop1 = hook.subscribe(() => {}); const stop2 = hook.subscribe(() => {});
  assert.equal(calls, 1);
  finish([{ id: 'a', name: '新名称', enabled: true, secret: 'discard' }]);
  await tick();
  assert.equal(hook.snapshot().tenants[0].name, '新名称');
  assert.deepEqual(JSON.parse(saved.get('envscope:aliyun-tenant-names:v1')), [{ id: 'a', name: '新名称' }]);
  stop1(); stop2();
});

test('handles unavailable cache and failed refresh without losing cached names', async () => {
  const bad = setup('{', () => Promise.reject(new Error('offline'))).hook;
  assert.equal(bad.snapshot().tenants, null);
  bad.subscribe(() => {}); await tick();
  assert.equal(bad.snapshot().failed, true);
  const cached = setup(JSON.stringify([{ id: 'a', name: '已缓存' }]), () => Promise.reject(new Error('offline'))).hook;
  cached.snapshot(); cached.subscribe(() => {}); await tick();
  assert.equal(cached.snapshot().tenants[0].name, '已缓存');
  assert.equal(cached.snapshot().failed, false);
});

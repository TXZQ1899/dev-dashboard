const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const output = ts.transpileModule(fs.readFileSync(path.join(__dirname, '../lib/deployment-catalog.ts'), 'utf8'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText;
const sandbox = { exports: {} };
vm.runInNewContext(output, sandbox);
const { catalogNeedsPolling, isHistoricalCatalog } = sandbox.exports;

test('catalog polls unfinished current inputs but does not loop on historical snapshots or failures', () => {
  const current = { source: { observedAt: '2026-10-21T08:00:00Z' }, activeSnapshot: { collectedAt: '2026-10-21T16:00:00+08:00', matchesCatalog: true }, job: { status: 'ready' } };
  assert.equal(catalogNeedsPolling(current), false);
  assert.equal(catalogNeedsPolling({ ...current, job: { status: 'queued' } }), true);
  assert.equal(catalogNeedsPolling({ ...current, activeSnapshot: { ...current.activeSnapshot, matchesCatalog: false } }), true);
  assert.equal(catalogNeedsPolling({ ...current, source: null }), true);
  assert.equal(catalogNeedsPolling({ ...current, source: null, job: { status: 'failed' } }), false);
  const historical = { ...current, activeSnapshot: { collectedAt: '2026-10-20T08:00:00Z', matchesCatalog: false } };
  assert.equal(isHistoricalCatalog(historical), true);
  assert.equal(catalogNeedsPolling(historical), false);
  assert.equal(catalogNeedsPolling({ ...historical, activeSnapshot: { collectedAt: null, matchesCatalog: false } }), false);
});

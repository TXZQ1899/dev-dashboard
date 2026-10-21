const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

function evaluate(file, requireModule = () => {}) {
  const sandbox = { exports: {}, require: requireModule };
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText, sandbox);
  return sandbox.exports;
}

const lib = evaluate(path.join(__dirname, '../lib/datetime.ts'));

test('UTC timestamps are converted to Beijing time (+8)', () => {
  assert.equal(lib.formatBeijingTime('2026-10-16T06:20:49.298316+00:00'), '2026-10-16 14:20:49');
  assert.equal(lib.formatBeijingTime('2026-09-28T01:55:21.608Z'), '2026-09-28 09:55:21');
});

test('offsets and naive local strings keep their Beijing wall clock', () => {
  assert.equal(lib.formatBeijingTime('2026-09-30T15:30:26+08:00'), '2026-09-30 15:30:26');
  assert.equal(lib.formatBeijingTime('2026-09-30 15:30:26'), '2026-09-30 15:30:26');
  assert.equal(lib.formatBeijingTime('2026-09-30 15:30'), '2026-09-30 15:30:00');
});

test('minute and date styles plus fallbacks', () => {
  assert.equal(lib.formatBeijingMinute('2026-10-16T06:20:49+00:00'), '2026-10-16 14:20');
  assert.equal(lib.formatBeijingDate('2026-10-16T06:20:49+00:00'), '2026-10-16');
  assert.equal(lib.formatBeijingTime(''), '');
  assert.equal(lib.formatBeijingTime('not-a-date'), '');
  assert.equal(lib.formatBeijingDate(null, '—'), '—');
});

test('epoch milliseconds and Date inputs are accepted', () => {
  const ms = Date.parse('2026-10-16T06:20:49Z');
  assert.equal(lib.formatBeijingTime(ms), '2026-10-16 14:20:49');
  assert.equal(lib.formatBeijingTime(new Date(ms)), '2026-10-16 14:20:49');
});

test('snapshot ids render their embedded Beijing wall clock', () => {
  assert.equal(lib.formatSnapshotId('20261019-205400-790994'), '2026-10-19 20:54:00');
  assert.equal(lib.formatSnapshotId('topology-20261019-205400-790994'), '2026-10-19 20:54:00');
  assert.equal(lib.formatSnapshotId('20261019-205400-790994', 'minute'), '2026-10-19 20:54');
  assert.equal(lib.formatSnapshotId('20261019-205400-790994', 'date'), '2026-10-19');
  assert.equal(lib.formatSnapshotId(''), '—');
  assert.equal(lib.formatSnapshotId('not-a-snapshot', 'second', 'raw'), 'raw');
});

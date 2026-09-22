/* eslint-disable typescript/no-require-imports -- CommonJS Node test harness. */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

// The progress panel is rendered with a real job payload, so these assertions cover
// what the user actually sees instead of matching source text.
function load() {
  const sandbox = {
    exports: {},
    require: (id) => {
      if (id === 'react') return { ...React, useState: (v) => [v, () => {}] };
      if (id === 'react/jsx-runtime') return require(id);
      return {};
    },
  };
  vm.runInNewContext(
    ts.transpileModule(fs.readFileSync('components/collection-progress.tsx', 'utf8'), {
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
    }).outputText,
    sandbox,
  );
  return sandbox.exports;
}
const mod = load();
const text = (job) =>
  renderToStaticMarkup(React.createElement(mod.CollectionProgress, { job }))
    .replace(/<[^>]+>/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();

const full = {
  status: 'running',
  phase: '并行采集：Codeup、DevOps',
  progress: {
    completed: 4, total: 7,
    sources: { ECS: '完成', CLB: '完成', NAT: '完成', JumpServer: '完成', Codeup: '采集中', 'Local GitLab': '完成', DevOps: '采集中' },
    slots: { 1: { source: 'DevOps', status: '采集中' }, 2: { source: 'Codeup', status: '采集中' }, 3: { source: null, status: '空闲' } },
    slotHistory: {
      1: [{ source: 'ECS', status: '完成' }, { source: 'JumpServer', status: '完成' }, { source: 'DevOps', status: '采集中' }],
      2: [{ source: 'CLB', status: '完成' }, { source: 'Codeup', status: '采集中' }],
      3: [{ source: 'NAT', status: '完成' }, { source: 'Local GitLab', status: '完成' }],
    },
  },
  slotLogs: { 1: 'JumpServer 10/421', 2: 'Codeup page 1: 16 records', 3: 'NAT gateways: 1' },
};

test('finished sources stay visible alongside running ones', () => {
  const out = text(full);
  // All seven sources, each with its own state, not just the active one.
  for (const label of ['阿里云 ECS', '阿里云 CLB', '阿里云 NAT', 'JumpServer', 'Codeup（云效）', 'Local GitLab', 'DevOps'])
    assert.ok(out.includes(label), `缺少数据源 ${label}`);
  assert.ok(out.includes('已完成'), '缺少“已完成”状态');
  assert.ok(out.includes('采集中'), '缺少“采集中”状态');
});

test('three concurrent slots show their current source and hand-off history', () => {
  const out = text(full);
  assert.ok(out.includes('并发槽位（3 个）'));
  assert.match(out, /阿里云 ECS ✓ → JumpServer ✓ → DevOps …/, '槽位 1 交接历史缺失');
  assert.match(out, /阿里云 CLB ✓ → Codeup（云效） …/, '槽位 2 交接历史缺失');
  assert.ok(out.includes('槽位 3 · 空闲'), '空闲槽位未标出');
});

test('each slot has its own log tab and they never mix', () => {
  const html = renderToStaticMarkup(React.createElement(mod.CollectionProgress, { job: full }));
  assert.equal((html.match(/role="tab"/g) || []).length, 3, '应有 3 个日志 Tab');
  assert.equal((html.match(/role="tabpanel"/g) || []).length, 3, '应有 3 个日志面板');
  const panels = html.split('role="tabpanel"').slice(1);
  assert.ok(panels[0].includes('JumpServer 10/421'), '槽位 1 面板缺少自身日志');
  assert.ok(!panels[0].includes('NAT gateways'), '槽位 1 面板混入了其他槽位日志');
  assert.ok(panels[1].includes('Codeup page 1'), '槽位 2 面板缺少自身日志');
});

test('a single-source run renders no slot grid', () => {
  // Single-source syncs run one worker; showing three slots would be misleading.
  const single = { status: 'running', phase: '正在独立采集阿里云 ECS 实例', progress: { phase: '正在独立采集阿里云 ECS 实例' }, slotLogs: { 1: 'Page 1: 100 instances' } };
  const out = text(single);
  assert.ok(!out.includes('并发槽位'), '单源同步不应显示三槽位');
  assert.ok(!out.includes('采集任务'), '单源同步不应显示采集任务清单');
});

test('an absent job renders nothing instead of an empty shell', () => {
  assert.equal(text({ status: 'succeeded', phase: '完成' }), '');
});

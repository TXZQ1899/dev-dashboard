/* eslint-disable typescript/no-require-imports -- CommonJS Node test harness. */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const compilerOptions = { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX };
const datetime = { exports: {} };
vm.runInNewContext(ts.transpileModule(fs.readFileSync('lib/datetime.ts', 'utf8'), { compilerOptions }).outputText, datetime);
const start = '2026-10-20T06:00:00Z';
const now = Date.parse(start) + 70000;
const job = { id: 'check', status: 'running', phase: '正在等待目标连接采样返回', startedAt: start,
  sourceVersion: '20261020-144403', duration: 60, updatedAt: start,
  progress: { samples: 10, upstream: 2, downstream: 3, samplingStartedAt: start, lastObservedAt: start },
  activities: [{ key: 'target', scope: 'target', serverIp: '10.0.0.1', phase: '正在等待目标连接采样返回',
    stage: 'sampling', updatedAt: start, samples: 10 },
  { key: 'peer:one', scope: 'peer', serverIp: '10.0.0.2', phase: '正在通过 JumpServer 登录对端服务器', stage: 'login', updatedAt: start }],
  events: [{ at: start, key: 'target', scope: 'target', serverIp: '10.0.0.1', message: '已收到目标有效采样 10 帧' },
    { at: '2026-10-20T06:00:10Z', key: 'peer:one', scope: 'peer', serverIp: '10.0.0.2', message: '正在登录对端' }],
};
function render(overrides = {}, sync = {}) {
  const effects = [], timers = [], changes = [], cleared = [];
  const sandbox = { exports: {}, setInterval: (fn, delay) => { timers.push({ fn, delay }); return 1; }, clearInterval: id => cleared.push(id) };
  sandbox.require = id => {
    if (id === 'react') return { ...React, useState: () => [now, value => changes.push(value)], useEffect: fn => effects.push(fn) };
    if (id === '@/lib/datetime') return datetime.exports;
    return require(id);
  };
  vm.runInNewContext(ts.transpileModule(fs.readFileSync('components/application-check-progress.tsx', 'utf8'), { compilerOptions }).outputText, sandbox);
  const html = renderToStaticMarkup(React.createElement(sandbox.exports.ApplicationCheckProgress, {
    job: { ...job, ...overrides }, lastSyncedAt: now, syncError: '', ...sync,
  }));
  return { html, effects, timers, changes, cleared };
}
test('waiting collection keeps real counters, per-host activity, clock and newest-first history', () => {
  const { html, effects, timers, cleared } = render();
  for (const text of ['任务耗时 70 秒', '采样窗口计时 70 秒 / 计划 60 秒', '等待当前采集与后续核验完成',
    '10 <small>帧', '2 <small>条连接', '3 <small>条连接', '1 <small>台', '10.0.0.1', '10.0.0.2',
    '此步骤已等待 70 秒', '较长时间未返回新进度', '14:00:00', '窗口计时不代表有效观察覆盖或任务完成']) assert.ok(html.includes(text), text);
  assert.match(html, /aria-label="采样窗口计时" value="60" max="60"/);
  assert.ok(html.indexOf('正在登录对端') < html.indexOf('已收到目标有效采样 10 帧'));
  assert.doesNotMatch(html, /进度同步暂时中断|100%/);
  const cleanup = effects[0]();
  assert.equal(timers[0].delay, 1000);
  cleanup();
  assert.deepEqual(cleared, [1]);
});
test('sync loss is separate from remote waiting and retains the last received evidence', () => {
  for (const sync of [{ lastSyncedAt: now - 12000 }, { syncError: '读取超时' }]) {
    const { html } = render({}, sync);
    assert.match(html, /进度同步暂时中断，正在重试/);
    assert.match(html, /10 <small>帧/);
    assert.match(html, /最后收到的采集状态/);
  }
});
test('finished and cancelled tasks stop clocks and leave bounded history available', () => {
  for (const status of ['complete', 'partial', 'failed', 'cancelled']) {
    const { html, effects, timers } = render({ status, finishedAt: '2026-10-20T06:00:35Z',
      progress: { ...job.progress, samplingFinishedAt: '2026-10-20T06:00:20Z' } });
    assert.match(html, /任务耗时 35 秒/);
    assert.match(html, /采样窗口计时 20 秒/);
    assert.match(html, /最近 2 条/);
    assert.doesNotMatch(html, /此步骤已等待|较长时间未返回|<details open|进度同步暂时中断/);
    effects[0]();
    assert.equal(timers.length, 0);
  }
});
test('old jobs show unknown counters rather than fabricating zero observations', () => {
  const { html } = render({ progress: undefined, activities: undefined, events: undefined });
  assert.match(html, /— <small>帧/);
  assert.match(html, /— <small>台/);
  assert.match(html, /旧任务仅提供阶段信息/);
  assert.doesNotMatch(html, /采样窗口计时/);
});

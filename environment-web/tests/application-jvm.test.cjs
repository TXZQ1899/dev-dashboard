/* eslint-disable typescript/no-require-imports -- CommonJS Node test harness. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { test } = require('node:test');
const vm = require('node:vm');
const React = require('react');
const ts = require('typescript');
const { renderToStaticMarkup } = require('react-dom/server');

const MiB = 1024 ** 2;
const source = ts.transpileModule(fs.readFileSync('components/application-jvm.tsx', 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
}).outputText;

const saved = {
  taskId: 'analysis-1', finishedAt: '2026-11-25T20:51:21+08:00',
  xmsBytes: 1024 * MiB, xmxBytes: 2048 * MiB, currentXmsBytes: 2048 * MiB, currentXmxBytes: 3072 * MiB,
  beforeBytes: Math.round(734.3 * MiB), afterBytes: Math.round(157.7 * MiB), stepBytes: 1024 * MiB,
};

function renderAssessment({ recommendation = saved, reportStatus = 'complete', jobId = 'analysis-1', historical = false } = {}) {
  const runtime = {
    flags: { InitialHeapSize: saved.currentXmsBytes, MaxHeapSize: saved.currentXmxBytes, UseParallelGC: true },
    dumpGc: {
      status: 'single_full_gc', before: { heapUsedBytes: saved.beforeBytes },
      after: { heapUsedBytes: saved.afterBytes },
    },
  };
  const report = { status: reportStatus, runtime: reportStatus === 'complete' ? runtime : undefined };
  const state = { job: { id: jobId, status: reportStatus, assetId: 'test', pid: 123 }, report, recommendation, historical };
  const aliases = {
    react: { ...React, useEffect: () => {}, useState: initial => [initial?.job === null ? state : initial, () => {}] },
    '@/components/ui/button': { Button: ({ variant: _variant, ...props }) => React.createElement('button', props) },
    '@/lib/datetime': { formatBeijingTime: () => '—' },
  };
  const sandbox = { exports: {}, require: id => aliases[id] ?? require(id) };
  vm.runInNewContext(source, sandbox);
  return renderToStaticMarkup(sandbox.exports.ApplicationJvm({ asset: 'test', pid: 123 }));
}

test('shows stored Xms and Xmx as text and the full command line', () => {
  const html = renderAssessment();
  assert.match(html, /拟调整<\/strong> <code>-Xms1G -Xmx2G<\/code>/);
  assert.match(html, /拟调整 Xms（MiB）<\/span><strong>1024<\/strong>/);
  assert.match(html, /拟调整 Xmx（MiB）<\/span><strong>2048<\/strong>/);
  assert.doesNotMatch(html, /<input/);
  assert.match(html, /734\.3 MiB/);
  assert.match(html, /157\.7 MiB/);
});

test('retains and identifies the last saved recommendation after a newer failed job', () => {
  const html = renderAssessment({ jobId: 'analysis-2', reportStatus: 'failed' });
  assert.match(html, /上次有效评估/);
  assert.match(html, /-Xms1G -Xmx2G/);
});

test('does not recalculate a missing saved recommendation in the browser', () => {
  const html = renderAssessment({ recommendation: null });
  assert.match(html, /暂不生成建议/);
  assert.doesNotMatch(html, /<code>-Xms\d/);
});

test('renders an archived PID without actions that can start another dump', () => {
  const html = renderAssessment({ historical: true });
  assert.match(html, /PID 123 的历史分析，仅供查看/);
  assert.match(html, /-Xms1G -Xmx2G/);
  assert.doesNotMatch(html, /<button[^>]*>普通堆转储/);
});

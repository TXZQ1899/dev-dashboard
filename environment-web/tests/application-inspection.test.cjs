/* eslint-disable typescript/no-require-imports -- CommonJS Node test harness. */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { Tabs: Primitive } = require('@base-ui/react/tabs');

function render(tab, visited) {
  const effects = [], changes = [], sandbox = { exports: {} };
  sandbox.require = id => {
    if (id === 'react') return { ...React, useState: () => [visited, value => changes.push(value)] };
    if (id === 'react/jsx-runtime') return require(id);
    if (id === '@/components/application-check') return { ApplicationCheck: props => {
      effects.push({ type: 'tcp', ...props });
      return React.createElement('p', null, 'TCP report');
    } };
    if (id === '@/components/application-files') return { ApplicationFiles: props => {
      effects.push({ type: 'files', ...props });
      return React.createElement('p', null, props.kind + ' capability');
    } };
    if (id === '@/components/ui/tabs') return {
      Tabs: props => React.createElement(Primitive.Root, { ...props, defaultValue: tab }),
      TabsList: ({ variant: _variant, ...props }) => React.createElement(Primitive.List, props),
      TabsTrigger: Primitive.Tab, TabsContent: Primitive.Panel,
    };
    throw new Error(id);
  };
  vm.runInNewContext(ts.transpileModule(fs.readFileSync('components/application-inspection.tsx', 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText, sandbox);
  const tree = sandbox.exports.ApplicationInspection({ asset: 'server', pid: 123, ip: '10.0.0.1', name: 'orders' });
  return { html: renderToStaticMarkup(tree), effects, changes, tabs: React.Children.toArray(tree.props.children)[1] };
}

test('inspection opens on TCP with three independent tabs and lazy file capabilities', () => {
  const { html, effects, tabs } = render('tcp', ['tcp']);
  for (const label of ['TCP 连接检测', '配置文件', '日志文件', '10.0.0.1', 'PID 123', 'orders']) assert.ok(html.includes(label), label);
  assert.deepEqual(effects, [{ type: 'tcp', asset: 'server', pid: 123 }]);
  assert.equal(tabs.props.defaultValue, 'tcp');
});

test('file tabs bind directly to the process and keep visited capabilities mounted', () => {
  const { effects, tabs, changes } = render('log', ['tcp', 'config', 'log']);
  assert.deepEqual(effects, [
    { type: 'tcp', asset: 'server', pid: 123 },
    { type: 'files', asset: 'server', pid: 123, kind: 'config' },
    { type: 'files', asset: 'server', pid: 123, kind: 'log' },
  ]);
  tabs.props.onValueChange('config');
  assert.deepEqual(Array.from(changes[0](['tcp'])), ['tcp', 'config']);
  assert.deepEqual(Array.from(changes[0](['tcp', 'config'])), ['tcp', 'config']);
});

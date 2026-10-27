/* eslint-disable typescript/no-require-imports -- CommonJS Node test harness. */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { Tabs: Primitive } = require('@base-ui/react/tabs');

// 组件的时间展示已统一委托给 lib/datetime.ts。
const datetimeModule = { exports: {} };
vm.runInNewContext(ts.transpileModule(fs.readFileSync('lib/datetime.ts', 'utf8'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText, datetimeModule);

const current = { id: 'current', kind: 'log', logGroup: 'current', path: '/app/logs/app.log', size: 4, modifiedAt: 10 };
const history = { id: 'history', kind: 'log', logGroup: 'history', path: '/app/logs/app.log.tar.gz', size: 80, modifiedAt: 100 };
const config = { id: 'config', kind: 'config', path: '/app/config/application.yml', size: 20, modifiedAt: 100 };
function harness({ files = [history, current], tab = 'current', kind = 'log', archive = { status: 'idle' }, fetch, contents } = {}) {
  const catalogue = { files, roots: { config: [], log: ['/app/logs'] }, warnings: [] };
  const state = [catalogue, contents || { current: { content: 'latest log line', observedAt: '2026-10-16T00:00:00Z' } }, 'current', '', '', archive];
  const effects = [], changes = [], sandbox = { exports: {}, fetch, setTimeout, clearTimeout };
  let index = 0;
  sandbox.require = (id) => {
    if (id === 'react') return { ...React, useCallback: fn => fn, useEffect: fn => effects.push(fn), useState: () => {
      const position = index++;
      return [state[position], value => changes.push({ position, value })];
    } };
    if (id === 'react/jsx-runtime') return require(id);
    if (id === '@/components/ui/button') return { Button: props => React.createElement('button', props) };
    if (id === '@/components/ui/tabs') return {
      Tabs: props => React.createElement(Primitive.Root, { ...props, defaultValue: tab }),
      TabsList: ({ variant: _variant, ...props }) => React.createElement(Primitive.List, props),
      TabsTrigger: Primitive.Tab,
      TabsContent: Primitive.Panel,
    };
    if (id === '@/lib/datetime') return datetimeModule.exports;
    throw new Error(id);
  };
  vm.runInNewContext(ts.transpileModule(fs.readFileSync('components/application-files.tsx', 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText, sandbox);
  const tree = sandbox.exports.ApplicationFileBrowser({ id: 'task', kind });
  const html = renderToStaticMarkup(tree);
  return { tree, html, effects, changes, catalogue };
}
function findElement(tree, predicate) {
  if (!tree || typeof tree !== 'object') return undefined;
  if (predicate(tree)) return tree;
  for (const child of React.Children.toArray(tree.props?.children)) {
    const found = findElement(child, predicate);
    if (found) return found;
  }
}

test('current tab limits the selector to text logs and retains the last-100-lines preview', () => {
  const { html } = harness({ files: [history, current, { ...current, id: 'out', path: '/app/logs/catalina.out' },
    { ...current, id: 'txt', path: '/app/logs/debug.txt' }, { ...current, id: 'junk', path: '/app/logs/app.jar' }] });
  assert.match(html, /当前日志（3）/);
  assert.match(html, /历史日志（1）/);
  assert.match(html, /role="tablist"/);
  assert.match(html, /role="tabpanel"/);
  for (const text of ['app.log', 'catalina.out', 'debug.txt', 'latest log line', '刷新末尾 100 行']) assert.ok(html.includes(text), text);
  assert.doesNotMatch(html, /<option[^>]*>[^<]*(?:tar\.gz|app\.jar)/);
  assert.doesNotMatch(html, /打包历史日志/);
});

test('history tab lists archive metadata and downloads only completed historical bundles', async () => {
  let payload;
  const { html, tree } = harness({ tab: 'history', archive: { status: 'complete', logGroup: 'history', size: 123 },
    fetch: async (_url, options) => {
      payload = JSON.parse(options.body);
      return { ok: true, json: async () => ({ status: 'running', logGroup: 'history' }) };
    } });
  assert.match(html, /历史日志文件列表/);
  for (const text of ['app.log.tar.gz', '文件路径', '修改时间', '80 B', '下载历史日志压缩包', '含 30 天以前']) assert.ok(html.includes(text), text);
  assert.doesNotMatch(html, /latest log line|<select/);
  assert.match(html, /\/logs\/download\?id=task/);
  const button = findElement(tree, element => element.props?.children === '打包历史日志');
  assert.equal(button.props.disabled, false);
  await button.props.onClick();
  assert.deepEqual(payload, { id: 'task', kind: 'log', action: 'archive', group: 'history' });
  assert.doesNotMatch(harness({ tab: 'history', archive: { status: 'complete', logGroup: 'all' } }).html, /下载历史日志压缩包/);
});

test('empty history cannot start a bundle; an active bundle can still be cancelled', async () => {
  const empty = harness({ tab: 'history', files: [current] });
  assert.match(empty.html, /未找到历史日志文件/);
  assert.equal(findElement(empty.tree, element => element.props?.children === '打包历史日志').props.disabled, true);
  let payload;
  const running = harness({ tab: 'history', files: [], archive: { status: 'running', logGroup: 'history', phase: '正在打包历史日志' },
    fetch: async (_url, options) => {
      payload = JSON.parse(options.body);
      return { ok: true, json: async () => ({ status: 'cancelled' }) };
    } });
  assert.match(running.html, /正在打包历史日志/);
  await findElement(running.tree, element => element.props?.children === '取消下载任务').props.onClick();
  assert.equal(payload.action, 'cancel-archive');
});

test('initial load never previews a newer compressed log or falls back to history alone', async () => {
  for (const files of [[history, { ...current, id: 'empty', size: 0, modifiedAt: 20 }, current], [history]]) {
    const calls = [];
    const view = harness({ files, fetch: async (_url, options) => {
      const payload = options?.body ? JSON.parse(options.body) : null;
      calls.push(payload);
      return { ok: true, json: async () => !payload ? { status: 'idle' } :
        payload.action === 'list' ? { files, roots: { config: [], log: ['/app/logs'] }, warnings: [] } :
          { content: 'new line', observedAt: '2026-10-16T00:00:00Z' } };
    } });
    view.effects[0]();
    await new Promise(resolve => setImmediate(resolve));
    const reads = calls.filter(call => call?.action === 'read');
    assert.deepEqual(reads, files.length === 1 ? [] : [{ id: 'task', kind: 'log', action: 'read', file: 'current' }]);
  }
});

test('configuration capability lists configs independently without reading logs or archive state', async () => {
  const calls = [];
  const { html, effects } = harness({ kind: 'config', files: [config, current, history],
    contents: { config: { content: 'password: ******', truncated: true } },
    fetch: async (url, options) => {
      calls.push({ url, body: JSON.parse(options.body) });
      return { ok: true, json: async () => ({ files: [config], roots: { config: ['/app/config'], log: [] }, warnings: [] }) };
    } });
  for (const text of ['application.yml', '刷新配置文件', '下载已脱敏内容', '仅展示和下载前 256 KiB']) assert.ok(html.includes(text), text);
  assert.doesNotMatch(html, /当前日志|历史日志|app.log|latest log line/);
  effects[0]();
  await new Promise(resolve => setTimeout(resolve, 10));
  assert.deepEqual(calls, [{ url: '/api/settings/application-files', body: { id: 'task', kind: 'config', action: 'list' } }]);
});

test('log capability does not render configuration controls', () => {
  const { html } = harness({ files: [config, current, history] });
  assert.match(html, /当前日志/);
  assert.doesNotMatch(html, /application.yml|刷新配置文件|读取配置内容/);
});

test('reusable file capability opens a process session without starting a TCP job', async () => {
  const effects = [], changes = [], calls = [];
  const sandbox = { exports: {}, fetch: async (url, options) => {
    calls.push({ url, body: JSON.parse(options.body) });
    return { ok: true, json: async () => ({ id: 'files-session' }) };
  } };
  let index = 0;
  const state = [{}, 0];
  sandbox.require = id => {
    if (id === 'react') return { ...React, useEffect: effect => effects.push(effect), useState: () => {
      const position = index++;
      return [state[position], value => changes.push({ position, value })];
    } };
    if (id === 'react/jsx-runtime') return require(id);
    if (id === '@/components/ui/button') return { Button: 'button' };
    if (id === '@/components/ui/tabs') return {};
    if (id === '@/lib/datetime') return datetimeModule.exports;
    throw new Error(id);
  };
  vm.runInNewContext(ts.transpileModule(fs.readFileSync('components/application-files.tsx', 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText, sandbox);
  const view = sandbox.exports.ApplicationFiles({ asset: 'server', pid: 123, kind: 'config' });
  assert.match(renderToStaticMarkup(view), /正在准备应用文件/);
  effects[0]();
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(calls, [{ url: '/api/settings/application-files', body: { action: 'open', asset: 'server', pid: 123 } }]);
  assert.equal(changes.at(-1).value.id, 'files-session');
});

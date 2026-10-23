/* eslint-disable typescript/no-require-imports -- CommonJS Node test harness. */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

const SOURCE = 'app/settings/page.tsx';

function renderPage(initialState = null) {
  let stateIndex = 0;
  const sandbox = {
    exports: {},
    require: (id) => {
      if (id === 'react') return { ...React, useState: (v) => [stateIndex++ === 0 ? initialState : v, () => {}], useEffect: () => {} };
      if (id === 'react/jsx-runtime') return require(id);
      if (id === '@/app/page') return { Shell: ({ children }) => children };
      if (id === '@/lib/datetime') return { formatBeijingTime: (value) => value || '—' };
      if (id === '@/components/aliyun-tenant-settings')
        return { AliyunTenantSettings: () => React.createElement('h2', null, '阿里云租户管理') };
      if (id.includes('input')) return { Input: (props) => React.createElement('input', props) };
      if (id.includes('button'))
        return {
          Button: ({ children, disabled }) =>
            React.createElement('button', { disabled: disabled ? '' : undefined }, children),
        };
      if (id.includes('table'))
        return {
          Table: ({ children }) => children,
          TableHeader: ({ children }) => children,
          TableBody: ({ children }) => children,
          TableRow: ({ children }) => children,
          TableHead: ({ children }) => children,
          TableCell: ({ children }) => children,
        };
      return {};
    },
  };
  vm.runInNewContext(
    ts.transpileModule(fs.readFileSync(SOURCE, 'utf8'), {
      compilerOptions: {
        target: ts.ScriptTarget.ES2022,
        module: ts.ModuleKind.CommonJS,
        jsx: ts.JsxEmit.ReactJSX,
        esModuleInterop: true,
      },
    }).outputText,
    sandbox,
  );
  return renderToStaticMarkup(React.createElement(sandbox.exports.default));
}

// Collect each <Button ...> opening tag by brace matching, because the arrow
// function body contains ">" and defeats a naive [^>]* scan.
function buttonTags() {
  const source = fs.readFileSync(SOURCE, 'utf8');
  const tags = [];
  let i = 0;
  while ((i = source.indexOf('<Button', i)) !== -1) {
    let depth = 0;
    let end = i + '<Button'.length;
    for (; end < source.length; end++) {
      const c = source[end];
      if (c === '{') depth++;
      else if (c === '}') depth--;
      else if (c === '>' && depth === 0) break;
    }
    tags.push(source.slice(i, end + 1));
    i = end + 1;
  }
  return { source, tags };
}

function tagFor(action) {
  const { source, tags } = buttonTags();
  const tag = tags.find((t) => t.includes('=> ' + action));
  return { source, tag };
}

test('an expired Cookie can be saved while a sync job is running', () => {
  // The failure that blocks recovery: a running (or failed, still-locked) job
  // must not disable 保存设置, or the user cannot replace a dead Cookie.
  const { tag } = tagFor("act('save')");
  assert.ok(tag, '未找到保存设置按钮');
  const disabled = tag.match(/disabled=\{([^}]*)\}/);
  assert.ok(disabled, '保存设置按钮缺少 disabled 条件');
  assert.equal(disabled[1].trim(), 'busy', '保存设置应只受 busy 约束');
  assert.doesNotMatch(disabled[1], /running/, '保存设置不应受 running 约束');
});

test('sync and topology stay locked while any task runs', () => {
  // Full sync and Topology generation must wait for every task (primary +
  // concurrent retries) to finish.
  for (const action of ["act('sync')", 'generateTopology()']) {
    const { tag } = tagFor(action);
    assert.ok(tag, `未找到 ${action} 按钮`);
    const disabled = tag.match(/disabled=\{([^}]*)\}/);
    assert.ok(disabled, `${action} 缺少 disabled 条件`);
    assert.match(disabled[1], /anyRunning/, `${action} 应在任一任务运行期间禁用`);
  }
});

test('per-source buttons are gated by server retryability', () => {
  // A single source is unlocked exactly when the server reports it retryable
  // (its own collection failed in the running full sync); otherwise locked.
  for (const action of [
    "act('local_gitlab')",
    "syncSource('ecs')",
    "syncSource('clb')",
    "syncSource('nat')",
    "syncSource('jumpserver')",
    "syncSource('devops')",
    "syncSource('codeup')",
  ]) {
    const { tag } = tagFor(action);
    assert.ok(tag, `未找到 ${action} 按钮`);
    const disabled = tag.match(/disabled=\{([^}]*)\}/);
    assert.ok(disabled, `${action} 缺少 disabled 条件`);
    assert.match(disabled[1], /!sourceEnabled\(/, `${action} 应受服务端 retryable 约束`);
  }
});

test('topology generation is exposed with persisted version metadata and download link', () => {
  const { source } = buttonTags();
  assert.match(source, /type Topology = \{/);
  assert.match(source, /<section className="panel settings-panel" id="topology-settings">/);
  assert.match(source, /state\.topology\.downloadHref/);
  assert.match(source, /基于当前数据版本生成 Topology/);
  assert.match(source, /每次生成都会创建新的 topology-\* 版本并持久化到数据卷/);
});

test('version switch inside the history table keeps its own lock', () => {
  const { source } = buttonTags();
  assert.match(source, /disabled=\{\s*busy \|\|\s*anyRunning \|\|\s*v\.status !== 'ready'/);
});

test('concurrent retry jobs are surfaced from server state', () => {
  const { source } = buttonTags();
  assert.match(source, /type Job = \{/);
  assert.match(source, /extraJobs\?: Job\[\]/);
  assert.match(source, /sourceStates\?: Record<string, SourceState>/);
  assert.match(source, /并发重试任务/);
  assert.match(source, /state\.extraJobs/);
});

test('the running-job notice names retryable sources', () => {
  const { source } = buttonTags();
  assert.match(source, /retryableLabels/);
  assert.match(source, /可立即点击对应「单独同步」并发重试/);
});

test('the running-job notice explains that saving stays available', () => {
  const { source } = buttonTags();
  assert.match(source, /任务进行中：仍可保存 Cookie/);
});

test('settings page renders its loading state without throwing', () => {
  const html = renderPage();
  assert.match(html, /Settings/);
  assert.match(html, /正在读取设置/);
});

test('loaded settings keeps other platform credentials without duplicate Aliyun fields', () => {
  const html = renderPage({
    aliyunTenants: [], cookies: { devops: {}, aliyun: {}, jumpserver: {}, local_gitlab: {} },
    schedule: { enabled: false, time: '18:00' }, current: { id: 'current', activatedAt: '2026-10-23' },
    versions: [], storage: 'Docker 数据卷',
  });
  assert.match(html, /阿里云租户管理/);
  for (const id of ['cookie-devops', 'cookie-jumpserver', 'cookie-local_gitlab', 'folidev-password'])
    assert.ok(html.includes(`id="${id}"`), id);
  for (const id of ['cookie-aliyun', 'aliyun-auth-mode', 'aliyun-access-key-id', 'aliyun-access-key-secret', 'codeup-organization-id'])
    assert.ok(!html.includes(`id="${id}"`), `重复入口仍存在：${id}`);
});

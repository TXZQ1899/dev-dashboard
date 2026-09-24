/* eslint-disable typescript/no-require-imports -- Node test harness uses CommonJS, matching the existing suite. */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

const SOURCE = 'app/topology/page.tsx';
const SHELL = 'app/page.tsx';

function renderPage() {
  const sandbox = {
    exports: {},
    require: (id) => {
      if (id === 'react')
        return { ...React, useState: (v) => [v, () => {}] };
      if (id === 'react/jsx-runtime') return require(id);
      if (id === '@/app/page') return { Shell: ({ children }) => children };
      if (id === 'lucide-react') return new Proxy({}, { get: () => () => null });
      if (id.includes('input'))
        return { Input: (props) => React.createElement('input', props) };
      if (id.includes('button'))
        return {
          Button: ({ children, ...rest }) =>
            React.createElement('button', rest, children),
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

test('path explorer page queries the runtime API with kind, query and filters', () => {
  const source = fs.readFileSync(SOURCE, 'utf8');
  assert.match(source, /fetch\('\/api\/topology\/path\?' \+ params\.toString\(\)/);
  assert.match(source, /new URLSearchParams\(\{ kind, q: query\.trim\(\), maxDepth \}\)/);
  assert.match(source, /if \(target\) params\.set\('to', target\)/);
  assert.match(source, /if \(environment\) params\.set\('env', environment\)/);
  assert.match(source, /<form className="resource-filters" onSubmit=\{search\}>/);
  for (const label of ['查询模式', '查询起点类型', '目标节点类型', '环境过滤', '最大深度']) {
    assert.match(source, new RegExp(`aria-label="${label}"`), `缺少 ${label} 控件`);
  }
  // The query input label switches with the mode (chain / request / paths).
  assert.match(source, /aria-label=\{mode === 'chain' \? '域名' : mode === 'request' \? 'URL 或域名' : '域名、应用名或 IP'\}/);
});

test('path explorer page defaults each kind to the documented target direction', () => {
  const source = fs.readFileSync(SOURCE, 'utf8');
  assert.match(
    source,
    /const DEFAULT_TARGETS: Record<Kind, string> = \{\s*domain: 'APPLICATION',\s*application: 'HOST',\s*host: 'APPLICATION',\s*\}/,
  );
  assert.match(source, /未指定目标类型时按默认方向查询：域名 → 应用、应用 → 主机、主机 → 应用。/);
  assert.match(source, /GLOBAL 连边在任何环境过滤下都会保留/);
});

test('path explorer page renders full paths, confidence, evidence and diagnostics', () => {
  const source = fs.readFileSync(SOURCE, 'utf8');
  assert.match(source, /path\.steps\.map\(\(step, position\)/);
  assert.match(source, /step\.edge\.evidence\.length/);
  assert.match(source, /链路置信度 \{confidenceLabel\(path\.confidence\)\}/);
  assert.match(source, /包含未解析节点/);
  assert.match(source, /result\.gaps\.map\(\(gap\)/);
  assert.match(source, /result\.reachableTypes\.length > 0/);
  assert.match(source, /查看逐段证据/);
});

test('path explorer is reachable from the sidebar navigation', () => {
  const shell = fs.readFileSync(SHELL, 'utf8');
  assert.match(shell, /<Route size=\{18\} \/>\s*拓扑路径查询/);
  assert.match(shell, /className=\{active === 'topology' \? 'active' : ''\} href="\/topology"/);
  assert.match(shell, /active === 'topology' \? '拓扑路径查询'/);
});

test('path explorer page defaults to the staged domain landing chain mode', () => {
  const html = renderPage();
  assert.match(html, /拓扑路径查询/);
  assert.match(html, /TOPOLOGY PATH EXPLORER/);
  assert.match(html, /aria-label="查询模式"/);
  assert.match(html, /域名落点链路（推荐域名）/);
  assert.match(html, /查询链路/);
  assert.match(html, /apis\.folidaymall\.com/);
  // In chain mode the kind selector is forced to domain and disabled.
  assert.match(
    html,
    /aria-label="查询起点类型" disabled=""[\s\S]*<option value="domain" selected="">/,
  );
  // Path-mode-only controls are hidden in chain mode.
  assert.doesNotMatch(html, /aria-label="目标节点类型"/);
  assert.doesNotMatch(html, /aria-label="最大深度"/);
  assert.match(html, /域名落点链路按 5 层展开/);
});

test('domain chain mode queries the staged API and renders all five layers', () => {
  const source = fs.readFileSync(SOURCE, 'utf8');
  assert.match(source, /fetch\('\/api\/topology\/domain-chain\?' \+ params\.toString\(\)/);
  assert.match(source, /new URLSearchParams\(\{ q: query\.trim\(\) \}\)/);
  assert.match(source, /<ChainView chain=\{chain\} topology=\{chainData\.topology\} \/>/);
  for (const marker of [
    '① DNS 解析',
    '② EIP 入口绑定（NAT 网关 / ECS / CLB）',
    '③ Nginx 接入层',
    '④ Upstream 后端落点（IP+端口 → DevOps 应用）',
    '⑤ 拓扑缺口 / 提示',
    '未匹配到同 IP+端口的 DevOps 部署',
    '同机其他端口（仅供排查，不构成匹配）',
    'http/unknown 协议标签已按 IP+端口归一',
  ]) {
    assert.match(source, new RegExp(marker.replace(/[（）+]/g, '\\$&')), `缺少 ${marker}`);
  }
});
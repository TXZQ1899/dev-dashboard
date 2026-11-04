const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const vm = require('vm');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

const info = { id:'a'.repeat(64), name:'app', image:'app:v1', imageId:'sha256:1', observedAt:'2026-11-04T09:00:00+08:00', cacheHit:true,
  mounts:[{type:'bind', source:'/srv/app', destination:'/app', readWrite:true, propagation:'rprivate', driver:'local'}] };
function render(state) {
  let cursor = 0;
  const sandbox = { exports:{}, require(id) {
    if (id === 'react') return { ...React, useState:() => [state[cursor++], () => {}], useCallback:fn => fn, useEffect:() => {} };
    if (id === 'react/jsx-runtime') return require(id);
    if (id === '@/components/ui/spinner') return { Spinner: props => React.createElement('svg', {'aria-label':props['aria-label'], 'data-spinner':'true'}) };
    throw new Error(id);
  }};
  vm.runInNewContext(ts.transpileModule(fs.readFileSync('components/container-info.tsx', 'utf8'), {
    compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX},
  }).outputText, sandbox);
  return renderToStaticMarkup(sandbox.exports.ContainerInfo({asset:'a',ip:'10.0.0.1',pid:1,containerId:info.id}));
}
test('container information hides requested technical rows and labels cached observation time', () => {
  const html = render([info, null, null, null, '']);
  assert.ok(html.includes('app:v1'));
  assert.ok(html.includes('（缓存）'));
  for (const text of ['容器 init PID','核验进程 PID','关联依据','权限：','传播：','驱动：','正在读取容器信息']) assert.ok(!html.includes(text), text);
});
test('loading spinner is attached to the active button', () => {
  const loadingInfo = render([null, null, null, 'info', '']);
  assert.match(loadingInfo, /刷新<svg[^>]*data-spinner="true"/);
  const loadingFiles = render([info, null, null, 'mount:0', '']);
  assert.match(loadingFiles, /查看文件列表<svg[^>]*data-spinner="true"/);
  assert.doesNotMatch(loadingFiles, /刷新<svg/);
  const listing = {path:'a', offset:200, truncated:true, warnings:[], entries:[{name:'nested',type:'directory'}]};
  for (const [busy, label] of [['up:0','返回上级'], ['prev:0','上一页'], ['next:0','下一页'], ['dir:0:nested','nested']]) {
    const html = render([info, listing, 0, busy, '']);
    assert.match(html, new RegExp(label+'<svg[^>]*data-spinner="true"'));
  }
});

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');
const React = require('react');

function harness(jobState, fetch) {
  const snapshot = { name:'orders', appId:'7', status:'ready', snapshotVersion:'v1', branch:'master', module:'orders', modifiedAt:1, warnings:[], notes:[], files:[] };
  const states = [snapshot, '', 'other', {}, {}, {}, jobState, '', false, 0];
  const changes = []; let index = 0;
  const sandbox = { exports:{}, fetch, setTimeout, clearTimeout, URLSearchParams };
  sandbox.require = (id) => {
    if (id === 'react') return { ...React, useState:() => { const i=index++; return [states[i], value => changes.push([i,value])]; }, useEffect:() => {}, useRef:() => ({current:''}) };
    if (id === 'react/jsx-runtime') return require(id);
    if (id === '@/components/ui/button') return { Button:props => React.createElement('button',props) };
    if (id === '@/components/ui/tabs') return Object.fromEntries(['Tabs','TabsContent','TabsList','TabsTrigger'].map(name => [name, props => React.createElement('div',props)]));
    if (id === '@/lib/datetime') return { formatBeijingTime:()=>'time' };
    throw new Error(id);
  };
  const source=fs.readFileSync('app/applications/config/[appId]/saved-application-config.tsx','utf8');
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX} }).outputText, sandbox);
  return { tree:sandbox.exports.SavedApplicationConfig({appId:'7'}), changes };
}
function find(tree, predicate) {
  if (!tree || typeof tree !== 'object') return;
  if (predicate(tree)) return tree;
  for (const child of React.Children.toArray(tree.props?.children)) { const value=find(child,predicate); if (value) return value; }
}

test('refresh requests exactly one application', async () => {
  let call;
  const {tree}=harness({job:{status:'idle'},batchStatus:'idle'}, async (url, options) => {
    call={url,options}; return {ok:true,json:async()=>({job:{status:'queued',appId:'7'}})};
  });
  const button=find(tree, item => item.props?.children === '刷新此应用配置');
  assert.equal(button.props.disabled,false);
  button.props.onClick();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(call.url,'/api/catalog/application-config/refresh');
  assert.equal(call.options.method,'POST');
  assert.deepEqual(JSON.parse(call.options.body),{appId:'7'});
});

test('refresh is disabled while a batch or another application runs', () => {
  for (const state of [{job:{status:'idle'},batchStatus:'running'}, {job:{status:'running',appId:'8'},batchStatus:'idle'}]) {
    const {tree}=harness(state,()=>{});
    assert.equal(find(tree, item => item.props?.children === '刷新此应用配置').props.disabled,true);
  }
});

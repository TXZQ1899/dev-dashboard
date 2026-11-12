const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
function load(initialLog) {
  const sandbox = { exports:{}, setInterval, clearInterval };
  let index=0;
  sandbox.require = id => {
    if (id==='react') return { ...React, useEffect:()=>{}, useState:()=>[index++ === 0 ? initialLog : '', ()=>{}] };
    if (id==='react/jsx-runtime') return require(id);
    if (id==='@/lib/datetime') return { formatBeijingTime:()=>'20:30:00' };
    throw new Error(id);
  };
  const source=fs.readFileSync('components/application-config-log.tsx','utf8');
  vm.runInNewContext(ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText,sandbox);
  return sandbox.exports;
}
test('log shows page, application, time, duration and safe error kind', () => {
  const log={events:[{time:'2026-11-11T20:30:00+08:00',event:'tree_error',name:'security-web',appId:'709',path:'src/resources',page:2,elapsedMs:15000,errorKind:'Codeup 连接失败或超时'}],truncated:true};
  const html=renderToStaticMarkup(React.createElement(load(log).ApplicationConfigLog));
  for (const text of ['security-web','709','src/resources','第 2 页','15.00 秒','Codeup 连接失败或超时','较早记录已省略']) assert.ok(html.includes(text),text);
});
test('empty log explains old tasks have no detailed records', () => {
  const html=renderToStaticMarkup(React.createElement(load({events:[],truncated:false}).ApplicationConfigLog));
  assert.match(html,/旧版本运行的任务不会产生这些记录/);
});

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');
const React = require('react');

test('page status polls lightweight endpoint, reports alerts and reloads on version change', async () => {
 const changes=[], requests=[]; let effect, interval, period, cleared=false, reloads=0;
 const responses=[{current:{id:'v1'},cookies:{jumpserver:{alert:{message:'cookie expired'}}}},{current:{id:'v2'},cookies:{}}];
 const mockReact={...React,useRef:initial=>({current:initial}),useState:initial=>[initial,value=>changes.push(value)],useEffect:fn=>{effect=fn;}};
 const sandbox={exports:{},require:id=>id==='react'?mockReact:id==='react/jsx-runtime'?require(id):{Settings:()=>null},
  fetch:async url=>{requests.push(url);return {ok:true,json:async()=>responses.shift()};},
  setInterval:(fn,ms)=>{interval=fn;period=ms;return 1;},clearInterval:()=>{cleared=true;},window:{location:{reload:()=>{reloads++;}}}};
 const source=fs.readFileSync('components/settings-link.tsx','utf8');
 vm.runInNewContext(ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText,sandbox);
 sandbox.exports.SettingsLink();const cleanup=effect();
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(requests[0],'/api/settings/page-state');assert.equal(period,180000);
 assert.equal(changes[0][0],'cookie expired');assert.equal(changes[1],'v1');
 await interval();assert.equal(reloads,1);cleanup();assert.equal(cleared,true);
});

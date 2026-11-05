const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');

test('concurrent and repeated requests share a versioned archive; failures can retry', async () => {
 let calls=0, fail=false, release;
 const gate=new Promise(resolve=>{release=resolve;});
 const sandbox={exports:{},URLSearchParams,fetch:async()=>{calls++;await gate;return {ok:!fail,json:async()=>fail?{error:'failed'}:{files:[{path:'example'}]}};}};
 const code=ts.transpileModule(fs.readFileSync('lib/nginx-config-cache.ts','utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText;
 vm.runInNewContext(code,sandbox);
 const load=sandbox.exports.loadNginxArchive;
 const a=load('v1','asset'), b=load('v1','asset');
 assert.equal(calls,1);release();await Promise.all([a,b]);
 await load('v1','asset');assert.equal(calls,1);
 await load('v2','asset');assert.equal(calls,2);
 fail=true;await assert.rejects(load('v3','asset'),/failed/);
 fail=false;await load('v3','asset');assert.equal(calls,4);
});

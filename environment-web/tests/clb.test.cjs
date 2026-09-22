const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const ts=require('typescript');
const sandbox={exports:{},require:()=>({available:false,instances:[]})};
vm.runInNewContext(ts.transpileModule(fs.readFileSync('lib/clb.ts','utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,esModuleInterop:true}}).outputText,sandbox);
test('backend counts distinguish memberships, unused groups, redirects and empty listeners',()=>{
 const server={id:'a',ip:'10.0.0.1'};
 const i={id:'lb',groups:[{id:'g',servers:[server]},{id:'unused',servers:[server,{id:'b',ip:'10.0.0.2'}]},{id:'empty',servers:[]}],listeners:[
 {groupId:'g',rules:[],forwardPort:null},{groupId:'empty',rules:[],forwardPort:null},{groupId:'empty',rules:[],forwardPort:443}]};
 const s=sandbox.exports.summarizeClb([i]);
 assert.equal(s.memberships,3);assert.equal(s.backends,2);assert.equal(s.used,1);assert.equal(s.empty,1);
 assert.equal(sandbox.exports.filterClb([i],'10.0.0.2').length,1);
});

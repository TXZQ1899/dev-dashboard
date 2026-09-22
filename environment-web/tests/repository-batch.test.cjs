/* eslint-disable typescript/no-require-imports -- CommonJS Node test harness. */
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const ts=require('typescript');
function load(file,imports){const sandbox={exports:{},URL,require:imports};vm.runInNewContext(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,esModuleInterop:true}}).outputText,sandbox);return sandbox.exports;}
const inventory=load('lib/inventory.ts',()=>({apps:[]}));
const {parseGitList,matchGitList,batchDetailCsv,gitKey}=load('lib/repository-batch.ts',()=>inventory);
const repo={id:'r',name:'repo',url:'https://host/group/repo.git',groupId:'g',apps:[{id:'a',name:'App',branch:'main'}]};
test('Markdown tables, blank rows and duplicate hrefs normalize without fuzzy matches',()=>{
 const batch=matchGitList('| [https://host/group/repo.git](https://host/group/repo.git) |\n| --- |\n| |\nhttp://host/group/repo\ngit@host:group/repo.git\nhttps://host/group/repo2.git\nnot-a-url',[repo]);
 assert.equal(batch.inputs.length,2);assert.equal(batch.results[0].repos.length,1);assert.equal(batch.results[1].repos.length,0);assert.equal(batch.invalid.length,1);
 assert.equal(parseGitList('[fyc\\_coupon](https://host/fcrm/fyc_coupon.git)').inputs.length,1);
 assert.notEqual(gitKey('https://other/group/repo'),gitKey(repo.url));assert.notEqual(gitKey('https://host/group/Repo'),gitKey(repo.url));assert.equal(gitKey('https://host'),'');
});
test('details preserve all environments, unmatched repositories, missing apps and successful Push In semantics',()=>{
 const app={id:'a',name:'=App',http:'x',port:'80',envs:{TEST:[{ip:'1.2.3.4',status:'成功',pushIn:[{operation:'Push In',status:'SUCCESS',endTime:'2026-09-01 10:00:00'},{operation:'Push In',status:'FAILED',endTime:'2026-09-02 10:00:00'}]}],SIMULATION:[],PRODUCT:[{status:'无环境配置'}]}};
 const repos=[repo,{...repo,id:'missing',url:'https://host/g/missing',apps:[{id:'missing',name:'Missing'}]},{...repo,id:'empty',url:'https://host/g/empty',apps:[]}];
 const csv=batchDetailCsv(matchGitList(repos.map(r=>r.url).join('\n')+'\nhttps://host/g/unknown\n=invalid',repos),[app],[{id:'g',name:'Group'}],'repo-date','app-date');
 assert.ok(csv.startsWith('\uFEFF'));assert.match(csv,/测试环境/);assert.match(csv,/仿真环境/);assert.match(csv,/生产环境/);assert.match(csv,/无环境记录/);assert.match(csv,/无环境配置/);assert.match(csv,/应用详情未采集/);assert.match(csv,/无关联应用/);assert.match(csv,/未匹配代码库/);assert.match(csv,/'=App/);assert.match(csv,/'=invalid/);
 const lines=csv.trim().split('\r\n');assert.equal(lines.length,8);
 // All rows keep the header's column count, including rows with missing data.
 const cells=line=>[...line.matchAll(/"((?:[^"]|"")*)"(?:,|$)/g)].map(m=>m[1].replace(/""/g,'"'));
 for(const line of lines)assert.equal(cells(line).length,35);
 const row=cells(lines[1]);assert.equal(row[20],'2026-09-01 10:00:00');assert.equal(row[22],row[20]);assert.equal(row[30],row[20]);assert.equal(row[33],'repo-date');
});

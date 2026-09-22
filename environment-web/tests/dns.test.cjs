/* eslint-disable typescript/no-require-imports -- Node test harness uses CommonJS, matching the existing suite. */
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const ts=require('typescript');
const snapshot=JSON.parse(fs.readFileSync('lib/dns-snapshot.json','utf8'));
const eipSandbox={exports:{},require:()=>JSON.parse(fs.readFileSync('lib/eip-snapshot.json','utf8'))};
vm.runInNewContext(ts.transpileModule(fs.readFileSync('lib/eip.ts','utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,esModuleInterop:true}}).outputText,eipSandbox);
const sandbox={exports:{},require:id=>id==='./eip'?eipSandbox.exports:snapshot};
vm.runInNewContext(ts.transpileModule(fs.readFileSync('lib/dns.ts','utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,esModuleInterop:true}}).outputText,sandbox);
const {integrateDns}=sandbox.exports;
const lb={id:'lb',ip:'1.2.3.4',listeners:[],groups:[]};
const record=(id,name,type,value,extra={})=>({id,name,type,value,status:'启用',line:'默认',...extra});
test('only enabled addresses in the two scoped zones associate, regardless of certificates',()=>{
 const rows=integrateDns([lb],[record('1','a.folidaymall.com','A',lb.ip),record('2','b.folidaymall.com','A',lb.ip,{status:'暂停'}),record('3','c.other.com','A',lb.ip),record('4','folidaymall.com','TXT',lb.ip)]);
 assert.equal(rows.length,3);assert.equal(rows[0].matches.length,1);assert.equal(rows[1].matches.length,0);assert.equal(rows[2].matches.length,0);
});
test('CNAME chains cross the two zones, preserve paths, and exclude other lines',()=>{
 const rows=integrateDns([lb],[record('1','x.folidaymall.com','CNAME','Y.FOSUNHOLIDAY.COM.'),record('2','y.fosunholiday.com','A',lb.ip),record('3','y.fosunholiday.com','A','5.6.7.8',{line:'境外'})]);
 assert.equal(rows[0].matches.length,1);assert.equal(rows[0].resolutions.length,1);assert.equal(rows[0].matches[0].paths[0].chain.length,3);
});
test('cycles, external targets and paused targets stay unresolved',()=>{
 const rows=integrateDns([lb],[record('1','a.folidaymall.com','CNAME','b.folidaymall.com'),record('2','b.folidaymall.com','CNAME','a.folidaymall.com'),record('3','c.folidaymall.com','CNAME','cdn.example.com'),record('4','d.folidaymall.com','CNAME','e.folidaymall.com'),record('5','e.folidaymall.com','A',lb.ip,{status:'暂停'})]);
 assert.ok(rows.every(r=>r.matches.length===0));
});
test('imports reconcile to both source workbooks and preserve separate lines and status',()=>{
 assert.equal(snapshot.records.length,481);
 assert.equal(snapshot.records.filter(r=>r.zone==='folidaymall.com').length,268);
 assert.equal(snapshot.records.filter(r=>r.zone==='fosunholiday.com').length,213);
 assert.equal(new Set(snapshot.records.map(r=>r.id)).size,481);
 assert.ok(snapshot.records.some(r=>r.status==='暂停'));
});
test('DNS resolves through static EIP binding to an intranet CLB without duplicate CLB matches',()=>{
 const bound={key:'owner:eip',id:'eip',ip:'8.8.4.4',bindingType:'SLB 实例',bindingId:'lb'};
 const rows=integrateDns([lb],[record('1','entry.folidaymall.com','CNAME','target.folidaymall.com'),record('2','target.folidaymall.com','A',bound.ip)],[bound]);
 assert.equal(rows[0].matches.length,1);assert.equal(rows[0].eipMatches.length,1);
 assert.match(rows[0].matches[0].paths[0].via,/静态绑定实例 ID/);
 const direct=integrateDns([{...lb,ip:bound.ip}],[record('3','target.folidaymall.com','A',bound.ip)],[bound]);
 assert.equal(direct[0].matches.length,1);assert.equal(direct[0].matches[0].paths.length,1);
});
test('NAT bindings and source notes never create CLB routes; EIP DNS links survive absent CLB',()=>{
 const nat={key:'nat:eip',ip:'8.8.4.4',bindingType:'NAT网关',bindingId:'lb',notes:[{value:lb.ip}]};
 const records=[record('1','entry.folidaymall.com','A',nat.ip),record('2','paused.folidaymall.com','A',nat.ip,{status:'暂停'})];
 const rows=integrateDns([lb],records,[nat]);
 assert.equal(rows[0].matches.length,0);assert.equal(rows[0].eipMatches.length,1);assert.equal(rows[1].eipMatches.length,0);
 assert.equal(integrateDns([],records,[nat])[0].eipMatches.length,1);
});
if(process.env.DNS_CLB_FIXTURE){
 const instances=JSON.parse(fs.readFileSync(process.env.DNS_CLB_FIXTURE)).instances;
 const rows=integrateDns(instances);
 console.log(JSON.stringify({instances:instances.length,records:rows.length,matched:rows.filter(r=>r.matches.length).length,clbs:new Set(rows.flatMap(r=>r.matches.map(m=>m.instance.id))).size,status:rows.reduce((a,r)=>(a[r.status]=(a[r.status]||0)+1,a),{})}));
}

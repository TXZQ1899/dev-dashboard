/* eslint-disable typescript/no-require-imports -- CommonJS Node test harness. */
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const ts=require('typescript');
const cache={};
function load(name){
 if(cache[name])return cache[name];
 if(name.endsWith('.json'))return JSON.parse(fs.readFileSync(`lib/${name}`,'utf8'));
 const sandbox={exports:{},require:id=>load(id.replace('./',''))};
 vm.runInNewContext(ts.transpileModule(fs.readFileSync(`lib/${name}.ts`,'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,esModuleInterop:true}}).outputText,sandbox);
 return cache[name]=sandbox.exports;
}
const {eip,integrateEip,eipClbEvidence}=load('eip');
const {integrateDns}=load('dns');
test('EIP associations retain ID and IP evidence independently without guessing from names',()=>{
 const record={key:'x',ip:'1.2.3.4',bindingId:'lb',bindingType:'SLB 实例',bindingName:'name'};
 const lb={id:'lb',ip:'1.2.3.4'};
 assert.equal(eipClbEvidence(record,lb).length,2);
 assert.equal(eipClbEvidence(record,{id:'other',ip:'10.1.1.1',name:'name'}).length,0);
 const rows=integrateEip([lb],[],[record]);
 assert.equal(rows.length,1);assert.equal(rows[0].matches.length,1);
 assert.equal(integrateEip([],[],[record])[0].matches.length,0);
});
test('source notes retain multiline domain text and original column positions',()=>{
 const record=eip.records.find(record=>record.ip==='139.224.36.156');
 assert.equal(record.notes.find(note=>note.column===20).value,'tcc.cn\nthomascook.com.cn');
 assert.equal(record.notes.find(note=>note.column===19).value,'10.58.6.8');
 assert.equal(eip.records.filter(record=>record.bindingType==='SLB 实例').length,3);
});
test('ECS is joined by exact binding ID and NAT by gateway ID plus external IP',()=>{
 const ecs=[{id:'i-exact',name:'other',privateIps:['10.0.0.8'],publicIps:[]},{id:'wrong',name:'i-exact',privateIps:['10.0.0.9'],publicIps:[]}];
 const record={key:'ecs',bindingType:'ECS实例',bindingId:'i-exact',ip:'1.2.3.4'};
 assert.equal(integrateEip([],[],[record],ecs)[0].ecsMatches[0].id,'i-exact');
 const nat={available:true,gateways:[{id:'ngw',entries:[{id:'one',externalIp:'1.2.3.4',internalIp:'10.0.0.8',externalPort:'443',internalPort:'8443',protocol:'tcp',status:'Available'},{id:'two',externalIp:'5.6.7.8',internalIp:'10.0.0.9',status:'Available'}]}]};
 const records=[{...record,bindingType:'NAT网关',bindingId:'ngw'},{...record,key:'wrong',bindingType:'NAT网关',bindingId:'other'}];
 const rows=integrateEip([{id:'lb',ip:'10.0.0.8'}],[],records,ecs,nat);
 assert.equal(rows[0].mappings.length,1);assert.equal(rows[0].matches.length,1);assert.equal(rows[0].ecsMatches.length,0);
 assert.ok(!JSON.stringify(rows[0]).includes('5.6.7.8'));
 assert.match(rows[0].matches[0].evidence[0],/443 → 10.0.0.8:8443/);
 assert.equal(rows[1].mappings.length,0);
 nat.gateways[0].entries[0].status='Pending';
 assert.equal(integrateEip([{id:'lb',ip:'10.0.0.8'}],[],records,ecs,nat)[0].matches.length,0);
});
if(process.env.NAT_FIXTURE && process.env.NAT_RESOURCE_FIXTURE){
 test('real NAT and ECS fixtures reconcile mappings and the requested ECS binding',()=>{
  const nat=JSON.parse(fs.readFileSync(process.env.NAT_FIXTURE,'utf8'));
  const resources=JSON.parse(fs.readFileSync(process.env.NAT_RESOURCE_FIXTURE,'utf8'));
  const dns=integrateDns(resources.clb.instances,undefined,undefined,nat);
  const rows=integrateEip(resources.clb.instances,dns,undefined,resources.ecs.instances,nat);
  const booking=rows.find(row=>row.record.bindingId==='i-uf62q56mzxz7qiw5f5bw');
  assert.equal(booking.ecsMatches.length,1);assert.ok(booking.ecsMatches[0].privateIps.length);
  for(const row of rows) for(const {gateway,entry} of row.mappings){
   assert.equal(gateway.id,row.record.bindingId);assert.equal(entry.externalIp,row.record.ip);
  }
  console.log(JSON.stringify({gateways:nat.gateways.length,dnat:nat.gateways.reduce((n,g)=>n+g.entries.length,0),ecsEips:rows.filter(r=>r.ecsMatches.length).length,natEips:rows.filter(r=>r.mappings.length).length,clbEips:rows.filter(r=>r.matches.length).length,booking:booking.ecsMatches.map(i=>({id:i.id,privateIps:i.privateIps}))}));
 });
}
test('DNS can reach a CLB through available DNAT with protocol and translated port evidence',()=>{
 const records=[{id:'dns',name:'test.folidaymall.com',type:'A',value:'1.2.3.4',status:'启用',line:'默认'}];
 const eips=[{key:'nat',id:'eip',bindingType:'NAT网关',bindingId:'ngw',ip:'1.2.3.4'}];
 const nat={available:true,gateways:[{id:'ngw',entries:[{id:'rule',externalIp:'1.2.3.4',externalPort:'443',internalIp:'10.0.0.8',internalPort:'8443',protocol:'tcp',status:'Available'}]}]};
 const row=integrateDns([{id:'lb',ip:'10.0.0.8'}],records,eips,nat)[0];
 assert.equal(row.matches.length,1);assert.match(row.matches[0].paths[0].via,/8443/);
 records[0].status='暂停';assert.equal(integrateDns([{id:'lb',ip:'10.0.0.8'}],records,eips,nat)[0].matches.length,0);
});
if(process.env.CLB_PAGE_FIXTURE){
 test('current CLB snapshot reconciles all EIP and DNS links in both directions',()=>{
  const instances=JSON.parse(fs.readFileSync(process.env.CLB_PAGE_FIXTURE,'utf8')).instances;
  const dns=integrateDns(instances);
  const rows=integrateEip(instances,dns);
  assert.equal(rows.length,53);
  for(const row of rows) for(const domain of row.domains){
   assert.ok(domain.eipMatches.some(match=>match.eip.key===row.record.key));
   for(const match of row.matches)assert.ok(domain.matches.some(item=>item.instance.id===match.instance.id));
  }
  console.log(JSON.stringify({clbs:instances.length,eips:rows.length,clbLinkedEips:rows.filter(row=>row.matches.length).length,dnsLinkedEips:rows.filter(row=>row.domains.length).length,dnsRecords:new Set(rows.flatMap(row=>row.domains.map(domain=>domain.record.id))).size,bindings:rows.filter(row=>row.record.bindingType==='SLB 实例').map(row=>({ip:row.record.ip,id:row.record.bindingId,matched:row.matches.length,domains:row.domains.length}))}));
 });
}

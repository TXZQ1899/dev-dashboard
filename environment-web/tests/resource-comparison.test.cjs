const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const ts=require('typescript');
const snapshots=Object.fromEntries(['eip-snapshot.json','nat-snapshot.json','dns-snapshot.json','clb-snapshot.json','snapshot.json','ecs-snapshot.json','jumpserver-snapshot.json','repositories.json'].map(name=>[name,JSON.parse(fs.readFileSync(process.env.RESOURCE_SNAPSHOT_DIR && fs.existsSync(process.env.RESOURCE_SNAPSHOT_DIR+'/'+name) ? process.env.RESOURCE_SNAPSHOT_DIR+'/'+name : 'lib/'+name,'utf8'))]));
const compiled=ts.transpileModule(fs.readFileSync('lib/resource-comparison.ts','utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText;
const sandbox={exports:{},require:name=>snapshots[name.replace('./','')],URL};
vm.runInNewContext(compiled,sandbox);
const c=sandbox.exports;
const plain=value=>JSON.parse(JSON.stringify(value));
const empty=()=>({apps:[],instances:[],projects:[],assets:[],jumpGroups:[],repos:[],codeGroups:[]});

test('all three input sets are preserved once per IP; missing coverage uses the union denominator',()=>{
  const input=empty();
  input.apps=[{id:'a',name:'A',envs:{TEST:[{ip:'10.58.1.1'},{ip:'10.58.1.1'}]}}];
  input.instances=[{id:'e',projectId:'',privateIps:['10.58.1.1'],publicIps:['1.2.3.4']}];
  input.assets=[{id:'j1',ip:'10.58.1.1'},{id:'j2',ip:'10.58.1.1'},{id:'j3',ip:'8.8.8.8'}];
  const rows=c.buildComparison(input),s=c.summarizeComparison(rows);
  assert.equal(rows.length,3);assert.equal(s.scope,2);assert.equal(s.missing,1);assert.equal(s.devopsMissing,0);assert.equal(s.aliyunMissing,1);
});
test('same ECS alternate IP does not imply exact-IP coverage or inherit unrelated fields',()=>{
  const input=empty();input.instances=[{id:'e',projectId:'',privateIps:['10.1.1.1'],publicIps:['1.2.3.4']}];
  input.assets=[{id:'j',ip:'10.1.1.1'}];input.jumpGroups=[{id:'g',key:'1',name:'Root',assetIds:['j']}];
  const row=c.buildComparison(input).find(r=>r.ip==='1.2.3.4');
  assert.equal(row.sources.jumpserver,false);assert.deepEqual(plain(row.otherManagedIps),['10.1.1.1']);
  assert.deepEqual(plain(row.jumpGroups),[]);assert.deepEqual(plain(row.associations),[]);assert.deepEqual(plain(row.projectNames),[]);
});
test('on-prem is only the confirmed 10.58/16 range; unmatched private IP stays blank',()=>{
  const input=empty();input.assets=['10.58.0.1','10.58.255.254','10.59.0.1','10.580.0.1'].map((ip,i)=>({id:String(i),ip}));
  const rows=c.buildComparison(input);
  assert.equal(rows.find(r=>r.ip==='10.58.0.1').resourceType,'公司机房');
  assert.equal(rows.find(r=>r.ip==='10.58.255.254').resourceType,'公司机房');
  for(const ip of ['10.59.0.1','10.580.0.1'])assert.equal(rows.find(r=>r.ip===ip).resourceType,'');
});
test('duplicate IPs retain distinct most-specific memberships for every JumpServer asset',()=>{
  const input=empty();input.assets=[{id:'j1',ip:'10.1.1.1'},{id:'j2',ip:'10.1.1.1'}];
  input.jumpGroups=[{id:'r',key:'1',name:'Default',assetIds:['j1','j2']},{id:'child',key:'1:0',name:'PROD',path:'Default / PROD',assetIds:['j1']}];
  assert.deepEqual(plain(c.buildComparison(input)[0].jumpGroups),['Default','Default / PROD']);
});
test('application, port, environment, Git group and Push In stay aligned; repeated deployment keeps latest time',()=>{
  const input=empty();
  input.codeGroups=[{id:'prod',name:'生产代码组'},{id:'test',name:'测试代码组'}];
  input.repos=[{url:'https://codeup.aliyun.com/org/prod/api.git',groupId:'prod'},{url:'https://codeup.aliyun.com/org/test/api.git',groupId:'test'}];
  input.apps=[{id:'a',name:'api',repository:'https://codeup.aliyun.com/org/prod/api.git',port:'8000',envs:{
    TEST:[{ip:'10.58.1.1',port:'8001',repository:'git@codeup.aliyun.com:org/test/api.git',lastPublishedAt:'2026-09-07 10:00:00'},{ip:'10.58.1.1',port:'8001',repository:'git@codeup.aliyun.com:org/test/api.git',lastPublishedAt:'2026-09-08 10:00:00'}],
    PRODUCT:[{ip:'10.58.1.1',lastPublishedAt:'2026-09-06 09:00:00'}]}}];
  const associations=c.buildComparison(input)[0].associations;assert.equal(associations.length,2);
  const test=associations.find(a=>a.env==='TEST'),prod=associations.find(a=>a.env==='PRODUCT');
  assert.equal(test.port,'8001');assert.equal(test.codeGroup,'测试代码组');assert.equal(test.pushIn,'2026-09-08 10:00:00');
  assert.equal(prod.port,'8000');assert.equal(prod.codeGroup,'生产代码组');assert.equal(prod.pushIn,'2026-09-06 09:00:00');
});
test('Git matches use complete host and case-sensitive path; unsafe links are not clickable',()=>{
  assert.equal(c.canonicalGit('git@codeup.aliyun.com:org/group/api.git'),c.canonicalGit('https://codeup.aliyun.com/org/group/api'));
  assert.notEqual(c.canonicalGit('https://codeup.aliyun.com/org/group/API'),c.canonicalGit('https://codeup.aliyun.com/org/group/api'));
  assert.notEqual(c.canonicalGit('https://other.example/org/group/api'),c.canonicalGit('https://codeup.aliyun.com/org/group/api'));
  for(const bad of ['javascript:alert(1)','https://user:password@example.com/a/b','https://example.com/a/b?token=x'])assert.equal(c.canonicalGit(bad),'');
});
test('resource conflicts and overlapping cloud IPs remain visible instead of choosing one project',()=>{
  const input=empty();input.projects=[{id:'p1',name:'P1'},{id:'p2',name:'P2'}];
  input.instances=[{id:'e1',projectId:'p1',privateIps:['10.58.1.1'],publicIps:[]},{id:'e2',projectId:'p2',privateIps:['10.58.1.1'],publicIps:[]}];
  const row=c.buildComparison(input)[0];assert.equal(row.classificationConflict,true);assert.equal(row.cloudInstanceIds.length,2);assert.deepEqual(plain(row.projectNames),['P1','P2']);
});
test('real snapshots reconcile IP counts and composable missing-DevOps filters',()=>{
  const d=snapshots['snapshot.json'],e=snapshots['ecs-snapshot.json'],j=snapshots['jumpserver-snapshot.json'];
  const ips=new Set([...d.apps.flatMap(a=>Object.values(a.envs).flatMap(rows=>rows.map(r=>r.ip))),...e.instances.flatMap(r=>[...r.privateIps,...r.publicIps]),...j.assets.map(a=>a.ip)].map(c.normalizeIp).filter(Boolean));
  const v4=text=>(text.match(/(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])/g)||[]).filter(ip=>ip.split('.').every(n=>Number(n)<=255));
  const clb=snapshots['clb-snapshot.json'],nat=snapshots['nat-snapshot.json'];
  const extra=[...(clb.available?clb.instances.flatMap(i=>[i.ip,...i.groups.flatMap(g=>g.servers.map(s=>s.ip))]):[]),
    ...snapshots['eip-snapshot.json'].records.flatMap(r=>[r.ip,...r.notes.flatMap(n=>v4(n.value))]),
    ...(nat.available?nat.gateways.flatMap(g=>g.entries.flatMap(e=>[e.externalIp,e.internalIp])):[]),
    ...snapshots['dns-snapshot.json'].records.flatMap(r=>r.type==='AAAA'?[r.value]:v4(r.value))];
  for(const raw of extra) { const ip=c.normalizeIp(raw); if(ip) ips.add(ip); }
  assert.equal(c.comparisonRows.length,ips.size);
  for(const ip of ips) {
    const row=c.comparisonRows.find(r=>r.ip===ip);
    assert.ok(row,`missing IP ${ip}`);
    assert.ok(row.sources.devops||row.sources.jumpserver||row.cloudInstanceIds.length||row.clbInstanceIds.length||row.references.length,`missing link ${ip}`);
  }
  const rows=c.filterComparison(c.comparisonRows,{...c.emptyComparisonFilters,coverage:'missing',source:'devops'});
  assert.equal(rows.length,c.summarizeComparison(c.comparisonRows).devopsMissing);
  assert.ok(rows.every(r=>r.sources.devops&&!r.sources.jumpserver));
  assert.equal(c.filterComparison(c.comparisonRows,{...c.emptyComparisonFilters,query:'no-such-ip-or-app'}).length,0);
});

test('CLB addresses merge by IP and retain cloud identity without inheriting backend applications',()=>{const input=empty();input.clbInstances=[{id:'lb',ip:'10.179.4.60'},{id:'lb',ip:'10.179.4.60'}];input.assets=[{id:'j',ip:'10.179.4.60'}];const rows=c.buildComparison(input);assert.equal(rows.length,1);assert.equal(rows[0].resourceType,'阿里云 CLB');assert.equal(rows[0].clbInstanceIds.length,1);assert.equal(rows[0].sources.jumpserver,true);assert.equal(rows[0].associations.length,0);assert.equal(c.summarizeComparison(rows).aliyun,1);assert.equal(c.filterComparison(rows,{...c.emptyComparisonFilters,resource:'阿里云 CLB'}).length,1);});

test('Push In filters keep matching associations and sorting keeps empty times last without changing source',()=>{
  const input=empty();
  input.apps=[{id:'a',name:'A',envs:{TEST:[
    {ip:'10.0.0.1',port:'1',lastPublishedAt:'2026-09-01 10:00:00'},
    {ip:'10.0.0.1',port:'2'},
    {ip:'10.0.0.1',port:'3',lastPublishedAt:'2026-09-15 10:00:00'},
    {ip:'10.0.0.2',lastPublishedAt:'2026-09-10 10:00:00'},
  ]}}];
  input.assets=[{id:'j',ip:'10.0.0.3'}];
  const rows=c.buildComparison(input),before=JSON.stringify(rows);
  const select=patch=>c.filterComparison(rows,{...c.emptyComparisonFilters,...patch});
  assert.deepEqual(plain(select({pushIn:'missing'}).map(r=>r.ip)),['10.0.0.1','10.0.0.3']);
  assert.equal(select({pushIn:'missing'})[0].associations.length,1);
  assert.deepEqual(plain(select({pushIn:'present'}).map(r=>r.associations.length)),[2,1]);
  assert.deepEqual(plain(select({pushInSort:'asc'}).map(r=>r.ip)),['10.0.0.2','10.0.0.1','10.0.0.3']);
  const descending=select({pushInSort:'desc'});
  assert.deepEqual(plain(descending.map(r=>r.ip)),['10.0.0.1','10.0.0.2','10.0.0.3']);
  assert.deepEqual(plain(descending[0].associations.map(a=>a.pushIn)),['2026-09-15 10:00:00','2026-09-01 10:00:00','']);
  assert.equal(select({pushIn:'present',query:'10.0.0.3'}).length,0);
  assert.equal(JSON.stringify(rows),before);
});

test('IP union includes EIP notes, NAT endpoints, unreferenced CLB backends and paused DNS with source links',()=>{
  const input=empty();
  input.eips=[{id:'eip-only',name:'公网',owner:'账户',ip:'203.0.113.1',notes:[{value:'后端 10.0.0.9；非法 999.0.0.1'}]}];
  input.natGateways=[{id:'nat',name:'网关',entries:[{id:'rule',externalIp:'203.0.113.1',internalIp:'10.0.0.2'},{id:'rule2',externalIp:'203.0.113.1',internalIp:'10.0.0.2'}]}];
  input.clbInstances=[{id:'lb',ip:'10.0.0.3',groups:[{servers:[{ip:'10.0.0.4'}]}]}];
  input.dnsRecords=[{id:'dns-pause',name:'paused.example',type:'A',value:'203.0.113.2',status:'暂停'}, {id:'dns6',name:'v6.example',type:'AAAA',value:'2001:db8::1',status:'启用'}];
  const rows=c.buildComparison(input),ips=rows.map(r=>r.ip);
  assert.equal(new Set(ips).size,7);
  for(const ip of ['203.0.113.1','10.0.0.9','10.0.0.2','10.0.0.3','10.0.0.4','203.0.113.2','2001:db8::1']) assert.ok(ips.includes(ip));
  assert.ok(!ips.includes('999.0.0.1'));
  assert.equal(rows.find(r=>r.ip==='10.0.0.2').references.length,1);
  assert.equal(rows.find(r=>r.ip==='10.0.0.2').cloudInstanceIds.length,0);
  assert.equal(rows.find(r=>r.ip==='10.0.0.9').sources.aliyun,false);
  assert.ok(rows.find(r=>r.ip==='10.0.0.4').references[0].href.includes('view=instances'));
  assert.ok(rows.find(r=>r.ip==='203.0.113.2').references[0].href.includes('view=dns&q=dns-pause'));
  assert.equal(c.filterComparison(rows,{...c.emptyComparisonFilters,query:'paused.example'}).length,1);
  assert.equal(c.filterComparison(rows,{...c.emptyComparisonFilters,source:'nat'}).length,2);
});

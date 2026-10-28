const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const ts=require('typescript');
function load(file, imports={}) {
 const context={exports:{},require:id=>imports[id]};
 vm.runInNewContext(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText,context);
 return context.exports;
}
const local=load('lib/datacenter-networks.ts');
const lib=load('lib/jumpserver-overview.ts',{'./datacenter-networks':local});
const asset=(id,ip,inspection)=>({id,ip,hostname:id,inspection});
const ecs=(id,ips,status='Running',tenantId='a')=>({id,name:id,privateIps:ips,publicIps:[],status,tenantId,region:'r'});
const login=(sudoStatus,account='folidev')=>({loginStatus:'can_login',sudoStatus,account});
function plain(value) {return JSON.parse(JSON.stringify(value));}

test('双向按实例匹配公网或私网，保留停止与不可登录',()=>{
 const assets=[asset('stopped','203.0.113.1',{loginStatus:'cannot_login',reason:'timeout'}),asset('private','10.0.0.2',login('password'))];
 const machines=[{...ecs('one',['10.0.0.1'],'Stopped'),publicIps:['203.0.113.1']},ecs('two',['10.0.0.2'])];
 const result=lib.buildJumpserverOverview(assets,machines);
 assert.deepEqual(plain(result.jump.map(r=>[r.login,r.location])),[['cannot_login','stopped'],['elevated','running']]);
 assert.deepEqual(plain(result.ecs.map(r=>r.coverage)),['managed','managed']);
});
test('多候选、缺失地址、网段冲突不被标为纳管或未纳管',()=>{
 const assets=[asset('a','10.0.0.1',login('denied')),asset('b','10.0.0.1'),asset('local','10.58.1.1'),asset('missing','')];
 const machines=[ecs('duplicate',['10.0.0.1']),ecs('conflict',['10.58.1.1']),ecs('no-ip',[])];
 const r=lib.buildJumpserverOverview(assets,machines);
 assert.deepEqual(plain(r.jump.map(v=>v.location)),['ambiguous','ambiguous','ambiguous','ambiguous']);
 assert.deepEqual(plain(r.ecs.map(v=>v.coverage)),['ambiguous','ambiguous','ambiguous']);
});
test('ECS 多地址匹配两个资产时保留歧义，跨租户重叠也不随意选择',()=>{
 const r=lib.buildJumpserverOverview([asset('a','10.0.0.1'),asset('b','203.0.113.1')],[{...ecs('x',['10.0.0.1']),publicIps:['203.0.113.1']}]);
 assert.deepEqual(plain(r.jump.map(v=>v.location)),['ambiguous','ambiguous']);
 assert.equal(r.ecs[0].coverage,'ambiguous');
 const overlapping=lib.buildJumpserverOverview([asset('a','10.0.0.1')],[ecs('x',['10.0.0.1'],'Running','one'),ecs('x',['10.0.0.1'],'Stopped','two')]);
 assert.equal(overlapping.jump[0].location,'ambiguous');
 assert.deepEqual(plain(overlapping.ecs.map(v=>v.coverage)),['ambiguous','ambiguous']);
});
test('不在 ECS、本地机房和未匹配区分，过渡状态及未知权限不混合',()=>{
 const assets=[asset('local','10.58.9.1',login('password_error')),asset('other','10.0.0.5',login('timeout')),asset('transition','10.0.0.6',login('root')),asset('unknown','10.0.0.7')];
 const r=lib.buildJumpserverOverview(assets,[ecs('t',['10.0.0.6'],'Starting'),ecs('orphan',['10.0.0.8'])]);
 assert.deepEqual(plain(r.jump.map(v=>[v.login,v.location])),[['ordinary','local'],['elevation_unknown','not_in_ecs'],['elevated','transition'],['unchecked','not_in_ecs']]);
 assert.equal(r.ecs[1].coverage,'unmatched');
});

test('联动视图展示不可登录服务器的 ECS 停止和未匹配明细',()=>{
 const React=require('react'); const {renderToStaticMarkup}=require('react-dom/server');
 const assets=[asset('stopped','203.0.113.1',{loginStatus:'cannot_login',reason:'失败',checkedAt:'2026-10-01T00:00:00Z'}),asset('outside','10.0.0.9',{loginStatus:'cannot_login',reason:'超时'}),asset('ok','10.0.0.2',login('password'))];
 const machines=[{...ecs('one',['10.0.0.1'],'Stopped'),publicIps:['203.0.113.1']},ecs('two',['10.0.0.2'])];
 let index=0; const states=['cannot_login','stopped',false,'all',null];
 const imports={react:{...React,useMemo:f=>f(),useState:initial=>[states[index++]??initial,()=>{}]},'react/jsx-runtime':require('react/jsx-runtime'),
  '@/lib/jumpserver':{jumpserver:{assets,collectedAt:'2026-10-01T00:00:00Z'}},'@/lib/ecs':{instances:machines,fetchedAt:'2026-10-01T00:00:00Z'},'@/lib/ecs-snapshot.json':{default:{tenants:[]}},
  '@/lib/datetime':{formatBeijingTime:(t,fallback)=>t||fallback},'@/lib/jumpserver-overview':lib};
 const source=ts.transpileModule(fs.readFileSync('components/jumpserver-overview.tsx','utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText;
 const context={exports:{},require:id=>imports[id]}; vm.runInNewContext(source,context);
 const html=renderToStaticMarkup(React.createElement(context.exports.JumpserverOverview));
 assert.match(html,/当前范围：不可登录/); assert.match(html,/ECS 已停止 1 台/); assert.match(html,/不在 ECS 清单中 1 台/);
 assert.match(html,/203\.0\.113\.1/); assert.doesNotMatch(html,/10\.0\.0\.9<\/td>/); assert.match(html,/JumpServer 明细 · 1 台/);
 assert.match(html,/导出 CSV/); assert.doesNotMatch(html,/登录 \/ 提权|<th[^>]*>登录<\/th>|登录原因|检查时间|账号：|提权：/);
 const outside=lib.buildJumpserverOverview([assets[1]],machines).jump;
 const csv=context.exports.unreachableOverviewCsv(outside);
 assert.match(csv,/IP,主机名,ECS 状态与关联/); assert.match(csv,/10\.0\.0\.9,outside,不在 ECS 清单中/);
 assert.doesNotMatch(csv,/当前 ECS 清单没有匹配地址/);
 const quoted=context.exports.unreachableOverviewCsv([{...outside[0],asset:{...outside[0].asset,hostname:'=evil,"test"'}}]);
 assert.match(quoted,/"'=evil,""test"""/);
});

test('未纳管明细按 ECS 状态筛选且隐藏零值图例',()=>{
 const React=require('react'); const {renderToStaticMarkup}=require('react-dom/server');
 const assets=[asset('a','10.0.0.2',login('password'))];
 const machines=[ecs('managed',['10.0.0.2']),ecs('unmanaged-stopped',['10.0.0.3'],'Stopped'),ecs('unmanaged-running',['10.0.0.4'],'Running')];
 let index=0; const states=[null,null,false,'stopped','unmatched'];
 const changes=[]; const imports={react:{...React,useMemo:f=>f(),useState:initial=>{const i=index++; return [states[i]??initial,value=>changes.push([i,value])];}},'react/jsx-runtime':require('react/jsx-runtime'),
  '@/lib/jumpserver':{jumpserver:{assets}},'@/lib/ecs':{instances:machines},'@/lib/ecs-snapshot.json':{default:{tenants:[]}},
  '@/lib/datetime':{formatBeijingTime:(t,fallback)=>t||fallback},'@/lib/jumpserver-overview':lib};
 const source=ts.transpileModule(fs.readFileSync('components/jumpserver-overview.tsx','utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText;
 const context={exports:{},require:id=>imports[id]}; vm.runInNewContext(source,context);
 const html=renderToStaticMarkup(React.createElement(context.exports.JumpserverOverview));
 assert.match(html,/ECS 明细 · 1 台/); assert.match(html,/unmanaged-stopped/); assert.doesNotMatch(html,/unmanaged-running/);
 assert.match(html,/ECS 状态 <select/); assert.match(html,/>未纳管</); assert.doesNotMatch(html,/当前账号未匹配|关联依据/);
 assert.doesNotMatch(html,/已纳管 0 台|关联待确认 0 台|不可登录 0 台/);
 index=0; const tree=context.exports.JumpserverOverview();
 function findSelects(node) {
  if(!node||typeof node!=='object')return [];
  if(Array.isArray(node))return node.flatMap(findSelects);
  return [...(node.type==='select'?[node]:[]),...findSelects(node.props?.children)];
 }
 const selects=findSelects(tree); assert.equal(selects.length,2);
 selects.forEach(select=>select.props.onChange({target:{value:'running'}}));
 assert.deepEqual(changes,[[3,'running'],[3,'running']]);
});

test('仅普通账号明细精简，导出遵循当前位置与 folidev 筛选',()=>{
 const React=require('react'); const {renderToStaticMarkup}=require('react-dom/server');
 const assets=[{...asset('local','10.58.9.1',login('denied')),hostname:''},asset('other','10.58.9.2',login('denied','other')),asset('cloud','10.0.0.1',login('denied'))];
 let index=0; const states=['ordinary','local',true,'all',null];
 const imports={react:{...React,useMemo:f=>f(),useState:initial=>[states[index++]??initial,()=>{}]},'react/jsx-runtime':require('react/jsx-runtime'),
  '@/lib/jumpserver':{jumpserver:{assets}},'@/lib/ecs':{instances:[]},'@/lib/ecs-snapshot.json':{default:{tenants:[]}},
  '@/lib/datetime':{formatBeijingTime:(t,fallback)=>t||fallback},'@/lib/jumpserver-overview':lib};
 const source=ts.transpileModule(fs.readFileSync('components/jumpserver-overview.tsx','utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText;
 const context={exports:{},require:id=>imports[id]}; vm.runInNewContext(source,context);
 const html=renderToStaticMarkup(React.createElement(context.exports.JumpserverOverview));
 assert.match(html,/JumpServer 明细 · 1 台/); assert.match(html,/10\.58\.9\.1<\/td>/);
 assert.doesNotMatch(html,/登录 \/ 提权|登录原因|检查时间|命中已确认本地机房网段/); assert.match(html,/导出 CSV/);
 const rows=lib.buildJumpserverOverview(assets,[]).jump.filter(r=>r.login==='ordinary'&&r.location==='local'&&r.asset.inspection.account==='folidev');
 const csv=context.exports.ordinaryOverviewCsv(rows);
 assert.equal(csv,'IP,主机名,ECS 状态与关联\r\n10.58.9.1,,本地机房');
 assert.doesNotMatch(csv,/10\.58\.9\.2|10\.0\.0\.1|命中已确认本地机房网段/);
});

test('可切Root与不在 ECS 清单筛选精简明细，CSV 保持一致',()=>{
 const React=require('react'); const {renderToStaticMarkup}=require('react-dom/server');
 const assets=[asset('root','10.0.0.1',login('root')),asset('failed','10.0.0.2',{loginStatus:'cannot_login',reason:'failure'})];
 const machines=[ecs('vm',['10.0.0.1'])]; let index=0, states=['elevated',null,false,'all',null];
 const imports={react:{...React,useMemo:f=>f(),useState:initial=>[states[index++]??initial,()=>{}]},'react/jsx-runtime':require('react/jsx-runtime'),
  '@/lib/jumpserver':{jumpserver:{assets}},'@/lib/ecs':{instances:machines},'@/lib/ecs-snapshot.json':{default:{tenants:[]}},
  '@/lib/datetime':{formatBeijingTime:(t,fallback)=>t||fallback},'@/lib/jumpserver-overview':lib};
 const source=ts.transpileModule(fs.readFileSync('components/jumpserver-overview.tsx','utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText;
 const context={exports:{},require:id=>imports[id]}; vm.runInNewContext(source,context);
 let html=renderToStaticMarkup(React.createElement(context.exports.JumpserverOverview));
 assert.match(html,/可切Root/); assert.doesNotMatch(html,/已提权|登录 \/ 提权|登录原因|检查时间|账号：|提权：/);
 index=0; states=[null,'not_in_ecs',false,'all',null];
 html=renderToStaticMarkup(React.createElement(context.exports.JumpserverOverview));
 assert.match(html,/10\.0\.0\.2/); assert.doesNotMatch(html,/当前 ECS 清单没有匹配地址|登录 \/ 提权|登录原因|检查时间|账号：|提权：/);
 const rows=lib.buildJumpserverOverview(assets,machines).jump.filter(r=>r.location==='not_in_ecs');
 assert.equal(context.exports.unreachableOverviewCsv(rows),'IP,主机名,ECS 状态与关联\r\n10.0.0.2,failed,不在 ECS 清单中');
 assert.equal(context.exports.ordinaryOverviewCsv(rows,true),'IP,主机名,ECS 状态与关联\r\n10.0.0.2,failed,不在 ECS 清单中');
});

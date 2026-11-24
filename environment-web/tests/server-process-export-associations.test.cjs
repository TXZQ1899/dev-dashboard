const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');const vm=require('node:vm');const ts=require('typescript');
function load(file, imports={}) {const c={exports:{},require:id=>imports[id]};vm.runInNewContext(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText,c);return c.exports;}
const proc=load('lib/server-processes.ts');
const local=load('lib/datacenter-networks.ts');
const overview=load('lib/jumpserver-overview.ts',{'./datacenter-networks':local});
const inventory={apps:[],envs:['TEST','SIMULATION','PRODUCT']};
const compare=load('lib/process-comparison.ts',{
 '@/lib/jumpserver':{jumpserver:{assets:[]}},'@/lib/server-processes':proc,'@/lib/inventory':inventory,
 '@/lib/server-specs':{specs:[]},'@/lib/ecs':{instances:[]},'@/lib/local-servers':{localServers:[],nominalMemoryGiB:()=>0},
 './datetime':load('lib/datetime.ts')
});
const lib=load('lib/server-process-export-associations.ts',{'./jumpserver-overview':overview,'./process-comparison':compare,'./inventory':inventory,'./server-processes':proc});
const js=(id,ip)=>({id,ip,hostname:ip+'_server',inspection:{loginStatus:'can_login',processStatus:'complete',processes:[]}});
const ecs={id:'ecs-1',name:'instance',tenantName:'tenant',privateIps:['10.1.1.2'],publicIps:['203.0.113.9'],status:'Running'};
const app=(id,name,ip,port='')=>({id,name,port,envs:{TEST:[{ip,port}],SIMULATION:[],PRODUCT:[]}});
const processRow=(asset,command,ports=[])=>({asset,type:'Java Jar',process:{name:'java',kind:'Java',command,ports}});
test('public JumpServer IP uses unique ECS private address to match application and direct ports',()=>{
 const asset=js('a','203.0.113.9'), application=app('42','booking','10.1.1.2','8080');
 const result=lib.buildExportAssociations([asset],[ecs],[application]);
 assert.equal(result.instance(asset).id,'ecs-1');
 assert.equal(result.match(asset,processRow(asset,'java -jar /apps/booking.jar',[8080])).id,'42');
 assert.equal(result.match(asset,processRow(asset,'java -jar /apps/booking.jar',[9090])),undefined);
});
test('ambiguous ECS, multiple matching applications and container associations are unresolved',()=>{
 const asset=js('a','203.0.113.9');const row=processRow(asset,'java -jar /apps/booking.jar');
 const duplicate={...ecs,id:'other',tenantId:'other'};
 const ambiguous=lib.buildExportAssociations([asset],[ecs,duplicate],[app('42','booking','10.1.1.2')]);
 assert.equal(ambiguous.instance(asset),undefined);assert.equal(ambiguous.canAssociate(asset),false);assert.equal(ambiguous.match(asset,row),undefined);
 const byProcess=lib.buildExportAssociations([asset],[ecs],[app('42','booking','10.1.1.2'),app('43','booking','10.1.1.2')]);
 assert.equal(byProcess.match(asset,row),undefined);
 const unique=lib.buildExportAssociations([asset],[ecs],[app('42','booking','10.1.1.2')]);
 assert.equal(unique.match(asset,{...row,docker:{}}),undefined);
});

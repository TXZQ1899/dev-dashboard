/* eslint-disable typescript/no-require-imports -- CommonJS Node test harness. */
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const ts=require('typescript');
const React=require('react');
const {renderToStaticMarkup}=require('react-dom/server');
function evaluate(file,requireModule){
 const sandbox={exports:{},require:requireModule};
 vm.runInNewContext(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText,sandbox);
 return sandbox.exports;
}
function render(view, fixture, resources={}){
 const listener={protocol:'HTTPS',port:443,status:'running',description:'',groupId:'g',forwardPort:null,rules:[],certificates:[{id:'cert',domain:'*.example.com'}]};
 const instances=fixture || [{id:'with-cert',name:'有证书实例',ip:'10.0.0.1',listeners:[listener],groups:[]},{id:'no-cert',name:'无证书实例',ip:'10.0.0.2',listeners:[],groups:[]}];
 const cloud=evaluate('lib/clb.ts',()=>({available:true,instances}));
 const eip=evaluate('lib/eip.ts',()=>JSON.parse(fs.readFileSync('lib/eip-snapshot.json','utf8')));
 const dns=evaluate('lib/dns.ts',id=>id==='./eip'?eip:{zones:[],sources:[],records:[]});
 const nat=evaluate('lib/nat.ts',()=>resources.nat || {available:false,gateways:[],collectedAt:null});
 const page=evaluate('app/aliyun/clb/page.tsx',id=>{
  if(id==='react')return {...React,useEffect:()=>{},useState:initial=>[initial==='certificates'&&view?view:initial,()=>{}]};
  if(id==='react/jsx-runtime')return require(id);
  if(id==='@/lib/clb')return cloud;
  if(id==='@/lib/dns')return dns;
  if(id==='@/lib/eip')return eip;
  if(id==='@/lib/nat')return nat;
  if(id==='@/lib/ecs')return {instances:resources.ecs || [],fetchedAt:'today'};
  if(id==='@/lib/jumpserver')return {jumpserver:{assets:[]}};
  if(id==='@/app/page')return {Shell:({children})=>children};
  if(id.includes('input'))return {Input:()=>null};
  if(id.includes('button'))return {Button:({children})=>React.createElement('button',null,children)};
  return {};
 });
 return renderToStaticMarkup(React.createElement(page.default));
}
test('default CLB entry remains certificate view even with no DNS matches',()=>{
 const html=render();assert.match(html,/有证书实例/);assert.match(html,/全部 2 个 CLB/);assert.match(html,/静态 DNS 关联/);
});
test('instance view retains every CLB including those without certificates or DNS',()=>{
 const html=render('instances');assert.match(html,/有证书实例/);assert.match(html,/无证书实例/);assert.match(html,/暂无启用记录/);
});
test('EIP view retains all 53 records and source notes without matched CLB',()=>{
 const html=render('eip',[]);
 const snapshot=JSON.parse(fs.readFileSync('lib/eip-snapshot.json','utf8'));
 assert.equal(snapshot.records.length,53);
 assert.equal(snapshot.records.filter(r=>r.owner==='泛宥').length,2);
 assert.equal(snapshot.records.filter(r=>r.owner==='修平').length,35);
 assert.equal(snapshot.records.filter(r=>r.owner==='thomascookjv').length,16);
 assert.equal(new Set(snapshot.records.map(r=>r.key)).size,53);
 for(const record of snapshot.records)assert.ok(html.includes(record.id),record.id);
 assert.match(html,/tcc.cn\n/);assert.match(html,/无ssl证书/);assert.match(html,/2026-06-26/);
});
test('CLB details show EIP linked by binding ID even when the address is private',()=>{
 const snapshot=JSON.parse(fs.readFileSync('lib/eip-snapshot.json','utf8'));
 const record=snapshot.records.find(r=>r.bindingType==='SLB 实例');
 const html=render('instances',[{id:record.bindingId,name:'内网 CLB',ip:'10.1.2.3',listeners:[],groups:[]}]);
 assert.ok(html.includes(record.ip));assert.match(html,/绑定实例 ID 一致/);
});
test('ECS EIP shows instance IP matched by ID without inventing ports',()=>{
 const html=render('eip',[],{ecs:[{id:'i-uf62q56mzxz7qiw5f5bw',name:'booking',privateIps:['10.179.9.8'],publicIps:[]}]});
 assert.match(html,/10.179.9.8/);assert.match(html,/ECS 绑定记录未提供端口转换规则/);
});
test('NAT view renders protocols, differing ports and unavailable versus empty states',()=>{
 assert.match(render('nat',[]),/该版本尚未采集 NAT/);
 const html=render('nat',[],{nat:{available:true,collectedAt:'today',gateways:[{id:'ngw',name:'gateway',entries:[{id:'rule',tableId:'table',name:'',externalIp:'1.2.3.4',externalPort:'443',internalIp:'10.0.0.1',internalPort:'8443',protocol:'tcp',status:'Available'}]}]}});
 assert.match(html,/8443/);assert.match(html,/443/);assert.match(html,/TCP/);
});

if(process.env.CLB_PAGE_FIXTURE){
 test('current full snapshot renders every instance without requiring DNS',()=>{
  const data=JSON.parse(fs.readFileSync(process.env.CLB_PAGE_FIXTURE));
  const html=render('instances',data.instances);
  for(const instance of data.instances) assert.ok(html.includes(instance.id),instance.id);
  console.log(`Verified all ${data.instances.length} CLB instance IDs in rendered page`);
 });
}

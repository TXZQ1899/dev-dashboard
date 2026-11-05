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
 const source=fs.readFileSync(file,'utf8')+(file==='app/aliyun/clb/page.tsx'?'\nexport { DnsDomains };':'');
 vm.runInNewContext(ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText,sandbox);
 return sandbox.exports;
}
function render(view, fixture, resources={}){
 let stateIndex=0;
 const listener={protocol:'HTTPS',port:443,status:'running',description:'',groupId:'g',forwardPort:null,rules:[],certificates:[{id:'cert',domain:'*.example.com'}]};
 const instances=fixture || [{id:'with-cert',name:'有证书实例',ip:'10.0.0.1',listeners:[listener],groups:[]},{id:'no-cert',name:'无证书实例',ip:'10.0.0.2',listeners:[],groups:[]}];
 const cloud=evaluate('lib/clb.ts',()=>({available:true,instances}));
 const downstream=evaluate('lib/dns-downstream.ts',()=>({}));
 const eip=evaluate('lib/eip.ts',()=>JSON.parse(fs.readFileSync('lib/eip-snapshot.json','utf8')));
 const cloudDns={cloudDnsRecords:[],cloudDns:{zones:[],tenants:[]}};
 const dns=evaluate('lib/dns.ts',id=>id==='./eip'?eip:id==='./cloud-dns'?cloudDns:{zones:[],sources:[],records:[]});
 const nat=evaluate('lib/nat.ts',()=>resources.nat || {available:false,gateways:[],collectedAt:null});
 // 页面时间展示已统一委托给 lib/datetime.ts。
 const datetimeLib=evaluate('lib/datetime.ts',()=>({}));
 const tenants=evaluate('lib/tenants.ts',()=>({}));
 const tenantFilter=evaluate('components/tenant-filter.tsx',id=>id==='@/lib/tenants'?tenants:require(id));
 const page=evaluate('app/aliyun/clb/page.tsx',id=>{
  if(id==='react')return {...React,useEffect:()=>{},useState:initial=>[(stateIndex++===1&&resources.tenant)?resources.tenant:initial==='certificates'&&view?view:initial,()=>{}]};
  if(id==='react/jsx-runtime')return require(id);
  if(id==='@/lib/clb')return cloud;
  if(id==='@/lib/dns-downstream')return downstream;
  if(id==='@/lib/dns')return dns;
  if(id==='@/lib/cloud-dns')return cloudDns;
  if(id==='@/lib/eip')return eip;
  if(id==='@/lib/nat')return nat;
  if(id==='@/lib/ecs')return {instances:resources.ecs || [],fetchedAt:'today'};
  if(id==='@/lib/jumpserver')return {jumpserver:{assets:resources.assets || []}};
  if(id==='@/lib/datetime')return datetimeLib;
  if(id==='@/lib/tenants')return tenants;
  if(id==='@/components/tenant-filter')return tenantFilter;
  if(id==='@/app/page')return {Shell:({children})=>children};
  if(id.includes('input'))return {Input:()=>null};
  if(id.includes('button'))return {Button:({children})=>React.createElement('button',null,children)};
  return {};
 });
 return renderToStaticMarkup(resources.dnsRows ? React.createElement(page.DnsDomains,{rows:resources.dnsRows,query:'',expanded:{},onQuery(){},onExpand(){},onCollapse(){},toggle(){}}) : React.createElement(page.default));
}
test('DNS view follows domain routes to the matching nginx host and keeps URI conditions',()=>{
 const listener={protocol:'HTTP',port:80,status:'running',groupId:'g',backendPort:80,forwardPort:null,rules:[{id:'other',domain:'other.example.com',path:'',groupId:'other'}],certificates:[]};
 const instance={id:'lb',name:'LB',ip:'10.0.0.1',listeners:[listener],groups:[{id:'g',name:'默认组',servers:[{id:'ecs',ip:'10.0.0.2',port:80}]},{id:'other',name:'无关组',servers:[{id:'x',ip:'10.0.0.9',port:80}]}]};
 const route=(uri,port,listen=['80'])=>({domains:['nexus.example.com'],listen,uri,directive:'proxy_pass',target:'http://upstream',backends:[{host:'192.0.2.1',port,resolution:'ip'}]});
 const assets=[{id:'asset',ip:'10.0.0.2',inspection:{nginxStatus:'complete',nginxRoutes:[route('/','8981'),route('/v2/','8082'),{...route('~ /private/',''),directive:'access_control',target:'deny all',upstream:'',backends:[]},route('/other-port','9999',['81'])]}}];
 const record={id:'r',name:'nexus.example.com',type:'A',value:'10.0.0.1',line:'默认',status:'启用',source:'fixture'};
 const html=render('dns',[instance],{assets,ecs:[{id:'ecs',name:'Nginx主机',privateIps:['10.0.0.2'],publicIps:[]}],dnsRows:[{record,matches:[{instance,paths:[]}],eipMatches:[],resolutions:[{ip:'10.0.0.1'}],status:'已关联 CLB'}]});
 assert.match(html,/默认路由/);assert.match(html,/Nginx主机/);assert.match(html,/8981/);assert.match(html,/8082/);assert.match(html,/\/v2\//);
 assert.match(html,/<a href="\/jumpserver\?q=10\.0\.0\.2&amp;view=processes&amp;exact=1">10\.0\.0\.2<\/a>/);
 assert.doesNotMatch(html,/候选声明来自已匹配的路由来源文件|按 IP、端口和域名关联/);
 assert.match(html,/<th>路径<\/th><th>处理方式<\/th><th>upstream \/ 下游<\/th><th>配置文件<\/th>/);
 assert.match(html,/proxy_pass：/);assert.match(html,/192\.0\.2\.1/);
 assert.match(html,/access_control/);assert.match(html,/deny all/);
 assert.doesNotMatch(html,/无关组|10\.0\.0\.9|9999/);
});
test('DNS downstream preserves missing and ambiguous nginx evidence',()=>{
 const f=evaluate('lib/dns-downstream.ts',()=>({}));
 const route={domains:['nexus.example.com'],listen:['80'],uri:'/',directive:'proxy_pass',target:'http://upstream',backends:[]};
 const asset={id:'one',ip:'10.0.0.2',inspection:{nginxStatus:'complete',nginxRoutes:[route]}};
 assert.equal(f.nginxRoutesForBackend([asset],asset.ip,81,'nexus.example.com').routes.length,0);
 assert.equal(f.nginxRoutesForBackend([asset,{...asset,id:'two'}],asset.ip,80,'nexus.example.com').status,'unknown');
 assert.equal(f.nginxRoutesForBackend([asset],asset.ip,null,'nexus.example.com').status,'unknown');
 const select=f.dnsListenerRoutes({rules:[{domain:'*.example.com',path:'/api',groupId:'x'}]},'nexus.example.com');
 assert.equal(select.rules.length,1);assert.equal(select.defaultPossible,true);
});
test('nginx config references expand archived includes and report missing files',()=>{
 const f=evaluate('lib/dns-downstream.ts',()=>({}));
 const file=(path,content)=>({path,content,instance:'nginx'});
 const files=[file('/etc/nginx/nginx.conf','include /etc/nginx/conf.d/*.conf;'),file('/etc/nginx/conf.d/site.conf','server {\n server_name sit.example.com h5.example.com;\n include /etc/nginx/config/routes.conf;\n include missing.conf;\n}'),file('/etc/nginx/config/routes.conf','include proxy.conf;'),file('/etc/nginx/proxy.conf','')];
 const result=f.nginxFileIncludes(files,[{file:'/etc/nginx/conf.d/site.conf',instance:'nginx'}]);
 const edges=result.edges.map(e=>`${e.from} → ${e.to}`);
 assert.ok(edges.includes('/etc/nginx/nginx.conf → /etc/nginx/conf.d/site.conf'));
 assert.ok(edges.includes('/etc/nginx/conf.d/site.conf → /etc/nginx/config/routes.conf'));
 assert.ok(edges.includes('/etc/nginx/config/routes.conf → /etc/nginx/proxy.conf'));
 assert.ok(result.warnings.some(w=>w.includes('missing.conf')));
 const names=f.nginxServerNames(files,[{file:'/etc/nginx/conf.d/site.conf',instance:'nginx'}],'h5.example.com');
 assert.equal(names.length,1);assert.equal(names[0].path,'/etc/nginx/conf.d/site.conf');assert.ok(names[0].names.includes('sit.example.com'));
 assert.equal(f.nginxServerNames(files,[{file:'/etc/nginx/conf.d/site.conf',instance:'nginx'}],'unknown.example.com').length,0);
});
test('DNS EIP view follows standard web DNAT endpoints and omits unrelated ports and CLBs',()=>{
 const listener=(protocol,port)=>({protocol,port,status:'running',groupId:'g',backendPort:80,forwardPort:null,rules:[],certificates:[]});
 const instance={id:'lb',name:'WEB-CLB',ip:'10.179.0.33',listeners:[listener('HTTP',80),listener('HTTPS',443)],groups:[{id:'g',name:'默认',servers:[{id:'ecs',ip:'10.179.1.224',port:80}]}]};
 const other={id:'other',name:'OTHER-CLB',ip:'10.179.2.151',listeners:[listener('TCP',60022)],groups:[]};
 const entry=(id,port,ip)=>({id,tableId:'table',name:id,externalIp:'47.100.20.243',externalPort:String(port),internalIp:ip,internalPort:String(port),protocol:'tcp',status:'Available'});
 const nat={available:true,collectedAt:'today',gateways:[{id:'ngw-uf6bgp5v4dujghijm0d12',name:'gateway',entries:[entry('http',80,instance.ip),entry('https',443,instance.ip),entry('sftp',60022,other.ip)]}]};
 const record={id:'r',name:'h5test.example.com',type:'A',value:'47.100.20.243',line:'默认',status:'启用',source:'fixture'};
 const eip=JSON.parse(fs.readFileSync('lib/eip-snapshot.json','utf8')).records.find(r=>r.ip==='47.100.20.243');
 const rows=[{record,matches:[{instance,paths:[{chain:['h5test.example.com',eip.ip],via:'dnat'}]},{instance:other,paths:[{chain:['h5test.example.com',eip.ip],via:'dnat'}]}],eipMatches:[{eip,paths:[{chain:['h5test.example.com',eip.ip]}]}],resolutions:[{ip:eip.ip}],status:'已关联 CLB'}];
 const html=render('dns',[instance,other],{nat,dnsRows:rows});
 assert.match(html,/WEB-CLB/);assert.match(html,/47\.100\.20\.243:80/);assert.match(html,/47\.100\.20\.243:443/);
 assert.doesNotMatch(html,/OTHER-CLB|60022|静态绑定/);assert.match(html,/非标准端口/);
});
test('default CLB entry remains certificate view even with no DNS matches',()=>{
 const html=render();assert.match(html,/有证书实例/);assert.match(html,/全部 2 个 CLB/);assert.doesNotMatch(html,/DNS 快照关联|静态 EIP 关联/);
});
test('certificate view shows only its default and explicitly bound domain routes',()=>{
 const cert={id:'cert',domain:'',commonName:'*.example.com',name:'example',expiresAt:''};
 const listener={protocol:'HTTPS',port:443,status:'running',groupId:'default',backendPort:80,forwardPort:null,
  certificates:[cert,{...cert,domain:'*.example.com'},{id:'other',domain:'other.com',commonName:'other.com'}],
  rules:[{id:'ours',domain:'*.example.com',path:'',groupId:'ours'},{id:'other-rule',domain:'other.com',path:'',groupId:'other'}]};
 const groups=[{id:'default',name:'默认组',servers:[{id:'a',ip:'10.0.0.1',port:80}]},{id:'ours',name:'相关组',servers:[{id:'b',ip:'10.0.0.2',port:80}]},{id:'other',name:'无关组',servers:[{id:'c',ip:'10.0.0.3',port:80}]}];
 const html=render('certificates',[{id:'lb',name:'LB',ip:'10.1.0.1',listeners:[listener],groups}]);
 const section=html.slice(html.indexOf('example · 到期'),html.indexOf('other.com</strong>'));
 assert.match(section,/默认组/);assert.match(section,/相关组/);assert.doesNotMatch(section,/无关组|10\.0\.0\.3|DNS 快照关联|静态 EIP 关联/);
});
test('instance view retains every CLB including those without certificates or DNS',()=>{
 const html=render('instances');assert.match(html,/有证书实例/);assert.match(html,/无证书实例/);assert.doesNotMatch(html,/DNS 快照关联|running · 0 条规则/);assert.match(html,/默认路由/);
});
test('CLB view folds DNAT and shows only mappings to that instance',()=>{
 const nat={available:true,collectedAt:'today',gateways:[{id:'ngw-uf6bgp5v4dujghijm0d12',name:'gateway',entries:[
  {id:'mine',tableId:'table',name:'mine',externalIp:'47.100.20.243',externalPort:'60022',internalIp:'10.179.2.151',internalPort:'60022',protocol:'tcp',status:'Available'},
  {id:'missing',tableId:'table',name:'missing',externalIp:'47.100.20.243',externalPort:'8100',internalIp:'10.179.2.151',internalPort:'8100',protocol:'tcp',status:'Available'},
  {id:'unknown',tableId:'table',name:'unknown',externalIp:'47.100.20.243',externalPort:'any',internalIp:'10.179.2.151',internalPort:'any',protocol:'tcp',status:'Available'},
  {id:'other',tableId:'table',name:'other',externalIp:'47.100.20.243',externalPort:'443',internalIp:'10.179.0.33',internalPort:'443',protocol:'tcp',status:'Available'}]}]};
 const instance={id:'lb',name:'UK_DEV_SLB',ip:'10.179.2.151',listeners:[{protocol:'TCP',port:60022,status:'running',description:'',groupId:'g',forwardPort:null,rules:[],certificates:[]}],groups:[]};
 const html=render('instances',[instance],{nat});
 assert.match(html,/<details><summary>DNAT 有效监听端口（1 条）/);
 assert.match(html,/<details><summary>DNAT 无效监听端口（1 条）/);
 assert.match(html,/<details><summary>DNAT 端口待核实（1 条）/);
 assert.ok(html.indexOf('DNAT 有效监听端口') < html.indexOf('DNAT 无效监听端口'));
 assert.match(html,/该端口无对应监听端口，访问路径不可达/);
 assert.match(html,/60022/);assert.doesNotMatch(html,/10\.179\.0\.33/);
 assert.doesNotMatch(html,/关联依据：/);
 const onlyValid=render('instances',[instance],{nat:{...nat,gateways:[{...nat.gateways[0],entries:[nat.gateways[0].entries[0]]}]}});
 assert.match(onlyValid,/DNAT 有效监听端口（1 条）/);assert.doesNotMatch(onlyValid,/DNAT 无效监听端口|DNAT 端口待核实/);
 const onlyInvalid=render('instances',[{...instance,listeners:[]}],{nat:{...nat,gateways:[{...nat.gateways[0],entries:[nat.gateways[0].entries[1]]}]}});
 assert.match(onlyInvalid,/DNAT 无效监听端口（1 条）/);assert.doesNotMatch(onlyInvalid,/DNAT 有效监听端口/);
 const eipHtml=render('eip',[instance],{nat});assert.match(eipHtml,/10\.179\.0\.33/);
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
 assert.ok(html.includes(record.ip));assert.doesNotMatch(html,/关联依据：/);
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
test('tenant selection filters cloud instances and NAT gateways while keeping tenant labels',()=>{
 const rows=[{id:'a',tenantId:'a',tenantName:'原租户',name:'A实例',ip:'10.1.0.1',listeners:[],groups:[]},
  {id:'b',tenantId:'b',tenantName:'新租户',name:'B实例',ip:'10.2.0.1',listeners:[],groups:[]}];
 const html=render('instances',rows,{tenant:'b'});
 assert.match(html,/B实例/);assert.match(html,/新租户/);assert.doesNotMatch(html,/A实例/);
 const gateways=[{id:'a',tenantId:'a',tenantName:'原租户',name:'A网关',entries:[]},
  {id:'b',tenantId:'b',tenantName:'新租户',name:'B网关',entries:[]}];
 const natHtml=render('nat',rows,{tenant:'b',nat:{available:true,collectedAt:'today',gateways}});
 assert.match(natHtml,/B网关/);assert.doesNotMatch(natHtml,/A网关/);
});

if(process.env.CLB_PAGE_FIXTURE){
 test('current full snapshot renders every instance without requiring DNS',()=>{
  const data=JSON.parse(fs.readFileSync(process.env.CLB_PAGE_FIXTURE));
  const html=render('instances',data.instances);
  for(const instance of data.instances) assert.ok(html.includes(instance.id),instance.id);
  console.log(`Verified all ${data.instances.length} CLB instance IDs in rendered page`);
 });
}

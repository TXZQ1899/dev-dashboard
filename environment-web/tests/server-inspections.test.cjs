const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const ts=require('typescript');
const React=require('react');
const {renderToStaticMarkup}=require('react-dom/server');
function evaluate(file,requireModule=()=>{}) {
 const sandbox={exports:{},require:requireModule};
 vm.runInNewContext(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText,sandbox);
 return sandbox.exports;
}
const lib=evaluate('lib/server-processes.ts');
const now=Date.parse('2026-09-18T12:00:00Z');
function render(assets,options={}) {
 let index=0;
 const state=[options.login||'can_login',options.query||'',options.type||'全部进程',options.duration||'all',options.page||1,options.size||25,options.selected||'',now];
 const ui=evaluate('components/server-inspections.tsx',id=>{
  if(id==='react')return {...React,useEffect:()=>{},useMemo:f=>f(),useState:initial=>[index<state.length?state[index++]:typeof initial==='function'?initial():initial,()=>{}]};
  if(id==='react/jsx-runtime')return require(id);
  if(id==='@/lib/jumpserver')return {jumpserver:{assets,collectedAt:'2026-09-17T12:00:00Z'}};
  if(id==='@/lib/server-processes')return lib;
  if(id==='@/components/ui/input')return {Input:()=>null};
  throw new Error(id);
 });
 return renderToStaticMarkup(React.createElement(ui.ServerInspections));
}
const process={pid:2,ppid:1,user:'app',name:'java',kind:'Java',elapsedSeconds:365*86400,startedAt:'2025-09-17T12:00:00Z',command:'java -jar /srv/app-api.jar --port=8080'};
const inspection={checkedAt:'2026-09-17T12:00:00Z',loginStatus:'can_login',reason:'',processStatus:'partial',nginxStatus:'partial',warnings:['权限不足'],processes:[process],nginxRoutes:[{domains:['example.com'],listen:['443 ssl'],uri:'/api',directive:'proxy_pass',target:'http://up',upstream:'up',backends:[{host:'10.0.0.1',port:'8080',resolution:'ip'}]}]};
const asset={id:'a',ip:'10.0.0.2',hostname:'server',inspection};
test('old snapshots remain unchecked and never become failed logins',()=>{
 const html=render([{id:'a',ip:'10.0.0.1',hostname:'legacy'}]);
 assert.match(html,/未检查（1 台）/);assert.match(html,/不能登录（0 台）/);assert.match(html,/没有服务器采集结果/);
});
test('command search locates the process and its IP across all types',()=>{
 const html=render([asset],{query:'app-api.jar'});
 for(const text of ['10.0.0.2','app-api.jar','366天','进程ID','详细命令','非 CPU 时间','未超过2星期'])assert.ok(html.includes(text),text);
 assert.match(html,/匹配 1 条进程/);
 assert.match(render([asset],{query:'missing.jar'}),/匹配 0 条进程/);
});
test('failed tab preserves reasons and excludes running processes',()=>{
 const html=render([asset,{...asset,id:'b',ip:'10.0.0.3',inspection:{...inspection,loginStatus:'cannot_login',reason:'连接超时',processes:[]}}],{login:'cannot_login'});
 assert.match(html,/连接超时/);assert.match(html,/10.0.0.3/);assert.ok(!html.includes('app-api.jar'));
});
test('server details retain nginx route and raw file access',()=>{
 const html=render([{...asset,inspection:{...inspection,configurationCount:2}}],{selected:'a'});
 for(const text of ['权限不足','example.com','/api','8080','查看原始配置文件'])assert.ok(html.includes(text),text);
});
test('process list paginates and clamps stale pages',()=>{
 const processes=Array.from({length:26},(_,i)=>({...process,pid:i+1,command:`java -jar app-${i+1}.jar`}));
 const html=render([{...asset,inspection:{...inspection,processes}}],{page:9});
 assert.match(html,/第 2 \/ 2 页/);assert.match(html,/app-26.jar/);assert.ok(!html.includes('app-1.jar'));
});
test('classifies tomcat, jar, kafka and node without classifying argument substrings',()=>{
 const classify=(command,name='java',kind='Java')=>lib.processType({...process,command,name,kind});
 assert.equal(classify('java -Dcatalina.base=/opt/tomcat org.apache.catalina.startup.Bootstrap'),'Tomcat');
 assert.equal(classify('java -jar /app/tomcat-api.jar'),'Java Jar');
 assert.equal(classify('java -cp libs/* kafka.Kafka config/server.properties'),'Kafka');
 assert.equal(classify('java -cp libs/* com.example.Main'),'其他 Java');
 assert.equal(classify('/usr/bin/node /srv/nginx-helper.js','node','Nginx'),'Node');
 assert.equal(classify('nginx: master process /usr/sbin/nginx','nginx','Nginx'),'Nginx');
 assert.equal(lib.jarName({...process,command:`java -jar '/srv/application api.jar'`}), 'application api.jar');
});
test('duration bounds are cumulative for first four and exclusive lower/inclusive upper thereafter',()=>{
 const d=86400;
 for(const [key,days] of [['week',7],['fortnight',14],['month',30],['two_months',60]]) {
  assert.equal(lib.matchesDuration(0,key),true);assert.equal(lib.matchesDuration(days*d,key),true);assert.equal(lib.matchesDuration(days*d+1,key),false);
 }
 for(const [key,low,high] of [['half_year',60,180],['year',180,365]]) {
  assert.equal(lib.matchesDuration(low*d,key),false);assert.equal(lib.matchesDuration(low*d+1,key),true);assert.equal(lib.matchesDuration(high*d,key),true);assert.equal(lib.matchesDuration(high*d+1,key),false);
 }
 assert.equal(lib.matchesDuration(365*d,'over_year'),false);assert.equal(lib.matchesDuration(365*d+1,'over_year'),true);
});
test('elapsed wall time advances from snapshot and does not shrink for a future collection timestamp',()=>{
 assert.equal(lib.runningSeconds(process,inspection.checkedAt,now),366*86400);
 assert.equal(lib.runningSeconds(process,'2027-09-17T12:00:00Z',now),365*86400);
 assert.equal(lib.runningSeconds(process,'invalid',now),365*86400);
});
test('search combines IP, command and type with duration; failed assets never contribute',()=>{
 const rows=lib.processRows([asset,{...asset,id:'b',inspection:{...inspection,loginStatus:'cannot_login'}}],now);
 assert.equal(rows.length,1);
 assert.equal(lib.filterProcesses(rows,'10.0.0.2 APP-API.jar','over_year','Java Jar').length,1);
 assert.equal(lib.filterProcesses(rows,'app-api.jar','week','Java Jar').length,0);
 assert.equal(lib.filterProcesses(rows,'app-api.jar','all','Kafka').length,0);
});

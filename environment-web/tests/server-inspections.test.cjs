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
const overviewLib=evaluate('lib/jumpserver-overview.ts',()=>({isLocalDatacenterIp:()=>false}));
// 组件的时间展示已统一委托给 lib/datetime.ts。
const datetimeLib=evaluate('lib/datetime.ts');
const now=Date.parse('2026-09-18T12:00:00Z');
function render(assets,options={}) {
 let index=0;
 // ServerInspections 的状态顺序：login, query, duration, env, page, size, sort, expanded, selected, procSort, procQuery, inspect, pwTarget, pwBusy, pwError, commandDialog, now；
 // 之后是 ServerDetails：routeQuery, webappQuery, routeIndex, files, filesBusy, filesError；最后是密码弹窗的 password 输入。
 const state=[options.login||'can_login',options.query||'',options.duration||'all',options.env||'all',options.page||1,options.size||25,options.sort||{field:'ip',dir:'asc'},options.expanded||{},options.selected||'',options.procSort||{},options.procQuery||{},options.inspect||{},options.pwTarget||null,options.pwBusy||false,options.pwError||'',options.commandDialog||null,now,
  options.routeQuery||'',options.webappQuery||'',typeof options.routeIndex==='number'?options.routeIndex:-1,options.files||null,false,'',''];
 const ui=evaluate('components/server-inspections.tsx',id=>{
  if(id==='react')return {...React,useEffect:(fn,deps)=>{if(options.runRequestEffect&&deps?.[0]===options.processRequest)fn();},useMemo:f=>f(),useState:initial=>{const i=index++;return [i<state.length?state[i]:typeof initial==='function'?initial():initial,value=>options.changes?.push([i,value])];}};
  if(id==='react/jsx-runtime')return require(id);
  if(id==='@/lib/jumpserver')return {jumpserver:{assets,collectedAt:'2026-09-17T12:00:00Z'},jumpserverLoginUrl:id=>'http://jump/luna/?login_to='+id};
  if(id==='@/lib/server-processes')return lib;
  if(id==='@/lib/jumpserver-overview')return overviewLib;
  if(id==='@/lib/process-comparison')return {
    ipSpecSummary:ip=>ip==='10.0.0.2'?'2C 4G':'',
    ipDeployEnvironments:(ip)=>options.ipEnvs&&options.ipEnvs[ip]?options.ipEnvs[ip]:[],
    appDeployEnvironments:(app)=>options.appEnvs&&options.appEnvs[app.id]?options.appEnvs[app.id]:[],
    // 与 lib/process-comparison.ts 的 tomcatInstanceName 同口径的最小实现。
    tomcatInstanceName:options.tomcatInstanceName||(cmd=>{const m=cmd.match(/(?:apache-)?tomcat[_\-]([^/\s]+)/i);return m?m[1]:undefined;}),
    tomcatProcessApp:options.tomcatProcessApp||(()=>undefined),
    jarBusinessName:options.jarBusinessName||(cmd=>{const t=(cmd.match(/"(?:\\.|[^"\\])*"|'[^']*'|[^\s]+/g)||[]).map(s=>s.replace(/^(['"])(.*)\1$/,'$2'));const i=t.indexOf('-jar');return i>=0&&t[i+1]?t[i+1].split('/').pop().replace(/\.(jar|war)$/i,''):undefined;}),
    jarProcessApp:options.jarProcessApp||(()=>undefined),
  };
  if(id==='@/lib/inventory')return {envs:['TEST','SIMULATION','PRODUCT'],shortLabels:{TEST:'测试',SIMULATION:'仿真',PRODUCT:'生产'},labels:{TEST:'测试环境',SIMULATION:'仿真环境',PRODUCT:'生产环境'}};
  if(id==='@/lib/datetime')return datetimeLib;
  if(id==='@/components/ui/input')return {Input:()=>null};
  if(id==='@/components/ui/button')return {Button:(props)=>React.createElement('button',props)};
  if(id==='@/components/application-check-link')return {ApplicationCheckLink:(props)=>React.createElement('a',{className:'check-link'},`检测上下游:${props.asset}:${props.pid}`)};
  if(id==='@/components/ip-meta-cell')return {IpMetaCell:(props)=>React.createElement('span',{className:'ip-meta-cell-stub'},'IPMETA ',React.createElement('code',null,props.ip),' ',React.createElement('small',null,props.hostName||props.ip),React.createElement('span',{className:'ip-meta-chips'},props.leadingChip,React.createElement('span',null,'环境标签'),React.createElement('span',null,'机房标签')))};
  if(id==='lucide-react')return {Download:()=>null,ChevronRight:props=>React.createElement('svg',props),X:props=>React.createElement('svg',props)};
  throw new Error(id);
 });
 return renderToStaticMarkup(React.createElement(ui.ServerInspections,{processRequest:options.processRequest}));
}
const process={pid:2,ppid:1,user:'app',name:'java',kind:'Java',elapsedSeconds:365*86400,startedAt:'2025-09-17T12:00:00Z',command:'java -jar /srv/app-api.jar --port=8080'};
const inspection={checkedAt:'2026-09-17T12:00:00Z',loginStatus:'can_login',reason:'',processStatus:'partial',nginxStatus:'partial',warnings:['权限不足'],processes:[process],nginxRoutes:[{domains:['example.com'],listen:['443 ssl'],uri:'/api',directive:'proxy_pass',target:'http://up',upstream:'up',backends:[{host:'10.0.0.1',port:'8080',resolution:'ip'}]}]};
const asset={id:'a',ip:'10.0.0.2',hostname:'server',inspection};
test('总览跳转按 IP 搜索并重置筛选、展开匹配资产',()=>{
 const changes=[]; const request={ip:'10.0.0.2'};
 render([asset],{processRequest:request,runRequestEffect:true,changes});
 assert.deepEqual(changes.map(([i,v])=>[i,JSON.parse(JSON.stringify(v))]),[[0,'can_login'],[1,'10.0.0.2'],[2,'all'],[3,'all'],[4,1],[8,''],[7,{a:{type:'全部进程',page:1}}],[10,{}]]);
});
test('Docker 容器 init、子进程和代理在进程列表中显示证据和容器信息',()=>{
 const container={id:'container1',name:'redis4',image:'redis:5',initPid:100,command:'redis-server --port 6379',ports:[{containerPort:'6379/tcp',hostIp:'0.0.0.0',hostPort:'6379'}]};
 const init={...process,pid:100,ppid:1,name:'redis-server',command:'redis-server'};
 const child={...process,pid:101,ppid:100,name:'worker',command:'worker'};
 const proxy={...process,pid:102,ppid:1,name:'docker-proxy-cu',command:'/usr/bin/docker-proxy-current -proto tcp -host-ip 0.0.0.0 -host-port 6379 -container-ip 172.17.0.2 -container-port 6379',ports:[6379]};
 const a={...asset,inspection:{...inspection,processes:[init,child,proxy],docker:{status:'complete',containers:[container]}}};
 const rows=lib.processRows([a],now);
 assert.equal(rows.find(r=>r.process.pid===100).docker.basis,'init');
 assert.equal(rows.find(r=>r.process.pid===101).docker.basis,'descendant');
 assert.equal(rows.find(r=>r.process.pid===102).docker.basis,'proxy');
 const html=render([a],{expanded:{a:{type:'全部进程',page:1}}});
 for(const text of ['redis4','容器启动命令','docker-proxy-cu'])assert.ok(html.includes(text),text);
 assert.equal((html.match(/title="redis:5">Docker<\/span>/g)||[]).length,3);
 for(const text of ['init PID 匹配','进程树推断','端口映射推断'])assert.ok(!html.includes(text),text);
 assert.equal(lib.filterProcesses(rows,'redis:5','all').length,3);
 assert.equal(lib.matchesProcessQuery(rows[0],'redis4'),true);
});
test('Docker 映射冲突或端口协议不一致时不猜测容器',()=>{
 const c={id:'c1',name:'one',image:'image',initPid:100,command:'app',ports:[{containerPort:'80/tcp',hostIp:'0.0.0.0',hostPort:'8080'}]};
 const proxy={...process,pid:102,ppid:1,name:'docker-proxy-cu',command:'docker-proxy -proto tcp -host-ip 0.0.0.0 -host-port 8080 -container-port 80'};
 const duplicate={...c,id:'c2',name:'two',initPid:101};
 const make=containers=>({...asset,inspection:{...inspection,processes:[proxy],docker:{status:'complete',containers}}});
 assert.equal(lib.processRows([make([c,duplicate])],now)[0].docker,undefined);
 assert.equal(lib.processRows([make([{...c,ports:[{...c.ports[0],containerPort:'80/udp'}]}])],now)[0].docker,undefined);
});
test('old snapshots remain unchecked and never become failed logins',()=>{
 const html=render([{id:'a',ip:'10.0.0.1',hostname:'legacy'}]);
 assert.match(html,/未检查（1 台）/);assert.match(html,/不可登录（0 台）/);assert.match(html,/没有服务器采集结果/);
});
test('command search locates the matching ip and the process appears after expanding',()=>{
 const collapsed=render([asset],{query:'app-api.jar'});
 assert.ok(collapsed.includes('10.0.0.2'));
 assert.ok(!collapsed.includes('匹配 1 条进程，涉及 1 个 IP'));
 assert.ok(!collapsed.includes('app-api.jar'),'进程详细在展开前不展示');
 const html=render([asset],{query:'app-api.jar',expanded:{a:{type:'全部进程',page:1}}});
 for(const text of ['10.0.0.2','app-api.jar','366天','进程ID','详细命令','未超过2星期'])assert.ok(html.includes(text),text);
 assert.ok(!html.includes('运行时间为实际经过时长'));
 assert.ok(!render([asset],{query:'missing.jar'}).includes('10.0.0.2'));
});
test('超长进程命令折叠为预览并提供展开详情弹窗',()=>{
 const longCommand='/bin/java '+Array.from({length:80},(_,i)=>`-Dkey${i}=value${i}`).join(' ');
 const longAsset={...asset,inspection:{...inspection,processes:[{...process,pid:9,command:longCommand}]}};
 const collapsed=render([longAsset],{expanded:{a:{type:'全部进程',page:1}}});
 assert.match(collapsed,/command-preview/);
 assert.match(collapsed,/展开详情/);
 // 短命令不折叠、不显示展开按钮。
 const shortHtml=render([asset],{expanded:{a:{type:'全部进程',page:1}}});
 assert.ok(!shortHtml.includes('展开详情'));
 // 弹窗展示格式化为逐行参数的命令，并提供复制与关闭。
 const dialog=render([longAsset],{commandDialog:longCommand});
 assert.match(dialog,/进程完整命令/);
 assert.match(dialog,/复制/);
 assert.match(dialog,/aria-label="关闭"/);
 assert.match(dialog,/command-dialog-pre/);
 assert.ok(dialog.includes('-Dkey0=value0\n-Dkey1=value1'),'参数应逐行展示');
});
test('failed tab preserves reasons and excludes running processes',()=>{
 const html=render([asset,{...asset,id:'b',ip:'10.0.0.3',inspection:{...inspection,loginStatus:'cannot_login',reason:'连接超时',processes:[]}}],{login:'cannot_login'});
 assert.match(html,/连接超时/);assert.match(html,/10.0.0.3/);assert.ok(!html.includes('app-api.jar'));
 assert.match(html,/不可登录/);assert.match(html,/导出 CSV（1 台）/);
});
test('server details retain nginx route and raw file access',()=>{
 const html=render([{...asset,inspection:{...inspection,configurationCount:2}}],{selected:'a'});
 for(const text of ['权限不足','example.com','/api','8080','查看原始配置文件'])assert.ok(html.includes(text),text);
});
test('nginx route table explains the location uri and shows the static directory',()=>{
 const routes=[{domains:['hotels.folidaymall.com'],listen:['443 ssl'],uri:'^~ /tc-fostay',directive:'static/other',target:'/var/www/webapps/tc-fostay',upstream:'',backends:[]}];
 const html=render([{...asset,inspection:{...inspection,nginxRoutes:routes}}],{selected:'a'});
 for(const text of ['URI 说明','匹配所有以 /tc-fostay 开头的请求','/var/www/webapps/tc-fostay'])assert.ok(html.includes(text),text);
 assert.ok(!html.includes('无代理目标'),'静态目录已解析，不应再显示无代理目标');
});
test('server details list webapps applications with update time',()=>{
 const html=render([{...asset,inspection:{...inspection,webapps:{status:'complete',apps:[{name:'tc-fostay',mtime:'2026-09-30T03:24:05+00:00'},{name:'portal',mtime:null}]}}}],{selected:'a'});
 // 时间统一按北京时间（UTC+8）格式化为 YYYY-MM-DD HH:mm:ss（源值 03:24:05+00:00 → 11:24:05）。
 for(const text of ['Webapps 应用目录（2）','tc-fostay','portal','2026-09-30 11:24:05','—','已忽略 __ 开头'])assert.ok(html.includes(text),text);
});
test('webapps absent directory explains itself without an app table',()=>{
 const html=render([{...asset,inspection:{...inspection,webapps:{status:'absent',apps:[]}}}],{selected:'a'});
 assert.match(html,/Webapps 应用目录（0）/);assert.match(html,/该服务器没有 \/var\/www\/webapps 目录/);
});
test('snapshots without webapps data keep rendering details',()=>{
 const html=render([{...asset,inspection:{...inspection,configurationCount:1}}],{selected:'a'});
 assert.ok(!html.includes('Webapps 应用目录（'));assert.ok(html.includes('与 Webapps 应用目录'));
});
test('collapsed ip rows summarize spec, usage, listening ports and process categories',()=>{
 const nginx={pid:9,ppid:1,user:'root',name:'nginx',kind:'Nginx',elapsedSeconds:100,startedAt:'2026-09-17T11:58:20Z',command:'nginx: master process /usr/sbin/nginx',ports:[80,443]};
 const insp={...inspection,processes:[process,{...process,pid:3,command:'java -jar /srv/other.jar'},nginx],
  appPorts:[{app:'app-api',kind:'Java',port:8080,addresses:['0.0.0.0'],pids:[2]},{app:'nginx',kind:'Nginx',port:443,addresses:['0.0.0.0'],pids:[9]}],
  serverUsage:{cpuPercent:45,memPercent:72,memTotalMB:4096,memUsedMB:2949}};
 const html=render([{...asset,inspection:insp}]);
 for(const text of ['2C 4G','45%','72%','Java Jar 2','Nginx 1','监听端口数'])assert.ok(html.includes(text),text);
 assert.ok(html.includes('title="443, 8080">2<'),'监听端口数取 appPorts 数量并在提示中列出端口');
 assert.ok(!html.includes('详细命令'),'未展开时不展示进程明细');
});
test('ip column renders meta cell with hostname (falls back to IP when unnamed)',()=>{
 const html=render([asset]);
 assert.ok(html.includes('ip-meta-cell-stub'),'IP 列接入名称/环境/归属元信息单元');
 assert.ok(html.includes('<code>10.0.0.2</code>'));
 assert.ok(html.includes('>server</small>'),'展示 JumpServer 主机名');
 const noName=render([{...asset,hostname:''}]);
 assert.ok(noName.includes('>10.0.0.2</small>'),'主机名为空时元信息单元回退为 IP');
});
test('summary row has a reinspect button that shows busy state without expanding',()=>{
 const html=render([asset],{inspect:{a:{busy:true,message:'',error:''}}});
 assert.ok(html.includes('>采集中…</button>'),'busy 时按钮文案为"采集中…"');
 assert.ok(!html.includes('ip-detail-box'),'按钮点击不会触发展开');
 const ok=render([asset],{inspect:{a:{busy:false,message:'采集任务已启动',error:''}}});
 assert.ok(ok.includes('单独采集'),'非 busy 时按钮文案为"单独采集"');
 assert.ok(ok.includes('采集任务已启动'),'成功消息显示在按钮下方');
 const err=render([asset],{inspect:{a:{busy:false,message:'',error:'连接超时'}}});
 assert.ok(err.includes('连接超时'),'错误消息显示在按钮下方');
});
test('服务器行仅为明确的普通账号权限显示标识',()=>{
 for(const sudoStatus of ['denied','password_error','sudo_password_error','password_required']) {
  const html=render([{...asset,inspection:{...inspection,sudoStatus,processStatus:'complete'}}]);
  assert.ok(html.includes('src="/permissions-icon.png"')&&html.includes('title="仅普通权限"')&&html.includes('alt="仅普通权限"'),sudoStatus);
  assert.match(html,/ip-meta-chips[^]*permissions-icon\.png[^]*环境标签[^]*机房标签/);
  assert.match(html,/border-t align-middle ip-summary-row/);
  assert.ok(!html.includes('台可登录服务器提权失败，进程采集不完整'));
 }
 for(const sudoStatus of ['root','passwordless','password','timeout',undefined]) {
  assert.ok(!render([{...asset,inspection:{...inspection,sudoStatus}}]).includes('/permissions-icon.png'),String(sudoStatus));
 }
});
test('elevation-failed server shows the root password dialog for single inspect',()=>{
 const failed={...inspection,warnings:['提权失败，root 密码登录错误'],sudoStatus:'password_error'};
 const html=render([{...asset,inspection:failed}],{pwTarget:{id:'a',ip:'10.0.0.2',hostname:'server',inspection:failed}});
 for(const text of ['确认并采集','直接采集','取消','新的 root 密码','更新密码映射表'])assert.ok(html.includes(text),text);
});
test('expanded ip shows a tab per present process category and filters rows by tab',()=>{
 const nginx={pid:9,ppid:1,user:'root',name:'nginx',kind:'Nginx',elapsedSeconds:100,startedAt:'2026-09-17T11:58:20Z',command:'nginx: master process /usr/sbin/nginx'};
 const insp={...inspection,processes:[process,nginx]};
 const all=render([{...asset,inspection:insp}],{expanded:{a:{type:'全部进程',page:1}}});
 for(const text of ['全部进程（2）','普通 Java Jar（1）','Nginx（1）'])assert.ok(all.includes(text),text);
 assert.ok(!all.includes('Kafka（'),'没有进程的类别不生成标签');
 assert.ok(all.includes('app-api.jar'));assert.ok(all.includes('nginx: master process'));
 const nginxOnly=render([{...asset,inspection:insp}],{expanded:{a:{type:'Nginx',page:1}}});
 assert.ok(nginxOnly.includes('nginx: master process'));assert.ok(!nginxOnly.includes('app-api.jar'));
});
test('expanded tomcat row shows instance business name without app link when unmatched',()=>{
 const tomcat={...process,pid:23251,user:'root',name:'java',kind:'Java',command:'/opt/jdk1.8.0_101/jre/bin/java -Djava.util.logging.config.file=/opt/apache-tomcat_dolphin-webhook/conf/logging.properties -Dcatalina.base=/opt/apache-tomcat_dolphin-webhook -Dcatalina.home=/opt/apache-tomcat_dolphin-webhook org.apache.catalina.startup.Bootstrap start'};
 const html=render([{...asset,inspection:{...inspection,processes:[tomcat]}}],{expanded:{a:{type:'Tomcat',page:1}}});
 assert.ok(html.includes('dolphin-webhook'),'进程名取 catalina 实例目录业务名');
 assert.ok(html.includes('Tomcat · root'),'保留进程类别与用户副信息');
 assert.ok(!html.includes('/applications?appId='),'未关联到 DevOps 应用时不渲染跳转链接');
 assert.ok(!html.includes('（1441）'),'未关联时不追加应用ID');
});
test('expanded tomcat row renders name with app id and links to applications page when matched',()=>{
 const tomcat={...process,pid:23251,user:'root',name:'java',kind:'Java',command:'/opt/jdk1.8.0_101/jre/bin/java -Dcatalina.base=/opt/apache-tomcat_dolphin-webhook -Dcatalina.home=/opt/apache-tomcat_dolphin-webhook org.apache.catalina.startup.Bootstrap start'};
 const html=render([{...asset,inspection:{...inspection,processes:[tomcat]}}],{
   expanded:{a:{type:'Tomcat',page:1}},
   tomcatProcessApp:(ip,p)=>ip==='10.0.0.2'&&p.pid===23251?{id:'1441',name:'dolphin-webhook'}:undefined,
 });
 assert.ok(html.includes('href="/applications?appId=1441#app-1441"'),'进程名链接到应用列表对应应用');
 assert.ok(html.includes('dolphin-webhook（1441）'),'展示业务名与 DevOps 应用ID');
});
test('expanded java jar row shows jar name without suffix when unmatched',()=>{
 const html=render([asset],{expanded:{a:{type:'Java Jar',page:1}}});
 assert.ok(html.includes('app-api<small'),'进程名列显示去 .jar 后缀的 jar 文件名');
 assert.ok(!html.includes('app-api.jar<small'),'进程名不再带 .jar 后缀（command 列仍保留完整命令）');
 assert.ok(!html.includes('/applications?appId='),'未关联到 DevOps 应用时不渲染跳转链接');
 assert.ok(!html.includes('（1441）'),'未关联时不追加应用ID');
});
test('expanded java jar row renders name with app id and links when matched',()=>{
 const html=render([asset],{
   expanded:{a:{type:'Java Jar',page:1}},
   jarProcessApp:(ip,p)=>ip==='10.0.0.2'&&p.pid===2?{id:'1441',name:'app-api'}:undefined,
 });
 assert.ok(html.includes('href="/applications?appId=1441#app-1441"'),'进程名链接到应用列表对应应用');
 assert.ok(html.includes('app-api（1441）'),'展示 jar 业务名与 DevOps 应用ID');
});
test('expanded process list paginates per server and clamps stale pages',()=>{
 const processes=Array.from({length:26},(_,i)=>({...process,pid:i+1,command:`java -jar app-${i+1}.jar`}));
 const html=render([{...asset,inspection:{...inspection,processes}}],{expanded:{a:{type:'全部进程',page:9}}});
 assert.match(html,/第 3 \/ 3 页/);assert.match(html,/app-26.jar/);assert.ok(!html.includes('app-1.jar'));
});
test('expanded process search filters rows by pid, process name and listening port',()=>{
 const p1={...process,pid:1011,command:'java -jar /srv/app-api.jar'};
 const p2={...process,pid:2022,name:'python3',kind:'Python',command:'python3 /srv/worker.py',ports:[9000]};
 const p3={...process,pid:3033,name:'faiss-server',command:'faiss-server --serve',ports:[7070]};
 const insp={...inspection,processes:[p1,p2,p3]};
 const open={a:{type:'全部进程',page:1}};
 const all=render([{...asset,inspection:insp}],{expanded:open});
 assert.ok(all.includes('1011')&&all.includes('2022')&&all.includes('3033'),'no search shows every row');
 const byPid=render([{...asset,inspection:insp}],{expanded:open,procQuery:{a:'1011'}});
 assert.ok(byPid.includes('>1011<')&&!byPid.includes('>3033<'),'pid search keeps only the matching row');
 assert.ok(byPid.includes('匹配 1 条进程'),'shows the matched count while searching');
 const byPort=render([{...asset,inspection:insp}],{expanded:open,procQuery:{a:'9000'}});
 assert.ok(byPort.includes('>9000<')&&!byPort.includes('>7070<'),'port search keeps only the matching row');
 const byName=render([{...asset,inspection:insp}],{expanded:open,procQuery:{a:'python3'}});
 assert.ok(byName.includes('>2022<')&&!byName.includes('>3033<'),'process name search keeps only the matching row');
 const none=render([{...asset,inspection:insp}],{expanded:open,procQuery:{a:'nope-xyz'}});
 assert.match(none,/没有匹配“nope-xyz”的进程/);
});
test('ip summary rows paginate by server',()=>{
 const assets=Array.from({length:26},(_,i)=>({id:'a'+i,ip:`10.0.0.${i+1}`,hostname:'h'+(i+1),inspection:{...inspection,processes:[{...process,pid:2,command:`java -jar app-${i+1}.jar`}]}}));
 const html=render(assets,{page:2});
 assert.match(html,/第 2 \/ 2 页/);assert.ok(html.includes('10.0.0.26'));assert.ok(!html.includes('>10.0.0.1<'));
});
test('sorting by cpu desc puts high-cpu servers first and shows arrow',()=>{
 const base={...inspection,processStatus:'complete',warnings:[]};
 const a1={...asset,id:'a1',ip:'10.0.0.1',inspection:{...base,serverUsage:{cpuPercent:30,memPercent:50,memTotalMB:2048,memUsedMB:1024}}};
 const a2={...asset,id:'a2',ip:'10.0.0.2',inspection:{...base,serverUsage:{cpuPercent:85,memPercent:60,memTotalMB:4096,memUsedMB:2458}}};
 const a3={...asset,id:'a3',ip:'10.0.0.3',inspection:{...base,serverUsage:{cpuPercent:50,memPercent:40,memTotalMB:8192,memUsedMB:3277}}};
 const html=render([a1,a2,a3],{sort:{field:'cpu',dir:'desc'}});
 const i1=html.indexOf('<code>10.0.0.1</code>');const i2=html.indexOf('<code>10.0.0.2</code>');const i3=html.indexOf('<code>10.0.0.3</code>');
 assert.ok(i2<i3&&i3<i1,'CPU desc: 85% before 50% before 30%');
 assert.ok(html.includes('CPU% \u2193'),'header shows down arrow for active desc sort');
});
test('sorting by spec asc orders by cpu cores then memory',()=>{
 const base={...inspection,processStatus:'complete',warnings:[]};
 const a1={...asset,id:'a1',ip:'10.0.0.1',inspection:{...base,serverUsage:{cpuPercent:10,memPercent:10,memTotalMB:2048,memUsedMB:200}}};
 const a2={...asset,id:'a2',ip:'10.0.0.2',inspection:{...base,serverUsage:{cpuPercent:10,memPercent:10,memTotalMB:2048,memUsedMB:200}}};
 const html=render([a2,a1],{sort:{field:'spec',dir:'asc'}});
 const i1=html.indexOf('<code>10.0.0.1</code>');const i2=html.indexOf('<code>10.0.0.2</code>');
 assert.ok(i1<i2,'both 2C 4G, stable order by IP asc');
});
test('sorting by mem desc puts high-memory servers first',()=>{
 const base={...inspection,processStatus:'complete',warnings:[]};
 const a1={...asset,id:'a1',ip:'10.0.0.1',inspection:{...base,serverUsage:{cpuPercent:10,memPercent:30,memTotalMB:2048,memUsedMB:600}}};
 const a2={...asset,id:'a2',ip:'10.0.0.2',inspection:{...base,serverUsage:{cpuPercent:10,memPercent:90,memTotalMB:4096,memUsedMB:3686}}};
 const html=render([a1,a2],{sort:{field:'mem',dir:'desc'}});
 const i1=html.indexOf('<code>10.0.0.1</code>');const i2=html.indexOf('<code>10.0.0.2</code>');
 assert.ok(i2<i1,'mem desc: 90% before 30%');
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
test('classifies common middleware by its process entry point before generic runtimes',()=>{
 const classify=(command,name='java')=>lib.processType({...process,command,name});
 const cases=[
  ['java -cp /usr/share/elasticsearch/lib/* org.elasticsearch.bootstrap.Elasticsearch','Elasticsearch'],
  ['/opt/elasticsearch/jdk/bin/java -Des.path.home=/opt/elasticsearch','Elasticsearch'],
  ['java -m org.elasticsearch.server/org.elasticsearch.bootstrap.Elasticsearch','Elasticsearch'],
  ['java -cp /opt/zookeeper/* org.apache.zookeeper.server.quorum.QuorumPeerMain /etc/zoo.cfg','ZooKeeper'],
  ['java -jar /home/nacos/target/nacos-server.jar','Nacos'],
  ['java -cp /opt/canal/lib/* com.alibaba.otter.canal.deployer.CanalLauncher','Canal'],
  ['java -cp libs/* org.apache.rocketmq.broker.BrokerStartup','RocketMQ'],
  ['java -cp libs/* org.apache.activemq.console.Main start','ActiveMQ'],
 ];
 for(const [command,type] of cases) assert.equal(classify(command),type,command);
 for(const [command,name,type] of [
  ['/usr/lib/erlang/erts/bin/beam.smp -W w -s rabbit boot','beam.smp','RabbitMQ'],
  ['nginx: master process /usr/local/openresty/nginx/sbin/nginx -p /usr/local/kong/ -c nginx.conf','nginx','Kong'],
  ['/usr/bin/mongod --config /etc/mongod.conf','mongod','MongoDB'],
 ]) assert.equal(classify(command,name),type,command);
 assert.equal(classify('java -jar /app/orders.jar --nacos-server=localhost --elasticsearch.host=localhost'),'Java Jar');
 assert.equal(classify('java -Des.path.home=/opt/elasticsearch -jar /app/orders.jar'),'Java Jar');
 assert.equal(classify('java -cp /lib/elasticsearch-client.jar com.example.Main'),'其他 Java');
 assert.equal(classify('java -cp /lib/zookeeper.jar com.example.Main'),'其他 Java');
 assert.equal(classify('node /srv/rabbitmq-consumer.js','node'),'Node');
 assert.equal(classify('nginx: master process /usr/sbin/nginx -c /etc/kong/nginx.conf','nginx'),'Nginx');
});
test('Docker process classification uses the observed process rather than the image name',()=>{
 const zk={...process,pid:100,name:'java',command:'java org.apache.zookeeper.server.quorum.QuorumPeerMain /conf/zoo.cfg'};
 const container={id:'c',name:'service',image:'unrelated:latest',initPid:100,command:'entrypoint',ports:[]};
 const rows=lib.processRows([{...asset,inspection:{...inspection,processes:[zk],docker:{status:'complete',containers:[container]}}}],now);
 assert.equal(rows[0].type,'ZooKeeper');
 assert.equal(rows[0].docker.basis,'init');
});
test('Docker init and uniquely mapped proxies use container evidence while children retain their own type',()=>{
 const init={...process,pid:7374,ppid:1,name:'entrypoint',command:'/entrypoint.sh'};
 const child={...init,pid:7375,ppid:7374,name:'helper',command:'/helper'};
 const proxy={...init,pid:7376,name:'docker-proxy',command:'docker-proxy -proto tcp -host-ip 0.0.0.0 -host-port 5672 -container-port 5672'};
 const container={id:'c',name:'rabbitmq',image:'registry.example/rabbitmq:3-management',initPid:7374,command:'/entrypoint.sh',ports:[{containerPort:'5672/tcp',hostIp:'0.0.0.0',hostPort:'5672'}]};
 const a={...asset,inspection:{...inspection,processes:[init,child,proxy],docker:{status:'complete',containers:[container]}}};
 const rows=lib.processRows([a],now);
 assert.equal(rows.find(r=>r.process.pid===7374).type,'RabbitMQ');
 assert.equal(rows.find(r=>r.process.pid===7374).typeBasis,'container-image');
 assert.equal(rows.find(r=>r.process.pid===7375).type,'其他进程');
 assert.equal(rows.find(r=>r.process.pid===7376).type,'RabbitMQ');
 assert.equal(rows.find(r=>r.process.pid===7376).typeBasis,'container-image');
 const html=render([a],{expanded:{a:{type:'RabbitMQ',page:1}}});
 assert.ok(html.includes('RabbitMQ（2）'));
 assert.ok(html.includes('容器推断'));
 assert.ok(html.includes('根据容器镜像推断'));
 assert.ok(html.includes('Docker 端口代理 · 容器 RabbitMQ'));
 const cmdRows=lib.processRows([{...a,inspection:{...a.inspection,docker:{status:'complete',containers:[{...container,image:'unknown',command:'rabbitmq-server'}]}}}],now);
 assert.equal(cmdRows[0].type,'RabbitMQ');
 assert.equal(cmdRows[0].typeBasis,'container-command');
 const unrelated=lib.processRows([{...a,inspection:{...a.inspection,docker:{status:'complete',containers:[{...container,image:'rabbitmq-client',command:'/entrypoint.sh'}]}}}],now);
 assert.equal(unrelated[0].type,'其他进程');
});
test('the reported RabbitMQ proxy is categorized using its uniquely mapped container command',()=>{
 const proxy={...process,pid:7374,ppid:1060,name:'docker-proxy-cu',command:'/usr/libexec/docker/docker-proxy-current -proto tcp -host-ip 0.0.0.0 -host-port 61614 -container-ip 172.17.0.2 -container-port 61614'};
 const container={id:'c',name:'rabbitmq',image:'private/custom:1',initPid:7494,command:'docker-entrypoint.sh rabbitmq-server',ports:[{containerPort:'61614/tcp',hostIp:'0.0.0.0',hostPort:'61614'}]};
 const rows=lib.processRows([{...asset,inspection:{...inspection,processes:[proxy],docker:{status:'complete',containers:[container]}}}],now);
 assert.equal(rows[0].docker.basis,'proxy');
 assert.equal(rows[0].type,'RabbitMQ');
 assert.equal(rows[0].typeBasis,'container-command');
});
test('middleware processes are recognized for upstream/downstream detection',()=>{
 const p=(over)=>lib.isMiddlewareProcess({...process,...over});
 assert.equal(p({command:'/usr/sbin/mysqld --defaults-file=/etc/my.cnf',name:'mysqld',kind:'MySQL'}),true,'MySQL');
 assert.equal(p({command:'/usr/bin/redis-server 127.0.0.1:6379',name:'redis-server',kind:'Redis'}),true,'Redis');
 assert.equal(p({command:'/usr/lib/postgresql/12/bin/postgres -D /var/lib/pgsql',name:'postgres',kind:'PostgreSQL'}),true,'PostgreSQL');
 assert.equal(p({command:'java -cp libs/* kafka.Kafka config/server.properties'}),true,'Kafka');
 assert.equal(p({command:'/usr/bin/mongod --config /etc/mongod.conf',name:'mongod',kind:'未分类'}),true,'MongoDB');
 assert.equal(p({command:'java -cp libs/elasticsearch org.elasticsearch.bootstrap.Elasticsearch'}),true,'Elasticsearch');
 assert.equal(p({command:'/usr/lib/rabbitmq/bin/rabbitmq-server',name:'rabbitmq-server',kind:'未分类'}),true,'原生 RabbitMQ');
 assert.equal(p({command:'node /srv/app.js',name:'node',kind:'Node'}),false,'Node 非中间件');
 assert.equal(p({command:'nginx: master process /usr/sbin/nginx',name:'nginx',kind:'Nginx'}),false,'Nginx 已另有入口');
 assert.equal(p({command:'[kworker/0:1]',name:'kworker/0:1',system:true}),false,'系统进程');
});
test('expanded rows expose checks for listening processes and preserve existing Java and middleware entries',()=>{
 const mysql={pid:5,ppid:1,user:'mysql',name:'mysqld',kind:'MySQL',elapsedSeconds:86400,startedAt:'2026-09-17T12:00:00Z',command:'/usr/sbin/mysqld --defaults-file=/etc/my.cnf'};
 const nodeProc={...process,pid:6,name:'node',kind:'Node',command:'node /srv/app.js',ports:[3000]};
 const pythonProc={...process,pid:7,name:'python3',kind:'Python',command:'python3 /srv/api.py',ports:[8000]};
 const otherProc={...process,pid:8,name:'custom-server',kind:'未分类',command:'/srv/custom-server',ports:[9000]};
 const idleProc={...nodeProc,pid:9,ports:[]};
 const legacyProc={...process,pid:10,name:'custom-server',kind:'未分类',command:'/srv/custom-server --port=9000'};
 const html=render([{...asset,inspection:{...inspection,processes:[process,mysql,nodeProc,pythonProc,otherProc,idleProc,legacyProc]}}],{expanded:{a:{type:'全部进程',page:1}}});
 for(const pid of [2,5,6,7,8])assert.ok(html.includes(`检测上下游:a:${pid}`),`PID ${pid} 显示检测入口`);
 for(const pid of [9,10])assert.ok(!html.includes(`检测上下游:a:${pid}`),`PID ${pid} 无监听证据，不新增检测入口`);
});
test('listening ports show in summary count and expanded rows, with an em dash when none',()=>{
 const open={a:{type:'全部进程',page:1}};
 const html=render([{...asset,inspection:{...inspection,processes:[{...process,ports:[8080,8081]}]}}],{expanded:open});
 assert.match(html,/监听端口/);assert.ok(html.includes('8080, 8081'));
 const none=render([{...asset,inspection:{...inspection,processes:[{...process,command:'java -jar /srv/app-api.jar'}]}}],{expanded:open});
 assert.match(none,/监听端口/);assert.ok(!none.includes('8080'));
});
test('unprivileged RabbitMQ ports appear as inferred and remain searchable',()=>{
 const inferredPorts=[5672,15672,25672].map(port=>({port,address:'*',protocol:'tcp',confidence:'INFERRED',basis:'socket UID 997；RabbitMQ 主进程角色'}));
 const proc={...process,pid:25409,name:'beam.smp',kind:'未分类',user:'rabbitmq',command:'beam.smp -s rabbit boot',inferredPorts};
 const appPorts=inferredPorts.map(p=>({app:'未知进程',kind:'未知',port:p.port,addresses:['*'],pids:[],candidatePids:[25409],ownershipConfidence:'INFERRED',basis:p.basis}));
 const host={...asset,inspection:{...inspection,portStatus:'complete',processes:[proc],appPorts}};
 const html=render([host],{expanded:{a:{type:'全部进程',page:1}},procQuery:{a:'5672'}});
 for(const text of ['25409','服务器 TCP 监听端口（3）','5672, 15672, 25672','（推断）','归属推断','候选 PID 25409'])assert.ok(html.includes(text),text);
 assert.ok(!html.includes('没有匹配“5672”'));
});
test('ambiguous owners stay in host inventory without filling a process port cell',()=>{
 const appPorts=[{app:'未知进程',kind:'未知',port:5672,addresses:['::'],pids:[],candidatePids:[10,20],ownershipConfidence:'AMBIGUOUS'}];
 const html=render([{...asset,inspection:{...inspection,processes:[{...process,command:'java -jar app.jar'}],appPorts}}],{expanded:{a:{type:'全部进程',page:1}}});
 assert.match(html,/归属有歧义/);assert.match(html,/候选 PID 10, 20/);
 assert.match(html,/未确认该进程的监听端口/);
 assert.ok(!html.includes('（推断）'));
});
test('host listening counts deduplicate shared ports and failed queries stay unknown',()=>{
 const appPorts=[{app:'one',kind:'Java',port:8080,addresses:['127.0.0.1'],pids:[10]},{app:'two',kind:'Java',port:8080,addresses:['10.0.0.2'],pids:[20]}];
 const html=render([{...asset,inspection:{...inspection,appPorts}}]);
 assert.ok(html.includes('title="8080">1<'));
 const failed=render([{...asset,inspection:{...inspection,portStatus:'failed',appPorts:[]}}],{expanded:{a:{type:'全部进程',page:1}}});
 assert.match(failed,/TCP 监听端口采集失败，不能据此判断没有监听/);
 assert.ok(!failed.includes('title="">0<'));
});
test('system processes are hidden when CPU gap is small',()=>{
 const sysProc={pid:3,ppid:2,user:'root',name:'kworker/0:1',kind:'未分类',elapsedSeconds:86400,startedAt:'2026-09-17T12:00:00Z',command:'[kworker/0:1]',cpuPercent:3,system:true};
 const appProc={...process,cpuPercent:12};
 const html=render([{...asset,inspection:{...inspection,processes:[appProc,sysProc],serverUsage:{cpuPercent:15,memPercent:40,memTotalMB:2048,memUsedMB:819}}}],{expanded:{a:{type:'全部进程',page:1}}});
 assert.ok(!html.includes('系统进程'),'gap 15-12=3 <= 5, no system chip');
 assert.ok(!html.includes('kworker'),'system process not in 全部进程');
 assert.match(html,/全部进程（1）/,'only non-system process counted');
});
test('system processes show when CPU gap exceeds 5%',()=>{
 const sysProc={pid:3,ppid:2,user:'root',name:'kworker/0:1',kind:'未分类',elapsedSeconds:86400,startedAt:'2026-09-17T12:00:00Z',command:'[kworker/0:1]',cpuPercent:80,memPercent:2,system:true};
 const sysProc2={pid:4,ppid:2,user:'root',name:'ksoftirqd/0',kind:'未分类',elapsedSeconds:86400,startedAt:'2026-09-17T12:00:00Z',command:'[ksoftirqd/0]',cpuPercent:5,memPercent:8,system:true};
 const html=render([{...asset,inspection:{...inspection,processes:[process,sysProc,sysProc2],serverUsage:{cpuPercent:100,memPercent:40,memTotalMB:2048,memUsedMB:819}}}],{expanded:{a:{type:'全部进程',page:1}}});
 assert.ok(html.includes('proc-chip-warn'),'warning chip shown for system processes');
 assert.ok(html.includes('系统进程 2'),'system chip with count');
 assert.ok(html.includes('系统进程（2）'),'system tab in expanded view');
 // System tab default sort: memory% desc
 const sysTab=render([{...asset,inspection:{...inspection,processes:[process,sysProc,sysProc2],serverUsage:{cpuPercent:100,memPercent:40,memTotalMB:2048,memUsedMB:819}}}],{expanded:{a:{type:'系统进程',page:1}}});
 const i1=sysTab.indexOf('kworker/0:1');const i2=sysTab.indexOf('ksoftirqd/0');
 assert.ok(i2<i1&&i2>0,'default mem desc: ksoftirqd (8%) before kworker (2%)');
 assert.ok(!sysTab.includes('app-api.jar'),'non-system process not in system tab');
});
test('process detail defaults to memory desc sort on all tabs',()=>{
 const p1={...process,pid:1,cpuPercent:30,memPercent:5};
 const p2={...process,pid:2,cpuPercent:10,memPercent:20};
 const html=render([{...asset,inspection:{...inspection,processes:[p1,p2]}}],{expanded:{a:{type:'全部进程',page:1}}});
 const i1=html.indexOf('app-api.jar');
 // Both processes have same command, so check PID order instead
 // p2 (mem 20%) should come before p1 (mem 5%)
 const idx1=html.indexOf('>1<');const idx2=html.indexOf('>2<');
 // The PID appears in the first column; p2 (higher mem) should appear first
 assert.ok(idx2>=0&&idx1>=0,'both PIDs present');
 // Check the down arrow on 内存%
 assert.ok(html.includes('内存%\u2193'),'mem header shows down arrow for default sort');
 // CPU desc
 const cpuHtml=render([{...asset,inspection:{...inspection,processes:[p1,p2]}}],{expanded:{a:{type:'全部进程',page:1}},procSort:{a:{field:'cpu',dir:'desc'}}});
 assert.ok(cpuHtml.includes('CPU%\u2193'),'CPU header shows down arrow');
});
test('only nginx processes expose the Nginx button that opens server details',()=>{
 const java=render([asset],{expanded:{a:{type:'全部进程',page:1}}});
 assert.ok(!java.includes('>Nginx</button>'));
 const nginxProc={...process,pid:9,name:'nginx',kind:'Nginx',command:'nginx: master process /usr/sbin/nginx -c /etc/nginx/nginx.conf'};
 const html=render([{...asset,inspection:{...inspection,processes:[nginxProc]}}],{expanded:{a:{type:'全部进程',page:1}}});
 assert.ok(html.includes('>Nginx</button>'));
});
test('server details nginx routes can be filtered by domain or URI',()=>{
 const routes=[
  {domains:['example.com'],listen:['443 ssl'],uri:'/api',directive:'proxy_pass',target:'http://up',upstream:'up',backends:[{host:'10.0.0.1',port:'8080',resolution:'ip'}]},
  {domains:['other.test'],listen:['80'],uri:'/',directive:'static/other',target:'',upstream:'',backends:[]}];
 const base={...inspection,nginxRoutes:routes};
 assert.ok(!render([{...asset,inspection:base}],{selected:'a',routeQuery:'example'}).includes('other.test'));
 const byUri=render([{...asset,inspection:base}],{selected:'a',routeQuery:'/API'});
 assert.ok(byUri.includes('example.com'));assert.ok(!byUri.includes('other.test'));
});
test('server details webapps can be searched by name',()=>{
 const web={...inspection,webapps:{status:'complete',apps:[{name:'tc-fostay',mtime:null},{name:'portal',mtime:null}]}};
 assert.ok(!render([{...asset,inspection:web}],{selected:'a',webappQuery:'portal'}).includes('tc-fostay'));
 assert.match(render([{...asset,inspection:web}],{selected:'a',webappQuery:'FOSTAY'}),/tc-fostay/);
});
test('clicking a route expands the raw config file recorded for it',()=>{
 const routes=[{domains:['example.com'],listen:['443 ssl'],uri:'/api',directive:'proxy_pass',target:'http://up',upstream:'up',file:'/etc/nginx/conf.d/app.conf',backends:[{host:'10.0.0.1',port:'8080',resolution:'ip'}]}];
 const files=[{path:'/etc/nginx/conf.d/app.conf',instance:'/usr/sbin/nginx',content:'server { listen 443; }',base64:'',bytes:22}];
 const html=render([{...asset,inspection:{...inspection,nginxRoutes:routes,configurationCount:1}}],{selected:'a',routeIndex:0,files});
 for(const text of ['来源配置文件','/etc/nginx/conf.d/app.conf','server { listen 443; }'])assert.ok(html.includes(text),text);
});
test('snapshots without route source fall back to the only collected config, never guess among many',()=>{
 const routes=[{domains:['example.com'],listen:['443 ssl'],uri:'/api',directive:'proxy_pass',target:'http://up',upstream:'up',backends:[]}];
 const files=[{path:'/etc/nginx/conf.d/only.conf',instance:'folidev',content:'http {}',base64:'',bytes:7}];
 const html=render([{...asset,inspection:{...inspection,nginxRoutes:routes,configurationCount:1}}],{selected:'a',routeIndex:0,files});
 assert.ok(html.includes('/etc/nginx/conf.d/only.conf'));
 const multi=render([{...asset,inspection:{...inspection,nginxRoutes:routes,configurationCount:2}}],{selected:'a',routeIndex:0,files:[...files,{path:'/etc/nginx/conf.d/other.conf',instance:'folidev',content:'',base64:'',bytes:0}]});
 assert.match(multi,/重新采集后可自动关联/);
});
test('clicking a route locates and highlights its location block in the raw config',()=>{
 const content=['server {','  listen 443;','  location /fotour {','    proxy_pass http://up;','  }','  location /other {','    return 404;','  }','}'].join('\n');
 const routes=[{domains:['example.com'],listen:['443 ssl'],uri:'/fotour',directive:'proxy_pass',target:'http://up',upstream:'up',file:'/etc/nginx/conf.d/app.conf',backends:[{host:'10.0.0.1',port:'8080',resolution:'ip'}]}];
 const files=[{path:'/etc/nginx/conf.d/app.conf',instance:'/usr/sbin/nginx',content,base64:'',bytes:content.length}];
 const html=render([{...asset,inspection:{...inspection,nginxRoutes:routes,configurationCount:1}}],{selected:'a',routeIndex:0,files});
 assert.match(html,/已定位到该 URI 的配置块：第 3–5 行/);
 assert.equal((html.match(/nginx-location-line/g)||[]).length,3,'仅 /fotour 块的三行被高亮');
 assert.ok(html.includes('location /other'),'未命中的块仍完整展示');
});
test('findLocationBlock anchors the location block by uri with modifiers and nesting',()=>{
 const content=['server {','  location   ^~   /api {','    proxy_pass http://up;','    if ($arg_x) {','      return 403;','    }','  }','  location /api-extra {','    return 404;','  }','}'].join('\n');
 const block=lib.findLocationBlock(content,'^~ /api');
 assert.ok(block&&block.start===1&&block.end===6,'嵌套 if 的花括号计入块边界');
 assert.equal(lib.findLocationBlock(content,'/api'),null,'更长的路径不应被前缀误匹配');
 assert.equal(lib.findLocationBlock(content,'(TCP/UDP)'),null,'stream 转发没有 location 块');
 const exact=['  location = /login {','    return 200;','  }'].join('\n');
 const eq=lib.findLocationBlock(exact,'= /login');
 assert.ok(eq&&eq.start===0&&eq.end===2,'精确匹配修饰符按原文定位');
 const php=['location ~ \\.php$ {','  fastcgi_pass x;','}'].join('\n');
 const re=lib.findLocationBlock(php,'~ \\.php$');
 assert.ok(re&&re.start===0&&re.end===2,'正则 location 按原文定位');
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
test('environment filter keeps only processes on IPs deployed in the selected environment',()=>{
 const b={...asset,id:'b',ip:'10.0.0.3',hostname:'prod-server',inspection:{...inspection,processes:[{...process,pid:20,command:'java -jar /srv/prod.jar'}]}};
 const opts={ipEnvs:{'10.0.0.2':['TEST'],'10.0.0.3':['PRODUCT']}};
 const all=render([asset,b],opts);
 assert.ok(all.includes('10.0.0.2')&&all.includes('10.0.0.3'));assert.ok(all.includes('全部环境')&&all.includes('>测试<')&&all.includes('>仿真<')&&all.includes('>生产<')&&all.includes('>待识别<'));
 const testOnly=render([asset,b],{...opts,env:'TEST'});
 assert.ok(testOnly.includes('10.0.0.2'));assert.ok(!testOnly.includes('10.0.0.3'));
 const prod=render([asset,b],{...opts,env:'PRODUCT'});
 assert.ok(!prod.includes('10.0.0.2'));assert.ok(prod.includes('10.0.0.3'));
 const simulation=render([asset,b],{...opts,env:'SIMULATION'});
 assert.ok(!simulation.includes('10.0.0.2')&&!simulation.includes('10.0.0.3'));
 // 无 DevOps 部署记录的 IP 落入"待识别"：仅匹配未关联任何环境的机器。
 const c={...asset,id:'c',ip:'10.0.0.4',hostname:'db-server',inspection:{...inspection,processes:[{...process,pid:30,command:'/usr/sbin/mysqld'}]}};
 const unknown=render([asset,b,c],{...opts,env:'unknown'});
 assert.ok(unknown.includes('10.0.0.4'));assert.ok(!unknown.includes('10.0.0.2'));assert.ok(!unknown.includes('10.0.0.3'));
});
test('multi-env host appends per-process environment label; unmatched flags 待识别',()=>{
 // IP 同时有 TEST 与 SIMULATION 部署记录，展开其 Java Jar 进程。
 const multi={...asset,ip:'10.179.1.224',hostname:'pub-server',inspection:{...inspection,processes:[
   {...process,pid:2,command:'java -jar /srv/phoenix-mappingtool.jar --server.port=37863'},
   {...process,pid:3,command:'/usr/sbin/mysqld'},
 ]}};
 const opts={
   expanded:{a:{type:'全部进程',page:1}},
   ipEnvs:{'10.179.1.224':['TEST','SIMULATION']},
   jarProcessApp:(ip,p)=>ip==='10.179.1.224'&&p.pid===2?{id:'1656',name:'phoenix-mappingtool'}:undefined,
   appEnvs:{'1656':['SIMULATION']},
 };
 const html=render([multi],opts);
 assert.match(html,/phoenix-mappingtool（1656）/);
 assert.ok(html.includes('仿真环境'),'关联应用在该 IP 的环境应标注为仿真环境');
 assert.ok(!html.includes('测试环境'),'该应用不部署于测试环境，不应出现测试标签');
 assert.ok(html.includes('环境待识别'),'MySQL 进程未关联 DevOps 应用，标注环境待识别');
});
test('nginx worker processes are grouped into one row with a worker count',()=>{
 const master={pid:1,ppid:0,user:'root',name:'nginx',kind:'Nginx',elapsedSeconds:100,startedAt:'2026-09-17T11:58:20Z',command:'nginx: master process /usr/sbin/nginx -c /etc/nginx/nginx.conf'};
 const w1={pid:10,ppid:1,user:'nginx',name:'nginx',kind:'Nginx',elapsedSeconds:90,startedAt:'2026-09-17T11:58:30Z',command:'nginx: worker process',ports:[80,443]};
 const w2={pid:11,ppid:1,user:'nginx',name:'nginx',kind:'Nginx',elapsedSeconds:88,startedAt:'2026-09-17T11:58:32Z',command:'nginx: worker process',ports:[443]};
 const w3={pid:12,ppid:1,user:'nginx',name:'nginx',kind:'Nginx',elapsedSeconds:87,startedAt:'2026-09-17T11:58:33Z',command:'nginx: worker process'};
 const insp={...inspection,processes:[master,w1,w2,w3]};
 const rows=lib.processRows([{...asset,inspection:insp}],now);
 const workers=rows.filter(r=>r.workerCount);
 assert.equal(workers.length,1);
 assert.equal(workers[0].workerCount,3);
 assert.equal(workers[0].process.pid,10);
 assert.equal(workers[0].process.ports?.length,2);
 assert.equal(workers[0].process.ports?.[0],80);
 assert.equal(workers[0].process.ports?.[1],443);
 assert.equal(rows.filter(r=>r.process.command.includes('master process')).length,1);
});
test('nginx check links use actual listening PIDs including the grouped worker row',()=>{
 const master={...process,pid:1,name:'nginx',kind:'Nginx',command:'nginx: master process /usr/sbin/nginx',ports:[80,443]};
 const idleWorker={...master,pid:10,ppid:1,command:'nginx: worker process',ports:[]};
 const worker={...idleWorker,pid:11,ports:[80]};
 const laterWorker={...idleWorker,pid:12,ports:[443]};
 const open={expanded:{a:{type:'全部进程',page:1}}};
 const html=render([{...asset,inspection:{...inspection,processes:[master,laterWorker,idleWorker,worker]}}],open);
 assert.ok(html.includes('检测上下游:a:1'),'Nginx master 有监听时提供入口');
 assert.ok(html.includes('检测上下游:a:11'),'合并行使用实际监听的最小 worker PID');
 assert.ok(!html.includes('检测上下游:a:10'),'不把合并端口当作空闲代表 PID 的监听证据');
 const none=render([{...asset,inspection:{...inspection,processes:[idleWorker]}}],open);
 assert.ok(!none.includes('检测上下游'),'worker 均无监听时不显示检测入口');
});

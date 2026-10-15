'use client';
import { Fragment, useEffect, useMemo, useRef, useState } from 'react';
import { ChevronRight, Download } from 'lucide-react';
import { jumpserver } from '@/lib/jumpserver';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';

import { processTypes, durations, processRows, filterProcesses, formatDuration, paginate, locationHint, findLocationBlock, type Asset, type Inspection, type ProcessRow, type ProcessType, type Duration } from '@/lib/server-processes';
import { ipSpecSummary } from '@/lib/process-comparison';
type ConfigFile = { path: string; instance: string; content: string; base64: string; bytes: number };
const statuses: Record<string,string> = {complete:'采集完整',partial:'采集不完整',failed:'采集失败',not_collected:'未采集',not_running:'未发现运行中的 Nginx',unknown:'无法确认',sudo_password_error:'sudo 密码错误，Nginx 未采集',sudo_unavailable:'sudo 不可用，Nginx 未采集'};
const webappStatuses: Record<string,string> = {complete:'采集完整',partial:'采集不完整',failed:'采集失败',not_collected:'未采集',absent:'目录不存在'};
function date(value: string) { return new Date(value).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai',hour12:false}); }
function csvCell(value: string): string {
  return /[",\n]/.test(value) ? '"' + value.replace(/"/g, '""') + '"' : value;
}
// 导出“不可登录/未检查”服务器列表，列与页面展示完全一致。
function exportServersCsv(assets: Asset[], login: string) {
  const statusText = login === 'unchecked' ? '未检查' : '不可登录';
  const header = ['IP','主机名','登录状态','原因','检查时间','账号尝试'];
  const lines = [header.map(csvCell).join(',')];
  for (const a of assets) {
    lines.push([
      a.ip || '',
      a.hostname || '',
      statusText,
      a.inspection?.reason || '当前版本未采集',
      a.inspection ? date(a.inspection.checkedAt) : '—',
      a.inspection?.attempts?.map(t => `${t.account}：${t.reason}`).join('；') || '—',
    ].map(csvCell).join(','));
  }
  const bom = '\uFEFF';
  const blob = new Blob([bom + lines.join('\r\n')], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = login === 'unchecked' ? 'unchecked-servers.csv' : 'unreachable-servers.csv';
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function downloadConfigFile(asset: Asset, file: ConfigFile) {
  const bytes=Uint8Array.from(atob(file.base64),c=>c.charCodeAt(0));
  const url=URL.createObjectURL(new Blob([bytes],{type:'application/octet-stream'}));
  const link=document.createElement('a');link.href=url;link.download=asset.ip+'-'+file.path.split('/').pop();link.click();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
}
function ConfigFileContent({asset, file, uri}: {asset: Asset; file: ConfigFile; uri?: string}) {
  // 按 URI 在原始配置文本中定位对应 location 块，渲染时高亮并在挂载后滚动到该行。
  const block=uri?findLocationBlock(file.content,uri):null;
  const boxRef=useRef<HTMLPreElement>(null);
  const lineRef=useRef<HTMLSpanElement>(null);
  useEffect(()=>{
    const box=boxRef.current, line=lineRef.current;
    // 把定位行滚到配置框顶部附近，避免整页跟着跳动。
    if(box&&line)box.scrollTop=Math.max(0,line.offsetTop-12);
  },[file.path,uri]);
  const lines=file.content.split(/\r?\n/);
  const missNote=!block&&uri?(uri==='(TCP/UDP)'?'该路由为四层 stream 转发，没有 location 块。':uri==='/'?'该路由由 server 级默认配置处理，未找到显式 location 块。':`未在原始配置中找到 ${uri} 对应的 location 块。`):'';
  return <div className="space-y-2">
    <p className="text-xs break-all text-muted-foreground">来源配置文件：<code>{file.path}</code> · {file.instance} · {file.bytes} 字节</p>
    <button className="border rounded p-2" onClick={()=>downloadConfigFile(asset,file)}>下载原始文件</button>
    {block&&<p className="text-xs text-muted-foreground">已定位到该 URI 的配置块：第 {block.start+1}–{block.end+1} 行，已高亮。</p>}
    {!!missNote&&<p className="text-xs text-muted-foreground">{missNote}</p>}
    <pre ref={boxRef} className="relative overflow-auto max-h-96 bg-muted rounded p-3 text-xs whitespace-pre">{lines.map((line,i)=>
      <span key={i} ref={block&&i===block.start?lineRef:undefined} className={block&&i>=block.start&&i<=block.end?'nginx-location-line':''}>{line}{'\n'}</span>)}
    </pre>
  </div>;
}
// 配置文件加载状态由 ServerDetails 持有并共享：路由行展开与原始配置列表复用同一次请求。
function NginxConfigs({asset, inspection, files, busy, error, load}: {asset: Asset; inspection: Inspection; files: ConfigFile[]|null; busy: boolean; error: string; load: ()=>void}) {
  return <div className="space-y-3">
    <h3 className="font-medium">Nginx 原始配置文件（{inspection.configurationCount||0}）</h3>
    {!!inspection.configurationCount && !files && <button className="border rounded p-2" disabled={busy} onClick={load}>{busy?'读取中…':'查看原始配置文件'}</button>}
    {error&&<p role="alert">{error}</p>}
    {files?.map((file,i)=><details className="border rounded p-3" key={i}><summary className="cursor-pointer">{file.path} · {file.bytes} 字节 · {file.instance}</summary><button className="border rounded p-2 my-2" onClick={()=>downloadConfigFile(asset,file)}>下载原始文件</button><pre className="overflow-auto max-h-96 bg-muted rounded p-3 text-xs whitespace-pre">{file.content}</pre></details>)}
  </div>;
}
// 提权失败服务器的单独采集弹窗：可填写新的 root 密码（更新密码映射表中这一台服务器的记录）后采集，
// 或不更新密码、按原有流程直接采集（不提权，仅当前账号可见进程）。
function RootPasswordDialog({asset, busy, error, onConfirm, onDirect, onClose}: {asset: Asset; busy: boolean; error: string; onConfirm: (password: string)=>void; onDirect: ()=>void; onClose: ()=>void}) {
  const [password,setPassword]=useState('');
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" role="dialog" aria-modal="true" aria-labelledby="root-password-dialog-title">
    <div className="w-full max-w-md space-y-4 rounded-lg border bg-background p-6 shadow-lg">
      <h3 id="root-password-dialog-title" className="font-semibold">{asset.ip} · {asset.hostname}</h3>
      <p className="text-sm text-muted-foreground">该服务器提权失败（sudo 与 root 密码均验证错误），进程采集不完整。可填写新的 root 密码，确认后更新密码映射表中这台服务器的记录并重新采集；也可以不更新密码，按原有流程直接采集（不提权，仅采集当前账号可见进程）。</p>
      <Input type="password" aria-label="新的 root 密码" placeholder="新的 root 密码" value={password} onChange={e=>setPassword(e.target.value)}/>
      {error&&<p role="alert" className="text-sm text-red-700">{error}</p>}
      <div className="flex flex-wrap justify-end gap-2">
        <button className="border rounded p-2 text-sm" disabled={busy} onClick={onClose}>取消</button>
        <button className="border rounded p-2 text-sm" disabled={busy} onClick={onDirect}>直接采集</button>
        <button className="rounded bg-foreground p-2 text-sm font-medium text-background disabled:opacity-40" disabled={busy||!password.trim()} onClick={()=>onConfirm(password)}>确认并采集</button>
      </div>
    </div>
  </div>;
}
// 单独采集的忙态/消息由 ServerInspections 统一持有（行内按钮与详情按钮共用同一状态）。
function ServerDetails({asset, inspectState, onInspect}: {asset: Asset; inspectState?: {busy:boolean;message:string;error:string}; onInspect: (asset: Asset)=>void}) {
  const inspection=asset.inspection!;
  const [routeQuery,setRouteQuery]=useState('');
  const [webappQuery,setWebappQuery]=useState('');
  const [routeIndex,setRouteIndex]=useState(-1);
  const [files,setFiles]=useState<ConfigFile[]|null>(null);
  const [filesBusy,setFilesBusy]=useState(false);
  const [filesError,setFilesError]=useState('');
  async function loadFiles() {
    setFilesBusy(true);setFilesError('');
    try {
      const response=await fetch('/api/settings/nginx-configs?'+new URLSearchParams({version:inspection.configurationVersion||'',asset:asset.id}),{cache:'no-store'});
      const result=await response.json() as {files?: ConfigFile[]; error?: string};
      if(!response.ok)throw new Error(result.error||'无法读取原始配置');
      setFiles(result.files||[]);
    } catch(e) {setFilesError(e instanceof Error?e.message:'读取失败');} finally {setFilesBusy(false);}
  }
  const routeFilter=routeQuery.trim().toLowerCase();
  const routes=routeFilter?inspection.nginxRoutes.filter(r=>(r.domains.join(' ')+' '+r.uri).toLowerCase().includes(routeFilter)):inspection.nginxRoutes;
  const webappFilter=webappQuery.trim().toLowerCase();
  const webappRows=inspection.webapps&&webappFilter?inspection.webapps.apps.filter(a=>a.name.toLowerCase().includes(webappFilter)):inspection.webapps?.apps||[];
  function toggleRoute(i: number) {
    if(routeIndex===i) {setRouteIndex(-1);return;}
    setRouteIndex(i);
    if(inspection.configurationCount&&!files&&!filesBusy&&!filesError)loadFiles();
  }
  const expandedRoute=routeIndex>=0&&routeIndex<routes.length?routes[routeIndex]:null;
  // 旧快照未记录路由来源文件：仅采集到一个配置文件时可直接关联，否则不猜测。
  const expandedFile=expandedRoute?(expandedRoute.file?files?.find(f=>f.path===expandedRoute.file):(files&&files.length===1?files[0]:undefined)):undefined;
  return <div className="space-y-4 border rounded-lg p-4">
    <h3 className="font-semibold">{asset.ip} · {asset.hostname} · 服务器详情</h3>
    <p>检查时间：{date(inspection.checkedAt)} · 账号：{inspection.account||'—'} · 进程：{statuses[inspection.processStatus]} · Nginx：{statuses[inspection.nginxStatus]}</p>
    <p>sudo：{({root:'已是 root',passwordless:'免密 sudo -i',password:'密码 sudo -i 成功',password_error:'sudo 密码错误',password_required:'未配置密码',denied:'无权限',timeout:'超时'} as Record<string,string>)[inspection.sudoStatus||'']||'未检查'}</p>
    {inspection.serverUsage&&<p>CPU 使用率：<strong className={inspection.serverUsage.cpuPercent>=80?'text-red-600':inspection.serverUsage.cpuPercent>=50?'text-amber-600':''}>{inspection.serverUsage.cpuPercent}%</strong> · 内存使用率：<strong className={inspection.serverUsage.memPercent>=80?'text-red-600':inspection.serverUsage.memPercent>=50?'text-amber-600':''}>{inspection.serverUsage.memPercent}%</strong>（{inspection.serverUsage.memUsedMB>=1024?`${(inspection.serverUsage.memUsedMB/1024).toFixed(1)}G`:`${inspection.serverUsage.memUsedMB}M`} / {inspection.serverUsage.memTotalMB>=1024?`${(inspection.serverUsage.memTotalMB/1024).toFixed(1)}G`:`${inspection.serverUsage.memTotalMB}M`}）</p>}
    <div className="space-y-1">
      <button className="border rounded p-2 text-sm" disabled={inspectState?.busy} onClick={()=>onInspect(asset)}>{inspectState?.busy?'任务启动中…':'单独重新采集此服务器'}</button>
      <p className="text-xs text-muted-foreground">仅重新采集这台服务器的进程、Nginx 配置与 Webapps 应用目录，不影响其他数据；完成后需刷新页面。提权失败的服务器会先弹出 root 密码填写框。</p>
      {inspectState?.message&&<p role="status" className="text-sm text-green-700">{inspectState.message}</p>}
      {inspectState?.error&&<p role="alert" className="text-sm text-red-700">{inspectState.error}</p>}
    </div>
    {!!inspection.warnings?.length&&<p className="text-amber-700">{inspection.warnings.join('；')}</p>}
    {!!inspection.attempts?.length&&<p>连接尝试：{inspection.attempts.map(a=>`${a.account}：${a.reason}`).join('；')}</p>}
    {!!inspection.appPorts?.length&&<details><summary className="cursor-pointer font-medium">应用监听端口（{inspection.appPorts.length}）</summary>
      <ul className="text-sm space-y-0.5">{inspection.appPorts.map((p,i)=><li key={i} title={`绑定地址：${p.addresses.join(', ')||'—'}；PID：${p.pids.join(', ')}`}>{p.app}{p.app!==p.kind?`（${p.kind}）`:''}：<code>{p.port}</code></li>)}</ul></details>}
    {!!inspection.webapps&&<details><summary className="cursor-pointer font-medium">Webapps 应用目录（{inspection.webapps.apps.length}）</summary>
      <p className="text-sm text-muted-foreground">采集 /var/www/webapps 一级子目录；已忽略 __ 开头、_buff 结尾及日期时间戳后缀的备份目录 · {webappStatuses[inspection.webapps.status]||inspection.webapps.status}</p>
      {!!inspection.webapps.apps.length&&<Input className="my-2 max-w-xs" type="search" aria-label="按名称查找 Webapps 应用目录" placeholder="按应用名查找" value={webappQuery} onChange={e=>setWebappQuery(e.target.value)}/>}
      {!!webappRows.length&&<div className="overflow-auto max-h-96"><table className="w-full text-sm text-left"><thead><tr>{['应用名','更新时间'].map(h=><th className="p-2" key={h}>{h}</th>)}</tr></thead><tbody>
        {webappRows.map((a,i)=><tr key={i} className="border-t"><td className="p-2"><code>{a.name}</code></td><td className="p-2 whitespace-nowrap">{a.mtime?date(a.mtime):'—'}</td></tr>)}
      </tbody></table></div>}
      {!!inspection.webapps.apps.length&&!webappRows.length&&<p className="text-sm">没有匹配的应用目录。</p>}
      {!inspection.webapps.apps.length&&<p className="text-sm">{inspection.webapps.status==='absent'?'该服务器没有 /var/www/webapps 目录':'没有符合采集规则的应用目录'}</p>}
    </details>}
    <details><summary className="cursor-pointer font-medium">Nginx 域名 / URI / Upstream（{inspection.nginxRoutes.length} 条）</summary>
      {!!inspection.nginxRoutes.length&&<Input className="my-2 max-w-xs" type="search" aria-label="按域名或 URI 筛选 Nginx 路由" placeholder="按域名或 URI 筛选，例如 /api" value={routeQuery} onChange={e=>{setRouteQuery(e.target.value);setRouteIndex(-1);}}/>}
      <div className="overflow-auto max-h-96"><table className="w-full text-sm text-left"><thead><tr>{['实例 / 监听','域名','URI','URI 说明','转发目标','后端 IP / 端口'].map(h=><th className="p-2" key={h}>{h}</th>)}</tr></thead><tbody>
      {routes.map((r,i)=><Fragment key={i}>
        <tr className={`border-t ${routeIndex===i?'bg-muted/60':'hover:bg-muted/40'} cursor-pointer`} title="点击展开/收起这条配置的原始配置文件" onClick={()=>toggleRoute(i)}><td className="p-2">{r.instance}<br/>{r.listen.join(', ')}</td><td className="p-2">{r.domains.join(', ')||'未配置域名'}</td><td className="p-2">{r.uri}</td><td className="p-2 text-muted-foreground">{locationHint(r.uri)||'—'}</td><td className="p-2 break-all">{r.directive}<br/>{r.target||'无代理目标'}</td><td className="p-2">{r.backends.map((b,j)=><div key={j}>{b.host} / {b.port||'—'}{b.resolution==='hostname'?'（域名，未解析 IP）':b.resolution==='dynamic'?'（动态变量）':b.resolution==='unix'?'（Unix socket）':''}</div>)}</td></tr>
        {routeIndex===i&&<tr><td colSpan={6} className="border-t bg-muted/30 p-3">
          {!inspection.configurationCount&&<p className="text-sm">该服务器未采集到 Nginx 配置文件，无法展开原始配置。</p>}
          {!!inspection.configurationCount&&!files&&!filesError&&<button className="border rounded p-2 text-sm" disabled={filesBusy} onClick={loadFiles}>{filesBusy?'读取中…':'查看相关原始配置文件'}</button>}
          {filesError&&<p role="alert" className="text-sm text-red-700">{filesError}</p>}
          {files&&expandedFile&&<ConfigFileContent asset={asset} file={expandedFile} uri={r.uri}/>}
          {files&&!expandedFile&&<p className="text-sm text-muted-foreground">{r.file?`未在采集到的配置文件中找到 ${r.file}（可能读取失败）`:'该快照未记录这条路由的来源配置文件，且采集到多个配置文件无法确定来源；重新采集后可自动关联'}</p>}
        </td></tr>}
      </Fragment>)}
      </tbody></table></div>
      {!routes.length&&<p className="text-sm">没有匹配的 Nginx 路由。</p>}
    </details>
    <NginxConfigs key={asset.id+inspection.configurationVersion} asset={asset} inspection={inspection} files={files} busy={filesBusy} error={filesError} load={loadFiles}/>
  </div>;
}
// 展开详情框中进程列表的每页条数。
const DETAIL_SIZE=10;
// 统计一台服务器的进程类别数量，按 processTypes 顺序返回有进程的类别。
function categoryCounts(rows: ProcessRow[]): [ProcessType,number][] {
  const counts=new Map<ProcessType,number>();
  for(const r of rows)counts.set(r.type,(counts.get(r.type)||0)+1);
  return processTypes.filter((t): t is ProcessType=>t!=='全部进程').map(t=>[t,counts.get(t)||0] as [ProcessType,number]).filter(([,n])=>n>0);
}
// 服务器监听端口列表：优先取采集的 appPorts，旧快照回退到各进程端口的并集；
// 完全没有端口数据（旧快照）时返回 null，页面显示“—”。
function listeningPorts(asset: Asset): number[]|null {
  const inspection=asset.inspection!;
  if(inspection.appPorts)return inspection.appPorts.map(p=>p.port).sort((a,b)=>a-b);
  const ports=new Set<number>();let collected=false;
  for(const p of inspection.processes)if(p.ports){collected=true;for(const port of p.ports)ports.add(port);}
  return collected?[...ports].sort((a,b)=>a-b):null;
}
// 将规格文本（如 "2C 4G"）解析为可比较的数值，CPU 权重高于内存；无数据返回 -1。
function specScore(ip: string): number {
  const s=ipSpecSummary(ip);
  const cpu=s.match(/(\d+)C/);
  const mem=s.match(/(\d+(?:\.\d+)?)G/);
  if(!cpu&&!mem)return -1;
  return (cpu?parseInt(cpu[1],10):0)*1000+(mem?parseFloat(mem[1]):0);
}
export function ServerInspections() {
  const [login,setLogin]=useState('can_login');
  const [query,setQuery]=useState('');
  const [duration,setDuration]=useState<Duration>('all');
  const [page,setPage]=useState(1);
  const [size,setSize]=useState(25);
  const [sort,setSort]=useState<{field:'ip'|'spec'|'cpu'|'mem';dir:'asc'|'desc'}>({field:'ip',dir:'asc'});
  // 每个可登录 IP 的展开状态：进程类别 + 详情分页页码。
  const [expanded,setExpanded]=useState<Record<string,{type:ProcessType;page:number}>>({});
  const [selected,setSelected]=useState('');
  // 进程详情表排序：按 asset.id 隔离，默认内存%降序。
  const [procSort,setProcSort]=useState<Record<string,{field:'cpu'|'mem';dir:'asc'|'desc'}>>({});
  // 摘要行单机采集按钮状态（按 asset.id 隔离）。
  const [inspect,setInspect]=useState<Record<string,{busy:boolean;message:string;error:string}>>({});
  // 提权失败弹窗：目标资产、提交忙态与错误消息。
  const [pwTarget,setPwTarget]=useState<Asset|null>(null);
  const [pwBusy,setPwBusy]=useState(false);
  const [pwError,setPwError]=useState('');
  // Stable SSR/hydration; after mount advance to the browser clock once a minute.
  const [now,setNow]=useState(()=>Date.parse(jumpserver.collectedAt));
  useEffect(()=>{setNow(Date.now());const timer=setInterval(()=>setNow(Date.now()),60000);return ()=>clearInterval(timer);},[]);
  useEffect(()=>{if(selected)document.getElementById('selected-server-details')?.scrollIntoView({behavior:'smooth',block:'start'});},[selected]);
  const assets=jumpserver.assets as Asset[];
  const rows=useMemo(()=>processRows(assets,now),[assets,now]);
  const searched=useMemo(()=>filterProcesses(rows,query,duration),[rows,query,duration]);
  // 筛选后的进程按服务器分组，每个 IP 一行；按当前排序字段排列。
  const ipGroups=useMemo(()=>{
    const byId=new Map<string,ProcessRow[]>();
    for(const r of searched){const list=byId.get(r.asset.id);if(list)list.push(r);else byId.set(r.asset.id,[r]);}
    const groups=[...byId.values()].map(list=>({asset:list[0].asset,rows:list}));
    const dir=sort.dir==='asc'?1:-1;
    return groups.sort((a,b)=>{
      if(sort.field==='ip')return a.asset.ip.localeCompare(b.asset.ip,undefined,{numeric:true})*dir;
      if(sort.field==='spec')return (specScore(a.asset.ip)-specScore(b.asset.ip))*dir;
      if(sort.field==='cpu')return ((a.asset.inspection!.serverUsage?.cpuPercent??-1)-(b.asset.inspection!.serverUsage?.cpuPercent??-1))*dir;
      return ((a.asset.inspection!.serverUsage?.memPercent??-1)-(b.asset.inspection!.serverUsage?.memPercent??-1))*dir;
    });
  },[searched,sort]);
  const ipPage=paginate(ipGroups,page,size);
  const loginCount=(status: string)=>assets.filter(a=>(a.inspection?.loginStatus||'unchecked')===status).length;
  const serverMatches=(a: Asset)=>`${a.ip} ${a.hostname} ${a.inspection?.reason||''} ${a.inspection?.warnings?.join(' ')||''}`.toLowerCase().includes(query.trim().toLowerCase());
  const serverRows=assets.filter(a=>(a.inspection?.loginStatus||'unchecked')===login&&serverMatches(a));
  const serverPage=paginate(serverRows,page,size);
  const current=login==='can_login'?ipPage:serverPage;
  const selectedAsset=assets.find(a=>a.id===selected);
  // 提权失败（sudo 与 root 密码均验证错误）导致采集不完整的服务器，仅列出 IP。
  const elevationFailed=assets.filter(a=>a.inspection?.loginStatus==='can_login'&&a.inspection.processStatus!=='complete'&&a.inspection.sudoStatus==='password_error');
  function reset() {setPage(1);setSelected('');}
  function cycleSort(field: 'ip'|'spec'|'cpu'|'mem') {
    setSort(prev=>prev.field===field?{field,dir:prev.dir==='asc'?'desc':'asc'}:{field,dir:'desc'});
    setPage(1);
  }
  const sortArrow=(field: string)=>sort.field===field?(sort.dir==='asc'?'\u2191':'\u2193'):'';
  function cycleProcSort(id: string,field: 'cpu'|'mem') {
    setProcSort(prev=>{const cur=prev[id];return {...prev,[id]:cur&&cur.field===field?{field,dir:cur.dir==='asc'?'desc':'asc'}:{field,dir:'desc'}};});
    setAssetView(id,expanded[id]?.type||'全部进程',1);
  }
  function toggleAsset(id: string) {setExpanded(prev=>{const next={...prev};if(next[id])delete next[id];else next[id]={type:'全部进程',page:1};return next;});}
  function setAssetView(id: string,type: ProcessType,viewPage: number) {setExpanded(prev=>({...prev,[id]:{type,page:viewPage}}));}
  // 单独采集入口：提权失败的服务器先弹出 root 密码填写框，其余走原有流程。
  function requestInspect(asset: Asset) {
    if (asset.inspection?.sudoStatus==='password_error') {setPwError('');setPwTarget(asset);return;}
    startInspect(asset);
  }
  // rootPassword 存在时，后端先更新密码映射表中这台服务器的记录再开始采集；否则按原有流程采集。
  async function startInspect(asset: Asset, rootPassword?: string) {
    setPwBusy(true);setPwError('');
    setInspect(prev=>({...prev,[asset.id]:{busy:true,message:'',error:''}}));
    try {
      const response=await fetch('/api/settings/jumpserver-inspect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(rootPassword===undefined?{asset:asset.id}:{asset:asset.id,rootPassword})});
      const result=await response.json() as {error?:string};
      if(!response.ok)throw new Error(result.error||'操作失败');
      setInspect(prev=>({...prev,[asset.id]:{busy:false,message:rootPassword===undefined?'采集任务已启动，完成后刷新页面查看最新结果':'已更新这台服务器的 root 密码，采集任务已启动，完成后刷新页面查看最新结果',error:''}}));
      setPwTarget(null);
    } catch(e) {
      const message=e instanceof Error?e.message:'操作失败';
      setInspect(prev=>({...prev,[asset.id]:{busy:false,message:'',error:message}}));
      if(rootPassword===undefined) setPwTarget(null); else setPwError(message);
    } finally {setPwBusy(false);}
  }
  const tabClass=(active: boolean)=>`rounded-md px-4 py-2 text-sm border ${active?'bg-foreground text-background font-medium':'bg-background hover:bg-muted'}`;
  // 单条进程行（IP 详情框内复用原进程表的列，去掉 IP 列）。
  function processDetailRow(r: ProcessRow) {
    return <tr className="border-t align-top" key={r.asset.id+':'+(r.workerCount?'workers':r.process.pid)}><td className="p-3">{r.workerCount?<span title={`${r.workerCount} 个 Nginx worker 进程`}>×{r.workerCount}</span>:r.process.pid}</td><td className="p-3 break-all">{r.workerCount?<span>Nginx worker<small className="block text-muted-foreground">{r.type} · {r.process.user} · 共 {r.workerCount} 个进程</small></span>:<>{r.name}<small className="block text-muted-foreground">{r.type} · {r.process.user}</small></>}</td><td className="p-3 whitespace-nowrap text-right" title={r.process.cpuPercent!=null?`CPU ${r.process.cpuPercent}%`:'旧快照无 CPU 数据'}>{r.process.cpuPercent!=null?<span className={r.process.cpuPercent>=80?'text-red-600 font-medium':r.process.cpuPercent>=50?'text-amber-600':''}>{r.process.cpuPercent}%</span>:'—'}</td><td className="p-3 whitespace-nowrap text-right" title={r.process.memPercent!=null?`内存 ${r.process.memPercent}%${r.process.rssKB!=null?` · RSS ${(r.process.rssKB/1024).toFixed(0)}M`:''}`:'旧快照无内存数据'}>{r.process.memPercent!=null?<span className={r.process.memPercent>=80?'text-red-600 font-medium':r.process.memPercent>=50?'text-amber-600':''}>{r.process.memPercent}%</span>:'—'}</td><td className="p-3">{r.process.ports?.length?<code>{r.process.ports.join(', ')}</code>:'—'}</td><td className="p-3 whitespace-nowrap">{r.workerCount?'—':date(r.process.startedAt)}</td><td className="p-3 whitespace-nowrap" title={r.workerCount?'worker 进程启动时间各异，已合并展示':`采集时：${formatDuration(r.process.elapsedSeconds)}；采集时间：${date(r.asset.inspection!.checkedAt)}`}>{r.workerCount?'—':formatDuration(r.seconds)}</td><td className="p-3 min-w-80 max-w-2xl"><pre className="text-xs whitespace-pre-wrap break-all font-mono">{r.workerCount?`nginx: worker process（共 ${r.workerCount} 个）`:r.process.command}</pre></td><td className="p-3">{r.type==='Nginx'&&<button className="border rounded p-2 whitespace-nowrap" title="查看该服务器的 Nginx 转发与原始配置" onClick={()=>setSelected(selected===r.asset.id?'':r.asset.id)}>Nginx</button>}</td></tr>;
  }
  return <section className="panel p-6 space-y-5">
    <h2 className="text-lg font-semibold">服务器与进程</h2>
    <div role="tablist" aria-label="服务器登录状态" className="flex flex-wrap gap-2">
      {[['can_login','可登录'],['cannot_login','不可登录'],...(loginCount('unchecked')?[['unchecked','未检查']]:[])].map(([key,label])=><button key={key} id={`login-tab-${key}`} role="tab" aria-selected={login===key} aria-controls="server-inventory-panel" className={tabClass(login===key)} onClick={()=>{setLogin(key);setQuery('');reset();}}>{label}（{loginCount(key)} 台）</button>)}
    </div>
    <div id="server-inventory-panel" role="tabpanel" aria-labelledby={`login-tab-${login}`} className="space-y-4">
      <Input className="max-w-2xl" type="search" aria-label={login==='can_login'?'搜索进程详细命令、IP、进程名或 PID':'搜索服务器 IP、主机名或失败原因'} placeholder={login==='can_login'?'搜索详细命令，例如 app-api.jar；也支持 IP、进程名、PID':'搜索 IP、主机名或失败原因'} value={query} onChange={e=>{setQuery(e.target.value);reset();}}/>
      {login==='can_login'?<>
        <div className="flex flex-wrap items-center gap-3">
          <label htmlFor="process-duration">运行时间</label><select id="process-duration" className="border rounded p-2" value={duration} onChange={e=>{setDuration(e.target.value as Duration);reset();}}>{durations.map(([key,label])=><option key={key} value={key}>{label}</option>)}</select>
          <button className="border rounded p-2 text-sm" onClick={()=>{setQuery('');setDuration('all');reset();}}>清空筛选</button>
        </div>
        <p className="text-sm text-muted-foreground">运行时间为实际经过时长，非 CPU 时间；按采集时已运行秒数推算至 {date(new Date(now).toISOString())}，每分钟更新。快照不能确认采集后是否退出或重启。1个月按30天、半年180天、一年365天；前四档为累计上限。</p>
        <p role="status" className="text-sm">匹配 {searched.length} 条进程，涉及 {ipGroups.length} 个 IP。搜索和时长筛选作用于全部进程；点击 IP 行展开该服务器的进程列表，展开后可按进程类别切换并分页浏览。</p>
        {!!elevationFailed.length&&<details className="text-sm text-amber-700"><summary className="cursor-pointer">{elevationFailed.length} 台可登录服务器提权失败，进程采集不完整</summary>{elevationFailed.map(a=><div key={a.id} className="py-1"><button className="underline" onClick={()=>setSelected(a.id)}>{a.ip}</button></div>)}</details>}
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm"><thead><tr>
            <th className="p-3 whitespace-nowrap"></th>
            <th className="p-3 whitespace-nowrap cursor-pointer hover:bg-muted/50" onClick={()=>cycleSort('ip')}>IP {sortArrow('ip')}</th>
            <th className="p-3 whitespace-nowrap cursor-pointer hover:bg-muted/50" onClick={()=>cycleSort('spec')}>规格 {sortArrow('spec')}</th>
            <th className="p-3 whitespace-nowrap cursor-pointer hover:bg-muted/50 text-right" onClick={()=>cycleSort('cpu')}>CPU% {sortArrow('cpu')}</th>
            <th className="p-3 whitespace-nowrap cursor-pointer hover:bg-muted/50 text-right" onClick={()=>cycleSort('mem')}>内存% {sortArrow('mem')}</th>
            <th className="p-3 whitespace-nowrap">监听端口数</th>
            <th className="p-3 whitespace-nowrap">进程分类</th>
            <th className="p-3 whitespace-nowrap">操作</th>
          </tr></thead><tbody>
          {ipPage.rows.map(group=>{
            const asset=group.asset;
            const open=!!expanded[asset.id];
            const requested=expanded[asset.id]||{type:'全部进程' as ProcessType,page:1};
            // 系统进程默认隐藏；当非系统进程 CPU 合计与整机 CPU 偏差 >5% 时展示系统进程。
            const nonSystemRows=group.rows.filter(r=>r.type!=='系统进程');
            const systemRows=group.rows.filter(r=>r.type==='系统进程');
            const sumNonSystemCPU=nonSystemRows.reduce((s,r)=>s+(r.process.cpuPercent??0),0);
            const serverCPU=asset.inspection!.serverUsage?.cpuPercent??0;
            const showSystem=systemRows.length>0&&serverCPU-sumNonSystemCPU>5;
            const categories=categoryCounts(nonSystemRows);
            const present=new Set(nonSystemRows.map(r=>r.type));
            const viewType: ProcessType = requested.type==='全部进程'||present.has(requested.type)||(showSystem&&requested.type==='系统进程') ? requested.type : '全部进程';
            const ps=procSort[asset.id]||{field:'mem' as const,dir:'desc' as const};
            const sortFn=(a:ProcessRow,b:ProcessRow)=>{
              const av=a.process[ps.field==='cpu'?'cpuPercent':'memPercent']??0;
              const bv=b.process[ps.field==='cpu'?'cpuPercent':'memPercent']??0;
              return ps.dir==='asc'?av-bv:bv-av;
            };
            const baseRows=viewType==='全部进程'?nonSystemRows:viewType==='系统进程'?systemRows:nonSystemRows.filter(r=>r.type===viewType);
            const viewRows=[...baseRows].sort(sortFn);
            const detailPage=paginate(viewRows,requested.page,DETAIL_SIZE);
            const usage=asset.inspection!.serverUsage;
            const ports=listeningPorts(asset);
            return <Fragment key={asset.id}>
              <tr className={'border-t align-top ip-summary-row'+(open?' open':'')} aria-expanded={open} title="点击展开/收起该服务器的进程列表" onClick={()=>toggleAsset(asset.id)}>
                <td className="p-3 w-8"><ChevronRight size={16} className="chevron"/></td>
                <td className="p-3"><code>{asset.ip}</code><small className="block text-muted-foreground">{asset.hostname}</small></td>
                <td className="p-3 whitespace-nowrap">{ipSpecSummary(asset.ip)||'—'}</td>
                <td className="p-3 whitespace-nowrap text-right" title="采集时刻的整机 CPU 使用率">{usage?<span className={usage.cpuPercent>=80?'text-red-600 font-medium':usage.cpuPercent>=50?'text-amber-600':''}>{usage.cpuPercent}%</span>:'—'}</td>
                <td className="p-3 whitespace-nowrap text-right" title={usage?`整机内存 ${usage.memUsedMB>=1024?`${(usage.memUsedMB/1024).toFixed(1)}G`:`${usage.memUsedMB}M`} / ${usage.memTotalMB>=1024?`${(usage.memTotalMB/1024).toFixed(1)}G`:`${usage.memTotalMB}M`}`:'旧快照无整机用量数据'}>{usage?<span className={usage.memPercent>=80?'text-red-600 font-medium':usage.memPercent>=50?'text-amber-600':''}>{usage.memPercent}%</span>:'—'}</td>
                <td className="p-3 whitespace-nowrap">{ports?<span title={ports.join(', ')}>{ports.length}</span>:'—'}</td>
                <td className="p-3">{categories.map(([t,n])=><span key={t} className="proc-chip">{t} {n}</span>)}{showSystem&&<span className="proc-chip proc-chip-warn" title={`整机 CPU ${serverCPU}% - 非系统进程合计 ${sumNonSystemCPU.toFixed(1)}% > 5%，可能有系统进程占用 CPU`}>系统进程 {systemRows.length}</span>}</td>
                <td className="p-3 whitespace-nowrap" onClick={e=>e.stopPropagation()}>
                  <button className="border rounded p-2 text-sm" disabled={inspect[asset.id]?.busy} onClick={()=>requestInspect(asset)}>{inspect[asset.id]?.busy?'采集中…':'单独采集'}</button>
                  {inspect[asset.id]?.message&&<p role="status" className="text-xs text-green-700 mt-1">{inspect[asset.id].message}</p>}
                  {inspect[asset.id]?.error&&<p role="alert" className="text-xs text-red-700 mt-1">{inspect[asset.id].error}</p>}
                </td>
              </tr>
              {open&&<tr className="border-t"><td colSpan={8} className="p-0">
                <div className="ip-detail-box space-y-3">
                  <div role="tablist" aria-label={`${asset.ip} 的进程类别`} className="flex flex-wrap gap-2">
                    <button role="tab" aria-selected={viewType==='全部进程'} className={tabClass(viewType==='全部进程')} onClick={()=>setAssetView(asset.id,'全部进程',1)}>全部进程（{nonSystemRows.length}）</button>
                    {categories.map(([t,n])=><button key={t} role="tab" aria-selected={viewType===t} className={tabClass(viewType===t)} onClick={()=>setAssetView(asset.id,t,1)}>{t==='Java Jar'?'普通 Java Jar':t}（{n}）</button>)}
                    {showSystem&&<button role="tab" aria-selected={viewType==='系统进程'} className={tabClass(viewType==='系统进程')} onClick={()=>setAssetView(asset.id,'系统进程',1)} title={`整机 CPU ${serverCPU}% - 非系统进程 ${sumNonSystemCPU.toFixed(1)}% > 5%`}>系统进程（{systemRows.length}）</button>}
                  </div>
                  <div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr>{(['进程ID','进程名','CPU%','内存%','监听端口','进程启动时间（北京时间）','运行时间（推算至当前）','详细命令','服务器详情'] as const).map(h=>{
                    const sortable=h==='CPU%'||h==='内存%';
                    const sf=h==='CPU%'?'cpu':'mem';
                    return <th key={h} className={'p-3 whitespace-nowrap'+(sortable?' cursor-pointer hover:bg-muted/50':'')} onClick={sortable?()=>cycleProcSort(asset.id,sf as 'cpu'|'mem'):undefined}>{h}{sortable&&(ps.field===sf?(ps.dir==='asc'?'\u2191':'\u2193'):'')}</th>;
                  })}</tr></thead><tbody>
                    {detailPage.rows.map(processDetailRow)}
                  </tbody></table></div>
                  <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
                    <span>共 {detailPage.total} 条进程</span>
                    <div className="flex items-center gap-3"><button className="border rounded px-3 py-2 disabled:opacity-40" disabled={detailPage.page===1} onClick={()=>setAssetView(asset.id,viewType,detailPage.page-1)}>上一页</button><span aria-live="polite">第 {detailPage.page} / {detailPage.pages} 页</span><button className="border rounded px-3 py-2 disabled:opacity-40" disabled={detailPage.page===detailPage.pages} onClick={()=>setAssetView(asset.id,viewType,detailPage.page+1)}>下一页</button></div>
                  </div>
                </div>
              </td></tr>}
            </Fragment>;
          })}
          </tbody></table>
          {!searched.length&&<p className="py-8 text-center text-muted-foreground">没有符合条件的进程。可清空筛选后重试。</p>}
        </div>
      </>:<div className="space-y-3">
        <div className="flex justify-end">
          <Button variant="default" size="sm" className="export-button" disabled={!serverRows.length} onClick={()=>exportServersCsv(serverRows,login)} aria-label="导出当前服务器列表 CSV（与展示列一致）">
            <Download size={16}/>导出 CSV（{serverRows.length} 台）
          </Button>
        </div>
        <div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr>{['IP','主机名','登录状态','原因','检查时间','账号尝试'].map(h=><th className="p-3" key={h}>{h}</th>)}</tr></thead><tbody>{serverPage.rows.map(a=><tr className="border-t align-top" key={a.id}><td className="p-3"><code>{a.ip}</code></td><td className="p-3">{a.hostname}</td><td className="p-3">{login==='unchecked'?'未检查':'不可登录'}</td><td className="p-3">{a.inspection?.reason||'当前版本未采集'}</td><td className="p-3 whitespace-nowrap">{a.inspection?date(a.inspection.checkedAt):'—'}</td><td className="p-3">{a.inspection?.attempts?.map(t=>`${t.account}：${t.reason}`).join('；')||'—'}</td></tr>)}</tbody></table>{!serverPage.total&&<p className="py-8 text-center">没有符合条件的服务器。</p>}</div>
      </div>}
      <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
        <label>每页 <select aria-label="每页台数" className="border rounded p-2" value={size} onChange={e=>{setSize(Number(e.target.value));setPage(1);}}>{[25,50,100].map(n=><option key={n} value={n}>{n}</option>)}</select> 台 · 共 {current.total} 台</label>
        <div className="flex items-center gap-3"><button className="border rounded px-3 py-2 disabled:opacity-40" disabled={current.page===1} onClick={()=>setPage(current.page-1)}>上一页</button><span aria-live="polite">第 {current.page} / {current.pages} 页</span><button className="border rounded px-3 py-2 disabled:opacity-40" disabled={current.page===current.pages} onClick={()=>setPage(current.page+1)}>下一页</button></div>
      </div>
      {login==='can_login'&&selectedAsset?.inspection&&<div id="selected-server-details" className="space-y-3 scroll-mt-6"><button className="text-sm underline" onClick={()=>setSelected('')}>收起服务器详情</button><ServerDetails key={selectedAsset.id} asset={selectedAsset} inspectState={inspect[selectedAsset.id]} onInspect={requestInspect}/></div>}
      {pwTarget&&<RootPasswordDialog asset={pwTarget} busy={pwBusy} error={pwError} onConfirm={password=>startInspect(pwTarget,password)} onDirect={()=>startInspect(pwTarget)} onClose={()=>setPwTarget(null)}/>}
      {!assets.some(a=>a.inspection)&&<p>当前历史版本没有服务器采集结果，请在设置中同步 JumpServer。</p>}
    </div>
  </section>;
}

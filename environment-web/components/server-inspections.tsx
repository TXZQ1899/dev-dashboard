'use client';
import { useEffect, useMemo, useState } from 'react';
import { jumpserver } from '@/lib/jumpserver';
import { Input } from '@/components/ui/input';

import { processTypes, durations, processRows, filterProcesses, formatDuration, paginate, type Asset, type Inspection, type ProcessType, type Duration } from '@/lib/server-processes';
type ConfigFile = { path: string; instance: string; content: string; base64: string; bytes: number };
const statuses: Record<string,string> = {complete:'采集完整',partial:'采集不完整',failed:'采集失败',not_collected:'未采集',not_running:'未发现运行中的 Nginx',unknown:'无法确认',sudo_password_error:'sudo 密码错误，Nginx 未采集',sudo_unavailable:'sudo 不可用，Nginx 未采集'};
function date(value: string) { return new Date(value).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai',hour12:false}); }
function NginxConfigs({asset, inspection}: {asset: Asset; inspection: Inspection}) {
  const [files,setFiles]=useState<ConfigFile[]|null>(null);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  async function load() {
    setBusy(true);setError('');
    try {
      const response=await fetch('/api/settings/nginx-configs?'+new URLSearchParams({version:inspection.configurationVersion||'',asset:asset.id}),{cache:'no-store'});
      const result=await response.json() as {files?: ConfigFile[]; error?: string};
      if(!response.ok)throw new Error(result.error||'无法读取原始配置');
      setFiles(result.files||[]);
    } catch(e) {setError(e instanceof Error?e.message:'读取失败');} finally {setBusy(false);}
  }
  function download(file: ConfigFile) {
    const bytes=Uint8Array.from(atob(file.base64),c=>c.charCodeAt(0));
    const url=URL.createObjectURL(new Blob([bytes],{type:'application/octet-stream'}));
    const link=document.createElement('a');link.href=url;link.download=asset.ip+'-'+file.path.split('/').pop();link.click();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  return <div className="space-y-3">
    <h3 className="font-medium">Nginx 原始配置文件（{inspection.configurationCount||0}）</h3>
    {!!inspection.configurationCount && !files && <button className="border rounded p-2" disabled={busy} onClick={load}>{busy?'读取中…':'查看原始配置文件'}</button>}
    {error&&<p role="alert">{error}</p>}
    {files?.map((file,i)=><details className="border rounded p-3" key={i}><summary className="cursor-pointer">{file.path} · {file.bytes} 字节 · {file.instance}</summary><button className="border rounded p-2 my-2" onClick={()=>download(file)}>下载原始文件</button><pre className="overflow-auto max-h-96 bg-muted rounded p-3 text-xs whitespace-pre">{file.content}</pre></details>)}
  </div>;
}
function ServerDetails({asset}: {asset: Asset}) {
  const inspection=asset.inspection!;
  const [inspectBusy,setInspectBusy]=useState(false);
  const [inspectMessage,setInspectMessage]=useState('');
  const [inspectError,setInspectError]=useState('');
  async function reinspect() {
    setInspectBusy(true);setInspectError('');setInspectMessage('');
    try {
      const response=await fetch('/api/settings/jumpserver-inspect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({asset:asset.id})});
      const result=await response.json() as {error?:string};
      if(!response.ok)throw new Error(result.error||'操作失败');
      setInspectMessage('单独采集任务已启动，完成后刷新页面即可看到最新结果');
    } catch(e) {setInspectError(e instanceof Error?e.message:'操作失败');} finally {setInspectBusy(false);}
  }
  return <div className="space-y-4 border rounded-lg p-4">
    <h3 className="font-semibold">{asset.ip} · {asset.hostname} · 服务器详情</h3>
    <p>检查时间：{date(inspection.checkedAt)} · 账号：{inspection.account||'—'} · 进程：{statuses[inspection.processStatus]} · Nginx：{statuses[inspection.nginxStatus]}</p>
    <p>sudo：{({root:'已是 root',passwordless:'免密 sudo -i',password:'密码 sudo -i 成功',password_error:'sudo 密码错误',password_required:'未配置密码',denied:'无权限',timeout:'超时'} as Record<string,string>)[inspection.sudoStatus||'']||'未检查'}</p>
    <div className="space-y-1">
      <button className="border rounded p-2 text-sm" disabled={inspectBusy} onClick={reinspect}>{inspectBusy?'任务启动中…':'单独重新采集此服务器'}</button>
      <p className="text-xs text-muted-foreground">仅重新采集这台服务器的进程与 Nginx 配置，不影响其他数据；完成后需刷新页面。</p>
      {inspectMessage&&<p role="status" className="text-sm text-green-700">{inspectMessage}</p>}
      {inspectError&&<p role="alert" className="text-sm text-red-700">{inspectError}</p>}
    </div>
    {!!inspection.warnings?.length&&<p className="text-amber-700">{inspection.warnings.join('；')}</p>}
    {!!inspection.attempts?.length&&<p>连接尝试：{inspection.attempts.map(a=>`${a.account}：${a.reason}`).join('；')}</p>}
    <details><summary className="cursor-pointer font-medium">Nginx 域名 / URI / Upstream（{inspection.nginxRoutes.length} 条）</summary>
      <div className="overflow-auto max-h-96"><table className="w-full text-sm text-left"><thead><tr>{['实例 / 监听','域名','URI','转发目标','后端 IP / 端口'].map(h=><th className="p-2" key={h}>{h}</th>)}</tr></thead><tbody>
      {inspection.nginxRoutes.map((r,i)=><tr key={i} className="border-t"><td className="p-2">{r.instance}<br/>{r.listen.join(', ')}</td><td className="p-2">{r.domains.join(', ')||'未配置域名'}</td><td className="p-2">{r.uri}</td><td className="p-2 break-all">{r.directive}<br/>{r.target||'无代理目标'}</td><td className="p-2">{r.backends.map((b,j)=><div key={j}>{b.host} / {b.port||'—'}{b.resolution==='hostname'?'（域名，未解析 IP）':b.resolution==='dynamic'?'（动态变量）':b.resolution==='unix'?'（Unix socket）':''}</div>)}</td></tr>)}
      </tbody></table></div>
    </details>
    <NginxConfigs key={asset.id+inspection.configurationVersion} asset={asset} inspection={inspection}/>
  </div>;
}
export function ServerInspections() {
  const [login,setLogin]=useState('can_login');
  const [query,setQuery]=useState('');
  const [type,setType]=useState<ProcessType>('全部进程');
  const [duration,setDuration]=useState<Duration>('all');
  const [page,setPage]=useState(1);
  const [size,setSize]=useState(25);
  const [selected,setSelected]=useState('');
  // Stable SSR/hydration; after mount advance to the browser clock once a minute.
  const [now,setNow]=useState(()=>Date.parse(jumpserver.collectedAt));
  useEffect(()=>{setNow(Date.now());const timer=setInterval(()=>setNow(Date.now()),60000);return ()=>clearInterval(timer);},[]);
  useEffect(()=>{if(selected)document.getElementById('selected-server-details')?.scrollIntoView({behavior:'smooth',block:'start'});},[selected]);
  const assets=jumpserver.assets as Asset[];
  const rows=useMemo(()=>processRows(assets,now),[assets,now]);
  const searched=useMemo(()=>filterProcesses(rows,query,duration),[rows,query,duration]);
  const filtered=type==='全部进程'?searched:searched.filter(r=>r.type===type);
  const processPage=paginate(filtered,page,size);
  const counts=Object.fromEntries(processTypes.map(t=>[t,t==='全部进程'?searched.length:searched.filter(r=>r.type===t).length]));
  const loginCount=(status: string)=>assets.filter(a=>(a.inspection?.loginStatus||'unchecked')===status).length;
  const serverMatches=(a: Asset)=>`${a.ip} ${a.hostname} ${a.inspection?.reason||''} ${a.inspection?.warnings?.join(' ')||''}`.toLowerCase().includes(query.trim().toLowerCase());
  const serverPage=paginate(assets.filter(a=>(a.inspection?.loginStatus||'unchecked')===login&&serverMatches(a)),page,size);
  const current=login==='can_login'?processPage:serverPage;
  const selectedAsset=assets.find(a=>a.id===selected);
  const incomplete=assets.filter(a=>a.inspection?.loginStatus==='can_login'&&a.inspection.processStatus!=='complete');
  function reset() {setPage(1);setSelected('');}
  const tabClass=(active: boolean)=>`rounded-md px-4 py-2 text-sm border ${active?'bg-foreground text-background font-medium':'bg-background hover:bg-muted'}`;
  return <section className="panel p-6 space-y-5">
    <h2 className="text-lg font-semibold">服务器与进程</h2>
    <div role="tablist" aria-label="服务器登录状态" className="flex flex-wrap gap-2">
      {[['can_login','可登录'],['cannot_login','不能登录'],...(loginCount('unchecked')?[['unchecked','未检查']]:[])].map(([key,label])=><button key={key} id={`login-tab-${key}`} role="tab" aria-selected={login===key} aria-controls="server-inventory-panel" className={tabClass(login===key)} onClick={()=>{setLogin(key);setQuery('');reset();}}>{label}（{loginCount(key)} 台）</button>)}
    </div>
    <div id="server-inventory-panel" role="tabpanel" aria-labelledby={`login-tab-${login}`} className="space-y-4">
      <Input className="max-w-2xl" type="search" aria-label={login==='can_login'?'搜索进程详细命令、IP、进程名或 PID':'搜索服务器 IP、主机名或失败原因'} placeholder={login==='can_login'?'搜索详细命令，例如 app-api.jar；也支持 IP、进程名、PID':'搜索 IP、主机名或失败原因'} value={query} onChange={e=>{setQuery(e.target.value);if(login==='can_login')setType('全部进程');reset();}}/>
      {login==='can_login'?<>
        <div className="flex flex-wrap items-center gap-3">
          <label htmlFor="process-duration">运行时间</label><select id="process-duration" className="border rounded p-2" value={duration} onChange={e=>{setDuration(e.target.value as Duration);reset();}}>{durations.map(([key,label])=><option key={key} value={key}>{label}</option>)}</select>
          <button className="border rounded p-2 text-sm" onClick={()=>{setQuery('');setDuration('all');setType('全部进程');reset();}}>清空筛选</button>
        </div>
        <p className="text-sm text-muted-foreground">运行时间为实际经过时长，非 CPU 时间；按采集时已运行秒数推算至 {date(new Date(now).toISOString())}，每分钟更新。快照不能确认采集后是否退出或重启。1个月按30天、半年180天、一年365天；前四档为累计上限。</p>
        <div role="tablist" aria-label="进程类型" className="flex flex-wrap gap-2">{processTypes.map(t=><button role="tab" id={`process-tab-${processTypes.indexOf(t)}`} aria-selected={type===t} aria-controls="process-list-panel" key={t} className={tabClass(type===t)} onClick={()=>{setType(t);reset();}}>{t==='Java Jar'?'普通 Java Jar':t}（{counts[t]}）</button>)}</div>
        <p role="status" className="text-sm">{type}：匹配 {filtered.length} 条进程，涉及 {new Set(filtered.map(r=>r.asset.ip)).size} 个 IP。搜索和时长筛选作用于全部进程，类型页签显示筛选后的数量。</p>
        {!!incomplete.length&&<details className="text-sm text-amber-700"><summary className="cursor-pointer">{incomplete.length} 台可登录服务器的进程采集不完整或失败</summary>{incomplete.map(a=><div key={a.id} className="py-1"><button className="underline" onClick={()=>setSelected(a.id)}>{a.ip} · {a.hostname}</button> · {statuses[a.inspection!.processStatus]} · {a.inspection!.warnings.join('；')}</div>)}</details>}
        <div role="tabpanel" id="process-list-panel" aria-labelledby={`process-tab-${processTypes.indexOf(type)}`} className="overflow-x-auto">
          <table className="w-full text-left text-sm"><thead><tr>{['IP','进程ID','进程名','进程启动时间（北京时间）','运行时间（推算至当前）','详细命令','服务器详情'].map(h=><th key={h} className="p-3 whitespace-nowrap">{h}</th>)}</tr></thead><tbody>
          {processPage.rows.map(r=><tr className="border-t align-top" key={r.asset.id+':'+r.process.pid}><td className="p-3"><code>{r.asset.ip}</code><small className="block text-muted-foreground">{r.asset.hostname}</small></td><td className="p-3">{r.process.pid}</td><td className="p-3 break-all">{r.name}<small className="block text-muted-foreground">{r.type} · {r.process.user}</small></td><td className="p-3 whitespace-nowrap">{date(r.process.startedAt)}</td><td className="p-3 whitespace-nowrap" title={`采集时：${formatDuration(r.process.elapsedSeconds)}；采集时间：${date(r.asset.inspection!.checkedAt)}`}>{formatDuration(r.seconds)}</td><td className="p-3 min-w-80 max-w-2xl"><pre className="text-xs whitespace-pre-wrap break-all font-mono">{r.process.command}</pre></td><td className="p-3"><button className="border rounded p-2 whitespace-nowrap" onClick={()=>setSelected(selected===r.asset.id?'':r.asset.id)}>详情 / Nginx</button></td></tr>)}
          </tbody></table>
          {!filtered.length&&<p className="py-8 text-center text-muted-foreground">没有符合条件的进程。可切换“全部进程”或清空筛选。</p>}
        </div>
      </>:<div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr>{['IP','主机名','登录状态','原因','检查时间','账号尝试'].map(h=><th className="p-3" key={h}>{h}</th>)}</tr></thead><tbody>{serverPage.rows.map(a=><tr className="border-t align-top" key={a.id}><td className="p-3"><code>{a.ip}</code></td><td className="p-3">{a.hostname}</td><td className="p-3">{login==='unchecked'?'未检查':'不能登录'}</td><td className="p-3">{a.inspection?.reason||'当前版本未采集'}</td><td className="p-3 whitespace-nowrap">{a.inspection?date(a.inspection.checkedAt):'—'}</td><td className="p-3">{a.inspection?.attempts?.map(t=>`${t.account}：${t.reason}`).join('；')||'—'}</td></tr>)}</tbody></table>{!serverPage.total&&<p className="py-8 text-center">没有符合条件的服务器。</p>}</div>}
      <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
        <label>每页 <select aria-label="每页条数" className="border rounded p-2" value={size} onChange={e=>{setSize(Number(e.target.value));setPage(1);}}>{[25,50,100].map(n=><option key={n} value={n}>{n}</option>)}</select> 条 · 共 {current.total} 条</label>
        <div className="flex items-center gap-3"><button className="border rounded px-3 py-2 disabled:opacity-40" disabled={current.page===1} onClick={()=>setPage(current.page-1)}>上一页</button><span aria-live="polite">第 {current.page} / {current.pages} 页</span><button className="border rounded px-3 py-2 disabled:opacity-40" disabled={current.page===current.pages} onClick={()=>setPage(current.page+1)}>下一页</button></div>
      </div>
      {login==='can_login'&&selectedAsset?.inspection&&<div id="selected-server-details" className="space-y-3 scroll-mt-6"><button className="text-sm underline" onClick={()=>setSelected('')}>收起服务器详情</button><ServerDetails key={selectedAsset.id} asset={selectedAsset}/></div>}
      {!assets.some(a=>a.inspection)&&<p>当前历史版本没有服务器采集结果，请在设置中同步 JumpServer。</p>}
    </div>
  </section>;
}

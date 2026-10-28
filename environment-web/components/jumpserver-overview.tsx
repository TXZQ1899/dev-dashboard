'use client';
import { useMemo, useState } from 'react';
import { jumpserver } from '@/lib/jumpserver';
import { instances } from '@/lib/ecs';
import ecsSnapshot from '@/lib/ecs-snapshot.json';
import { formatBeijingTime } from '@/lib/datetime';
import { buildJumpserverOverview, type JumpOverviewRow, type LoginClass, type LocationClass, type CoverageClass, type EcsStatus } from '@/lib/jumpserver-overview';
import type { Asset } from '@/lib/server-processes';

type Item<T extends string> = {key:T; label:string; color:string};
const loginItems: Item<LoginClass>[] = [
  {key:'elevated',label:'可切Root',color:'#27866e'}, {key:'ordinary',label:'仅普通账号',color:'#d99134'},
  {key:'cannot_login',label:'不可登录',color:'#cf5a4d'}, {key:'unchecked',label:'未检查',color:'#929ea7'},
  {key:'elevation_unknown',label:'提权情况未知',color:'#8971a8'},
];
const locationItems: Item<LocationClass>[] = [
  {key:'running',label:'ECS 运行中',color:'#27866e'}, {key:'stopped',label:'ECS 已停止',color:'#83919b'},
  {key:'transition',label:'ECS 状态过渡中',color:'#d99134'}, {key:'status_unknown',label:'ECS 状态未知',color:'#a18fba'},
  {key:'local',label:'本地机房',color:'#5480c1'}, {key:'not_in_ecs',label:'不在 ECS 清单中',color:'#cf7651'},
  {key:'ambiguous',label:'关联待确认',color:'#c6a247'},
];
const coverageItems: Item<CoverageClass>[] = [
  {key:'managed',label:'已纳管',color:'#27866e'}, {key:'unmatched',label:'未纳管',color:'#cf7651'},
  {key:'ambiguous',label:'关联待确认',color:'#c6a247'},
];
const statusLabels: Record<EcsStatus,string> = {running:'运行中',stopped:'已停止',transition:'启动中 / 停止中',status_unknown:'状态未知'};
const nameOf = <T extends string>(items: Item<T>[], key: T) => items.find(item => item.key === key)?.label || key;

function locationDetail(row: JumpOverviewRow, hideLocal = false) {
  if (row.instance) return `${row.instance.name} / ${row.instance.id}`;
  if ((!hideLocal && row.location === 'not_in_ecs') || (hideLocal && row.location === 'local')) return '';
  return row.evidence + (row.location==='ambiguous'&&row.candidates.length>0?` · ECS 候选：${row.candidates.map(c=>`${c.tenantName||c.tenantId||'历史租户'} / ${c.id}`).join('、')}`:'');
}
function overviewCsv(values: string[][]) {
  const csvCell = (value: string) => {
    const safe = /^[\s]*[=+@\-\t\r]/.test(value) ? "'" + value : value;
    return /[",\r\n]/.test(safe) ? '"' + safe.replace(/"/g, '""') + '"' : safe;
  };
  return values.map(row=>row.map(csvCell).join(',')).join('\r\n');
}
export function unreachableOverviewCsv(rows: JumpOverviewRow[]) {
  const values = [['IP','主机名','ECS 状态与关联'], ...rows.map(row=>[
    row.asset.ip || '', row.asset.hostname || '',
    [nameOf(locationItems,row.location), locationDetail(row)].filter(Boolean).join(' · '),
  ])];
  return overviewCsv(values);
}
export function ordinaryOverviewCsv(rows: JumpOverviewRow[], hideDetail = false) {
  return overviewCsv([['IP','主机名','ECS 状态与关联'], ...rows.map(row=>[
    row.asset.ip || '', row.asset.hostname || '',
    [nameOf(locationItems,row.location), hideDetail ? '' : locationDetail(row, true)].filter(Boolean).join(' · '),
  ])]);
}
function downloadOverviewCsv(rows: JumpOverviewRow[], ordinary: boolean, notInEcs: boolean) {
  const blob = new Blob(['\uFEFF' + (ordinary ? ordinaryOverviewCsv(rows,notInEcs) : unreachableOverviewCsv(rows))], {type:'text/csv;charset=utf-8;'});
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a'); link.href=url; link.download=ordinary?'jumpserver-ordinary-overview.csv':'jumpserver-unreachable-overview.csv'; link.click();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
}

function Donut<T extends string>({title, description, items, counts, selected, onSelect}: {
  title:string; description?:string; items:Item<T>[]; counts:Record<T,number>; selected:T | null; onSelect:(key:T)=>void;
}) {
  const total = items.reduce((sum, item) => sum + counts[item.key], 0);
  let offset = 0;
  return <div className="rounded-xl border p-4 min-w-0">
    <h3 className="font-semibold">{title}</h3>{description&&<p className="text-xs text-muted-foreground mt-1">{description}</p>}
    <div className="flex flex-wrap items-center justify-center gap-5 mt-4">
      <div className="relative w-40 h-40 shrink-0">
        <svg viewBox="0 0 120 120" className="w-full h-full" aria-label={title} role="group">
          <circle cx="60" cy="60" r="45" fill="none" stroke="#e8eeeb" strokeWidth="18" />
          {items.map(item => {
            const share = total ? counts[item.key] / total * 100 : 0; const start = offset; offset += share;
            return share > 0 ? <circle key={item.key} cx="60" cy="60" r="45" pathLength="100" fill="none" stroke={item.color} strokeWidth={selected===item.key?21:18} strokeDasharray={`${share} ${100-share}`} strokeDashoffset={-start} transform="rotate(-90 60 60)" className="cursor-pointer" role="button" tabIndex={0} aria-label={`${item.label} ${counts[item.key]} 台`} aria-pressed={selected===item.key} onClick={()=>onSelect(item.key)} onKeyDown={e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();onSelect(item.key);}}} /> : null;
          })}
        </svg>
        <div className="absolute inset-0 pointer-events-none flex flex-col items-center justify-center"><strong className="text-2xl">{total}</strong><span className="text-xs text-muted-foreground">台</span></div>
      </div>
      <div className="flex-1 min-w-44 space-y-1">{items.filter(item=>counts[item.key]>0).map(item=><button type="button" key={item.key} onClick={()=>onSelect(item.key)} aria-pressed={selected===item.key} className={`w-full text-left flex items-center gap-2 rounded px-2 py-1 text-sm ${selected===item.key?'bg-muted font-semibold':''}`}>
        <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{background:item.color}} /><span className="flex-1">{item.label}</span><span>{counts[item.key]} 台</span><span className="text-xs text-muted-foreground w-10 text-right">{total ? Math.round(counts[item.key]/total*100) : 0}%</span>
      </button>)}</div>
    </div>
  </div>;
}
function counts<T extends string>(items:Item<T>[], values:T[]) { return Object.fromEntries(items.map(i=>[i.key, values.filter(v=>v===i.key).length])) as Record<T,number>; }
const time = (value?:string|null) => formatBeijingTime(value,'未提供');

export function JumpserverOverview() {
  const {jump,ecs} = useMemo(()=>buildJumpserverOverview(jumpserver.assets as Asset[], instances),[]);
  const [login,setLogin] = useState<LoginClass|null>(null);
  const [location,setLocation] = useState<LocationClass|null>(null);
  const [folidevOnly,setFolidevOnly] = useState(false);
  const [status,setStatus] = useState<EcsStatus|'all'>('all');
  const [coverage,setCoverage] = useState<CoverageClass|null>(null);
  const loginRows = login ? jump.filter(row=>row.login===login) : jump;
  const locationRows = location ? loginRows.filter(row=>row.location===location) : loginRows;
  const jumpRows = login==='ordinary' && folidevOnly ? locationRows.filter(row=>row.asset.inspection?.account==='folidev') : locationRows;
  const hideLogin = login==='ordinary' || login==='elevated' || login==='cannot_login' || location==='not_in_ecs';
  const hideExtra = hideLogin || login==='cannot_login';
  const ecsScope = status==='all' ? ecs : ecs.filter(row=>row.status===status);
  const ecsRows = coverage ? ecsScope.filter(row=>row.coverage===coverage) : ecsScope;
  const scopes = (ecsSnapshot as unknown as {tenants?:{tenantId:string;tenantName:string;region:string;collectedAt:string|null;complete?:boolean}[]}).tenants || [];
  return <section className="panel p-6 space-y-5 mb-6" aria-label="服务器纳管总览">
    <div><h2 className="text-lg font-semibold">服务器纳管总览</h2>
      {!!scopes.length&&<details className="text-xs text-muted-foreground mt-1"><summary className="cursor-pointer">查看各 ECS 租户 / 地域采集时间</summary>{scopes.map((scope,i)=><p key={i}>{scope.tenantName||scope.tenantId} · {scope.region||'地域未知'} · {time(scope.collectedAt)} · {scope.complete===false?'采集不完整':'采集状态以快照为准'}</p>)}</details>}
    </div>
    <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
      <Donut title="JumpServer 已纳管服务器" items={loginItems} counts={counts(loginItems,jump.map(r=>r.login))} selected={login} onSelect={key=>{setLogin(login===key?null:key);setLocation(null);setFolidevOnly(false);}} />
      <Donut title="已纳管服务器的 ECS 状态" description={login?`当前范围：${nameOf(loginItems,login)}`:undefined} items={locationItems} counts={counts(locationItems,loginRows.map(r=>r.location))} selected={location} onSelect={key=>setLocation(location===key?null:key)} />
    </div>
    {login==='ordinary'&&<p className="text-sm">仅普通账号中，登录账号为 folidev：{loginRows.filter(row=>row.asset.inspection?.account==='folidev').length} 台。 <label><input type="checkbox" checked={folidevOnly} onChange={e=>setFolidevOnly(e.target.checked)}/> 明细仅看 folidev</label></p>}
    {(login||location)&&<div className="space-y-2"><div className="flex flex-wrap justify-between gap-2"><h3 className="font-medium">JumpServer 明细 · {jumpRows.length} 台</h3><div className="flex gap-3">{(login==='cannot_login'||login==='ordinary')&&<button type="button" className="text-sm underline" disabled={!jumpRows.length} onClick={()=>downloadOverviewCsv(jumpRows,login==='ordinary',location==='not_in_ecs')}>导出 CSV</button>}<button type="button" className="text-sm underline" onClick={()=>{setLogin(null);setLocation(null);setFolidevOnly(false);}}>清除筛选</button></div></div>
      <div className="overflow-auto max-h-96"><table className="w-full text-sm text-left"><thead><tr>{(hideLogin?['IP / 主机名','ECS 状态与关联']:['IP / 主机名','登录 / 提权','ECS 状态与关联','登录原因','检查时间']).map(h=><th key={h} className="p-2">{h}</th>)}</tr></thead><tbody>{jumpRows.map(row=><tr key={row.asset.id} className="border-t align-top"><td className="p-2">{row.asset.ip||'—'}{row.asset.hostname&&<><br/><span className="text-muted-foreground">{row.asset.hostname}</span></>}</td>{!hideLogin&&<td className="p-2">{nameOf(loginItems,row.login)}{login!=='cannot_login'&&<><br/><span className="text-muted-foreground">账号：{row.asset.inspection?.account||'—'} · 提权：{row.asset.inspection?.sudoStatus||'未知'}</span></>}</td>}<td className="p-2">{nameOf(locationItems,row.location)}{!((row.location==='not_in_ecs'&&(login==='cannot_login'||location==='not_in_ecs'))||(login==='ordinary'&&row.location==='local'))&&<><br/><span className="text-muted-foreground">{row.instance?`${row.instance.name} / ${row.instance.id}`:row.evidence}{row.location==='ambiguous'&&row.candidates.length>0?` · ECS 候选：${row.candidates.map(c=>`${c.tenantName||c.tenantId||'历史租户'} / ${c.id}`).join('、')}`:''}</span></>}</td>{!hideExtra&&<><td className="p-2">{row.asset.inspection?.reason||row.asset.inspection?.warnings?.join('；')||'—'}</td><td className="p-2 whitespace-nowrap">{time(row.asset.inspection?.checkedAt)}</td></>}</tr>)}</tbody></table>{!jumpRows.length&&<p className="p-3">当前筛选没有服务器。</p>}</div>
    </div>}
    <div className="border-t pt-5 space-y-3"><div className="flex flex-wrap items-center justify-between gap-3"><h3 className="font-semibold">阿里云 ECS 纳管覆盖</h3><label className="text-sm">实例状态 <select className="border rounded p-2" value={status} onChange={e=>setStatus(e.target.value as EcsStatus|'all')}><option value="all">全部</option>{Object.entries(statusLabels).map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label></div>
      <Donut title="ECS 纳管覆盖" description={status==='all'?undefined:`当前范围：${statusLabels[status]}`} items={coverageItems} counts={counts(coverageItems,ecsScope.map(r=>r.coverage))} selected={coverage} onSelect={key=>setCoverage(coverage===key?null:key)} />
      {(coverage||status!=='all')&&<div className="space-y-2"><div className="flex justify-between"><h4 className="font-medium">ECS 明细 · {ecsRows.length} 台</h4><label className="text-sm">ECS 状态 <select className="border rounded p-2" value={status} onChange={e=>setStatus(e.target.value as EcsStatus|'all')}><option value="all">全部</option>{Object.entries(statusLabels).map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label><button type="button" className="text-sm underline" onClick={()=>{setCoverage(null);setStatus('all');}}>清除筛选</button></div><div className="overflow-auto max-h-96"><table className="w-full text-sm text-left"><thead><tr>{['实例 / 租户','IP','ECS 状态','纳管 / 登录'].map(h=><th className="p-2" key={h}>{h}</th>)}</tr></thead><tbody>{ecsRows.map((row,i)=><tr key={row.instance.resourceKey||`${row.instance.tenantId||'legacy'}:${row.instance.region}:${row.instance.id}:${i}`} className="border-t align-top"><td className="p-2">{row.instance.name}<br/><span className="text-muted-foreground">{row.instance.tenantName||row.instance.tenantId||'历史租户'} · {row.instance.id}</span></td><td className="p-2">{[...row.instance.privateIps,...row.instance.publicIps].join('、')||'—'}</td><td className="p-2">{statusLabels[row.status]}{row.instance.status&&row.status==='status_unknown'?`（${row.instance.status}）`:''}</td><td className="p-2">{nameOf(coverageItems,row.coverage)}{row.asset&&<> · {nameOf(loginItems,jump.find(j=>j.asset.id===row.asset?.id)?.login||'unchecked')}<br/><span className="text-muted-foreground">{row.asset.ip} · {row.asset.hostname}</span></>}</td></tr>)}</tbody></table>{!ecsRows.length&&<p className="p-3">当前筛选没有 ECS 实例。</p>}</div></div>}
    </div>
  </section>;
}

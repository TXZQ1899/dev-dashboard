'use client';
import { useEffect, useRef, useState } from 'react';
import { formatBeijingTime } from '@/lib/datetime';

type Item = {name: string; status: string; reason?: string; details?: string[][]};
type Node = {instanceId: string; ip: string | null; pid: number | null; status: string; reason?: string};
type Job = {id: string; unitName?: string; environment: string; sourceVersion: string; status: string; currentIndex?: number | null; items: Item[]; nodes: Node[]; events: {at: string; item: number; message: string}[]; error?: string};
type State = {job: Job; reportReady?: boolean};
const status: Record<string, string> = {pending:'待巡检', running:'巡检中', complete:'已完成', partial:'部分完成', failed:'失败', unresolved:'待确认', unknown:'未确认', cancelled:'已取消'};
const envLabels: Record<string, string> = {PRODUCT:'生产环境', TEST:'测试环境', SIMULATION:'仿真环境'};

export function EnvironmentInspection({ unitId, environment }: {unitId: string; environment: string}) {
  const started = useRef(false);
  const [state, setState] = useState<State | null>(null);
  const [error, setError] = useState('');
  const key = `environment-check:${unitId}:${environment}`;
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    async function open() {
      try {
        const existing = sessionStorage.getItem(key);
        const response = existing ? await fetch(`/api/catalog/environment-check?id=${encodeURIComponent(existing)}`, {cache:'no-store'}) :
          await fetch('/api/catalog/environment-check', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({unitId, environment})});
        const body = await response.json() as State & {error?: string};
        if (!response.ok) throw new Error(body.error || '无法启动巡检');
        sessionStorage.setItem(key, body.job.id);
        setState(body);
      } catch (e) { setError(e instanceof Error ? e.message : '无法启动巡检'); }
    }
    void open();
  }, [unitId, environment, key]);
  useEffect(() => {
    if (!state?.job || state.job.status !== 'running') return;
    let live = true;
    const timer = setInterval(() => {
      fetch(`/api/catalog/environment-check?id=${encodeURIComponent(state.job.id)}`, {cache:'no-store'})
        .then(async response => {const body = await response.json() as State & {error?: string}; if (!response.ok) throw new Error(body.error || '读取进度失败'); return body;})
        .then(body => {if (live) {setState(body); setError('');}})
        .catch(e => {if (live) setError(e instanceof Error ? e.message : '读取进度失败');});
    }, 2000);
    return () => {live = false; clearInterval(timer);};
  }, [state?.job?.id, state?.job?.status]);
  const job = state?.job;
  const logs = job?.events.filter(event => event.item === job.currentIndex) || [];
  return <section className="application-check-panel" aria-label="环境巡检">
    <div className="check-heading"><div><h2>环境巡检{job?.unitName ? ` · ${job.unitName}` : ''}</h2><p>{envLabels[environment] || environment} · 逐项检查，TCP 对各已确认进程采样 300 秒</p></div>
      {state?.reportReady && <a className="catalog-config-button" href={`/api/catalog/environment-check/download?id=${encodeURIComponent(job!.id)}`}>下载 Excel 巡检报告</a>}</div>
    {error && <p role="alert" className="check-error">{error}</p>}
    {!job ? !error && <p>正在启动巡检…</p> : <>
      <p className="check-note">状态：{status[job.status] || job.status} · 来源版本：{job.sourceVersion}。无法确认进程的实例保留为待确认。</p>
      <div className="overflow-auto"><table className="w-full text-sm text-left"><thead><tr><th>巡检内容</th><th>状态</th><th>说明</th></tr></thead><tbody>
        {job.items.map(item => <tr key={item.name} className="border-t"><td>{item.name}</td><td>{status[item.status] || item.status}</td><td>{item.reason || item.details?.map(row => `${row[0]}：${row[2] || row[1]}`).join('；') || '—'}</td></tr>)}
      </tbody></table></div>
      <details open><summary>本环境实例（{job.nodes.length}）</summary>{job.nodes.map(node => <p key={node.instanceId}>{node.ip || '未提供 IP'} · PID {node.pid ?? '待确认'} · {status[node.status] || node.status}{node.reason ? ` · ${node.reason}` : ''}</p>)}</details>
      <h3>当前巡检日志</h3><div className="catalog-message" aria-live="polite" style={{display:'block', maxHeight:300, overflowY:'auto'}}>
        {logs.length ? logs.map((event, index) => <div key={index}>{formatBeijingTime(event.at, '—')} {event.message}</div>) : <p>{job.status === 'running' ? '等待巡检开始…' : '巡检已结束。'}</p>}
      </div>
      {job.error && <p role="alert" className="check-error">{job.error}</p>}
    </>}
  </section>;
}

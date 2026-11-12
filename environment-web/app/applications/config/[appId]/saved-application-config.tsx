'use client';

import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { formatBeijingTime } from '@/lib/datetime';

type File = { id: string; group: string; source: string; path: string; file: string; size: number; namespace: string; nacosGroup: string; profile: string };
type Snapshot = { name: string; appId: string; status: string; snapshotVersion: string; branch: string; module: string; modifiedAt: number; warnings: string[]; notes: string[]; files: File[] };
type Content = { content: string; truncated: boolean };
type Job = { id?: string; status: string; appId?: string; startedAt?: string; finishedAt?: string; error?: string; current?: { phase?: string }; result?: { error?: string; reason?: string } };
type JobState = { job: Job; batchStatus: string };
const groups = ['dev', 'test', 'uat', 'prod', 'other'];
const labels: Record<string, string> = { dev: 'DEV', test: 'TEST', uat: 'UAT', prod: 'PROD', other: '公共 / 其他' };
async function read<T>(response: Response): Promise<T> {
  const data: unknown = await response.json();
  if (!response.ok) throw new Error(data && typeof data === 'object' && 'error' in data && typeof data.error === 'string' ? data.error : '读取失败');
  return data as T;
}

export function SavedApplicationConfig({ appId }: { appId: string }) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState('');
  const [tab, setTab] = useState('');
  const [contents, setContents] = useState<Record<string, Content>>({});
  const [loading, setLoading] = useState<Record<string, boolean>>({});
  const [fileErrors, setFileErrors] = useState<Record<string, string>>({});
  const [jobState, setJobState] = useState<JobState | null>(null);
  const [jobError, setJobError] = useState('');
  const [starting, setStarting] = useState(false);
  const [revision, setRevision] = useState(0);
  const finished = useRef('');
  useEffect(() => {
    const controller = new AbortController();
    fetch(`/api/catalog/application-config?appId=${encodeURIComponent(appId)}`, { cache: 'no-store', signal: controller.signal })
      .then(read<Snapshot>).then((value) => { if (!controller.signal.aborted) { setSnapshot(value); setError(''); setContents({}); setFileErrors({}); setTab(groups.find((group) => value.files.some((f) => f.group === group)) || 'other'); } })
      .catch((err: unknown) => { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : '读取失败'); });
    return () => controller.abort();
  }, [appId, revision]);
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const state = await fetch('/api/catalog/application-config/refresh', { cache: 'no-store' }).then(read<JobState>);
        if (cancelled) return;
        setJobState(state);
        setJobError((previous) => previous === '无法读取刷新状态，请稍后重试。' ? '' : previous);
        const job = state.job;
        const key = `${job.id || job.startedAt || ''}:${job.finishedAt || ''}`;
        if (job.appId === appId && job.finishedAt && finished.current !== key) {
          finished.current = key;
          if (job.status === 'ready' || job.status === 'partial') setRevision((n) => n + 1);
        }
      } catch { if (!cancelled) setJobError('无法读取刷新状态，请稍后重试。'); }
      if (!cancelled) timer = setTimeout(poll, 2000);
    }
    void poll();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [appId]);
  async function refresh() {
    setStarting(true); setJobError('');
    try {
      const response = await fetch('/api/catalog/application-config/refresh', { method:'POST', headers:{ 'Content-Type':'application/json' }, body:JSON.stringify({ appId }) });
      const value = await read<{job: Job}>(response);
      setJobState((prev) => ({ job:value.job, batchStatus:prev?.batchStatus || 'idle' }));
    } catch (err) { setJobError(err instanceof Error ? err.message : '刷新失败'); }
    finally { setStarting(false); }
  }
  async function load(file: File) {
    if (contents[file.id] || loading[file.id]) return;
    setLoading((prev) => ({ ...prev, [file.id]: true }));
    setFileErrors((prev) => ({ ...prev, [file.id]: '' }));
    try {
      const params = new URLSearchParams({ appId, file: file.id });
      const value = await fetch(`/api/catalog/application-config?${params}`, { cache: 'no-store' }).then(read<Content>);
      setContents((prev) => ({ ...prev, [file.id]: value }));
    } catch (err) { setFileErrors((prev) => ({ ...prev, [file.id]: err instanceof Error ? err.message : '读取失败' })); }
    finally { setLoading((prev) => ({ ...prev, [file.id]: false })); }
  }
  return <section className="check-files" aria-label="应用配置文件">
    <div className="check-heading"><h1>应用配置{snapshot ? ` · ${snapshot.name}` : ''}</h1><Button variant="outline" onClick={() => void refresh()} disabled={!jobState || starting || ['queued', 'running'].includes(jobState.job.status) || ['queued', 'running'].includes(jobState.batchStatus)}>{starting || (jobState?.job.appId === appId && ['queued', 'running'].includes(jobState.job.status)) ? '正在刷新…' : '刷新此应用配置'}</Button></div>
    {jobError && <p className="check-error" role="alert">{jobError}</p>}
    {jobState && ['queued', 'running'].includes(jobState.batchStatus) && <p className="check-note">全量配置同步进行中，请完成后刷新此应用。</p>}
    {jobState && jobState.job.appId !== appId && ['queued', 'running'].includes(jobState.job.status) && <p className="check-note">另一个应用的配置同步进行中，请完成后重试。</p>}
    {jobState?.job.appId === appId && <p className="check-note">刷新状态：{({ queued:'排队中', running:'进行中', ready:'完成', partial:'部分完成', failed:'失败', skipped:'跳过' } as Record<string,string>)[jobState.job.status] || jobState.job.status}{jobState.job.current?.phase ? ` · ${jobState.job.current.phase}` : ''}{jobState.job.error || jobState.job.result?.error || jobState.job.result?.reason ? ` · ${jobState.job.error || jobState.job.result?.error || jobState.job.result?.reason}` : ''}</p>}
    {error && <p className="check-error" role="alert">{error}</p>}
    {!snapshot && !error && <output>正在读取应用配置…</output>}
    {snapshot && <>
      <p className="check-note">应用 ID：{snapshot.appId} · 保存时间：{formatBeijingTime(snapshot.modifiedAt * 1000, '—')} · 应用快照：{snapshot.snapshotVersion || '未知'} · 分支：{snapshot.branch || '未知'} · Maven 模块：{snapshot.module || '未知'}</p>
      <p className="check-note">展示最近一次成功保存的仓库及 Nacos 配置，按文件所属 profile 分组；不代表当前运行实例的生效配置。密码明文显示，常见令牌和私钥会遮盖。</p>
      {snapshot.status === 'partial' && <p className="check-note">本次保存部分完成，请查看下方说明。</p>}
      {[...snapshot.warnings, ...snapshot.notes].map((item, i) => <p className="check-note" key={i}>{item}</p>)}
      <Tabs value={tab} onValueChange={(value) => setTab(value ?? '')} className="check-config-tabs">
        <div className="check-config-tab-scroll"><TabsList aria-label="配置文件 profile" variant="line">
          {groups.map((group) => <TabsTrigger key={group} value={group}>{labels[group]}（{snapshot.files.filter((f) => f.group === group).length}）</TabsTrigger>)}
        </TabsList></div>
        {groups.map((group) => <TabsContent value={group} key={group}>
          {!snapshot.files.some((f) => f.group === group) && <p>该分类暂无配置文件。</p>}
          <div className="check-config-list">{snapshot.files.filter((f) => f.group === group).map((file) => <details className="check-file" key={file.id} onToggle={(event) => { if (event.currentTarget.open) void load(file); }}>
            <summary>{file.path}<small>{file.source === 'nacos' ? `Nacos · namespace: ${file.namespace || '默认'} · Group: ${file.nacosGroup || '默认'}` : '仓库'} · {file.size} 字节 · 保存路径：{file.file}</small></summary>
            {loading[file.id] && <output>正在读取配置内容…</output>}
            {fileErrors[file.id] && <p role="alert">{fileErrors[file.id]} <button type="button" onClick={() => void load(file)}>重试</button></p>}
            {contents[file.id] && <><pre>{contents[file.id].content || '文件为空'}</pre>{contents[file.id].truncated && <p>文件较大，仅展示前 256 KiB 的内容。</p>}</>}
          </details>)}</div>
        </TabsContent>)}
      </Tabs>
    </>}
  </section>;
}

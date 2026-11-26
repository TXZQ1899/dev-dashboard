'use client';
import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { formatBeijingTime } from '@/lib/datetime';

type RssPart = { key: string; label: string; bytes: number; percent: number; basis: 'measured' | 'estimated' | 'derived' };
type Gc = { heapUsedBytes?: number; heapCommittedBytes?: number; youngUsedBytes?: number; oldUsedBytes?: number;
  metaspaceCommittedBytes?: number; classSpaceCommittedBytes?: number; youngCollections?: number; youngTimeSeconds?: number;
  fullCollections?: number; fullTimeSeconds?: number; totalTimeSeconds?: number };
type DumpMode = 'all' | 'live';
type DumpGc = { before: Gc; after: Gc; fullCollectionsDelta?: number; fullTimeDeltaSeconds?: number;
  status: 'single_full_gc' | 'multiple_full_gc' | 'no_full_gc' | 'unavailable' | 'unverified_process' };
type HeapRecommendation = { taskId?: string; finishedAt?: string; xmsBytes: number; xmxBytes: number;
  currentXmsBytes: number; currentXmxBytes: number; beforeBytes: number; afterBytes: number; stepBytes: number };
type Report = {
  status: string; summary?: string; startedAt?: string; finishedAt?: string;
  target?: { assetId?: string; serverIp?: string; pid?: number; processName?: string; javaHome?: string; exe?: string; command?: string };
  dump?: { mode?: DumpMode; dumpBytes?: number; compressedBytes?: number; sha256?: string; archivePath?: string;
    application?: { appId?: string | null; appName?: string | null; confidence?: string } };
  heap?: { usedBytes?: number | null; objects?: number | null; classes?: number | null; classLoaders?: number | null;
    gcRoots?: number | null; fileLength?: number | null; jvmVersion?: string | null };
  runtime?: { threads?: number; rssBytes?: number; rssAnonBytes?: number; stackBytes?: number; gc?: Gc; dumpGc?: DumpGc;
    flags?: Record<string, number | boolean> };
  rss?: RssPart[];
  topComponents?: { name: string; percent: number }[];
  suspects?: { title: string; detail: string }[];
  reports?: Record<string, { name: string; file: string; size: number }>;
  warnings?: string[];
  error?: { stage: string; code: string; message: string } | null;
};
type Job = { id: string; status: string; phase: string; startedAt: string; updatedAt?: string; finishedAt?: string;
  assetId?: string; serverIp?: string; pid?: number; processName?: string; processStartedAt?: string;
  dumpMode?: DumpMode; progress?: { transferred?: number; total?: number };
  events?: { at: string; message: string }[]; error?: string };
type State = { job: Job | null; report: Report | null; recommendation?: HeapRecommendation | null; historical?: boolean };

const api = '/api/settings/application-jvm';
const basisLabels = { measured: '实测', estimated: '估算', derived: '差额推算' };
const statusLabels: Record<string, string> = { running: '分析中', complete: '已完成', cancelled: '已取消', failed: '失败' };
const MiB = 1024 ** 2;
const GiB = 1024 ** 3;

function size(bytes?: number | null) {
  if (bytes === undefined || bytes === null) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KiB`;
  if (bytes < 1073741824) return `${(bytes / 1048576).toFixed(1)} MiB`;
  return `${(bytes / 1073741824).toFixed(2)} GiB`;
}
function percent(part?: number, total?: number) { return part !== undefined && total ? `${(part * 100 / total).toFixed(1)}%` : '—'; }
function elapsed(start?: string, end?: number) {
  const value = start ? Date.parse(start) : NaN;
  if (!Number.isFinite(value) || typeof end !== 'number' || !Number.isFinite(end)) return null;
  return Math.max(0, Math.floor((end - value) / 1000));
}

function jvmSizeArgument(bytes: number) {
  if (bytes % GiB === 0) return `${bytes / GiB}G`;
  if (bytes % MiB === 0) return `${bytes / MiB}M`;
  return String(bytes);
}

function keepRecommendation(next: State, previous: State): State {
  const job = next.job;
  if (next.recommendation || !job?.processStartedAt || job.assetId !== previous.job?.assetId || job.pid !== previous.job?.pid ||
    job.processStartedAt !== previous.job?.processStartedAt) return next;
  return { ...next, recommendation: previous.recommendation };
}

function HeapSizing({ runtime, recommendation, jobId }: { runtime?: Report['runtime']; recommendation?: HeapRecommendation | null; jobId?: string }) {
  const flags = runtime?.flags || {};
  const xms = typeof flags.InitialHeapSize === 'number' && flags.InitialHeapSize > 0 ? flags.InitialHeapSize : undefined;
  const xmx = typeof flags.MaxHeapSize === 'number' && flags.MaxHeapSize > 0 ? flags.MaxHeapSize : undefined;
  const evidence = runtime?.dumpGc;
  const before = evidence ? evidence.before : runtime?.gc;
  const after = evidence?.after;
  const canShrink = recommendation && (recommendation.xmsBytes < recommendation.currentXmsBytes || recommendation.xmxBytes < recommendation.currentXmxBytes);
  const collector = flags.UseParallelGC ? 'Parallel GC' : flags.UseG1GC ? 'G1 GC' : flags.UseConcMarkSweepGC ? 'CMS' : '—';
  const statuses = {
    single_full_gc: '采样期间观察到 1 次 Full GC。转储后采样可能含少量新分配。',
    multiple_full_gc: '采样期间观察到多次 Full GC，前后差值包含整个区间。',
    no_full_gc: '采样期间 Full GC 计数未增加，无法确认回收后的占用。',
    unavailable: 'Full GC 计数缺失或不可比较。',
    unverified_process: '无法确认前后样本来自同一进程。',
  };

  return <section aria-label="堆缩容评估">
    <h3>堆缩容评估</h3>
    <div className="check-live-metrics">
      <div><span>生效 Xms</span><strong>{size(xms)}</strong></div>
      <div><span>生效 Xmx</span><strong>{size(xmx)}</strong></div>
      <div><span>GC 收集器</span><strong>{collector}</strong></div>
    </div>
    {evidence ? <>
      <p className="check-note">{statuses[evidence.status]}
        {evidence.fullTimeDeltaSeconds !== undefined && ` 区间 Full GC 累计耗时增加 ${evidence.fullTimeDeltaSeconds.toFixed(3)} 秒。`}</p>
      <div className="check-flows"><table aria-label="转储前后堆用量">
        <thead><tr><th>指标</th><th>转储前</th><th>转储后</th></tr></thead>
        <tbody>{(['heapUsedBytes', 'oldUsedBytes', 'youngUsedBytes', 'heapCommittedBytes'] as const).map((metric, index) => <tr key={metric}>
          <td>{['堆已用', 'Old 已用', 'Young 已用', '堆已提交'][index]}</td>
          <td>{size(before?.[metric])}</td><td>{size(after?.[metric])}</td>
        </tr>)}</tbody>
      </table></div>
    </> : <p className="check-note">当前堆采样 {size(before?.heapUsedBytes)}，Old {size(before?.oldUsedBytes)}。未采集 Full GC 前后用量。</p>}
    <p><strong>拟调整</strong>{canShrink ? <> <code>-Xms{jvmSizeArgument(recommendation.xmsBytes)} -Xmx{jvmSizeArgument(recommendation.xmxBytes)}</code>（灰度建议）</>
      : <>：{recommendation ? '按当前规则没有更小的候选值。' : '缺少可比较的 Full GC 前后用量或生效配置，暂不生成建议。'}</>}</p>
    {canShrink && <div className="check-live-metrics">
      <div><span>拟调整 Xms（MiB）</span><strong>{recommendation.xmsBytes / MiB}</strong></div>
      <div><span>拟调整 Xmx（MiB）</span><strong>{recommendation.xmxBytes / MiB}</strong></div>
    </div>}
    {recommendation && <>
      <p className="check-note">{recommendation.taskId === jobId ? '建议生成于' : '上次有效评估'} {formatBeijingTime(recommendation.finishedAt, '—')}。</p>
      <details>
        <summary>建议值怎么算</summary>
        <p className="check-note">使用整个堆的转储前用量 {size(recommendation.beforeBytes)} 和 Full GC 后用量 {size(recommendation.afterBytes)}。按 {size(recommendation.stepBytes)} 的档位向上取整。</p>
        <p><code>Xms 候选 = 向上取整(max(回收前堆已用, 2 × 回收后堆已用))</code></p>
        <p><code>Xmx 候选 = 向上取整(max(2 × 回收前堆已用, 3 × 回收后堆已用))</code></p>
        <p className="check-note">算出的值高于当前配置时保留当前值，Xms 同时受拟调整 Xmx 限制。倍率是给这次采样预留的余量，业务峰值需要灰度验证。</p>
      </details>
    </>}
    <p className="check-note">单次采样无法覆盖业务高峰。灰度时请对比 RSS、GC 停顿、接口延迟和定时任务。</p>
  </section>;
}

export function ApplicationJvm({ asset, pid, readOnly = false }: { asset: string; pid: number; readOnly?: boolean }) {
  const [state, setState] = useState<State>({ job: null, report: null });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [clock, setClock] = useState<number | null>(null);
  const [syncedAt, setSyncedAt] = useState<number | null>(null);
  const historical = readOnly || state.historical;
  const running = !historical && state.job?.status === 'running';
  const jobId = state.job?.id;

  useEffect(() => {
    let live = true;
    const query = new URLSearchParams({ asset, pid: String(pid) });
    if (readOnly) query.set('history', '1');
    void fetch(`${api}?${query}`, { cache: 'no-store' }).then(async (response) => {
      if (!response.ok) throw new Error('读取 JVM 分析记录失败');
      return await response.json() as State;
    }).then((result) => {
      if (live && (!result.job || (result.job.assetId === asset && result.job.pid === pid))) {
        setState(previous => previous.job?.status === 'running' && previous.job.assetId === asset && previous.job.pid === pid
          ? keepRecommendation(previous, result) : result);
        setSyncedAt(Date.now());
      }
    }).catch(() => { if (live) setError('读取 JVM 分析记录失败'); });
    return () => { live = false; };
  }, [asset, pid, readOnly]);

  useEffect(() => {
    if (!running || !jobId) return;
    let live = true;
    let timer: ReturnType<typeof setTimeout>;
    const id = jobId;
    async function poll() {
      try {
        const response = await fetch(api + '?id=' + encodeURIComponent(id), { cache: 'no-store', signal: AbortSignal.timeout(10000) });
        const result = await response.json() as State;
        if (!response.ok) throw new Error((result as unknown as { error?: string }).error || '读取分析进度失败');
        if (live) { setState(previous => keepRecommendation(result, previous)); setError(''); setSyncedAt(Date.now()); }
      } catch (e) {
        if (live) setError(e instanceof Error ? e.message : '读取分析进度失败');
      }
      if (live) timer = setTimeout(poll, 3000);
    }
    timer = setTimeout(poll, 1000);
    return () => { live = false; clearTimeout(timer); };
  }, [running, jobId]);

  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => setClock(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [jobId, running]);

  async function submit(cancel = false, dumpMode: DumpMode = 'all') {
    const live = !cancel && dumpMode === 'live';
    if (live && !window.confirm(`PID ${pid} 将执行 live 堆转储，请求 Full GC 并暂停业务线程。请确认当前允许停顿。`)) return;
    setBusy(true); setError('');
    try {
      const response = await fetch(api + (cancel ? '/cancel' : ''), {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(cancel ? { id: state.job?.id } : { asset, pid, dumpMode, confirmFullGc: live }),
      });
      const result = await response.json() as State & { error?: string };
      if (!response.ok) throw new Error(result.error || 'JVM 分析任务启动失败');
      setState(previous => keepRecommendation(result, previous)); setSyncedAt(Date.now());
    } catch (e) { setError(e instanceof Error ? e.message : '请求失败'); }
    finally { setBusy(false); }
  }

  const report = state.report;
  const runtime = report?.runtime;
  const gc = runtime?.gc;
  const heap = report?.heap;
  const finished = Date.parse(state.job?.finishedAt || state.job?.updatedAt || '');
  const spent = elapsed(state.job?.startedAt, running ? clock ?? undefined : (Number.isFinite(finished) ? finished : undefined));
  const transferred = state.job?.progress?.transferred;
  const total = state.job?.progress?.total;
  return <section aria-label="JVM 堆分析">
    {historical ? <p className="check-note">PID {pid} 的历史分析，仅供查看。{syncedAt !== null && !state.job && ' 未找到已保存的报告。'}</p> : <>
      <div className="check-controls">
        <Button onClick={() => void submit()} disabled={busy || running}>普通堆转储</Button>
        <Button variant="outline" onClick={() => void submit(false, 'live')} disabled={busy || running}>Full GC 后堆转储（live）</Button>
        {running && <Button variant="outline" onClick={() => void submit(true)} disabled={busy}>取消分析</Button>}
      </div>
      <p className="check-note">普通转储保留待回收对象，live 转储请求 Full GC 并采集前后堆用量。两种方式都会暂停进程，校验通过的转储保存在数据卷。</p>
    </>}
    {error && <p role="alert" className="check-error">{error}</p>}
    {state.job && <section className="check-live" aria-label="JVM 分析进度">
      <div className="check-live-heading">
        <h3>{state.job.dumpMode === 'live' ? 'Full GC 后堆转储' : 'JVM 堆分析'}</h3>
        <span className="check-live-status" data-running={running}><i aria-hidden="true" />{statusLabels[state.job.status] || state.job.status}</span>
        <span className="check-live-elapsed">任务耗时 {spent ?? '—'} 秒</span>
      </div>
      <output className="check-live-phase">{state.job.phase}</output>
      <p className="check-note">{state.job.serverIp} · PID {state.job.pid} · {state.job.processName || 'JVM 进程'} · 开始于 {formatBeijingTime(state.job.startedAt, '—')}</p>
      {transferred !== undefined && total ? <div className="check-live-sampling">
        <div><span>堆转储传输 {size(transferred)} / {size(total)}</span><span>{percent(transferred, total)}</span></div>
        <progress aria-label="堆转储传输进度" value={transferred} max={total} />
      </div> : null}
      {running && <p className="check-note">每 3 秒读取分析进度；MAT 解析大堆可能持续数分钟。最近同步：{formatBeijingTime(syncedAt ? new Date(syncedAt).toISOString() : '', '等待同步')}</p>}
    </section>}
    {report?.error && <div role="alert" className="check-error">
      <p>失败阶段：{report.error.stage} · 错误代码：{report.error.code}</p>
      <p>{report.error.message}</p>
    </div>}
    {report?.dump?.archivePath && <p className="check-note">堆转储归档：<code>{report.dump.archivePath}</code>
      {report.dump.application?.confidence !== 'INFERRED' && report.dump.application?.confidence !== 'EXACT' ? '（应用未能唯一识别）' : ''}</p>}
    {(runtime?.gc || runtime?.dumpGc || runtime?.flags || state.recommendation) &&
      <HeapSizing runtime={runtime} recommendation={state.recommendation} jobId={jobId} />}
    {report && report.status !== 'failed' && <>
      {report.summary && <p><strong>{report.summary}</strong></p>}
      <p className="check-note">目标：{report.target?.serverIp} · PID {report.target?.pid} · {report.target?.processName || 'JVM 进程'}
        {report.target?.javaHome ? ` · JDK ${report.target.javaHome}` : ''}。分析完成于 {formatBeijingTime(report.finishedAt, '—')}。</p>
      <div className="check-live-metrics">
        <div><span>MAT 纳入分析的对象</span><strong>{heap?.objects?.toLocaleString() ?? '—'} <small>个</small></strong></div>
        <div><span>MAT 纳入分析的堆用量</span><strong>{size(heap?.usedBytes)}</strong></div>
        <div><span>类 / 类加载器</span><strong>{heap?.classes ?? '—'} / {heap?.classLoaders ?? '—'}</strong></div>
        <div><span>GC 根</span><strong>{heap?.gcRoots ?? '—'}</strong></div>
      </div>
      <div className="check-live-metrics">
        <div><span>进程 RSS</span><strong>{size(runtime?.rssBytes)}</strong></div>
        <div><span>堆已提交</span><strong>{size(gc?.heapCommittedBytes)}</strong></div>
        <div><span>堆实际使用</span><strong>{size(gc?.heapUsedBytes)} <small>{percent(gc?.heapUsedBytes, gc?.heapCommittedBytes)}</small></strong></div>
        <div><span>线程数</span><strong>{runtime?.threads ?? '—'} <small>栈按 {size(runtime?.stackBytes)}/线程估算</small></strong></div>
        <div><span>Metaspace 已提交</span><strong>{size(gc?.metaspaceCommittedBytes)}</strong></div>
        <div><span>Young / Old 使用</span><strong>{size(gc?.youngUsedBytes)} / {size(gc?.oldUsedBytes)}</strong></div>
        <div><span>YGC / FGC</span><strong>{gc?.youngCollections ?? '—'} / {gc?.fullCollections ?? '—'}</strong></div>
        <div><span>GC 累计耗时</span><strong>{gc?.totalTimeSeconds ?? '—'} <small>秒</small></strong></div>
      </div>
      <h3>RSS 与 JVM 内存参考</h3>
      {report.rss?.length ? <>
        <p className="check-note">RSS 是进程驻留内存。堆与 Metaspace 显示 JVM 已提交容量，线程栈按线程数 × Xss 估算。差额仅供参考，无法据此确认各项实际驻留量。</p>
        <div className="check-flows"><table aria-label="RSS 构成">
          <thead><tr><th>构成</th><th>大小</th><th>占 RSS</th><th>口径</th></tr></thead>
          <tbody>{report.rss.map((part) => <tr key={part.key}>
            <td>{part.label}</td><td>{size(part.bytes)}</td><td>{part.percent}%</td><td>{basisLabels[part.basis]}</td>
          </tr>)}</tbody>
        </table></div>
      </> : <p>未能读取 jstat 指标，无法拆分 RSS 构成。</p>}
      {!!report.suspects?.length && <><h3>MAT 泄漏嫌疑</h3>
        {report.suspects.map((suspect, index) => <details key={index} open={index === 0}>
          <summary>{suspect.title}</summary><p>{suspect.detail}</p>
        </details>)}
      </>}
      {!!report.topComponents?.length && <><h3>TOP 组件（按支配占用）</h3>
        <div className="check-flows"><table aria-label="TOP 组件">
          <thead><tr><th>组件</th><th>占比</th></tr></thead>
          <tbody>{report.topComponents.map((component, index) => <tr key={index}>
            <td><code>{component.name}</code></td><td>{component.percent}%</td>
          </tr>)}</tbody>
        </table></div>
      </>}
      {!!Object.keys(report.reports || {}).length && <div className="check-controls">
        {Object.entries(report.reports || {}).map(([doc, entry]) => <a className="check-download" key={doc}
          href={api + '/report?id=' + encodeURIComponent(state.job?.id || '') + '&doc=' + doc}>
          下载 MAT {entry.name === 'System_Overview' ? '系统概览' : entry.name === 'Leak_Suspects' ? '泄漏嫌疑' : 'TOP 组件'}报告（{size(entry.size)}）
        </a>)}
      </div>}
      {!!report.warnings?.length && report.warnings.map((warning, index) => <p className="check-note" key={index}>{warning}</p>)}
      <details><summary>转储与分析明细</summary>
        <p>转储大小：{size(report.dump?.dumpBytes)} · 压缩后：{size(report.dump?.compressedBytes)} · SHA-256：<code>{report.dump?.sha256 || '—'}</code></p>
        <p>JVM 版本：{heap?.jvmVersion || '—'} · MAT 读取的堆文件长度：{report.dump?.dumpBytes ?? '—'} 字节</p>
        {report.target?.command && <p><code>{report.target.command}</code></p>}
      </details>
    </>}
  </section>;
}

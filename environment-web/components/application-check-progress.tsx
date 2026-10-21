'use client';
import { useEffect, useState } from 'react';
import type { ApplicationCheckState } from '@/lib/application-check';
import { formatBeijingTime, formatSnapshotId } from '@/lib/datetime';

type CheckJob = NonNullable<ApplicationCheckState['job']>;
const finishedStages = new Set(['complete', 'partial', 'failed', 'cancelled']);
const statusLabels: Record<string, string> = {
  running: '采集中', complete: '已完成', partial: '部分完成', failed: '失败', cancelled: '已取消',
};

function secondsSince(start: string | undefined, end: number): number | null {
  const value = start ? Date.parse(start) : NaN;
  return Number.isFinite(value) && Number.isFinite(end) ? Math.max(0, Math.floor((end - value) / 1000)) : null;
}

export function ApplicationCheckProgress({ job, lastSyncedAt, syncError }: {
  job: CheckJob; lastSyncedAt: number | null; syncError: string;
}) {
  const [clock, setClock] = useState<number | null>(null);
  const running = job.status === 'running';
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => setClock(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [job.id, running]);
  const now = running ? clock ?? Date.parse(job.updatedAt || job.startedAt) : Date.parse(job.finishedAt || job.updatedAt || job.startedAt);
  const elapsed = secondsSince(job.startedAt, now);
  const progress = job.progress;
  const sampleElapsed = secondsSince(progress?.samplingStartedAt, progress?.samplingFinishedAt ? Date.parse(progress.samplingFinishedAt) : now);
  const activities = job.activities || [];
  const stale = running && lastSyncedAt !== null && now - lastSyncedAt >= 10000;
  return <section className="check-live" aria-label="实时采集进度">
    <div className="check-live-heading">
      <h3>实时采集</h3>
      <span className="check-live-status" data-running={running}><i aria-hidden="true" />{statusLabels[job.status] || job.status}</span>
      <span className="check-live-elapsed">任务耗时 {elapsed ?? '—'} 秒</span>
    </div>
    <output className="check-live-phase">{job.phase}</output>
    <p className="check-note">{job.environment || (running ? '环境识别中' : '部署环境未确认')} · 快照 {formatSnapshotId(job.sourceVersion, 'second', job.sourceVersion || '—')}</p>
    <div className="check-live-metrics">
      <div><span>目标有效采样</span><strong>{progress?.samples ?? '—'} <small>帧</small></strong></div>
      <div><span>已观察上游</span><strong>{progress?.upstream ?? '—'} <small>条连接</small></strong></div>
      <div><span>已观察下游</span><strong>{progress?.downstream ?? '—'} <small>条连接</small></strong></div>
      <div><span>对端服务器</span><strong>{job.activities ? activities.filter((activity) => activity.scope === 'peer').length : '—'} <small>台</small></strong></div>
    </div>
    {sampleElapsed !== null && <div className="check-live-sampling">
      <div><span>采样窗口计时 {sampleElapsed} 秒 / 计划 {job.duration} 秒</span>
        <span>{running && (progress?.samplingFinishedAt || sampleElapsed >= job.duration) ? '等待当前采集与后续核验完成' : ''}</span></div>
      <progress aria-label="采样窗口计时" value={Math.min(sampleElapsed, job.duration)} max={job.duration} />
    </div>}
    <p className="check-note">有效采样与连接数量仅在探针返回后更新，窗口计时不代表有效观察覆盖或任务完成。
      {progress?.lastObservedAt && <> 最近目标观察：{formatBeijingTime(progress.lastObservedAt, '—')}。</>}
    </p>
    {(syncError || stale) && running && <p role="alert" className="check-error">
      进度同步暂时中断，正在重试。下方保留最后收到的采集状态。{syncError && ` ${syncError}`}
    </p>}
    <div className="check-live-activities" aria-label="当前采集活动">
      {activities.length ? activities.map((activity) => {
        const active = running && !finishedStages.has(activity.stage);
        const age = secondsSince(activity.updatedAt, now);
        return <div className="check-live-activity" key={activity.key}>
          <span className="check-live-indicator" data-active={active} data-warning={active && age !== null && age >= 15} aria-hidden="true" />
          <div><strong>{activity.scope === 'target' ? '目标机' : '对端'} <code>{activity.serverIp || '—'}</code></strong>
            <p>{activity.phase}</p>
            <small>{activity.samples !== undefined && `已返回 ${activity.samples} 帧 · `}
              {active ? `此步骤已等待 ${age ?? '—'} 秒` : `更新于 ${formatBeijingTime(activity.updatedAt, '—')}`}
              {active && age !== null && age >= 15 && ' · 较长时间未返回新进度，可继续等待或取消检测'}
            </small></div>
        </div>;
      }) : <p className="check-note">等待采集活动上报；旧任务仅提供阶段信息。</p>}
    </div>
    <details open={running} className="check-live-events">
      <summary>采集活动记录 · 最近 {job.events?.length || 0} 条</summary>
      <ol aria-label="采集活动记录">{[...(job.events || [])].reverse().map((event, index) => <li key={`${event.at}-${index}`}>
        <time dateTime={event.at}>{formatBeijingTime(event.at, '—')}</time>
        <span>{event.scope === 'target' ? '目标机' : '对端'} {event.serverIp} · {event.message}</span>
      </li>)}</ol>
    </details>
    <p className="check-note">{running ? '每 2 秒读取采集进度' : '采集已结束'} · 最近同步：{formatBeijingTime(lastSyncedAt, '等待同步')}</p>
  </section>;
}

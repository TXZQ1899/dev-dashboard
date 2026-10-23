'use client';
import { useState } from 'react';

// The three concurrent collection slots are what the user watches during a sync.
// Kept as a pure component so it can be rendered with any job state in tests.
export type JobProgress = {
  phase?: string;
  completed?: number;
  total?: number;
  current?: string;
  sources?: Record<string, string>;
  tasks?: Record<string, { label?: string; tenantId?: string; source?: string; status: string }>;
  slots?: Record<string, { source: string | null; status: string }>;
  slotHistory?: Record<string, { source: string; status: string }[]>;
  jumpServer?: { phase?: string; completed?: number; total?: number };
};
export type JobView = {
  status: string;
  phase: string;
  progress?: JobProgress;
  slotLogs?: Record<string, string>;
};

export const slotCount = 3;
export const sourceOrder = ['ECS', 'CLB', 'NAT', 'DMS', 'CLOUD-DNS', 'JumpServer', 'Codeup', 'Local GitLab', 'DevOps'];
export const sourceLabels: Record<string, string> = {
  ECS: '阿里云 ECS',
  CLB: '阿里云 CLB',
  NAT: '阿里云 NAT',
  DMS: '阿里云 DMS',
  'CLOUD-DNS': '阿里云 DNS',
  JumpServer: 'JumpServer',
  Codeup: 'Codeup（云效）',
  'Local GitLab': 'Local GitLab',
  DevOps: 'DevOps',
};
const stateLabels: Record<string, string> = {
  完成: '已完成',
  采集中: '采集中',
  等待: '等待中',
  失败: '失败',
};
const mark = (status: string) => (status === '完成' ? ' ✓' : status === '失败' ? ' ✗' : ' …');

function orderedSources(sources: Record<string, string>) {
  const known = sourceOrder.filter((name) => name in sources);
  const extra = Object.keys(sources).filter((name) => !sourceOrder.includes(name));
  return [...known, ...extra];
}

export function CollectionProgress({ job }: { job: JobView }) {
  const progress = job.progress;
  const taskLabel = (name: string) => progress?.tasks?.[name]?.label ?? sourceLabels[name] ?? name;
  const taskStep = (entry: { source: string; status: string }) => `${taskLabel(entry.source)}${mark(entry.status)}`;
  const [activeTab, setActiveTab] = useState('');
  // Only a full sync runs the three-slot pool; single-source syncs show no slots.
  const hasCollectors = Boolean(progress?.sources || progress?.slots);
  const slotState = progress?.slots ?? {};
  const logs = job.slotLogs ?? {};
  const keys = Array.from(
    new Set([...Array.from({ length: slotCount }, (_, i) => String(i + 1)), ...Object.keys(logs)]),
  );
  const tabs = keys
    .map((key) => {
      const slot = slotState[key];
      const history = progress?.slotHistory?.[key] ?? [];
      // Prefer the slot's current source; when idle show its last one, not the
      // whole hand-off chain, so the tab labels stay short.
      const last = history[history.length - 1];
      const label = slot?.source
        ? `槽位 ${key} · ${taskLabel(slot.source)}`
        : last
          ? `槽位 ${key} · ${taskLabel(last.source)}${mark(last.status)}`
          : `槽位 ${key}`;
      return { key, label, text: logs[key] ?? '' };
    })
    .filter((entry) => entry.text || slotState[entry.key]);
  const active = tabs.some((entry) => entry.key === activeTab)
    ? activeTab
    : (tabs.find((entry) => entry.text)?.key ?? tabs[0]?.key ?? '');
  if (!hasCollectors) return null;
  return (
    <>
      <h3 className="settings-subhead">采集任务</h3>
      <div className="settings-sources">
        {orderedSources(progress?.sources ?? {}).map((name) => {
          const value = progress!.sources![name];
          const tone = value === '完成' ? 'done' : value === '失败' ? 'failed' : value === '采集中' ? 'active' : 'waiting';
          return (
            <span key={name} className={`source-chip ${tone}`}>
              <b>{sourceLabels[name] ?? name}</b>
              <em>{stateLabels[value] ?? value}</em>
            </span>
          );
        })}
      </div>
      {progress?.tasks && <div className="settings-sources">
        {Object.entries(progress.tasks).filter(([, task]) => task.tenantId).map(([key, task]) =>
          <span key={key} className={`source-chip ${task.status === '失败' ? 'failed' : task.status === '完成' ? 'done' : 'waiting'}`}>
            <b>{task.label ?? key}</b><em>{task.status}</em>
          </span>)}
      </div>}
      <h3 className="settings-subhead">并发槽位（{slotCount} 个）</h3>
      <div className="settings-slots">
        {Array.from({ length: slotCount }, (_, index) => {
          const key = String(index + 1);
          const slot = slotState[key];
          const history = progress?.slotHistory?.[key] ?? [];
          return (
            <div key={key} className={`slot-card ${slot?.source ? 'active' : ''}`}>
              <strong>{slot?.source ? taskLabel(slot.source) : `槽位 ${key} · 空闲`}</strong>
              <small>{history.length ? history.map(taskStep).join(' → ') : '尚未分配任务'}</small>
            </div>
          );
        })}
      </div>
      {tabs.length ? (
        <div className="settings-log-tabs">
          <div className="settings-log-tablist" role="tablist">
            {tabs.map((entry) => (
              <button
                key={entry.key}
                type="button"
                role="tab"
                aria-selected={entry.key === active}
                className={entry.key === active ? 'active' : ''}
                onClick={() => setActiveTab(entry.key)}
              >
                {entry.label}
              </button>
            ))}
          </div>
          {tabs.map((entry) => (
            <pre key={entry.key} role="tabpanel" hidden={entry.key !== active} className="settings-log-panel">
              {entry.text || '（该槽位暂无日志输出）'}
            </pre>
          ))}
        </div>
      ) : null}
    </>
  );
}

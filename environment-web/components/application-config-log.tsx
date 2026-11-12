'use client';

import { useEffect, useState } from 'react';
import { formatBeijingTime } from '@/lib/datetime';

type Event = { time: string; event: string; appId?: string; name?: string; phase?: string; status?: string; path?: string; errorKind?: string; page?: number; count?: number; elapsedMs?: number; completed?: number; total?: number };
type Log = { events: Event[]; truncated: boolean };
const labels: Record<string, string> = {
  task_start:'同步开始 / 继续', task_finish:'同步结束', task_error:'同步中断', phase:'进入阶段', app_finish:'应用处理结束',
  file_start:'读取仓库文件', file_done:'仓库文件读取完成', file_error:'仓库文件读取失败',
  tree_start:'读取仓库目录', tree_done:'仓库目录读取完成', tree_error:'仓库目录读取失败',
  mapper_skip:'跳过 mapper 目录',
  resource_skip:'跳过静态资源 / 模板目录',
  nacos_start:'读取 Nacos 配置', nacos_done:'Nacos 配置读取完成', nacos_error:'Nacos 配置读取失败',
};
const statuses: Record<string, string> = { ready:'完成', partial:'部分完成', failed:'失败', skipped:'跳过', authentication:'登录失效', interrupted:'中断', start:'新任务', resume:'继续任务', retry:'重试失败应用' };
export function formatConfigLog(event: Event): string {
  const parts = [`[${formatBeijingTime(event.time, '—')}]`];
  if (event.appId) parts.push(`${event.name || '应用'}（${event.appId}）`);
  parts.push(labels[event.event] || event.event);
  if (event.phase) parts.push(event.phase);
  if (event.path) parts.push(event.path);
  if (event.page != null) parts.push(`第 ${event.page} 页`);
  if (event.count != null) parts.push(`${event.count} 条`);
  if (event.elapsedMs != null) parts.push(`耗时 ${(event.elapsedMs / 1000).toFixed(2)} 秒`);
  if (event.status) parts.push(statuses[event.status] || event.status);
  if (event.errorKind) parts.push(event.errorKind);
  if (event.total) parts.push(`${event.completed || 0} / ${event.total}`);
  return parts.join(' · ');
}

export function ApplicationConfigLog() {
  const [log, setLog] = useState<Log | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let stopped = false;
    async function poll() {
      try {
        const response = await fetch('/api/settings/application-config-sync/log', { cache:'no-store' });
        if (!response.ok) throw new Error(response.status === 404 ? '当前运行版本尚不支持详细日志。' : '详细日志暂不可用，请稍后重试。');
        const data = await response.json() as Log;
        if (!stopped) { setLog(data); setError(''); }
      } catch (err) { if (!stopped) setError(err instanceof Error ? err.message : '详细日志暂不可用。'); }
    }
    void poll();
    const timer = setInterval(poll, 4000);
    return () => { stopped = true; clearInterval(timer); };
  }, []);
  return <div className="settings-log" aria-label="应用配置同步详细日志">
    <h3>同步详细日志</h3>
    <small>每 4 秒更新，最新记录在前；记录目录、文件和请求耗时，不包含配置内容。</small>
    {error && <p role="alert">{error}</p>}
    {log?.truncated && <small>仅展示最近日志，较早记录已省略。</small>}
    <pre aria-live="polite">{log?.events.length ? [...log.events].reverse().map(formatConfigLog).join('\n') : '暂无详细日志；旧版本运行的任务不会产生这些记录。'}</pre>
  </div>;
}

'use client';

import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { formatBeijingTime } from '@/lib/datetime';

const endpoint = '/api/settings/nginx-export';
const files = ['nginx-routes-full.csv', 'nginx-applications.xlsx'] as const;
type ExportStatus = {
  sourceVersion: string;
  job: {
    sourceVersion: string;
    status: 'running' | 'succeeded' | 'failed';
    error?: string;
  } | null;
  result: {
    sourceVersion: string;
    generatedAt: string;
    counts: {
      routeRows: number;
      frontendRows: number;
      backendRows: number;
      matchedRows: number;
      unresolvedRows: number;
      ambiguousRows: number;
      kongRows: number;
      kongTargetRows: number;
      unclassifiedRows?: number;
      explanationCounts?: Record<string, number>;
    };
    downloads: Record<typeof files[number], string>;
  } | null;
};

export function NginxExportSettings({ sourceVersion }: { sourceVersion: string }) {
  const [status, setStatus] = useState<ExportStatus | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [pollError, setPollError] = useState('');
  const requestEpoch = useRef(0);
  useEffect(() => {
    const controller = new AbortController();
    let reading = false;
    async function refresh() {
      if (reading) return;
      reading = true;
      const epoch = requestEpoch.current;
      try {
        const response = await fetch(endpoint, { cache: 'no-store', signal: controller.signal });
        if (!response.ok) throw new Error();
        const data: ExportStatus = await response.json();
        if (!controller.signal.aborted && epoch === requestEpoch.current) {
          setStatus(data);
          setPollError('');
        }
      } catch {
        if (!controller.signal.aborted && epoch === requestEpoch.current) setPollError('无法读取 Nginx 导出状态');
      } finally {
        reading = false;
      }
    }
    void refresh();
    const timer = setInterval(refresh, 3000);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, [sourceVersion]);

  async function generate() {
    requestEpoch.current++;
    setSubmitting(true);
    setError('');
    try {
      const response = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sourceVersion }),
      });
      const data = (await response.json()) as ExportStatus & { error?: string };
      if (!response.ok) throw new Error(data.error || '无法启动 Nginx 导出');
      requestEpoch.current++;
      setStatus(data);
    } catch (error) {
      setError(error instanceof Error ? error.message : '无法启动 Nginx 导出');
    } finally {
      setSubmitting(false);
    }
  }

  const running = status?.job?.status === 'running';
  const result = status?.result?.sourceVersion === sourceVersion ? status.result : null;
  const jobError = status?.job?.sourceVersion === sourceVersion && status.job.status === 'failed' ? status.job.error : '';
  const versionChanged = Boolean(status && status.sourceVersion !== sourceVersion);
  const explanations = Object.entries(result?.counts.explanationCounts ?? {}).sort((a, b) => b[1] - a[1]);
  return (
    <section className="panel settings-panel" id="nginx-export-settings">
      <h2>Nginx 路由与应用导出</h2>
      <p>基于当前激活版本 {sourceVersion} 生成路由 CSV 和前后端应用 Excel。</p>
      <div className="flex flex-wrap gap-3 items-center">
        <Button disabled={!status || running || submitting || versionChanged} onClick={() => void generate()}>
          {running || submitting ? '生成中…' : '生成导出文件'}
        </Button>
        {running && status?.job && <small role="status">正在处理版本 {status.job.sourceVersion}</small>}
      </div>
      {versionChanged && <p role="status">激活版本已变化，请刷新 Settings 后生成。</p>}
      {(error || jobError || pollError) && <p role="alert">{error || jobError || pollError}</p>}
      {result ? (
        <>
          <p>生成时间 {formatBeijingTime(result.generatedAt, '—')} · 路由 {result.counts.routeRows} 行 · 前端 {result.counts.frontendRows} 行 · 后端 {result.counts.backendRows} 行</p>
          <p>已匹配应用 {result.counts.matchedRows} 行 · 未到达最终端点 {result.counts.unresolvedRows} 行 · 有歧义 {result.counts.ambiguousRows} 行</p>
          {explanations.length > 0 && (
            <details>
              <summary>查看应用匹配说明{result.counts.unclassifiedRows != null && `（待归因 ${result.counts.unclassifiedRows} 行）`}</summary>
              <ul>{explanations.map(([label, count]) => <li key={label}>{label} {count} 行</li>)}</ul>
            </details>
          )}
          {result.counts.kongRows > 0 && <p>到达 Kong {result.counts.kongRows} 行 · 已取得 Kong Target {result.counts.kongTargetRows} 行</p>}
          <div className="flex flex-wrap gap-3">
            {files.map(file => (
              <a key={file} href={result.downloads[file]} download
                className="inline-flex h-8 items-center rounded-lg border border-border bg-background px-2.5 text-sm font-medium hover:bg-muted">
                下载 {file}
              </a>
            ))}
          </div>
        </>
      ) : !running && <p className="settings-hint">当前版本尚无导出文件。</p>}
    </section>
  );
}

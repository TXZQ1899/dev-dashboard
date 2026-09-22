'use client';
import { useEffect, useState } from 'react';
import { Shell } from '@/app/page';
import { CollectionProgress } from '@/components/collection-progress';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from '@/components/ui/table';

type Source = 'devops' | 'aliyun' | 'jumpserver' | 'local_gitlab';
type Version = {
  id: string;
  createdAt: string;
  finishedAt?: string;
  status: string;
  kind: string;
  note?: string;
  error?: string;
  counts?: { applications: number; repositories: number; servers: number; ecs?: number; clb?: number | null; nat?: number | null; dnat?: number | null };
};
type Topology = {
  id: string;
  sourceVersion: string;
  generatedAt: string;
  stats: {
    nodeCount: number;
    edgeCount: number;
    ambiguousEdges: number;
    unresolvedNodes: number;
  };
  validation?: { valid: boolean; errorCount: number; warningCount: number };
  viewHref: string;
  downloadHref: string;
};
type State = {
  topology?: Topology | null;
  folidev?: { configured: boolean; updatedAt?: string };
  cookies: Record<Source, { configured: boolean; updatedAt?: string; alert?: { message: string; detectedAt: string } }>;
  schedule: { enabled: boolean; time: string };
  current: { id: string; activatedAt: string };
  versions: Version[];
  job?: {
    id: string;
    kind: string;
    status: string;
    phase: string;
    startedAt: string;
    progress?: {
      phase?: string;
      completed?: number;
      total?: number;
      current?: string;
      sources?: Record<string, string>;
      slots?: Record<string, { source: string | null; status: string }>;
      slotHistory?: Record<string, { source: string; status: string }[]>;
      jumpServer?: { phase?: string; completed?: number; total?: number };
    };
    logTail?: string;
    slotLogs?: Record<string, string>;
  };
  storage: string;
};
const sources: [Source, string][] = [
  ['devops', 'DevOps 平台'],
  ['aliyun', '阿里云（ECS / Codeup / CLB / NAT 共用）'],
  ['jumpserver', 'JumpServer'],
  ['local_gitlab', 'Local GitLab（http://gitlab.dev.thomascook.com.cn/）'],
];
function date(value?: string) {
  return value
    ? new Date(value).toLocaleString('zh-CN', {
        timeZone: 'Asia/Shanghai',
        hour12: false,
      })
    : '—';
}
export default function SettingsPage() {
  const [state, setState] = useState<State | null>(null);
  const [cookies, setCookies] = useState({
    devops: '',
    aliyun: '',
    jumpserver: '',
    local_gitlab: '',
  });
  const [folidevPassword, setFolidevPassword] = useState('');
  const [schedule, setSchedule] = useState({ enabled: false, time: '18:00' });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  useEffect(() => {
    let stopped = false,
      first = true;
    async function refresh() {
      try {
        const response = await fetch('/api/settings/state', {
          cache: 'no-store',
        });
        if (!response.ok) throw new Error('无法读取设置，请稍后刷新');
        const data: State = await response.json();
        if (stopped) return;
        setState(data);
        if (first) {
          setSchedule(data.schedule);
          first = false;
        }
      } catch (e) {
        if (!stopped) setError(e instanceof Error ? e.message : '连接失败');
      }
    }
    void refresh();
    const timer = setInterval(refresh, 4000);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, []);
  async function post(path: string, body: unknown) {
    const response = await fetch('/api/settings/' + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = (await response.json()) as State & { error?: string };
    if (!response.ok) throw new Error(data.error || '操作失败');
    setState(data);
    return data;
  }
  async function syncSource(source: 'local-gitlab' | 'codeup' | 'devops' | 'ecs' | 'clb' | 'nat' | 'jumpserver') {
    setBusy(true);
    setError('');
    try {
      await post('config', { cookies, schedule, folidevPassword });
      setFolidevPassword('');
      setCookies({ devops: '', aliyun: '', jumpserver: '', local_gitlab: '' });
      const data = await post(`sync/${source}`, {});
      setMessage(`${source} 独立同步任务已启动`);
      setState(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : '操作失败');
    } finally {
      setBusy(false);
    }
  }
  async function generateTopology() {
    setBusy(true);
    setError('');
    setMessage('');
    try {
      const data = await post('topology', {});
      setMessage(`Topology 已生成：${data.topology?.id ?? '未知版本'}，可查看或下载`);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Topology 生成失败');
    } finally {
      setBusy(false);
    }
  }
  async function act(kind: 'save' | 'sync' | 'local_gitlab' | 'switch', id?: string) {
    setBusy(true);
    setError('');
    setMessage('');
    try {
      if (kind !== 'switch') {
        await post('config', { cookies, schedule, folidevPassword });
        setFolidevPassword('');
        setCookies({ devops: '', aliyun: '', jumpserver: '', local_gitlab: '' });
      }
      if (kind === 'sync' || kind === 'local_gitlab') await post(kind === 'local_gitlab' ? 'sync/local-gitlab' : 'sync', {});
      if (kind === 'switch') await post('switch', { id });
      setMessage(
        kind === 'save'
          ? '设置已保存'
          : kind === 'sync' || kind === 'local_gitlab'
            ? '同步任务已启动，可以离开本页，稍后查看结果'
            : '正在准备指定版本，完成后页面将自动刷新',
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : '操作失败');
    } finally {
      setBusy(false);
    }
  }
  const running = state?.job?.status === 'running';
  return (
    <Shell active="settings">
      <main className="settings-page">
        <div className="page-heading">
          <div>
            <h1>Settings</h1>
            <p>配置登录凭据、同步数据和管理历史版本。全量同步最多并行采集 3 个数据源，全部成功后启用；单独同步保留其他来源的原采集日期。</p>
          </div>
        </div>
        {error && (
          <p role="alert" className="settings-error">
            {error}
          </p>
        )}
        {message && (
          <p role="status" className="settings-message">
            {message}
          </p>
        )}
        {!state ? (
          <p>正在读取设置…</p>
        ) : (
          <>
            <section id="cookie-settings" className="panel settings-panel">
              <h2>Cookie 与每日同步</h2>
              <p className="settings-hint">
                Cookie 仅保存到本机 Docker
                数据卷，不回显。留空表示保留已保存内容。
              </p>
              {sources.map(([key, label]) => (
                <div className="settings-field" key={key}>
                  <label htmlFor={`cookie-${key}`}>
                    {label} Cookie{' '}
                    <span>
                      {state.cookies[key].alert ? 'Cookie 失效，需更新' : state.cookies[key].configured ? '已配置' : '未配置'}
                    </span>
                  </label>
                  {state.cookies[key].alert && <p role="alert" className="settings-error">{state.cookies[key].alert.message}</p>}
                  <Input
                    id={`cookie-${key}`}
                    type="password"
                    autoComplete="off"
                    spellCheck={false}
                    value={cookies[key]}
                    onChange={(e) =>
                      setCookies((prev) => ({ ...prev, [key]: e.target.value }))
                    }
                    placeholder="粘贴最新 Cookie 请求头内容"
                  />
                  <small>最后保存：{date(state.cookies[key].updatedAt)}</small>
                </div>
              ))}
              <div className="settings-field">
                <label htmlFor="folidev-password">folidev 用户密码 · {state.folidev?.configured ? '已配置' : '未配置'}</label>
                <Input id="folidev-password" type="password" autoComplete="new-password" value={folidevPassword} onChange={(e) => setFolidevPassword(e.target.value)} placeholder="留空保留已保存密码" />
                <small>用于 JumpServer 的 folidev 登录及只读 sudo 采集；不回显。最后保存：{date(state.folidev?.updatedAt)}</small>
              </div>
              <div className="settings-schedule">
                <label>
                  <input
                    type="checkbox"
                    checked={schedule.enabled}
                    onChange={(e) =>
                      setSchedule((prev) => ({
                        ...prev,
                        enabled: e.target.checked,
                      }))
                    }
                  />{' '}
                  每日自动同步
                </label>
                <Input
                  type="time"
                  aria-label="每日同步时间"
                  value={schedule.time}
                  onChange={(e) =>
                    setSchedule((prev) => ({ ...prev, time: e.target.value }))
                  }
                />
                <span>北京时间；Docker 需运行且可访问公司内网</span>
              </div>
              <div className="flex flex-wrap gap-3">
                <Button disabled={busy} onClick={() => act('save')}>
                  保存设置
                </Button>
                <Button disabled={busy || running} onClick={() => act('sync')}>
                  {running ? '任务进行中…' : '保存并一键同步'}
                </Button>
                <Button disabled={busy || running} variant="outline" onClick={() => act('local_gitlab')}>
                  单独同步 Local GitLab
                </Button>
                <Button disabled={busy || running} variant="outline" onClick={() => syncSource('ecs')}>单独同步 ECS</Button>
                <Button disabled={busy || running} variant="outline" onClick={() => syncSource('clb')}>单独同步 CLB</Button>
                <Button disabled={busy || running} variant="outline" onClick={() => syncSource('nat')}>单独同步 NAT</Button>
                <Button disabled={busy || running} variant="outline" onClick={() => syncSource('jumpserver')}>单独同步 JumpServer</Button>
                <Button disabled={busy || running} variant="outline" onClick={() => syncSource('devops')}>单独同步 DevOps</Button>
                <Button disabled={busy || running} variant="outline" onClick={() => syncSource('codeup')}>单独同步云效</Button>
              </div>
              {running && (
                <p className="settings-hint" role="status">
                  任务进行中：仍可保存 Cookie（不会影响正在运行的任务，下次同步生效）；同步与版本切换需等待任务结束。
                </p>
              )}
              <p className="settings-hint">
                同步 DevOps、云效、Local GitLab、JumpServer、上海 ECS、CLB 和 NAT（DNAT IP / 端口映射）。ECS 项目名称目录沿用前版，实例及项目 ID 每次重新采集。每次同步新建目录，完整采集并验证后才启用。同步可能需要数十分钟。
              </p>
            </section>
            {state.job && (
              <section className="panel settings-panel" aria-live="polite">
                <h2>
                  {state.job.status === 'running'
                    ? '任务进行中'
                    : state.job.status === 'succeeded'
                      ? '最近任务已完成'
                      : '最近任务未完成'}
                </h2>
                <p>{state.job.phase}</p>
                {state.job.progress?.total ? (
                  <p className="settings-progress">
                    进度：{state.job.progress.completed ?? 0} / {state.job.progress.total}
                    {state.job.progress.current ? ` · 当前：${state.job.progress.current}` : ''}
                  </p>
                ) : null}
                <CollectionProgress job={state.job} />
                {state.job.status === 'running' && state.job.logTail ? (
                  <details className="settings-log">
                    <summary>查看完整合并日志（实时刷新）</summary>
                    <pre>{state.job.logTail}</pre>
                  </details>
                ) : null}
                <small>
                  {date(state.job.startedAt)} · {state.job.id}
                </small>
              </section>
            )}
            <section className="panel settings-panel" id="topology-settings">
              <h2>Topology 数据</h2>
              {state.topology ? (
                <>
                  <p className="settings-version">{state.topology.id}</p>
                  <p>基于数据版本：{state.topology.sourceVersion}</p>
                  <p>生成时间：{date(state.topology.generatedAt)}</p>
                  <p>
                    节点 {state.topology.stats.nodeCount} / 边 {state.topology.stats.edgeCount} / 待确认边{' '}
                    {state.topology.stats.ambiguousEdges} / 未解析或外部节点 {state.topology.stats.unresolvedNodes}
                    {state.topology.validation?.errorCount ? ` / 校验错误 ${state.topology.validation.errorCount}` : ''}
                  </p>
                  <div className="flex flex-wrap gap-3">
                    <a
                      className="inline-flex h-8 items-center rounded-lg border border-border bg-background px-2.5 text-sm font-medium hover:bg-muted"
                      href={state.topology.viewHref}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      查看 JSON
                    </a>
                    <a
                      className="inline-flex h-8 items-center rounded-lg border border-border bg-background px-2.5 text-sm font-medium hover:bg-muted"
                      href={state.topology.downloadHref}
                      download
                    >
                      下载当前 Topology
                    </a>
                  </div>
                </>
              ) : (
                <p className="settings-hint">尚未生成 Topology。生成后会保存到 Docker 数据卷，并保留独立版本号。</p>
              )}
              <div className="flex flex-wrap gap-3" style={{ marginTop: 16 }}>
                <Button disabled={busy || running} onClick={() => generateTopology()}>
                  {busy ? '生成中…' : '基于当前数据版本生成 Topology'}
                </Button>
              </div>
              <p className="settings-hint">
                Topology 使用当前激活版本中的 DevOps、仓库、JumpServer、ECS、CLB 和 NAT 数据；DNS 与 EIP 静态快照来自应用镜像。每次生成都会创建新的 topology-* 版本并持久化到数据卷。
              </p>
            </section>
            <section className="panel settings-panel">
              <h2>当前数据版本</h2>
              <p className="settings-version">{state.current.id}</p>
              <p>启用时间：{date(state.current.activatedAt)}</p>
              <p className="settings-hint">{state.storage}</p>
            </section>
            <section className="panel">
              <div className="panel-title">
                <div>
                  <h2>同步历史与版本切换</h2>
                  <p>
                    只允许启用完整版本。切换期间继续展示当前数据，完成后自动刷新。
                  </p>
                </div>
              </div>
              <div className="shared-table-scroll">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>同步日期 / 版本</TableHead>
                      <TableHead>状态</TableHead>
                      <TableHead>数据量</TableHead>
                      <TableHead>说明</TableHead>
                      <TableHead>操作</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {state.versions.map((v) => (
                      <TableRow key={v.id}>
                        <TableCell>
                          {date(v.createdAt)}
                          <small className="block">{v.id}</small>
                        </TableCell>
                        <TableCell>
                          {v.id === state.current.id
                            ? '当前版本'
                            : v.status === 'ready'
                              ? '完整'
                              : v.status === 'collecting'
                                ? '采集中'
                                : '失败'}
                        </TableCell>
                        <TableCell>
                          {v.counts
                            ? `${v.counts.applications} 应用 / ${v.counts.repositories} 仓库 / ${v.counts.servers} 堡垒机资产 / ${v.counts.ecs ?? '—'} ECS / ${v.counts.clb ?? '未采集'} CLB / ${v.counts.nat ?? '未采集'} NAT / ${v.counts.dnat ?? '未采集'} DNAT`
                            : '—'}
                        </TableCell>
                        <TableCell>{v.error || v.note || '—'}</TableCell>
                        <TableCell>
                          <Button
                            variant="outline"
                            disabled={
                              busy ||
                              running ||
                              v.status !== 'ready' ||
                              v.id === state.current.id
                            }
                            onClick={() => act('switch', v.id)}
                          >
                            {v.id === state.current.id
                              ? '正在使用'
                              : '切换到此版本'}
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </section>
          </>
        )}
      </main>
    </Shell>
  );
}

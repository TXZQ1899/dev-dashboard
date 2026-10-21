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
import { formatBeijingTime } from '@/lib/datetime';

type Source = 'devops' | 'aliyun' | 'jumpserver' | 'local_gitlab';
type Version = {
  id: string;
  createdAt: string;
  finishedAt?: string;
  status: string;
  kind: string;
  note?: string;
  error?: string;
  counts?: { applications: number; repositories: number; servers: number; ecs?: number; clb?: number | null; nat?: number | null; dnat?: number | null; dms?: number | null };
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
type Job = {
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
  failures?: { source: string; type: string; credential?: string; message: string; credentialUpdatedAt?: string }[];
};
type SourceState = { busy: boolean; retryable: boolean; reason?: string };
type AliyunAccessKey = {
  configured: boolean;
  mode: 'cookie' | 'accesskey';
  accessKeyId?: string;
  updatedAt?: string;
  organizationId?: string;
  alert?: { message: string; detectedAt: string };
};
type State = {
  topology?: Topology | null;
  aliyunAccessKey?: AliyunAccessKey;
  folidev?: { configured: boolean; updatedAt?: string };
  rootAlt1?: { configured: boolean; updatedAt?: string };
  rootAlt2?: { configured: boolean; updatedAt?: string };
  cookies: Record<Source, { configured: boolean; updatedAt?: string; alert?: { message: string; detectedAt: string } }>;
  schedule: { enabled: boolean; time: string };
  current: { id: string; activatedAt: string };
  versions: Version[];
  job?: Job;
  extraJobs?: Job[];
  sourceStates?: Record<string, SourceState>;
  storage: string;
};
const sources: [Source, string][] = [
  ['devops', 'DevOps 平台'],
  ['aliyun', '阿里云（ECS / Codeup / CLB / NAT 共用）'],
  ['jumpserver', 'JumpServer'],
  ['local_gitlab', 'Local GitLab（http://gitlab.dev.thomascook.com.cn/）'],
];
// 展示时间统一走 lib/datetime（北京时间），无值时显示占位符。
function date(value?: string) {
  return formatBeijingTime(value, '—');
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
  const [rootPasswordAlt1, setRootPasswordAlt1] = useState('');
  const [rootPasswordAlt2, setRootPasswordAlt2] = useState('');
  const [schedule, setSchedule] = useState({ enabled: false, time: '18:00' });
  const [aliyunAuthMode, setAliyunAuthMode] = useState<'cookie' | 'accesskey'>('cookie');
  const [aliyunAccessKeyId, setAliyunAccessKeyId] = useState('');
  const [aliyunAccessKeySecret, setAliyunAccessKeySecret] = useState('');
  const [codeupOrganizationId, setCodeupOrganizationId] = useState('');
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
          setAliyunAuthMode(data.aliyunAccessKey?.mode === 'accesskey' ? 'accesskey' : 'cookie');
          setCodeupOrganizationId(data.aliyunAccessKey?.organizationId ?? '');
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
  function configBody() {
    return {
      cookies,
      schedule,
      folidevPassword,
      rootPasswordAlt1,
      rootPasswordAlt2,
      aliyunAccessKey: {
        mode: aliyunAuthMode,
        accessKeyId: aliyunAccessKeyId,
        accessKeySecret: aliyunAccessKeySecret,
        organizationId: codeupOrganizationId,
      },
    };
  }
  async function syncSource(source: 'local-gitlab' | 'codeup' | 'devops' | 'ecs' | 'clb' | 'nat' | 'dms' | 'jumpserver') {
    setBusy(true);
    setError('');
    try {
      await post('config', configBody());
      setFolidevPassword('');
      setRootPasswordAlt1('');
      setRootPasswordAlt2('');
      setCookies({ devops: '', aliyun: '', jumpserver: '', local_gitlab: '' });
      setAliyunAccessKeySecret('');
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
        await post('config', configBody());
        setFolidevPassword('');
        setRootPasswordAlt1('');
        setRootPasswordAlt2('');
        setCookies({ devops: '', aliyun: '', jumpserver: '', local_gitlab: '' });
        setAliyunAccessKeySecret('');
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
  const extraRunningJobs = (state?.extraJobs ?? []).filter((j) => j.status === 'running');
  const anyRunning = running || extraRunningJobs.length > 0;
  // Full sync / switch / topology stay locked while any task runs; a single
  // source is unlocked by the server exactly when its own collection failed and
  // a concurrent retry is safe.
  const sourceEnabled = (key: string) => state?.sourceStates?.[key]?.retryable ?? !anyRunning;
  const sourceReason = (key: string) => state?.sourceStates?.[key]?.reason || undefined;
  const sourceLabel: Record<string, string> = {
    ecs: 'ECS',
    clb: 'CLB',
    nat: 'NAT',
    dms: 'DMS',
    jumpserver: 'JumpServer',
    codeup: '云效',
    local_gitlab: 'Local GitLab',
    devops: 'DevOps',
  };
  const retryableLabels = Object.entries(state?.sourceStates ?? {})
    .filter(([, s]) => s.retryable)
    .map(([key]) => sourceLabel[key] ?? key);
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
              <h2>凭据与每日同步</h2>
              <p className="settings-hint">
                Cookie / AccessKey 仅保存到本机 Docker
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
                <label htmlFor="aliyun-auth-mode">
                  阿里云采集方式 ·{' '}
                  <span>{aliyunAuthMode === 'accesskey' ? 'AccessKey（OpenAPI）' : 'Cookie（原有方式）'}</span>
                </label>
                <select
                  id="aliyun-auth-mode"
                  value={aliyunAuthMode}
                  onChange={(e) => setAliyunAuthMode(e.target.value as 'cookie' | 'accesskey')}
                >
                  <option value="cookie">Cookie（原有方式）</option>
                  <option value="accesskey">AccessKey（OpenAPI）</option>
                </select>
                <small>
                  只影响下一次 ECS / CLB / NAT / DMS / 云效采集；Cookie 与 AccessKey 相互独立，可随时切回。
                </small>
              </div>
              {state.aliyunAccessKey?.alert && (
                <p role="alert" className="settings-error">{state.aliyunAccessKey.alert.message}</p>
              )}
              <div className="settings-field">
                <label htmlFor="aliyun-access-key-id">
                  AccessKey ID ·{' '}
                  <span>
                    {state.aliyunAccessKey?.configured
                      ? `已配置${state.aliyunAccessKey.accessKeyId ? `（${state.aliyunAccessKey.accessKeyId}）` : ''}`
                      : '未配置'}
                  </span>
                </label>
                <Input
                  id="aliyun-access-key-id"
                  autoComplete="off"
                  spellCheck={false}
                  value={aliyunAccessKeyId}
                  onChange={(e) => setAliyunAccessKeyId(e.target.value)}
                  placeholder="LTAI...；留空保留已保存内容"
                />
                <small>
                  建议使用只读权限的 RAM 用户 AccessKey；最后保存：{date(state.aliyunAccessKey?.updatedAt)}
                </small>
              </div>
              <div className="settings-field">
                <label htmlFor="aliyun-access-key-secret">AccessKey Secret · {state.aliyunAccessKey?.configured ? '已配置' : '未配置'}</label>
                <Input
                  id="aliyun-access-key-secret"
                  type="password"
                  autoComplete="new-password"
                  spellCheck={false}
                  value={aliyunAccessKeySecret}
                  onChange={(e) => setAliyunAccessKeySecret(e.target.value)}
                  placeholder="留空保留已保存内容"
                />
                <small>仅保存到 Docker 数据卷，不回显。</small>
              </div>
              <div className="settings-field">
                <label htmlFor="codeup-organization-id">云效 Codeup 企业 ID</label>
                <Input
                  id="codeup-organization-id"
                  autoComplete="off"
                  spellCheck={false}
                  value={codeupOrganizationId}
                  onChange={(e) => setCodeupOrganizationId(e.target.value)}
                  placeholder="留空则从历史仓库快照自动识别"
                />
                <small>
                  取自 https://devops.aliyun.com/organization/【OrganizationId】；已存在历史快照时可留空。
                </small>
              </div>
              <div className="settings-field">
                <label htmlFor="folidev-password">folidev 用户密码 · {state.folidev?.configured ? '已配置' : '未配置'}</label>
                <Input id="folidev-password" type="password" autoComplete="new-password" value={folidevPassword} onChange={(e) => setFolidevPassword(e.target.value)} placeholder="留空保留已保存密码" />
                <small>用于 JumpServer 的 folidev 登录及只读 sudo 采集；不回显。最后保存：{date(state.folidev?.updatedAt)}</small>
              </div>
              <div className="settings-field">
                <label htmlFor="root-alt1">备选 root 密码 1 · {state.rootAlt1?.configured ? '已配置' : '未配置'}</label>
                <Input id="root-alt1" type="password" autoComplete="new-password" value={rootPasswordAlt1} onChange={(e) => setRootPasswordAlt1(e.target.value)} placeholder="留空保留已保存密码" />
                <small>folidev sudo 失败时，通过 su 使用此 root 密码登录；不回显。最后保存：{date(state.rootAlt1?.updatedAt)}</small>
              </div>
              <div className="settings-field">
                <label htmlFor="root-alt2">备选 root 密码 2 · {state.rootAlt2?.configured ? '已配置' : '未配置'}</label>
                <Input id="root-alt2" type="password" autoComplete="new-password" value={rootPasswordAlt2} onChange={(e) => setRootPasswordAlt2(e.target.value)} placeholder="留空保留已保存密码" />
                <small>第二个备选 root 密码（su 方式）；不回显。最后保存：{date(state.rootAlt2?.updatedAt)}</small>
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
                <Button disabled={busy || anyRunning} onClick={() => act('sync')}>
                  {anyRunning ? '任务进行中…' : '保存并一键同步'}
                </Button>
                <Button disabled={busy || !sourceEnabled('local_gitlab')} variant="outline" title={sourceReason('local_gitlab')} onClick={() => act('local_gitlab')}>
                  单独同步 Local GitLab
                </Button>
                <Button disabled={busy || !sourceEnabled('ecs')} variant="outline" title={sourceReason('ecs')} onClick={() => syncSource('ecs')}>单独同步 ECS</Button>
                <Button disabled={busy || !sourceEnabled('clb')} variant="outline" title={sourceReason('clb')} onClick={() => syncSource('clb')}>单独同步 CLB</Button>
                <Button disabled={busy || !sourceEnabled('nat')} variant="outline" title={sourceReason('nat')} onClick={() => syncSource('nat')}>单独同步 NAT</Button>
                <Button disabled={busy || !sourceEnabled('dms')} variant="outline" title={sourceReason('dms')} onClick={() => syncSource('dms')}>单独同步 DMS</Button>
                <Button disabled={busy || !sourceEnabled('jumpserver')} variant="outline" title={sourceReason('jumpserver')} onClick={() => syncSource('jumpserver')}>单独同步 JumpServer</Button>
                <Button disabled={busy || !sourceEnabled('devops')} variant="outline" title={sourceReason('devops')} onClick={() => syncSource('devops')}>单独同步 DevOps</Button>
                <Button disabled={busy || !sourceEnabled('codeup')} variant="outline" title={sourceReason('codeup')} onClick={() => syncSource('codeup')}>单独同步云效</Button>
              </div>
              {anyRunning && (
                <p className="settings-hint" role="status">
                  任务进行中：仍可保存 Cookie（不会影响正在运行的任务，下次同步生效）。
                  {retryableLabels.length > 0
                    ? `本轮同步中${retryableLabels.join('、')}已失败，可立即点击对应「单独同步」并发重试；`
                    : '同步与版本切换需等待任务结束；'}
                  单个数据源仅在其本轮采集失败后才能并发重试，其余来源需等待任务结束。
                </p>
              )}
              <p className="settings-hint">
                同步 DevOps、云效、Local GitLab、JumpServer、上海 ECS、CLB、NAT（DNAT IP / 端口映射）和 DMS 数据库实例。ECS 项目名称目录沿用前版，实例及项目 ID 每次重新采集。每次同步新建目录，完整采集并验证后才启用。同步可能需要数十分钟。
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
                {state.job.logTail ? (
                  <details className="settings-log">
                    <summary>
                      {state.job.status === 'running'
                        ? '查看完整合并日志（实时刷新）'
                        : '查看完整合并日志'}
                    </summary>
                    <pre>{state.job.logTail}</pre>
                  </details>
                ) : null}
                {state.job.failures && state.job.failures.length > 0 ? (
                  <details className="settings-log settings-failures" open>
                    <summary>
                      采集失败原因（{state.job.failures.length} 项）
                    </summary>
                    <ul className="settings-failure-list">
                      {state.job.failures.map((f, i) => (
                        <li key={i} className="settings-failure-item">
                          <span className="settings-failure-source">{f.source}</span>
                          <span className="settings-failure-type">
                            {f.credential ? `${f.type}（凭据：${f.credential}）` : f.type}
                          </span>
                          <span className="settings-failure-message">{f.message}</span>
                        </li>
                      ))}
                    </ul>
                  </details>
                ) : null}
                <small>
                  {date(state.job.startedAt)} · {state.job.id}
                </small>
              </section>
            )}
            {(state.extraJobs ?? []).length > 0 && (
              <section className="panel settings-panel" aria-live="polite">
                <h2>并发重试任务</h2>
                <p className="settings-hint">
                  全量同步该来源已失败时发起的单独重试；结果构建为独立版本，不影响进行中的主任务。
                </p>
                {(state.extraJobs ?? []).map((job) => (
                  <div key={job.id} className="settings-extra-job">
                    <p>
                      <strong>{sourceLabel[job.kind] ?? job.kind}</strong> ·{' '}
                      {job.status === 'running'
                        ? '进行中'
                        : job.status === 'succeeded'
                          ? '已完成'
                          : '失败'}
                    </p>
                    <p>{job.phase}</p>
                    {job.progress?.total ? (
                      <p className="settings-progress">
                        进度：{job.progress.completed ?? 0} / {job.progress.total}
                        {job.progress.current ? ` · 当前：${job.progress.current}` : ''}
                      </p>
                    ) : null}
                    {job.logTail ? (
                      <details className="settings-log">
                        <summary>查看完整日志</summary>
                        <pre>{job.logTail}</pre>
                      </details>
                    ) : null}
                    {job.failures && job.failures.length > 0 ? (
                      <details className="settings-log settings-failures" open>
                        <summary>采集失败原因（{job.failures.length} 项）</summary>
                        <ul className="settings-failure-list">
                          {job.failures.map((f, i) => (
                            <li key={i} className="settings-failure-item">
                              <span className="settings-failure-source">{f.source}</span>
                              <span className="settings-failure-type">
                                {f.credential ? `${f.type}（凭据：${f.credential}）` : f.type}
                              </span>
                              <span className="settings-failure-message">{f.message}</span>
                            </li>
                          ))}
                        </ul>
                      </details>
                    ) : null}
                    <small>
                      {date(job.startedAt)} · {job.id}
                    </small>
                  </div>
                ))}
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
                <Button disabled={busy || anyRunning} onClick={() => generateTopology()}>
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
                            ? `${v.counts.applications} 应用 / ${v.counts.repositories} 仓库 / ${v.counts.servers} 堡垒机资产 / ${v.counts.ecs ?? '—'} ECS / ${v.counts.clb ?? '未采集'} CLB / ${v.counts.nat ?? '未采集'} NAT / ${v.counts.dnat ?? '未采集'} DNAT / ${v.counts.dms ?? '未采集'} DMS`
                            : '—'}
                        </TableCell>
                        <TableCell>{v.error || v.note || '—'}</TableCell>
                        <TableCell>
                          <Button
                            variant="outline"
                            disabled={
                              busy ||
                              anyRunning ||
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

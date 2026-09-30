'use client';
import { Fragment, Suspense, useEffect, useState } from 'react';
import { flushSync } from 'react-dom';
import { useSearchParams } from 'next/navigation';
import {
  Search,
  GitBranch,
  ChevronDown,
  ChevronRight,
  ChevronLeft,
  ShieldAlert,
  CircleAlert,
  CircleHelp,
  Server,
  TriangleAlert,
  X,
  Download,
} from 'lucide-react';
import { Shell } from '../page';
import { Button } from '@/components/ui/button';
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
  TooltipProvider,
} from '@/components/ui/tooltip';
import { Input } from '@/components/ui/input';
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from '@/components/ui/table';
import {
  apps,
  inventoryDate,
  envs,
  labels,
  info,
  sharedProductionAppIds,
  environmentPresent,
  matchesEnvironmentCombination,
  matchesDeploymentPeriod,
  latestSuccessfulPushIn,
  overYearUndeployed,
  repositoryCategory,
  type Env,
  type App,
} from '@/lib/inventory';
import { appHasProcessOnIp, appProcessStatusSummary, envServerVerdicts, ipServerState, ipSpecSummary, type ServerVerdicts } from '@/lib/process-comparison';
import { appRepositoryNameMatches } from '@/lib/repositories';
type Filter = 'all' | 'single' | 'unknown';

// 环境列文案，页面展示与 CSV 导出共用同一口径。
function envCellText(a: App, e: Env): string {
  const s = info(a, e);
  if (environmentPresent(a, e) === false) return '未配置';
  if (s.unknown && !s.ips.length) return '待核实';
  return `${s.ips.length} 台`;
}

// 环境规格列文案：每个 IP 附带服务器规格（多少核、多少内存），无数据标注未采集。
function envSpecCellText(a: App, e: Env): string {
  return info(a, e)
    .ips.map((ip) => `${ip}：${ipSpecSummary(ip) || '未采集'}`)
    .join('、');
}

// 服务器登录状态列：已收录服务器的登录情况（未收录的由「服务器未收录」列表达）。
function loginVerdictText(v: ServerVerdicts): string {
  if (v.canLogin && v.cannotLogin) return '部分不能登录';
  if (v.cannotLogin) return '不能登录';
  if (v.canLogin) return '能登录';
  return '—';
}

// 进程未发现列：未登录/未收录的服务器无法核实进程，先标注原因，
// 仅对可登录服务器给出 是（进程匹配）/ 否（进程未发现）。
function processVerdictText(v: ServerVerdicts): string {
  const parts: string[] = [];
  if (v.cannotLogin) parts.push('未登录');
  if (v.notCollected) parts.push('未收录');
  if (v.canLogin) parts.push(v.unmatched ? '否' : '是');
  return parts.join('、') || '—';
}

function csvCell(value: string): string {
  if (value.includes(',') || value.includes('"') || value.includes('\n')) {
    return '"' + value.replace(/"/g, '""') + '"';
  }
  return value;
}

// 按当前查询/筛选结果导出应用列表。登录状态与进程核实以环境为单位，
// 因此每个应用固定导出三行（测试/仿真/生产各一行）。
function exportApplicationsCsv(rows: App[]) {
  const header = [
    '应用ID',
    '应用名称',
    '环境',
    '仓库类型',
    '仓库地址',
    '生产单点',
    '服务器共用（生产环境）',
    '进程未发现',
    '服务器未收录',
    '服务器登录状态',
    '服务器数量',
    '服务器规格',
    '最近成功 Push In',
    'HTTP 名',
    '端口号',
  ];
  const lines = [header.map(csvCell).join(',')];
  for (const a of rows) {
    for (const e of envs) {
      const v = envServerVerdicts(a, e);
      lines.push(
        [
          a.id,
          a.name,
          labels[e],
          repositoryCategory(a.repository),
          a.repository || '未提供',
          info(a, 'PRODUCT').single ? '是' : '否',
          sharedProductionAppIds.has(a.id) ? '是' : '否',
          processVerdictText(v),
          v.notCollected ? '是' : '否',
          loginVerdictText(v),
          envCellText(a, e),
          envSpecCellText(a, e),
          latestSuccessfulPushIn(a, e) || '无成功记录',
          a.http || '未提供',
          a.port || '未提供',
        ]
          .map(csvCell)
          .join(','),
      );
    }
  }
  const bom = '\uFEFF';
  const blob = new Blob([bom + lines.join('\r\n')], {
    type: 'text/csv;charset=utf-8;',
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = 'applications.csv';
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function repositorySearchValue(value: string) {
  return value
    .replace(/^https?:\/\/gitlab\.dev\.thomascook\.com\.cn/i, 'gitlab.dev.thomascook.com.cn')
    .replace(/^https?:\/\/(codeup\.aliyun\.com|code\.aliyun\.com)/i, 'code.aliyun.com')
    .replace(/\.git\/?$/, '')
    .toLowerCase();
}

// 服务器 IP 的 JumpServer 收录/登录状态标记：红色=未收录，绿色=可登录，灰色=不能登录。
function ServerStateMark({ ip }: { ip: string }) {
  const meta = {
    not_collected: {
      className: 'server-state not-collected',
      label: '未被JumpServer收录',
      tooltip: '该服务器 IP 未被 JumpServer 收录',
    },
    can_login: {
      className: 'server-state can-login',
      label: '',
      tooltip: '服务器已被 JumpServer 收录，可登录',
    },
    cannot_login: {
      className: 'server-state cannot-login',
      label: '服务器不能登录',
      tooltip: '服务器已被 JumpServer 收录，但无法登录',
    },
  }[ipServerState(ip)];
  return (
    <Tooltip>
      <TooltipTrigger className={meta.className} aria-label={meta.tooltip}>
        <Server size={14} />
        {meta.label && <span>{meta.label}</span>}
      </TooltipTrigger>
      <TooltipContent>{meta.tooltip}</TooltipContent>
    </Tooltip>
  );
}
function AppList() {
  const params = useSearchParams();
  const targetId = params.get('appId');
  const [query, setQuery] = useState('');
  const [repoQuery, setRepoQuery] = useState('');
  const [filter, setFilter] = useState<Filter>('all');
  const [env, setEnv] = useState<Env | 'ALL'>('ALL');
  const [combination, setCombination] = useState<Env[] | null>(null);
  const [publishEnv, setPublishEnv] = useState<Env | ''>('');
  const [publishPeriod, setPublishPeriod] = useState('');
  const [page, setPage] = useState(1);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  useEffect(() => {
    setCombination(null);setPublishEnv('');setPublishPeriod('');setRepoQuery('');
    const f = params.get('filter');
    const e = params.get('env');
    setFilter(f === 'single' || f === 'unknown' ? f : 'all');
    setEnv(envs.includes(e as Env) ? (e as Env) : 'ALL');
    const index = apps.findIndex((app) => app.id === params.get('appId'));
    if (index >= 0) {
      setQuery('');
      setFilter('all');
      setEnv('ALL');
      setPage(Math.floor(index / 20) + 1);
    } else {
      const search = params.get('q') || '';
      setQuery(search);
      setPage(1);
      if (search.trim()) {
        const matching: Record<string, boolean> = {};
        for (const app of apps)
          for (const environment of envs) {
            if (
              info(app, environment).ips.some((ip) =>
                ip.includes(search.trim()),
              )
            )
              matching[`${app.id}-${environment}`] = true;
          }
        setExpanded(matching);
      }
    }
  }, [params]);
  useEffect(() => {
    if (!targetId) return;
    const frame = requestAnimationFrame(() => {
      const row = document.getElementById(`app-${targetId}`);
      row?.scrollIntoView({ block: 'center' });
      row?.focus({ preventScroll: true });
    });
    return () => cancelAnimationFrame(frame);
  }, [targetId, page]);
  useEffect(() => {
    const context = (
      document as Document & {
        modelContext?: {
          registerTool: (
            tool: unknown,
            options: { signal: AbortSignal },
          ) => void | Promise<void>;
        };
      }
    ).modelContext;
    if (!context) return;
    const lifecycle = new AbortController();
    try {
      Promise.resolve(
        context.registerTool(
          {
            name: 'filter_applications',
            title: '筛选应用',
            description:
              '按名称搜索并筛选生产单点或数据异常应用，更新当前列表。',
            inputSchema: {
              type: 'object',
              properties: {
                query: { type: 'string' },
                filter: { type: 'string', enum: ['all', 'single', 'unknown'] },
              },
              additionalProperties: false,
            },
            annotations: { readOnlyHint: false, untrustedContentHint: true },
            execute: (input: unknown) => {
              if (!input || typeof input !== 'object')
                throw new Error('参数必须是对象');
              const value = input as { query?: unknown; filter?: unknown };
              if (
                Object.keys(value).some(
                  (k) => k !== 'query' && k !== 'filter',
                ) ||
                (value.query !== undefined &&
                  typeof value.query !== 'string') ||
                (value.filter !== undefined &&
                  !['all', 'single', 'unknown'].includes(String(value.filter)))
              )
                throw new Error('筛选参数无效');
              flushSync(() => {
                setQuery((value.query as string) || '');
                setFilter((value.filter as Filter) || 'all');
                setEnv('ALL');
                setCombination(null);setPublishEnv('');setPublishPeriod('');setRepoQuery('');
                setPage(1);
              });
              return {
                query: value.query || '',
                filter: value.filter || 'all',
              };
            },
          },
          { signal: lifecycle.signal },
        ),
      ).catch(() => {});
    } catch {}
    return () => lifecycle.abort();
  }, []);
  const filtered = apps.filter((a) => {
    const needle = query.trim().toLowerCase();
    const match = [
      a.name,
      a.id,
      a.http,
      a.port,
      a.repository || '',
      ...envs.flatMap((e) => info(a, e).ips),
    ].some((v) => v.toLowerCase().includes(needle) || (a.repository && repositorySearchValue(a.repository).includes(repositorySearchValue(query))));
    const repositoryUrls = [
      a.repository,
      ...envs.flatMap((e) => info(a, e).rows.map((r) => r.repository)),
    ];
    const selected = env === 'ALL' ? envs : [env];
    return (
      match &&
      appRepositoryNameMatches(a.id, repositoryUrls, repoQuery) &&
      matchesEnvironmentCombination(a,combination) &&
      matchesDeploymentPeriod(a,publishEnv,publishPeriod) &&
      (filter === 'single'
        ? info(a, 'PRODUCT').single
        : filter === 'unknown'
          ? selected.some((e) => info(a, e).unknown)
          : true) &&
      (env === 'ALL' || filter === 'unknown' || info(a, env).ips.length > 0)
    );
  });
  const pages = Math.max(1, Math.ceil(filtered.length / 20));
  const current = Math.min(page, pages);
  const visible = filtered.slice((current - 1) * 20, current * 20);
  const reset = () => {
    setCombination(null);setPublishEnv('');setPublishPeriod('');
    setQuery('');
    setRepoQuery('');
    setFilter('all');
    setEnv('ALL');
    setPage(1);
  };
  return (
    <Shell active="apps">
      <TooltipProvider>
        <main>
          <div className="page-heading">
            <div>
              <div className="eyebrow">APPLICATION INVENTORY</div>
              <h1>
                应用列表 <span className="count-pill">{apps.length}</span>
              </h1>
              <p>按应用查看环境分布，点击服务器数量展开部署明细。</p>
            </div>
            <span className="subtle-tag">APPLICATIONS / ENVIRONMENTS</span>
          </div>
          <div className="list-notice">
            <CircleAlert size={16} />
            <span>
              服务器数量按应用、环境内 IP 去重。<b>待核实</b>表示读取失败或缺少
              IP；HTTP 名与端口来自同批次应用清单。
            </span>
          </div>
          <section className="list-panel">
            <div className="list-toolbar">
              <div className="search-field">
                <Search size={17} />
                <Input
                  aria-label="搜索应用、HTTP名、端口或IP"
                  placeholder="搜索应用、HTTP 名、端口或 IP…"
                  value={query}
                  onChange={(e) => {
                    setQuery(e.target.value);
                    setPage(1);
                  }}
                />
                {query && (
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label="清空搜索"
                    onClick={() => {
                      setQuery('');
                      setPage(1);
                    }}
                  >
                    <X size={14} />
                  </Button>
                )}
              </div>
              <div className="search-field">
                <GitBranch size={17} />
                <Input
                  aria-label="搜索 Git 仓库名"
                  placeholder="搜索 Git 仓库名，如 tc-taicang…"
                  value={repoQuery}
                  onChange={(e) => {
                    setRepoQuery(e.target.value);
                    setPage(1);
                  }}
                />
                {repoQuery && (
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label="清空 Git 仓库名搜索"
                    onClick={() => {
                      setRepoQuery('');
                      setPage(1);
                    }}
                  >
                    <X size={14} />
                  </Button>
                )}
              </div>
              <div className="filter-tabs">
                {(
                  [
                    ['all', '全部应用'],
                    ['single', '生产单点'],
                    ['unknown', '数据异常'],
                  ] as const
                ).map(([f, label]) => (
                  <Button
                    key={f}
                    variant="ghost"
                    className={filter === f ? 'selected' : ''}
                    onClick={() => {
                      setFilter(f);
                      setPage(1);
                    }}
                  >
                    {f === 'single' && <ShieldAlert size={14} />} {label}
                  </Button>
                ))}
              </div>
            </div>
            <div className="application-advanced-filters">
              <fieldset>
                <legend>环境组合（精确匹配）</legend>
                <div className="application-filter-controls">
                  <Button variant={combination===null?'secondary':'outline'} size="sm" onClick={()=>{setCombination(null);setEnv('ALL');setPage(1);}}>不限环境</Button>
                  <Button variant={combination?.length===0?'secondary':'outline'} size="sm" onClick={()=>{setCombination([]);setEnv('ALL');setPage(1);}}>三个环境都没有</Button>
                  {envs.map(e=><label key={e}><input type="checkbox" checked={combination?.includes(e)??false} onChange={event=>{setCombination(prev=>event.target.checked?[...(prev||[]),e]:(prev||[]).filter(v=>v!==e));setEnv('ALL');setPage(1);}} />{labels[e]}</label>)}
                </div>
                <p>{combination===null ? (env==='ALL'?'当前不限环境；勾选后启用精确匹配。':`当前包含：${labels[env]}。`) : combination.length ? `仅包含：${combination.map(e=>labels[e]).join('、')}；未勾选的环境必须不存在。` : '三个环境都不存在。'} 环境配置读取异常的应用不归入“没有环境”。</p>
              </fieldset>
              <fieldset>
                <legend>最近 Push In 成功时间</legend>
                <div className="application-filter-controls">
                  <select aria-label="部署时间所属环境" value={publishEnv} onChange={e=>{setPublishEnv(e.target.value as Env|'');setPublishPeriod('');setPage(1);}}>
                    <option value="">先选择环境</option>{envs.map(e=><option key={e} value={e}>{labels[e]}</option>)}
                  </select>
                  <select aria-label="成功部署时间范围" disabled={!publishEnv} value={publishPeriod} onChange={e=>{setPublishPeriod(e.target.value);setPage(1);}}>
                    <option value="">不限时间</option><option value="week">一个星期内</option><option value="fortnight">半个月内（15 天）</option><option value="month">一个月内</option><option value="quarter">三个月内</option><option value="half">半年内</option><option value="year">一年内</option><option value="older">超过一年</option>
                  </select>
                </div>
                <p>列表默认显示全部环境中最近的 SUCCESS Push In 时间；选择环境后显示该环境的时间，以快照时间为基准；没有成功记录不匹配时间条件。</p>
              </fieldset>
            </div>
            <div className="result-line">
              <span aria-live="polite">
                共 <b>{filtered.length}</b> 个应用
                {filter === 'single'
                  ? ' · 生产环境单点'
                  : filter === 'unknown'
                    ? ' · 环境数据待核实'
                    : ''}
              </span>
              <div className="result-actions">
                <Button
                  variant="default"
                  size="lg"
                  className="export-button"
                  onClick={() => exportApplicationsCsv(filtered)}
                  disabled={!filtered.length}
                  aria-label="按当前查询和筛选条件导出应用列表 CSV（每个应用每个环境一行）"
                >
                  <Download size={16} />
                  导出 CSV（{filtered.length} 个应用）
                </Button>
                <Button variant="ghost" size="sm" onClick={reset}>
                  重置筛选
                </Button>
              </div>
            </div>
            <Table className="app-table">
              <TableHeader>
                <TableRow>
                  <TableHead>应用名称</TableHead>
                  {envs.map((e) => (
                    <TableHead key={e}>
                      <span className={'env-dot ' + e} />
                      {labels[e]}
                    </TableHead>
                  ))}
                  <TableHead>仓库分类</TableHead>
                  <TableHead>{publishEnv ? labels[publishEnv] : '全部环境'}最近成功 Push In</TableHead>
                  <TableHead>HTTP 名</TableHead>
                  <TableHead>端口号</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {visible.map((a) => {
                  const processStatus = appProcessStatusSummary(a);
                  return (
                  <Fragment key={a.id}>
                    <TableRow
                      id={`app-${a.id}`}
                      tabIndex={-1}
                      className={
                        targetId === a.id ? 'target-app-row' : undefined
                      }
                    >
                      <TableCell>
                        <div className="app-name app-name-with-alert">
                          <a
                            href={`http://devops.folidaymall.com/#/project/application/info?id=${encodeURIComponent(a.id)}`}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="hover:underline"
                            title="在新标签页打开 DevOps 应用详情"
                          >
                            {a.name}
                          </a>
                          {sharedProductionAppIds.has(a.id) && (
                            <Tooltip>
                              <TooltipTrigger
                                className="shared-alert"
                                aria-label="服务器共用（生产环境）"
                              >
                                <CircleAlert size={16} />
                              </TooltipTrigger>
                              <TooltipContent>
                                服务器共用（生产环境）
                              </TooltipContent>
                            </Tooltip>
                          )}
                          {processStatus.hasUnmatched && (
                            <Tooltip>
                              <TooltipTrigger
                                className="shared-alert"
                                aria-label="存在环境未在 JumpServer 进程中发现此应用"
                              >
                                <TriangleAlert size={16} />
                              </TooltipTrigger>
                              <TooltipContent>
                                存在环境的服务器已收录但未在 JumpServer 进程中发现此应用
                              </TooltipContent>
                            </Tooltip>
                          )}
                          {processStatus.hasNotCollected && (
                            <Tooltip>
                              <TooltipTrigger
                                className="shared-alert"
                                aria-label="存在环境的服务器 IP 未被 JumpServer 收录"
                              >
                                <CircleHelp size={16} />
                              </TooltipTrigger>
                              <TooltipContent>
                                存在环境的服务器 IP 未被 JumpServer 收录
                              </TooltipContent>
                            </Tooltip>
                          )}
                        </div>
                        <div className="app-id">
                          APP · {a.id}
                          {info(a, 'PRODUCT').single && (
                            <span className="single-badge">生产单点</span>
                          )}
                        </div>
                      </TableCell>
                      {envs.map((e) => {
                        const s = info(a, e),
                          key = `${a.id}-${e}`;
                        return (
                          <TableCell key={e}>
                            <Button
                              variant="ghost"
                              className={
                                'server-count ' + (s.unknown ? 'uncertain' : '')
                              }
                              aria-expanded={!!expanded[key]}
                              aria-controls={
                                expanded[key] ? `details-${key}` : undefined
                              }
                              aria-label={`${a.name} ${labels[e]}，${s.ips.length}台${s.unknown ? '，数据待核实' : ''}，展开或收起详情`}
                              onClick={() =>
                                setExpanded((prev) => ({
                                  ...prev,
                                  [key]: !prev[key],
                                }))
                              }
                            >
                              {expanded[key] ? (
                                <ChevronDown />
                              ) : (
                                <ChevronRight />
                              )}
                              {envCellText(a, e)}
                              {s.unknown && s.ips.length > 0 && (
                                <CircleAlert size={12} />
                              )}
                            </Button>
                          </TableCell>
                        );
                      })}
                      <TableCell>{repositoryCategory(a.repository)}</TableCell>
                      <TableCell>{latestSuccessfulPushIn(a,publishEnv || undefined)||'无成功记录'}</TableCell>
                      <TableCell>
                        <span className="http-name">{a.http || '未提供'}</span>
                      </TableCell>
                      <TableCell>
                        <code className="port">{a.port || '未提供'}</code>
                      </TableCell>
                    </TableRow>
                    {envs
                      .filter((e) => expanded[`${a.id}-${e}`])
                      .map((e) => {
                        const s = info(a, e);
                        return (
                          <TableRow key={e} className="expanded-row">
                            <TableCell colSpan={8}>
                              <div
                                className="server-details"
                                id={`details-${a.id}-${e}`}
                              >
                                <div className="details-heading">
                                  <span>
                                    <Server size={16} />
                                    {a.name} <b>/</b> {labels[e]}
                                  </span>
                                  <span>
                                    {s.ips.length} 个已知服务器 IP ·{' '}
                                    {s.rows.length} 条记录
                                  </span>
                                </div>
                                <Table className="detail-table">
                                  <TableHeader>
                                    <TableRow>
                                      <TableHead>服务器 IP</TableHead>
                                      <TableHead>规格</TableHead>
                                      <TableHead>端口号</TableHead>
                                      <TableHead>Git 分支</TableHead>
                                      <TableHead>
                                        最后发布时间（Push In）
                                      </TableHead>
                                      <TableHead>发布状态</TableHead>
                                      <TableHead>部署 ID</TableHead>
                                      <TableHead>配置 ID</TableHead>
                                      <TableHead>读取状态</TableHead>
                                      <TableHead>说明</TableHead>
                                    </TableRow>
                                  </TableHeader>
                                  <TableBody>
                                    {s.rows.map((r, i) => (
                                      <TableRow key={`${r.deploy}-${i}`}>
                                        <TableCell>
                                          <code>{r.ip || '未返回 IP'}</code>
                                          {r.ip && <ServerStateMark ip={r.ip} />}
                                          {r.ip && !appHasProcessOnIp(a.name, r.ip) && (
                                            <Tooltip>
                                              <TooltipTrigger
                                                className="process-miss-icon"
                                                aria-label={`未在 JumpServer 进程中发现 ${a.name}`}
                                              >
                                                <TriangleAlert size={14} className="text-red-600" />
                                              </TooltipTrigger>
                                              <TooltipContent>
                                                未在 JumpServer 进程中发现 {a.name}
                                              </TooltipContent>
                                            </Tooltip>
                                          )}
                                        </TableCell>
                                        <TableCell>
                                          {ipSpecSummary(r.ip) || '—'}
                                        </TableCell>
                                        <TableCell>
                                          {r.port || a.port || '未提供'}
                                        </TableCell>
                                        <TableCell title={r.branchSource}>
                                          {r.branch || '未提供'}
                                        </TableCell>
                                        <TableCell title={r.publishTimeSource}>
                                          {r.lastPublishedAt || '无记录'}
                                        </TableCell>
                                        <TableCell title={overYearUndeployed(a, e) ? '当前环境存在服务器，但没有一年内成功 Push In 记录' : undefined}>
                                          {overYearUndeployed(a, e)
                                            ? '超过一年未部署'
                                            : r.publishStatus || '未采集'}
                                        </TableCell>
                                        <TableCell>{r.deploy || '—'}</TableCell>
                                        <TableCell>{r.config || '—'}</TableCell>
                                        <TableCell>
                                          <span
                                            className={
                                              r.status === '成功' && r.ip
                                                ? 'status-ok'
                                                : 'status-error'
                                            }
                                          >
                                            {r.status || '未知'}
                                          </span>
                                        </TableCell>
                                        <TableCell className="error-text">
                                          {r.error ||
                                            (r.ip
                                              ? '—'
                                              : '缺少服务器 IP，数量待核实')}
                                        </TableCell>
                                      </TableRow>
                                    ))}
                                  </TableBody>
                                </Table>
                                {!s.rows.length && (
                                  <p className="empty-details">
                                    清单中未找到该应用环境的记录，数量待核实。
                                  </p>
                                )}
                              </div>
                            </TableCell>
                          </TableRow>
                        );
                      })}
                  </Fragment>
                  );
                })}
                {!visible.length && (
                  <TableRow>
                    <TableCell colSpan={8}>
                      <div className="empty-state">
                        <Search size={28} />
                        <h2>未找到匹配的应用</h2>
                        <p>尝试其他关键词，或重置筛选条件。</p>
                        <Button variant="outline" onClick={reset}>
                          查看全部应用
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
            <div className="pagination">
              <span>
                显示 {filtered.length ? (current - 1) * 20 + 1 : 0}–
                {Math.min(current * 20, filtered.length)} 条，共{' '}
                {filtered.length} 条
              </span>
              <div>
                <Button
                  variant="outline"
                  aria-label="上一页"
                  disabled={current === 1}
                  onClick={() => setPage(current - 1)}
                >
                  <ChevronLeft />
                </Button>
                <span>
                  第 {current} / {pages} 页
                </span>
                <Button
                  variant="outline"
                  aria-label="下一页"
                  disabled={current === pages}
                  onClick={() => setPage(current + 1)}
                >
                  <ChevronRight />
                </Button>
              </div>
            </div>
          </section>
          <footer className="page-footer">
            <span>
              <i />
              应用按应用 ID 聚合 · 同名不同 ID 分开展示
            </span>
            <span>
              快照时间：{inventoryDate.replace('T', ' ').slice(0, 19)}
            </span>
          </footer>
        </main>
      </TooltipProvider>
    </Shell>
  );
}
export default function Applications() {
  return (
    <Suspense
      fallback={
        <Shell active="apps">
          <main>正在加载应用清单…</main>
        </Shell>
      }
    >
      <AppList />
    </Suspense>
  );
}

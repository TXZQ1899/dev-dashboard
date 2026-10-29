'use client';
import { useRef, useState, useEffect } from 'react';
import { ChevronRight, Search, Download, ListChecks } from 'lucide-react';
import { ServerInspections } from '@/components/server-inspections';
import { JumpserverOverview } from '@/components/jumpserver-overview';
import { ApplicationCheckLink } from '@/components/application-check-link';
import { IpMetaCell } from '@/components/ip-meta-cell';
import { Shell } from '@/app/page';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import {
  Collapsible,
  CollapsibleTrigger,
  CollapsibleContent,
} from '@/components/ui/collapsible';
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from '@/components/ui/table';
import {
  jumpserver,
  topGroups,
  distribution,
  serverGroups,
} from '@/lib/jumpserver';
import { apps } from '@/lib/inventory';
import { formatBeijingTime } from '@/lib/datetime';
import { parseIpList } from '@/lib/process-comparison';
import { type Asset } from '@/lib/server-processes';
import {
  buildIpManagedRows,
  managedRowsCsv,
  buildIpSummaryRows,
  summaryRowsCsv,
  nginxConfigRequest,
  formatMemPercent,
  formatMemorySize,
  formatPorts,
  DASH,
  type IpManagedRow,
  type IpSummaryRow,
  type NginxConfigResult,
} from '@/lib/ip-managed-query';

// 单机采集完成后页面会重载，用 sessionStorage 找回查询内容并自动重新查询
// （重查不再触发采集，避免循环）。
const RESTORE_KEY = 'ip-set-query-restore';

const colors = [
  '#24866a',
  '#60a5fa',
  '#eab85d',
  '#a78bfa',
  '#ed8796',
  '#5cbaba',
  '#81936b',
  '#d38b56',
  '#7483a0',
  '#b47cb4',
  '#a3a3a3',
];

// 按一组 IP 查询 JumpServer 纳管情况，并按"每个进程 / webapps 目录 / Nginx 配置文件一行"
// 导出应用类型、进程详细与关联的 DevOps 应用信息。
function IpManagedQueryPanel({onRestore}: {onRestore: () => void}) {
  const [input, setInput] = useState('');
  const [rows, setRows] = useState<IpManagedRow[] | null>(null);
  const [summaryRows, setSummaryRows] = useState<IpSummaryRow[] | null>(null);
  const [activeTab, setActiveTab] = useState<'detail' | 'summary'>('detail');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  // 输入框收缩：默认/聚焦时 10 行，失焦且已有内容时收缩为 3 行。
  const [collapsed, setCollapsed] = useState(false);
  const runIdRef = useRef(0);
  // 单机采集任务状态（仅单 IP 查询时自动触发）。
  const [collect, setCollect] = useState<{
    phase: string;
    running: boolean;
  } | null>(null);
  const collectRunRef = useRef(0);

  // 单 IP 查询时自动触发该服务器的单机采集（进程含内存占用信息）。
  // 任务在后台跑，当前结果先用已有快照展示；完成后页面自动刷新。
  async function triggerCollection(ip: string, text: string) {
    const asset = (jumpserver.assets as Asset[]).find((a) => a.ip === ip);
    if (!asset) {
      setCollect({ phase: '该 IP 未被 JumpServer 收录，无法触发单机采集', running: false });
      return;
    }
    const runId = ++collectRunRef.current;
    setCollect({ phase: '正在启动单机采集任务', running: true });
    try {
      const response = await fetch('/api/settings/jumpserver-inspect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ asset: asset.id }),
      });
      const result = (await response.json()) as { error?: string };
      if (!response.ok) throw new Error(result.error || '采集任务启动失败');
      // 单机采集包含登录、进程、Nginx 与版本构建切换，给足等待时间。
      const deadline = Date.now() + 20 * 60 * 1000;
      while (Date.now() < deadline) {
        await new Promise((resolve) => setTimeout(resolve, 4000));
        if (runId !== collectRunRef.current) return;
        const state = (await fetch('/api/settings/state', { cache: 'no-store' }).then(
          (r) => r.json(),
        )) as { job?: { kind?: string; status?: string; phase?: string } | null };
        const job = state.job;
        if (job?.kind === 'jumpserver_asset' && job.status !== 'running') {
          if (job.status === 'succeeded') {
            sessionStorage.setItem(RESTORE_KEY, text);
            window.location.reload();
          } else {
            setCollect({ phase: job.phase || '采集失败', running: false });
          }
          return;
        }
        if (job?.kind === 'jumpserver_asset') {
          setCollect({ phase: job.phase || '采集中', running: true });
        }
      }
      setCollect({ phase: '采集任务超时未完成，请稍后手动刷新页面', running: false });
    } catch (e) {
      setCollect({
        phase: e instanceof Error ? e.message : '采集任务启动失败',
        running: false,
      });
    }
  }

  async function runQuery(text = input, triggerCollect = true) {
    const ips = parseIpList(text);
    if (!ips.length) return;
    const runId = ++runIdRef.current;
    setBusy(true);
    setError('');
    setRows(null);
    setSummaryRows(null);
    if (triggerCollect && ips.length === 1) {
      void triggerCollection(ips[0], text);
    } else if (triggerCollect) {
      collectRunRef.current += 1; // 多 IP 查询终止进行中的采集等待
      setCollect(null);
    }
    const nginxByIp = new Map<string, NginxConfigResult>();
    await Promise.all(
      ips.map(async (ip) => {
        const req = nginxConfigRequest(ip);
        if (!req) return;
        try {
          const response = await fetch(
            '/api/settings/nginx-configs?' + new URLSearchParams(req),
            { cache: 'no-store' },
          );
          const result = (await response.json()) as {
            files?: { path: string }[];
            error?: string;
          };
          if (!response.ok) throw new Error(result.error || '读取失败');
          nginxByIp.set(ip, { files: result.files || [] });
        } catch (e) {
          nginxByIp.set(ip, {
            files: [],
            error: e instanceof Error ? e.message : '读取失败',
          });
        }
      }),
    );
    if (runId !== runIdRef.current) return;
    setRows(buildIpManagedRows(ips, apps, nginxByIp));
    setSummaryRows(buildIpSummaryRows(ips));
    setBusy(false);
  }

  // 采集完成重载后恢复上次查询（triggerCollect=false，不再触发采集）。
  useEffect(() => {
    const saved = sessionStorage.getItem(RESTORE_KEY);
    if (!saved) return;
    onRestore();
    sessionStorage.removeItem(RESTORE_KEY);
    setInput(saved);
    void runQuery(saved, false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function clearAll() {
    runIdRef.current += 1;
    collectRunRef.current += 1;
    setInput('');
    setRows(null);
    setSummaryRows(null);
    setActiveTab('detail');
    setError('');
    setBusy(false);
    setCollapsed(false);
    setCollect(null);
  }

  function exportCsv() {
    if (!rows?.length) return;
    const bom = '\uFEFF';
    const blob = new Blob([bom + managedRowsCsv(rows)], {
      type: 'text/csv;charset=utf-8;',
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'jumpserver-ip-managed.csv';
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function exportSummaryCsv() {
    if (!summaryRows?.length) return;
    const bom = '\uFEFF';
    const blob = new Blob([bom + summaryRowsCsv(summaryRows)], {
      type: 'text/csv;charset=utf-8;',
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'jumpserver-ip-summary.csv';
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  const ipCount = rows ? new Set(rows.map((r) => r.ip)).size : 0;
  const managedCount = rows
    ? new Set(rows.filter((r) => r.managed === '已纳管').map((r) => r.ip)).size
    : 0;
  const unmanagedCount = ipCount - managedCount;

  return (
    <section className="panel ip-set-panel">
      <div className="panel-title">
        <div>
          <h2>按 IP 批量查询纳管情况</h2>
          <p>
            每行输入一个 IP（也支持逗号、空格分隔）。已纳管服务器按每个进程、
            每个 Webapps 应用目录、每个 Nginx 配置文件各输出一行；未纳管 IP
            单独标识。
          </p>
        </div>
      </div>
      <div className="ip-set-input-area">
        <Textarea
          className={
            'ip-set-textarea' + (collapsed ? ' ip-set-textarea-collapsed' : '')
          }
          aria-label="IP 地址列表"
          placeholder={'10.58.9.10\n10.58.9.11\n10.179.1.224'}
          value={input}
          rows={collapsed ? 3 : 10}
          onFocus={() => setCollapsed(false)}
          onBlur={() => setCollapsed(!!input.trim())}
          onChange={(e) => setInput(e.target.value)}
        />
        <div className="ip-set-actions">
          <Button
            size="default"
            onClick={() => runQuery()}
            disabled={!input.trim() || busy}
          >
            <Search size={15} />
            {busy ? '查询中…' : '查询'}
          </Button>
          <Button
            variant="ghost"
            size="default"
            onClick={clearAll}
            disabled={busy}
          >
            清空
          </Button>
        </div>
      </div>
      {!rows && (
        <p className="ip-set-hint">
          输入 IP 后点击查询；Nginx、MySQL、Redis、MQ
          等共享中间件不关联 DevOps 应用，Tomcat
          按实例目录名尝试关联。仅查询 1 个 IP
          时会自动触发该服务器的单机采集（含进程内存占用信息），完成后自动刷新结果
        </p>
      )}
      {error && (
        <p role="alert" className="text-sm text-red-700">
          {error}
        </p>
      )}
      {collect && (
        <p
          role="status"
          className={
            'text-sm ' +
            (collect.running ? 'text-muted-foreground' : 'text-amber-700')
          }
        >
          单机采集：{collect.phase}
          {collect.running ? '…' : ''}
        </p>
      )}
      {rows && (
        <div className="ip-set-result">
          <div className="result-line ip-set-summary">
            <span aria-live="polite">
              共 <b>{ipCount}</b> 个 IP · 已纳管 <b>{managedCount}</b> 个 ·
              未纳管 <b>{unmanagedCount}</b> 个
              {activeTab === 'detail'
                ? ` · 明细 ${rows.length} 行`
                : ` · 汇总 ${summaryRows?.length ?? 0} 行`}
            </span>
            <div className="result-actions">
              {activeTab === 'detail' ? (
                <Button
                  size="default"
                  className="export-button"
                  onClick={exportCsv}
                  disabled={!rows.length}
                  aria-label="导出进程明细 CSV"
                >
                  <Download size={15} />
                  导出 CSV
                </Button>
              ) : (
                <Button
                  size="default"
                  className="export-button"
                  onClick={exportSummaryCsv}
                  disabled={!summaryRows?.length}
                  aria-label="导出按IP汇总 CSV"
                >
                  <Download size={15} />
                  导出 CSV
                </Button>
              )}
            </div>
          </div>
          <div role="tablist" aria-label="查询结果视图" className="ip-set-tabs">
            <button
              role="tab"
              aria-selected={activeTab === 'detail'}
              className={'ip-set-tab' + (activeTab === 'detail' ? ' active' : '')}
              onClick={() => setActiveTab('detail')}
            >
              进程明细
            </button>
            <button
              role="tab"
              aria-selected={activeTab === 'summary'}
              className={'ip-set-tab' + (activeTab === 'summary' ? ' active' : '')}
              onClick={() => setActiveTab('summary')}
            >
              按 IP 汇总
            </button>
          </div>
          {activeTab === 'detail' ? (
            <Table className="ip-set-table">
              <TableHeader>
                <TableRow>
                  <TableHead>IP / 名称 / 环境 / 归属</TableHead>
                  <TableHead>是否纳管</TableHead>
                  <TableHead>服务器规格</TableHead>
                  <TableHead>应用类型</TableHead>
                  <TableHead>进程ID</TableHead>
                  <TableHead>进程/应用详细</TableHead>
                  <TableHead>进程命令</TableHead>
                  <TableHead>监听端口</TableHead>
                  <TableHead>内存占用率</TableHead>
                  <TableHead>实际内存</TableHead>
                  <TableHead>DevOps 应用ID</TableHead>
                  <TableHead>DevOps 应用名称</TableHead>
                  <TableHead>最新 Push In 时间</TableHead>
                  <TableHead>应用核验</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((row, i) => (
                  <TableRow
                    key={`${row.ip}-${i}`}
                    className={row.source === 'unmanaged' ? 'ip-miss-row' : ''}
                  >
                    <TableCell>
                      <IpMetaCell
                        ip={row.ip}
                        hostName={row.hostName}
                        envList={row.appEnvs ?? []}
                        envEmpty="blank"
                      />
                    </TableCell>
                    <TableCell>{row.managed}</TableCell>
                    <TableCell className="whitespace-nowrap">{row.spec}</TableCell>
                    <TableCell>{row.appType}</TableCell>
                    <TableCell className="whitespace-nowrap">
                      {row.pid !== undefined ? row.pid : DASH}
                    </TableCell>
                    <TableCell className="break-all">{row.detail}</TableCell>
                    <TableCell className="break-all font-mono text-xs">
                      {row.command ?? DASH}
                    </TableCell>
                    <TableCell className="whitespace-nowrap">
                      {formatPorts(row.ports)}
                    </TableCell>
                    <TableCell>{formatMemPercent(row.memPercent)}</TableCell>
                    <TableCell
                      className="whitespace-nowrap"
                      title={
                        row.vszKB !== undefined
                          ? `VSZ 虚拟内存：${formatMemorySize(row.vszKB)}`
                          : undefined
                      }
                    >
                      {formatMemorySize(row.rssKB)}
                    </TableCell>
                    <TableCell>{row.appId}</TableCell>
                    <TableCell>{row.appName}</TableCell>
                    <TableCell className="whitespace-nowrap">
                      {row.pushIn}
                    </TableCell>
                    <TableCell>
                      {row.source === 'process' && row.pid && (!!row.ports?.length || ['Tomcat', 'Java'].includes(row.appType)) && (() => {
                        const assets = (jumpserver.assets as Asset[]).filter((a) => parseIpList(a.ip).includes(row.ip));
                        return <ApplicationCheckLink asset={assets[0]?.id || ''} pid={row.pid!} disabled={assets.length !== 1 || !!collect?.running} />;
                      })()}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <Table className="ip-set-table">
              <TableHeader>
                <TableRow>
                  <TableHead>IP / 名称 / 环境 / 归属</TableHead>
                  <TableHead>是否纳管</TableHead>
                  <TableHead>服务器规格</TableHead>
                  <TableHead className="text-right">CPU%</TableHead>
                  <TableHead className="text-right">内存%</TableHead>
                  <TableHead>监听端口数</TableHead>
                  <TableHead>进程分类</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {summaryRows?.map((row, i) => (
                  <TableRow
                    key={`${row.ip}-${i}`}
                    className={row.managed === '未纳管' ? 'ip-miss-row' : ''}
                  >
                    <TableCell>
                      <IpMetaCell
                        ip={row.ip}
                        hostName={row.hostName}
                        envList={row.deployEnvs}
                      />
                    </TableCell>
                    <TableCell>{row.managed}</TableCell>
                    <TableCell className="whitespace-nowrap">{row.spec}</TableCell>
                    <TableCell className="text-right whitespace-nowrap">
                      {row.cpuPercent !== undefined ? (
                        <span
                          className={
                            row.cpuPercent >= 80
                              ? 'text-red-600 font-medium'
                              : row.cpuPercent >= 50
                                ? 'text-amber-600'
                                : ''
                          }
                        >
                          {row.cpuPercent}%
                        </span>
                      ) : (
                        DASH
                      )}
                    </TableCell>
                    <TableCell
                      className="text-right whitespace-nowrap"
                      title={
                        row.memTotalMB !== undefined
                          ? `整机内存 ${row.memUsedMB! >= 1024 ? `${(row.memUsedMB! / 1024).toFixed(1)}G` : `${row.memUsedMB}M`} / ${row.memTotalMB >= 1024 ? `${(row.memTotalMB / 1024).toFixed(1)}G` : `${row.memTotalMB}M`}`
                          : undefined
                      }
                    >
                      {row.memPercent !== undefined ? (
                        <span
                          className={
                            row.memPercent >= 80
                              ? 'text-red-600 font-medium'
                              : row.memPercent >= 50
                                ? 'text-amber-600'
                                : ''
                          }
                        >
                          {row.memPercent}%
                        </span>
                      ) : (
                        DASH
                      )}
                    </TableCell>
                    <TableCell
                      className="whitespace-nowrap"
                      title={row.portList.length ? row.portList.join(', ') : undefined}
                    >
                      {row.portCount === null ? DASH : row.portCount}
                    </TableCell>
                    <TableCell>
                      {row.categories.length ? (
                        row.categories.map(([t, n]) => (
                          <span key={t} className="proc-chip">
                            {t} {n}
                          </span>
                        ))
                      ) : (
                        DASH
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}

        </div>
      )}
    </section>
  );
}

export default function JumpServerPage() {
  const [activeTab, setActiveTab] = useState<'overview' | 'servers'>('overview');
  const [processRequest, setProcessRequest] = useState<{ip: string} | null>(null);
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const search = query.trim();
  const groups = serverGroups.map((g) => ({
    ...g,
    assets: g.assets.filter((a) => a.ip.includes(search)),
  }));
  const matched = jumpserver.assets.filter((a) => a.ip.includes(search)).length;
  function updateSearch(value: string) {
    setQuery(value);
    setOpen(
      Object.fromEntries(
        serverGroups.map((g) => [
          g.id,
          !!value.trim() && g.assets.some((a) => a.ip.includes(value.trim())),
        ]),
      ),
    );
  }
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const search = params.get('q');
    if (search) {
      setActiveTab('servers');
      updateSearch(search);
      if (params.get('view') === 'processes') setProcessRequest({ip: search});
    }
  }, []);
  let angle = 0;
  const gradient = distribution
    .map((d, i) => {
      const start = angle;
      angle += (d.value / jumpserver.assets.length) * 360;
      return `${colors[i % colors.length]} ${start}deg ${angle}deg`;
    })
    .join(',');
  return (
    <Shell active="jumpserver">
      <main className="main-content jumpserver-page">
        <div className="page-heading">
          <div>
            <h1>JumpServer 纳管服务器</h1>
            <p>当前账号授权资产 · 登录状态、进程类型、运行时间与命令搜索</p>
          </div>
        </div>
        <div role="tablist" aria-label="JumpServer 页面" className="flex flex-wrap gap-2 mb-6">
          <button type="button" role="tab" id="jump-tab-overview" aria-selected={activeTab==='overview'} aria-controls="jump-panel-overview" className={`rounded border px-4 py-2 text-sm ${activeTab==='overview'?'bg-primary text-primary-foreground':'bg-background'}`} onClick={()=>setActiveTab('overview')}>信息总览</button>
          <button type="button" role="tab" id="jump-tab-servers" aria-selected={activeTab==='servers'} aria-controls="jump-panel-servers" className={`rounded border px-4 py-2 text-sm ${activeTab==='servers'?'bg-primary text-primary-foreground':'bg-background'}`} onClick={()=>setActiveTab('servers')}>服务器与进程</button>
        </div>
        <div id="jump-panel-overview" role="tabpanel" aria-labelledby="jump-tab-overview" hidden={activeTab!=='overview'}><JumpserverOverview onShowProcesses={ip=>{setProcessRequest({ip});setActiveTab('servers');}} /></div>
        <div id="jump-panel-servers" role="tabpanel" aria-labelledby="jump-tab-servers" hidden={activeTab!=='servers'}>
        <ServerInspections processRequest={processRequest} />
        <div className="mt-6">
          <IpManagedQueryPanel onRestore={()=>setActiveTab('servers')} />
        </div>
        <details className="mt-6"><summary className="cursor-pointer p-4 font-medium"><ListChecks size={14} className="inline mr-1" />资产分组概览</summary>
        <section className="panel jump-summary">
          <div>
            <p className="text-sm text-muted-foreground">服务器总数</p>
            <div className="jump-total">
              {jumpserver.assets.length}
              <small> 台</small>
            </div>
            <p>
              {new Set(jumpserver.assets.map((a) => a.ip)).size} 个独立 IP ·{' '}
              {topGroups.length} 个一级分组
            </p>
            <p className="mt-3 text-sm text-muted-foreground">
              采集时间：{formatBeijingTime(jumpserver.collectedAt)}
            </p>
          </div>
          <div
            className="jump-pie"
            role="img"
            aria-label={`服务器分组占比：${distribution.map((d) => `${d.name} ${d.value} 台，占 ${((d.value / jumpserver.assets.length) * 100).toFixed(1)}%`).join('；')}`}
            style={{ background: `conic-gradient(${gradient})` }}
          />
          <div className="jump-legend">
            {distribution.map((d, i) => (
              <div key={d.name}>
                <span style={{ background: colors[i % colors.length] }} />
                <span>
                  {d.name}（{d.value}）
                </span>
                <strong>
                  {((d.value / jumpserver.assets.length) * 100).toFixed(1)}%
                </strong>
              </div>
            ))}
          </div>
          <p className="jump-note">
            饼图按资产去重：跨组服务器单列，其余分组展示独占数量。下方原始分组包含子组及跨组资产，数量不可直接相加。
          </p>
        </section>
        <section className="panel environment-servers">
          <div className="panel-title">
            <div>
              <h2>按分组查看服务器 IP</h2>
              <p>点击组名展开；支持完整或部分 IP 搜索。</p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-3 px-6 pb-5">
            <Input
              type="search"
              className="max-w-sm"
              aria-label="搜索 JumpServer 服务器 IP"
              placeholder="搜索服务器 IP，支持部分 IP"
              value={query}
              onChange={(e) => updateSearch(e.target.value)}
            />
            {query && (
              <Button variant="ghost" onClick={() => updateSearch('')}>
                清空
              </Button>
            )}
            <span role="status" className="text-sm text-muted-foreground">
              {search
                ? `匹配 ${matched} 台服务器（按资产去重）`
                : '所有分组默认折叠'}
            </span>
          </div>
          {groups
            .filter((g) => !search || g.assets.length)
            .map((g) => (
              <Collapsible
                key={g.id}
                open={!!open[g.id]}
                onOpenChange={(value) =>
                  setOpen((prev) => ({ ...prev, [g.id]: value }))
                }
              >
                <h3>
                  <CollapsibleTrigger className="environment-server-trigger">
                    <ChevronRight size={18} />
                    {g.path.replace(/^Default \/ /, '')}
                    <span>（{g.assets.length} 台服务器）</span>
                  </CollapsibleTrigger>
                </h3>
                <CollapsibleContent>
                  <div className="shared-table-scroll">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>服务器 IP</TableHead>
                          <TableHead>主机名</TableHead>
                          <TableHead>操作系统</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {g.assets.map((a) => (
                          <TableRow key={a.id}>
                            <TableCell>
                              <code>{a.ip || '无'}</code>
                            </TableCell>
                            <TableCell>{a.hostname}</TableCell>
                            <TableCell>
                              {a.os || a.platform || '未提供'}
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                </CollapsibleContent>
              </Collapsible>
            ))}
          {!matched && <p className="px-6 pb-6">没有匹配的服务器 IP。</p>}
        </section>
        </details>
        </div>
      </main>
    </Shell>
  );
}

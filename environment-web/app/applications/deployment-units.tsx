'use client';

import { Fragment, useEffect, useRef, useState } from 'react';
import { ChevronDown, ChevronRight, RefreshCw, Search, Layers3, Settings2, Download } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Table, TableHeader, TableBody, TableHead, TableRow, TableCell } from '@/components/ui/table';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter, DialogTrigger } from '@/components/ui/dialog';
import { envs, labels } from '@/lib/inventory';
import { formatBeijingTime } from '@/lib/datetime';
import type { BusinessTab } from '@/lib/application-business';
import { extractJvmFlags } from '@/lib/server-processes';
import {
  catalogNeedsPolling, configurationLabels, isHistoricalCatalog,
  type CatalogEnvironment, type CatalogInstance, type CatalogResult,
} from '@/lib/deployment-catalog';
import './deployment-units.css';

const catalogEnvs: CatalogEnvironment[] = [...envs, 'UNIDENTIFIED'];
const catalogLabels: Record<CatalogEnvironment, string> = { ...labels, UNIDENTIFIED: '待识别' };
const categoryLabel = (category: string) => ({ APPLICATION: '应用', Elasticsearch: 'ES', Zookeeper: 'ZK' }[category] || category);

async function readJson<T>(response: Response): Promise<T> {
  const body: unknown = await response.json();
  if (!response.ok) {
    const error = body && typeof body === 'object' && 'error' in body ? body.error : null;
    throw new Error(typeof error === 'string' ? error : '部署单元暂不可用');
  }
  return body as T;
}

type JvmAdjustmentItem = {
  id: string; instanceId: string | null; ip: string | null; unitId: string | null; environment: string | null;
  currentJvmConfig: string | null; xmsNew: string | null; xmxNew: string | null;
  operator: string; note: string | null; source: string; createdAt: string;
};

function mergeAdjustedFlags(adjustment: JvmAdjustmentItem | null, originalFlags: string): string {
  if (!adjustment) return originalFlags;
  const parts: string[] = [];
  if (adjustment.xmsNew) parts.push(`-Xms${adjustment.xmsNew}`);
  if (adjustment.xmxNew) parts.push(`-Xmx${adjustment.xmxNew}`);
  // 用调整值覆盖原始；没调整的项（如 -Xmn -Xss）保留原始里的其他 flag
  if (parts.length && originalFlags) {
    const kept = originalFlags.split(/\s+/).filter((f) => f && !/^-Xm[xs]\d/i.test(f));
    return [...parts, ...kept].join(' ');
  }
  if (parts.length) return parts.join(' ');
  return originalFlags;
}

function EnvironmentJvmPanel({ unitId, environment, snapshotHash, application, onAdjusted }: {
  unitId: string; environment: CatalogEnvironment; snapshotHash: string; application: boolean; onAdjusted: () => void;
}) {
  const [originalFlags, setOriginalFlags] = useState('');
  const [adjustment, setAdjustment] = useState<JvmAdjustmentItem | null>(null);
  const [adjustments, setAdjustments] = useState<JvmAdjustmentItem[]>([]);
  const [configError, setConfigError] = useState('');
  const [adjError, setAdjError] = useState('');
  const [adjustOpen, setAdjustOpen] = useState(false);
  const [xms, setXms] = useState('');
  const [xmx, setXmx] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);

  // 只对 APPLICATION 类型展示；非应用组件跳过
  useEffect(() => {
    if (!application) { setOriginalFlags(''); setAdjustment(null); return; }
    const controller = new AbortController();
    const params = new URLSearchParams({ unitId, environment, presence: 'present' });
    fetch(`/api/catalog/deployment-units?${params}`, { cache: 'no-store', signal: controller.signal })
      .then((r) => readJson<{ source?: { snapshotSha256?: string }; instances: CatalogInstance[] }>(r))
      .then((body) => {
        if (body.source?.snapshotSha256 !== snapshotHash) throw new Error('目录已更新');
        if (controller.signal.aborted) return;
        // 取第一个有 MATCHED 进程且能提取到 JVM flags 的实例
        const firstFlags = body.instances
          .map((inst) => (inst.processObservation?.status === 'MATCHED' ? extractJvmFlags(inst.processObservation?.command || '') : ''))
          .find((f) => !!f) || '';
        setOriginalFlags(firstFlags); setConfigError('');
      })
      .catch((err) => { if (!controller.signal.aborted) setConfigError(err instanceof Error ? err.message : 'JVM 配置读取失败'); });
    return () => controller.abort();
  }, [unitId, environment, snapshotHash, application]);

  // 并行 fetch 最近人工调整记录
  useEffect(() => {
    if (!application) { setAdjustment(null); setAdjustments([]); return; }
    const controller = new AbortController();
    const params = new URLSearchParams({ unitId, environment, limit: '5' });
    fetch(`/api/catalog/jvm-adjustments?${params}`, { cache: 'no-store', signal: controller.signal })
      .then((r) => readJson<{ items: JvmAdjustmentItem[] }>(r))
      .then((body) => { if (!controller.signal.aborted) { setAdjustments(body.items); setAdjustment(body.items[0] || null); setAdjError(''); } })
      .catch(() => { if (!controller.signal.aborted) setAdjError('历史调整加载失败'); });
    return () => controller.abort();
  }, [unitId, environment, application, onAdjusted]);

  const displayFlags = mergeAdjustedFlags(adjustment, originalFlags);
  const hasAdjustment = !!adjustment;

  async function submit() {
    if (!xms && !xmx) return;
    setBusy(true);
    try {
      const response = await fetch('/api/catalog/jvm-adjustments', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ instanceId: null, ip: null, unitId, environment, currentJvmConfig: originalFlags || null, xmsNew: xms || null, xmxNew: xmx || null, note: note || null, source: 'MANUAL' }),
      });
      if (!response.ok) {
        const body: { error?: string } = await response.json().catch(() => ({}));
        throw new Error(body.error || '保存失败');
      }
      setXms(''); setXmx(''); setNote('');
      onAdjusted();
    } catch (err) {
      alert(err instanceof Error ? err.message : '提交失败');
    } finally { setBusy(false); }
  }

  if (!application) return null;
  return <span className="jvm-panel-inline">
    <span className="jvm-panel-label">JVM 配置：</span>
    {configError ? <span className="jvm-empty">—</span>
      : !originalFlags && !hasAdjustment ? <span className="jvm-empty">未采集</span>
      : <code className={`jvm-flags ${hasAdjustment ? 'jvm-flags-adjusted' : ''}`} title={hasAdjustment ? `已人工调整 · ${formatBeijingTime(adjustment!.createdAt, '—')} · 原始：${adjustment!.currentJvmConfig || '未采集'}` : '来自 JumpServer 采集'}>{displayFlags || '未采集'}</code>}
    {hasAdjustment && <span className="jvm-adjust-tag">已调整</span>}

    <Dialog>
      <DialogTrigger render={<Button variant="outline" size="sm" className="jvm-adjust-trigger" title="提交一条 JVM 参数调整记录（仅留档）"><Settings2 size={13} /> 调整 JVM 参数</Button>} />
      <DialogContent className="sm:max-w-md jvm-adjust-dialog">
        <DialogHeader>
          <DialogTitle>调整 JVM 参数</DialogTitle>
          <DialogDescription>{catalogLabels[environment]}环境 · 调整记录仅用于留档，不会实际修改进程启动参数。</DialogDescription>
        </DialogHeader>
        <div className="jvm-adjust-form">
          <p className="check-note">JumpServer 采集的 JVM 配置：<code className="jvm-current">{originalFlags || '未采集（非 Java 或进程未匹配）'}</code></p>
          {hasAdjustment && <p className="check-note">最近已有人工调整：<code className="jvm-current">{adjustment!.xmsNew ? `-Xms${adjustment!.xmsNew}` : ''} {adjustment!.xmxNew ? `-Xmx${adjustment!.xmxNew}` : ''}</code> · {formatBeijingTime(adjustment!.createdAt, '—')}{adjustment!.note && ` · ${adjustment!.note}`}</p>}
          <label className="jvm-adjust-field"><span>新 Xms（如 512M / 4G）</span>
            <Input placeholder="留空表示不调整此项" value={xms} onChange={(e) => setXms(e.target.value.trim())} disabled={busy} />
          </label>
          <label className="jvm-adjust-field"><span>新 Xmx（如 2G / 8G）</span>
            <Input placeholder="留空表示不调整此项" value={xmx} onChange={(e) => setXmx(e.target.value.trim())} disabled={busy} />
          </label>
          <label className="jvm-adjust-field"><span>备注（可选）</span>
            <Textarea rows={2} placeholder="调整原因 / 目标 / 审批人等" value={note} onChange={(e) => setNote(e.target.value)} disabled={busy} />
          </label>
        </div>
        {adjustments.length > 0 && <details className="jvm-history">
          <summary>历史调整记录（{adjustments.length}）</summary>
          <ul>
            {adjustments.map((h) => <li key={h.id}>
              <span>{formatBeijingTime(h.createdAt, '—')}</span>
              <code>{h.xmsNew ? `-Xms${h.xmsNew}` : '—'} {h.xmxNew ? `-Xmx${h.xmxNew}` : '—'}</code>
              {h.note && <small>{h.note}</small>}
              {h.ip && <small>{h.ip}</small>}
            </li>)}
          </ul>
        </details>}
        {adjError && <p className="check-error">{adjError}</p>}
        <DialogFooter showCloseButton>
          <Button onClick={() => void submit()} disabled={busy || (!xms && !xmx)}>{busy ? '保存中…' : '保存调整记录'}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  </span>;
}

function InstanceDetails({ unitId, environment, snapshotHash, includeHistory, onReload, application, cache, cacheKey }: {
  unitId: string; environment: CatalogEnvironment; snapshotHash: string;
  includeHistory: boolean; onReload: () => void; application: boolean; cache: Map<string, CatalogInstance[]>; cacheKey: string;
}) {
  const [rows, setRows] = useState<CatalogInstance[] | null>(() => cache.get(cacheKey) ?? null);
  const [error, setError] = useState('');
  useEffect(() => {
    const saved = cache.get(cacheKey);
    if (saved) { setRows(saved); return; }
    const controller = new AbortController();
    const params = new URLSearchParams({ unitId, environment, presence: includeHistory ? 'all' : 'present' });
    fetch(`/api/catalog/deployment-units?${params}`, { cache: 'no-store', signal: controller.signal })
      .then((response) => readJson<Pick<CatalogResult, 'source'> & { instances: CatalogInstance[] }>(response)).then((body) => {
        if (body.source?.snapshotSha256 !== snapshotHash) throw new Error('部署目录已更新，请刷新列表后查看实例。');
        if (!controller.signal.aborted) { cache.set(cacheKey, body.instances); setRows(body.instances); }
      }).catch((err: unknown) => {
        if (!controller.signal.aborted) setError(err instanceof Error ? err.message : '实例加载失败');
      });
    return () => controller.abort();
  }, [unitId, environment, snapshotHash, includeHistory, cache, cacheKey]);

  if (error) return <div className="catalog-message" role="alert">{error} <Button variant="outline" size="sm" onClick={onReload}>刷新列表</Button></div>;
  if (!rows) return <output className="catalog-message">正在读取实例…</output>;
  if (!rows.length) return <div className="catalog-message">此环境暂无可定位实例。未配置、未解析或尚未观察到的情况请结合环境状态查看。</div>;
  return <div className="catalog-instance-scroll"><Table className="catalog-instance-table">
    <TableHeader><TableRow>
      <TableHead>服务器 IP / 服务地址</TableHead><TableHead>声明端口</TableHead>
      {application && <TableHead>实际端口</TableHead>}
      <TableHead>Git 分支</TableHead><TableHead>{application ? '部署 ID / 进程 ID' : '部署 ID / 配置 ID'}</TableHead>
      <TableHead>{application ? '进程启动时间' : '最后发布时间（Push In）'}</TableHead><TableHead>{application ? '进程运行时间' : '发布状态'}</TableHead>
    </TableRow></TableHeader>
    <TableBody>{rows.map((instance) => <TableRow key={instance.id}>
      <TableCell>{instance.attributes.componentRole === 'KIBANA' && <span>Kibana（配套服务） </span>}{instance.ip ? <a className="catalog-app-link" href={`/jumpserver?q=${encodeURIComponent(instance.ip)}&view=processes`}><code>{instance.ip}</code></a> : <><code>{instance.attributes.serviceEndpoints?.map((endpoint) => endpoint.address).join(', ') || '未提供'}</code>{!!instance.dmsIps?.length && <small title="DMS 登记的实例 IP，不代表已核实的物理主机">DMS 实例 IP：{instance.dmsIps.join(', ')}</small>}</>}</TableCell>
      <TableCell title={instance.attributes.portBasis === 'DMS_RECORD' ? 'DMS 登记的访问端口，未核验实际监听' : instance.attributes.portBasis === 'DOCKER_PUBLISHED' ? 'Docker 发布的宿主机 TCP 端口；服务监听容器端口' : instance.attributes.portBasis === 'LIVE_LISTENER' ? '核查时进程持有的 TCP 监听端口' : instance.attributes.portBasis === 'APPLICATION_DEFAULT' ? '沿用应用声明端口，未核验实际监听' : '部署记录中的声明端口，未核验实际监听'}>{instance.port || instance.attributes.observedEndpoints?.map((endpoint) => `${endpoint.ip}:${endpoint.port}/${endpoint.protocol}`).join(', ') || '未提供'}</TableCell>
      {application && <TableCell title={instance.processObservation?.reason}>{instance.processObservation?.status === 'MATCHED' ? instance.processObservation.ports?.join('、') || (instance.processObservation.portStatus === 'complete' ? '未发现监听端口' : '未采集') : instance.processObservation?.status === 'NOT_FOUND' ? <span className="catalog-warning">无进程</span> : instance.processObservation?.status === 'AMBIGUOUS' ? '关联待确认' : '未采集或待确认'}</TableCell>}
      <TableCell title={instance.attributes.branchSource}>{instance.attributes.branch || '未提供'}</TableCell>
      <TableCell><code>{instance.attributes.deploy || '—'} / {application ? instance.processObservation?.pid ?? '—' : instance.attributes.config || '—'}</code></TableCell>
      <TableCell title={application ? `采集时间：${formatBeijingTime(instance.processObservation?.observedAt, '未知')}` : instance.attributes.publishTimeSource}>{application ? formatBeijingTime(instance.processObservation?.startedAt, '—') : instance.attributes.lastPublishedAt || '无记录'}</TableCell>
      <TableCell title={application ? '运行时间截至采集时刻' : undefined}>{application ? typeof instance.processObservation?.elapsedSeconds === 'number' && instance.processObservation.elapsedSeconds >= 0 ? `${Math.floor(instance.processObservation.elapsedSeconds / 86400)} 天` : '—' : instance.attributes.publishStatus || '未提供'}</TableCell>
    </TableRow>)}</TableBody>
  </Table></div>;
}

export default function DeploymentUnits() {
  const instanceCache = useRef(new Map<string, CatalogInstance[]>());
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState('APPLICATION');
  const [business, setBusiness] = useState<BusinessTab>('group');
  const [categoryList, setCategoryList] = useState<{ key: string; values: string[] }>({ key: '', values: [] });
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [includeHistory, setIncludeHistory] = useState(false);
  const [result, setResult] = useState<CatalogResult | null>(null);
  const [error, setError] = useState('');
  const [completedRequest, setCompletedRequest] = useState('');
  const [importing, setImporting] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [configIds, setConfigIds] = useState<Set<string>>(new Set());
  const [configError, setConfigError] = useState('');
  const [jvmAdjustedKey, setJvmAdjustedKey] = useState(0);
  const today = new Date().toISOString().slice(0, 10);
  const [exportOpen, setExportOpen] = useState(false);
  const [exportEnv, setExportEnv] = useState('PRODUCT');
  const [exportStart, setExportStart] = useState(today);
  const [exportEnd, setExportEnd] = useState(today);
  const [exportBusy, setExportBusy] = useState(false);
  const [exportError, setExportError] = useState('');
  const requestKey = JSON.stringify([search, page, includeHistory, category, business, refresh]);
  const categoryListKey = JSON.stringify([business, includeHistory]);
  const categories = categoryList.key === categoryListKey ? categoryList.values : [];
  const loading = completedRequest !== requestKey;
  const reload = () => { instanceCache.current.clear(); setExpanded(null); setRefresh((n) => n + 1); };

  useEffect(() => {
    const controller = new AbortController();
    fetch('/api/catalog/application-config', { cache: 'no-store', signal: controller.signal })
      .then((response) => readJson<{ appIds: string[] }>(response)).then((body) => {
        if (!controller.signal.aborted) { setConfigIds(new Set(body.appIds)); setConfigError(''); }
      }).catch(() => { if (!controller.signal.aborted) { setConfigIds(new Set()); setConfigError('应用配置列表暂不可用，请刷新列表重试。'); } });
    return () => controller.abort();
  }, [refresh]);

  useEffect(() => {
    const timer = window.setTimeout(() => { setSearch(query.trim()); setPage(1); }, 200);
    return () => window.clearTimeout(timer);
  }, [query]);
  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams({ q: search, page: String(page), presence: includeHistory ? 'all' : 'present', category, business });
    fetch(`/api/catalog/deployment-units?${params}`, { cache: 'no-store', signal: controller.signal })
      .then((response) => readJson<CatalogResult>(response)).then((body) => {
        if (!controller.signal.aborted) {
          setCategoryList({ key: categoryListKey, values: body.categories });
          if (body.categories.length && !body.categories.includes(category)) {
            setCategory(body.categories[0]); setPage(1); setExpanded(null);
          }
          setResult(body); setError('');
        }
      }).catch((err: unknown) => {
        if (!controller.signal.aborted) { setResult(null); setError(err instanceof Error ? err.message : '部署单元读取失败'); }
      }).finally(() => { if (!controller.signal.aborted) setCompletedRequest(requestKey); });
    return () => controller.abort();
  }, [search, page, includeHistory, category, business, refresh, requestKey, categoryListKey]);
  useEffect(() => {
    if (!result || !catalogNeedsPolling(result)) return;
    const timer = window.setTimeout(() => setRefresh((n) => n + 1), 3000);
    return () => window.clearTimeout(timer);
  }, [result]);

  async function importCurrent() {
    setImporting(true); setError('');
    try {
      await fetch('/api/catalog/deployment-units/import', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
      }).then(readJson);
      reload();
    } catch (err) { setError(err instanceof Error ? err.message : '导入失败，请重试'); }
    finally { setImporting(false); }
  }

  async function exportJvmAdjustments() {
    setExportBusy(true); setExportError('');
    try {
      const params = new URLSearchParams({
        startDate: exportStart, endDate: exportEnd,
        ...(exportEnv && exportEnv !== 'ALL' ? { environment: exportEnv } : {}),
      });
      const res = await fetch(`/api/catalog/jvm-adjustments/export?${params}`, { cache: 'no-store' });
      if (!res.ok) {
        const body: { error?: string } = await res.json().catch(() => ({}));
        throw new Error(body.error || '导出失败');
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `jvm-adjustments-${exportStart}-${exportEnd}-${exportEnv.toLowerCase()}.csv`;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
      setExportOpen(false);
    } catch (err) { setExportError(err instanceof Error ? err.message : '导出失败，请重试'); }
    finally { setExportBusy(false); }
  }

  const pending = importing || ['queued', 'running'].includes(result?.job.status || '');
  const historical = result ? isHistoricalCatalog(result) : false;
  return <div className="deployment-catalog">
    <div className="catalog-heading">
      <div><h1><Layers3 size={19} />部署单元</h1><p>管理应用及基础组件的部署单元和部署实例。</p></div>
      <Button onClick={importCurrent} disabled={pending || historical}><RefreshCw size={15} className={pending ? 'catalog-spinning' : ''} />{pending ? '正在更新…' : '从当前快照创建 / 更新'}</Button>
    </div>
    <div className="catalog-tabs" role="tablist" aria-label="部署单元业务分类">
      {([['group', '地中海度假集团'], ['travel', '商旅'], ['other', '其它']] as const).map(([value, label]) => <button key={value} type="button" role="tab" aria-selected={business === value} className={business === value ? 'active' : ''} onClick={() => { setBusiness(value); setPage(1); setExpanded(null); }}>{label}</button>)}
    </div>
    <div className="catalog-tabs" role="tablist" aria-label="部署单元类型">
      {categories.map((item) => <button key={item} type="button" role="tab" aria-selected={category === item} className={category === item ? 'active' : ''} onClick={() => { setCategory(item); setPage(1); setExpanded(null); }}>{categoryLabel(item)}</button>)}
    </div>
    <div className="catalog-summary" aria-label={`${categoryLabel(category)}汇总`}>
      <span>部署单元个数 <b>{loading ? '—' : result?.categorySummary.units ?? '—'}</b></span>
      {catalogEnvs.map((env) => <span key={env}>{catalogLabels[env]}{env === 'UNIDENTIFIED' ? '' : '环境'}实例数 / 服务器总数 <b>{loading || !result ? '—' : `${result.categorySummary.environments[env]?.instances || 0} / ${result.categorySummary.environments[env]?.servers || 0}`}</b></span>)}
      <small>实例按当前观察统计；服务器按 IP 去重，托管服务按实例计数，不代表物理节点数。汇总随搜索和历史单元筛选变化。</small>
    </div>
    <div className="list-notice catalog-notice"><span>应用数据来自 DevOps 快照；组件来自进程、配置核查及 DMS 登记。环境待识别不表示任何正式环境。</span></div>
    {error && !loading && <div className="catalog-message catalog-error" role="alert">{error}<Button variant="outline" size="sm" onClick={reload}>重试读取</Button></div>}
    {configError && <div className="catalog-message catalog-error" role="alert">{configError}</div>}
    {result?.job.error && <div className="catalog-message catalog-error" role="alert">{result.job.error}</div>}
    {result?.source && !result.activeSnapshot.matchesCatalog && <output className="catalog-message">
      {historical ? '当前应用快照较旧或采集时间未知，部署目录保留可确定时间的较新观察及管理信息。' : '当前应用快照尚未完成目录更新，下面仍显示最近成功导入的数据。'}
    </output>}
    <section className="list-panel">
      <div className="list-toolbar">
        <div className="search-field"><Search size={17} /><Input aria-label="搜索部署单元名称、编码或应用 ID" placeholder="搜索部署单元名称、编码或应用 ID…" value={query} onChange={(e) => setQuery(e.target.value)} /></div>
        <label className="catalog-history"><input type="checkbox" checked={includeHistory} onChange={(e) => { setIncludeHistory(e.target.checked); setPage(1); }} />包含来源未再观察到的单元</label>
        <Button variant="outline" size="sm" onClick={reload} disabled={loading}>刷新列表</Button>
        <Dialog open={exportOpen} onOpenChange={setExportOpen}>
          <DialogTrigger render={<Button variant="outline" size="sm"><Download size={13} /> 导出 JVM 参数调整记录</Button>} />
          <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle>导出 JVM 参数调整记录</DialogTitle>
              <DialogDescription>导出指定环境和日期区间内，每个应用（部署单元）最后一次的 JVM 参数调整记录。</DialogDescription>
            </DialogHeader>
            <div className="jvm-export-form">
              <label className="jvm-adjust-field">
                <span>环境</span>
                <select value={exportEnv} onChange={(e) => setExportEnv(e.target.value)} disabled={exportBusy} className="jvm-export-select">
                  {(['PRODUCT', 'TEST', 'SIMULATION', 'UNIDENTIFIED', 'ALL'] as const).map((env) => (
                    <option key={env} value={env}>{env === 'ALL' ? '全部环境' : catalogLabels[env as CatalogEnvironment]}</option>
                  ))}
                </select>
              </label>
              <div className="jvm-export-date-row">
                <label className="jvm-adjust-field">
                  <span>开始日期</span>
                  <Input type="date" value={exportStart} onChange={(e) => setExportStart(e.target.value)} disabled={exportBusy} />
                </label>
                <label className="jvm-adjust-field">
                  <span>结束日期</span>
                  <Input type="date" value={exportEnd} onChange={(e) => setExportEnd(e.target.value)} disabled={exportBusy} />
                </label>
              </div>
              {exportError && <p className="catalog-message catalog-error">{exportError}</p>}
            </div>
            <DialogFooter>
              <Button variant="outline" size="sm" onClick={() => setExportOpen(false)} disabled={exportBusy}>取消</Button>
              <Button size="sm" onClick={exportJvmAdjustments} disabled={exportBusy || !exportStart || !exportEnd}>
                {exportBusy ? '导出中…' : <><Download size={13} /> 导出 CSV</>}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>
      <output className="result-line">{loading ? '正在读取部署目录…' : `共 ${result?.pagination.total || 0} 个部署单元`}</output>
      <Table className="app-table catalog-table">
        <TableHeader><TableRow>
          <TableHead>部署单元</TableHead><TableHead>应用配置</TableHead><TableHead>来源应用 ID</TableHead><TableHead>单元类型</TableHead>
          <TableHead>业务领域 / 系统</TableHead>{catalogEnvs.map((env) => <TableHead key={env}>{catalogLabels[env]}</TableHead>)}
        </TableRow></TableHeader>
        <TableBody>{!loading && result?.units.map((unit) => <Fragment key={unit.id}>
          <TableRow>
            <TableCell><strong>{unit.name}</strong><small className="catalog-code" title={unit.id}>{unit.code}</small>{unit.groupingState === 'PENDING' && <small className="catalog-warning">编组待确认</small>}</TableCell>
            <TableCell>{unit.unitType === 'APPLICATION' && unit.appId && configIds.has(unit.appId) && <a className="catalog-config-button" href={`/applications/config/${encodeURIComponent(unit.appId)}`} target="_blank" rel="noopener noreferrer" aria-label={`查看 ${unit.name} 应用配置`}>应用配置 ↗</a>}</TableCell>
            <TableCell>{unit.appId ? <a className="catalog-app-link" href={`/applications?appId=${encodeURIComponent(unit.appId)}`}>{unit.appId}</a> : '无应用关联'}</TableCell>
            <TableCell>{unit.unitType === 'APPLICATION' ? '应用' : unit.unitType}<small>{unit.appType || '来源类型未提供'}</small></TableCell>
            <TableCell>{unit.domainName || '未分类'}<small>{unit.systemName || '未归属系统'}</small></TableCell>
            {catalogEnvs.map((env) => {
              const value = unit.environments.find((entry) => entry.environment === env);
              const key = `${unit.id}:${env}`;
              const open = expanded === key;
              return <TableCell key={env}>{!value?.instanceCount && (!value || value.configurationState === 'UNKNOWN') ? '-' : <Button variant="ghost" size="sm" aria-label={`${unit.name} ${catalogLabels[env]}实例明细`} aria-expanded={open} aria-controls={open ? `catalog-${key}` : undefined} onClick={() => setExpanded(open ? null : key)}>
                {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                {value?.instanceCount ? `${value.instanceCount} 实例 / ${value.serverCount} 台` : configurationLabels[value?.configurationState || 'UNKNOWN']}
              </Button>}{!!value?.unresolvedCount && <small className="catalog-warning">{value.unresolvedCount} 条待解析</small>}
              </TableCell>;
            })}
          </TableRow>
          {catalogEnvs.filter((env) => !loading && expanded === `${unit.id}:${env}`).map((env) => <TableRow key={env} className="expanded-row"><TableCell colSpan={9}>
            <div id={`catalog-${unit.id}:${env}`} className="catalog-instance-heading">{unit.name} / {catalogLabels[env]}{unit.unitType === 'APPLICATION' && env !== 'UNIDENTIFIED' && <a className="catalog-config-button" href={`/deployment-units/inspection?unitId=${encodeURIComponent(unit.id)}&environment=${encodeURIComponent(env)}`} target="_blank" rel="noopener noreferrer">一键巡检 ↗</a>}{unit.unitType === 'APPLICATION' && <EnvironmentJvmPanel key={`${unit.id}:${env}:${jvmAdjustedKey}`} unitId={unit.id} environment={env as CatalogEnvironment} snapshotHash={result.source?.snapshotSha256 || ''} application onAdjusted={() => setJvmAdjustedKey((k) => k + 1)} />}</div>
            <InstanceDetails key={`${result.source?.snapshotSha256}:${includeHistory}`} unitId={unit.id} environment={env} snapshotHash={result.source?.snapshotSha256 || ''} includeHistory={includeHistory} onReload={reload} application={unit.unitType === 'APPLICATION'} cache={instanceCache.current} cacheKey={JSON.stringify([unit.id, env, includeHistory, result.source?.runId, result.activeSnapshot.version, result.job.finishedAt])} />
          </TableCell></TableRow>)}
        </Fragment>)}
        {!loading && !result?.units.length && <TableRow><TableCell colSpan={9}><div className="empty-state"><Layers3 size={28} />
          <h2>{result?.source ? '未找到匹配的部署单元' : '尚未建立部署目录'}</h2>
          <p>{result?.source ? '调整搜索或勾选历史单元后重试。' : '使用当前已有应用快照创建部署单元和环境实例，无需重新采集。'}</p>
        </div></TableCell></TableRow>}
        </TableBody>
      </Table>
      <div className="pagination"><span>第 {result?.pagination.page || 1} / {result?.pagination.totalPages || 1} 页</span><div>
        <Button variant="outline" disabled={loading || (result?.pagination.page || 1) <= 1} onClick={() => setPage((result?.pagination.page || 1) - 1)}>上一页</Button>
        <Button variant="outline" disabled={loading || !result || result.pagination.page >= result.pagination.totalPages} onClick={() => setPage(result!.pagination.page + 1)}>下一页</Button>
      </div></div>
    </section>
    <footer className="page-footer"><span>应用缺失保留管理对象和历史，不自动删除。</span><span>来源观察：{formatBeijingTime(result?.source?.observedAt) || '尚未导入'} · 导入：{formatBeijingTime(result?.source?.importedAt) || '—'}</span></footer>
  </div>;
}

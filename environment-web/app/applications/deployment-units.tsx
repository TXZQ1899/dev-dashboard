'use client';

import { Fragment, useEffect, useState } from 'react';
import { ChevronDown, ChevronRight, RefreshCw, Search, Layers3 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Table, TableHeader, TableBody, TableHead, TableRow, TableCell } from '@/components/ui/table';
import { envs, labels } from '@/lib/inventory';
import { formatBeijingTime } from '@/lib/datetime';
import {
  catalogNeedsPolling, configurationLabels, isHistoricalCatalog,
  type CatalogEnvironment, type CatalogInstance, type CatalogResult,
} from '@/lib/deployment-catalog';
import './deployment-units.css';

async function readJson<T>(response: Response): Promise<T> {
  const body: unknown = await response.json();
  if (!response.ok) {
    const error = body && typeof body === 'object' && 'error' in body ? body.error : null;
    throw new Error(typeof error === 'string' ? error : '部署单元暂不可用');
  }
  return body as T;
}

function InstanceDetails({ unitId, environment, snapshotHash, includeHistory, onReload }: {
  unitId: string; environment: CatalogEnvironment; snapshotHash: string;
  includeHistory: boolean; onReload: () => void;
}) {
  const [rows, setRows] = useState<CatalogInstance[] | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams({ unitId, environment, presence: includeHistory ? 'all' : 'present' });
    fetch(`/api/catalog/deployment-units?${params}`, { cache: 'no-store', signal: controller.signal })
      .then((response) => readJson<Pick<CatalogResult, 'source'> & { instances: CatalogInstance[] }>(response)).then((body) => {
        if (body.source?.snapshotSha256 !== snapshotHash) throw new Error('部署目录已更新，请刷新列表后查看实例。');
        if (!controller.signal.aborted) setRows(body.instances);
      }).catch((err: unknown) => {
        if (!controller.signal.aborted) setError(err instanceof Error ? err.message : '实例加载失败');
      });
    return () => controller.abort();
  }, [unitId, environment, snapshotHash, includeHistory]);

  if (error) return <div className="catalog-message" role="alert">{error} <Button variant="outline" size="sm" onClick={onReload}>刷新列表</Button></div>;
  if (!rows) return <output className="catalog-message">正在读取实例…</output>;
  if (!rows.length) return <div className="catalog-message">此环境暂无可定位实例。未配置、未解析或尚未观察到的情况请结合环境状态查看。</div>;
  return <div className="catalog-instance-scroll"><Table className="catalog-instance-table">
    <TableHeader><TableRow>
      <TableHead>服务器 IP</TableHead><TableHead>声明端口</TableHead>
      <TableHead>Git 分支</TableHead><TableHead>部署 ID / 配置 ID</TableHead>
      <TableHead>最后发布时间（Push In）</TableHead><TableHead>发布状态</TableHead>
    </TableRow></TableHeader>
    <TableBody>{rows.map((instance) => <TableRow key={instance.id}>
      <TableCell><code>{instance.ip || '未提供'}</code></TableCell>
      <TableCell title={instance.attributes.portBasis === 'APPLICATION_DEFAULT' ? '沿用应用声明端口，未核验实际监听' : '部署记录中的声明端口，未核验实际监听'}>{instance.port || '未提供'}</TableCell>
      <TableCell title={instance.attributes.branchSource}>{instance.attributes.branch || '未提供'}</TableCell>
      <TableCell><code>{instance.attributes.deploy || '—'} / {instance.attributes.config || '—'}</code></TableCell>
      <TableCell title={instance.attributes.publishTimeSource}>{instance.attributes.lastPublishedAt || '无记录'}</TableCell>
      <TableCell>{instance.attributes.publishStatus || '未提供'}</TableCell>
    </TableRow>)}</TableBody>
  </Table></div>;
}

export default function DeploymentUnits() {
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [includeHistory, setIncludeHistory] = useState(false);
  const [result, setResult] = useState<CatalogResult | null>(null);
  const [error, setError] = useState('');
  const [completedRequest, setCompletedRequest] = useState('');
  const [importing, setImporting] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [expanded, setExpanded] = useState<string | null>(null);
  const requestKey = JSON.stringify([search, page, includeHistory, refresh]);
  const loading = completedRequest !== requestKey;
  const reload = () => { setExpanded(null); setRefresh((n) => n + 1); };

  useEffect(() => {
    const timer = window.setTimeout(() => { setSearch(query.trim()); setPage(1); }, 200);
    return () => window.clearTimeout(timer);
  }, [query]);
  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams({ q: search, page: String(page), presence: includeHistory ? 'all' : 'present' });
    fetch(`/api/catalog/deployment-units?${params}`, { cache: 'no-store', signal: controller.signal })
      .then((response) => readJson<CatalogResult>(response)).then((body) => {
        if (!controller.signal.aborted) { setResult(body); setError(''); }
      }).catch((err: unknown) => {
        if (!controller.signal.aborted) { setResult(null); setError(err instanceof Error ? err.message : '部署单元读取失败'); }
      }).finally(() => { if (!controller.signal.aborted) setCompletedRequest(requestKey); });
    return () => controller.abort();
  }, [search, page, includeHistory, refresh, requestKey]);
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

  const pending = importing || ['queued', 'running'].includes(result?.job.status || '');
  const historical = result ? isHistoricalCatalog(result) : false;
  return <div className="deployment-catalog">
    <div className="catalog-heading">
      <div><h2><Layers3 size={19} />部署单元</h2><p>一个应用对应一个逻辑单元，按测试、仿真、生产环境管理部署实例。</p></div>
      <Button onClick={importCurrent} disabled={pending || historical}><RefreshCw size={15} className={pending ? 'catalog-spinning' : ''} />{pending ? '正在更新…' : '从当前快照创建 / 更新'}</Button>
    </div>
    {result && <div className="catalog-summary">
      <span>管理单元 <b>{result.summary.units}</b></span><span>当前来源可见 <b>{result.summary.presentUnits}</b></span>
      <span>当前观察实例 <b>{result.summary.instances}</b></span><span>业务领域 / 系统 <b>待分类</b></span>
    </div>}
    <div className="list-notice catalog-notice"><span>数据来自已有 DevOps 快照。规格、进程和中间件识别将在后续补充；实例端口是声明值。</span></div>
    {error && !loading && <div className="catalog-message catalog-error" role="alert">{error}<Button variant="outline" size="sm" onClick={reload}>重试读取</Button></div>}
    {result?.job.error && <div className="catalog-message catalog-error" role="alert">{result.job.error}</div>}
    {result?.source && !result.activeSnapshot.matchesCatalog && <output className="catalog-message">
      {historical ? '当前应用快照较旧或采集时间未知，部署目录保留可确定时间的较新观察及管理信息。' : '当前应用快照尚未完成目录更新，下面仍显示最近成功导入的数据。'}
    </output>}
    <section className="list-panel">
      <div className="list-toolbar">
        <div className="search-field"><Search size={17} /><Input aria-label="搜索部署单元名称、编码或应用 ID" placeholder="搜索部署单元名称、编码或应用 ID…" value={query} onChange={(e) => setQuery(e.target.value)} /></div>
        <label className="catalog-history"><input type="checkbox" checked={includeHistory} onChange={(e) => { setIncludeHistory(e.target.checked); setPage(1); }} />包含来源未再观察到的单元</label>
        <Button variant="outline" size="sm" onClick={reload} disabled={loading}>刷新列表</Button>
      </div>
      <output className="result-line">{loading ? '正在读取部署目录…' : `共 ${result?.pagination.total || 0} 个部署单元`}</output>
      <Table className="app-table catalog-table">
        <TableHeader><TableRow>
          <TableHead>部署单元</TableHead><TableHead>来源应用 ID</TableHead><TableHead>单元类型</TableHead>
          <TableHead>业务领域 / 系统</TableHead>{envs.map((env) => <TableHead key={env}>{labels[env]}</TableHead>)}
          <TableHead>来源状态</TableHead>
        </TableRow></TableHeader>
        <TableBody>{result?.units.map((unit) => <Fragment key={unit.id}>
          <TableRow>
            <TableCell><strong>{unit.name}</strong><small className="catalog-code" title={unit.id}>{unit.code}</small></TableCell>
            <TableCell>{unit.appId ? <a className="catalog-app-link" href={`/applications?appId=${encodeURIComponent(unit.appId)}`}>{unit.appId}</a> : '无应用关联'}</TableCell>
            <TableCell>{unit.unitType === 'APPLICATION' ? '应用' : unit.unitType}<small>{unit.appType || '来源类型未提供'}</small></TableCell>
            <TableCell>{unit.domainName || '未分类'}<small>{unit.systemName || '未归属系统'}</small></TableCell>
            {envs.map((env) => {
              const value = unit.environments.find((entry) => entry.environment === env);
              const key = `${unit.id}:${env}`;
              const open = expanded === key;
              return <TableCell key={env}><Button variant="ghost" size="sm" aria-label={`${unit.name} ${labels[env]}实例明细`} aria-expanded={open} aria-controls={open ? `catalog-${key}` : undefined} onClick={() => setExpanded(open ? null : key)}>
                {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                {value?.instanceCount ? `${value.instanceCount} 实例 / ${value.serverCount} 台` : configurationLabels[value?.configurationState || 'UNKNOWN']}
              </Button>{!!value?.unresolvedCount && <small className="catalog-warning">{value.unresolvedCount} 条待解析</small>}
              {value?.coverageState !== 'COMPLETE' && <small>观察范围待核实</small>}</TableCell>;
            })}
            <TableCell>{unit.sourcePresence === 'PRESENT' ? '已观察到' : '未再观察到'}<small>{unit.managementStatus === 'ACTIVE' ? '管理状态：启用' : unit.managementStatus}</small></TableCell>
          </TableRow>
          {envs.filter((env) => !loading && expanded === `${unit.id}:${env}`).map((env) => <TableRow key={env} className="expanded-row"><TableCell colSpan={8}>
            <div id={`catalog-${unit.id}:${env}`} className="catalog-instance-heading">{unit.name} / {labels[env]}<small>未定位目标和冲突记录保留为待解析，不生成占位实例。</small></div>
            <InstanceDetails key={`${result.source?.snapshotSha256}:${includeHistory}`} unitId={unit.id} environment={env} snapshotHash={result.source?.snapshotSha256 || ''} includeHistory={includeHistory} onReload={reload} />
          </TableCell></TableRow>)}
        </Fragment>)}
        {!loading && !result?.units.length && <TableRow><TableCell colSpan={8}><div className="empty-state"><Layers3 size={28} />
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

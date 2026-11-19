'use client';
import { createContext, useContext, useEffect, useState, useSyncExternalStore } from 'react';
import {
  Cloud,
  Cpu,
  MemoryStick,
  Server,
  Plus,
  X,
  ArrowUpRight,
  ChevronDown,
  Download,
  Tag,
} from 'lucide-react';
import { Shell } from '../page';
import { formatBeijingTime } from '@/lib/datetime';
import { TenantFilter } from '@/components/tenant-filter';
import { tenantLabel, resourceIdentity } from '@/lib/tenants';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { NativeSelect } from '@/components/ui/native-select';
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
  instances,
  compareText,
  projects,
  projectName,
  projectToken,
  fetchedAt,
  tenantSnapshots,
  tagValues,
  tagLabel,
  statusName,
  chargeTypeName,
  renewalStatusName,
  emptyFilters,
  filterInstances,
  summarize,
  groupInstances,
  filtersQuery,
  filtersFromQuery,
  type Filters,
  type Instance,
  type Group,
} from '@/lib/ecs';
import { partitionCloud, serverTenantPath, type CloudSite } from '@/lib/server-resources';
import { businessLabel, businessCategories, classifyServers, type BusinessResult, type ServerBusiness } from '@/lib/server-business';
import { apps as inventoryApps } from '@/lib/inventory';
import { matchAppToProcess, processesForIp } from '@/lib/process-comparison';
import { associateServerApps, csvCell, sortServerRows, type ServerApps, type ServerSort } from '@/lib/ecs-apps';
import './ecs.css';

function Picker({
  label,
  value,
  onChange,
  children,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  children: React.ReactNode;
}) {
  return (
    <label className="ecs-field">
      <span>{label}</span>
      <NativeSelect
        aria-label={label}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        {children}
      </NativeSelect>
    </label>
  );
}
function Metrics({ rows }: { rows: Instance[] }) {
  const s = summarize(rows);
  return (
    <div className="ecs-metrics">
      {[
        {
          label: 'ECS 实例',
          value: s.count,
          unit: '台',
          icon: Server,
          note: `${s.running} 台运行中`,
        },
        {
          label: 'CPU 总量',
          value: s.cpu,
          unit: 'vCPU',
          icon: Cpu,
          note: '实例配置核数合计',
        },
        {
          label: '内存总量',
          value: s.memory,
          unit: 'GiB',
          icon: MemoryStick,
          note: '实例配置容量合计',
        },
        {
          label: '有公网 IP',
          value: s.publicIp,
          unit: '台',
          icon: Cloud,
          note: `${s.count - s.publicIp} 台无公网 IP · 包含 EIP`,
        },
      ].map((m) => (
        <section className="panel ecs-metric" key={m.label}>
          <span>
            {m.label}
            <m.icon size={18} />
          </span>
          <strong>
            {m.value.toLocaleString('zh-CN')}
            <small>{m.unit}</small>
          </strong>
          <p>{m.note}</p>
        </section>
      ))}
    </div>
  );
}
const businessColors = ['#328979', '#6286cc', '#e0a15a', '#a768a6', '#ce6e74', '#81a957', '#529cb2', '#c28c62', '#7a78b1', '#a0a9a5'];
function BusinessDistribution({ rows, business }: { rows: Instance[]; business: Map<string, ServerBusiness> }) {
  const buckets = businessCategories(rows, business).map(key => ({
    key,
    ...summarize(rows.filter(row => business.get(resourceIdentity(row))?.category === key)),
  }));
  const totals = summarize(rows);
  const metrics = [
    { key: 'count' as const, label: '服务器台数', unit: '台' },
    { key: 'cpu' as const, label: 'CPU 总量', unit: 'vCPU' },
    { key: 'memory' as const, label: '内存总量', unit: 'GiB' },
    { key: 'publicIp' as const, label: '有公网 IP 的服务器', unit: '台' },
  ];
  const shared = rows.filter(row => (business.get(resourceIdentity(row))?.categories.length ?? 0) > 1).length;
  const ambiguous = rows.filter(row => business.get(resourceIdentity(row))?.ambiguous).length;
  return <section className="panel ecs-business-section">
    <div className="ecs-section-title"><div><h2>业务资源分布</h2>
      <p>覆盖所有部署环境，按 ECS 实例计数。业务关联多于一类的 {shared} 台按集团 → 商旅 → 其它顺序计入一类；{ambiguous} 台存在 IP 关联待确认。</p>
      <p>其它包括无业务领域的应用和组件；未匹配时使用有效项目名称，未识别表示没有可用的业务或项目分类。这些分类不代表已确认的业务所有权。</p>
    </div></div>
    <div className="ecs-business-pies">{metrics.map(metric => {
      let offset = 0;
      const stops = buckets.map((bucket, i) => {
        const start = offset;
        offset += totals[metric.key] ? bucket[metric.key] / totals[metric.key] * 100 : 0;
        return `${businessColors[i % businessColors.length]} ${start}% ${offset}%`;
      });
      const format = (n: number) => n.toLocaleString('zh-CN', { maximumFractionDigits: 2 });
      return <div className="ecs-business-pie-card" key={metric.key}>
        <h3>{metric.label}</h3>
        <div className="ecs-business-pie" aria-hidden="true" style={{ background: totals[metric.key] ? `conic-gradient(${stops.join(',')})` : '#e8eeeb' }}>
          <div><strong>{format(totals[metric.key])}</strong><small>{metric.unit}</small></div>
        </div>
        <div className="ecs-business-legend">{buckets.map((bucket, i) => <div key={bucket.key}>
          <span><i style={{ background: businessColors[i % businessColors.length] }} />{businessLabel(bucket.key)}</span><b>{format(bucket[metric.key])} {metric.unit}</b>
        </div>)}</div>
      </div>;
    })}</div>
  </section>;
}
function AppCell({ association }: { association?: ServerApps }) {
  const [expanded, setExpanded] = useState(false);
  const apps = association?.apps ?? [];
  const visible = expanded ? apps : apps.slice(0, 2);
  return <>{visible.map(app => <div key={app.id}><a href={`/applications?appId=${encodeURIComponent(app.id)}#app-${encodeURIComponent(app.id)}`}>{app.name}（{app.id}）</a></div>)}
    {!apps.length && '—'}
    {apps.length > 2 && <button type="button" className="ecs-app-toggle" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>{expanded ? '收起' : `展开全部 ${apps.length} 个应用`}</button>}
    {association?.ambiguous && <small>部分 IP 关联待确认</small>}
  </>;
}

const formatResourceTag = (key: string, value: string) => value === '' ? key : `${key}: ${value}`;

function exportResources(rows: Instance[], business: Map<string, ServerBusiness>, associations: Map<string, ServerApps>) {
  const header = ['实例名', '实例 ID', '实例规格', '业务关联', '租户', '账号 ID', 'CPU', '内存 GiB', '内网 IP', '公网 IP', '关联应用数', '关联应用', '应用关联待确认', '付费方式', '续费方式', '到期时间', '标签', '项目'];
  const lines = [header, ...rows.map(r => {
    const id = resourceIdentity(r);
    const association = associations.get(id);
    const renewal = renewalStatusName(r.renewalStatus);
    return [r.name, r.id, r.instanceType, (business.get(id)?.categories ?? []).map(businessLabel).join('、') || '未识别', tenantLabel(r), r.accountId || '', String(r.cpu), String(r.memoryGiB), r.privateIps.join('、'), r.publicIps.join('、'), String(association?.apps.length ?? 0), (association?.apps ?? []).map(app => `${app.name}（${app.id}）`).join('、'), association?.ambiguous ? '是' : '否', chargeTypeName(r.chargeType), r.chargeType !== 'PostPaid' && renewal !== '未知' ? renewal : '', r.chargeType !== 'PostPaid' && r.expiredAt ? formatBeijingTime(r.expiredAt, r.expiredAt) : '', [...Object.entries(r.ecsTags ?? r.tags).map(([key, value]) => `${formatResourceTag(key, value)}（ECS）`), ...Object.entries(r.manualTags ?? {}).map(([key, value]) => `${formatResourceTag(key, value)}（App Atlas）`)].join('、'), projectName(r.projectId, r.tenantId)];
  })].map(row => row.map(csvCell).join(',')).join('\r\n');
  const url = URL.createObjectURL(new Blob(['\uFEFF' + lines], { type: 'text/csv;charset=utf-8;' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = 'ecs-servers.csv';
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

type ManualTags = Record<string, Record<string, string>>;
const manualIdentity = (r: Instance) => JSON.stringify([r.tenantId || 'legacy-aliyun', r.region || '', r.id]);
const TagsContext = createContext<{ save: (row: Instance, key: string, value: string, action: 'add' | 'delete') => Promise<void>; ready: boolean }>({ save: async () => {}, ready: false });
function TagCell({ row }: { row: Instance }) {
  const { save, ready } = useContext(TagsContext);
  const [editing, setEditing] = useState(false);
  const [key, setKey] = useState('');
  const [value, setValue] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  async function submit(action: 'add' | 'delete', tagKey = key) {
    setBusy(true); setError('');
    try { await save(row, tagKey, value, action); if (action === 'add') { setEditing(false); setKey(''); setValue(''); } }
    catch (e) { setError(e instanceof Error ? e.message : '保存失败'); }
    finally { setBusy(false); }
  }
  return <div className="ecs-tags">
    {Object.entries(row.ecsTags ?? row.tags).map(([k, v]) => <span key={`ecs-${k}`} title="ECS 采集">{formatResourceTag(k, v)}</span>)}
    {Object.entries(row.manualTags ?? {}).map(([k, v]) => <span key={`manual-${k}`} className="ecs-manual-tag"><Tag size={12} aria-label="App Atlas 人工添加" /> {formatResourceTag(k, v)} <button type="button" aria-label={`删除人工标签 ${k}`} disabled={busy} onClick={() => submit('delete', k)}><X size={12} /></button></span>)}
    {!Object.keys(row.tags).length && !Object.keys(row.manualTags ?? {}).length && '无标签'}
    <button type="button" className="ecs-tag-add" aria-label={`为 ${row.name} 添加标签`} title="添加标签" disabled={!ready || busy} onClick={() => setEditing(!editing)}><Plus size={15} /></button>
    {editing && <form className="ecs-tag-form" onSubmit={e => { e.preventDefault(); void submit('add'); }}><input aria-label="标签键" placeholder="标签键" maxLength={128} required value={key} onChange={e => setKey(e.target.value)} /><input aria-label="标签值" placeholder="标签值（可为空）" maxLength={256} value={value} onChange={e => setValue(e.target.value)} /><button disabled={busy} type="submit">保存</button><button type="button" onClick={() => setEditing(false)}>取消</button></form>}
    {error && <small role="alert">{error}</small>}
  </div>;
}

function ResourceTable({ rows, business, associations, sort, setSort }: { rows: Instance[]; business: Map<string, ServerBusiness>; associations: Map<string, ServerApps>; sort: ServerSort | null; setSort: (sort: ServerSort) => void }) {
  const sortKeys: Record<string, ServerSort['key']> = { CPU: 'cpu', 内存: 'memory', 关联应用数: 'apps', 到期时间: 'expiry' };
  return (
    <Table className="ecs-table">
      <TableHeader>
        <TableRow>
          {[
            '实例名 / ID',
            '业务关联',
            '租户',
            'CPU',
            '内存',
            '内网 IP',
            '公网 IP',
            '关联应用数',
            '关联应用',
            '付费方式',
            '到期时间',
            '标签',
            '项目',
          ].map((h) => {
            const key = sortKeys[h];
            const active = sort?.key === key;
            return <TableHead key={h} aria-sort={key ? (active ? (sort.direction === 'asc' ? 'ascending' : 'descending') : 'none') : undefined}>
              {key ? <button type="button" className="ecs-sort" onClick={() => setSort({ key, direction: active && sort.direction === 'asc' ? 'desc' : 'asc' })}>{h} <span aria-hidden="true">{active ? (sort.direction === 'asc' ? '▲' : '▼') : '↕'}</span></button> : h}
            </TableHead>;
          })}
        </TableRow>
      </TableHeader>
      <TableBody>
        {sortServerRows(rows, associations, sort).map((r) => (
          <TableRow key={resourceIdentity(r)}>
            <TableCell>
              <b>{r.name}</b>
              <small>{r.id}</small>
              <small>{r.instanceType}</small>
            </TableCell>
            <TableCell>{(business.get(resourceIdentity(r))?.categories ?? []).map(businessLabel).join('、') || '未识别'}
              {(business.get(resourceIdentity(r))?.categories.length ?? 0) > 1 && <small>关联多个分类</small>}
              {business.get(resourceIdentity(r))?.ambiguous && <small>部分 IP 关联待确认</small>}
            </TableCell>
            <TableCell>{tenantLabel(r)}<small>{r.accountId}</small></TableCell>
            <TableCell>{r.cpu} vCPU</TableCell>
            <TableCell>{r.memoryGiB} GiB</TableCell>
            <TableCell className="ecs-ip">
              {r.privateIps.length
                ? r.privateIps.map((ip) => <div key={ip}>{ip}</div>)
                : '无'}
            </TableCell>
            <TableCell className="ecs-ip">
              {r.publicIps.length
                ? r.publicIps.map((ip) => <div key={ip}>{ip}</div>)
                : '无'}
            </TableCell>
            <TableCell>{associations.has(resourceIdentity(r)) ? associations.get(resourceIdentity(r))!.apps.length : '待核实'}</TableCell>
            <TableCell>{associations.has(resourceIdentity(r)) ? <AppCell association={associations.get(resourceIdentity(r))} /> : '待核实'}</TableCell>
            <TableCell>
              {chargeTypeName(r.chargeType)}
              {r.chargeType !== 'PostPaid' && renewalStatusName(r.renewalStatus) !== '未知' && <small>{renewalStatusName(r.renewalStatus)}</small>}
            </TableCell>
            <TableCell>{r.chargeType === 'PostPaid' ? '—' : r.expiredAt ? formatBeijingTime(r.expiredAt, r.expiredAt) : '未知'}</TableCell>
            <TableCell>
              <TagCell row={r} />
            </TableCell>
            <TableCell>{projectName(r.projectId, r.tenantId)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
function ResourceGroup({
  group,
  path,
  open,
  setOpen,
  business,
  associations,
  sort,
  setSort,
}: {
  group: Group;
  path: string;
  open: Record<string, boolean>;
  setOpen: (path: string, value: boolean) => void;
  business: Map<string, ServerBusiness>;
  associations: Map<string, ServerApps>;
  sort: ServerSort | null;
  setSort: (sort: ServerSort) => void;
}) {
  const s = summarize(group.rows);
  return (
    <Collapsible
      className="ecs-group"
      open={open[path] ?? true}
      onOpenChange={(value) => setOpen(path, value)}
    >
      <CollapsibleTrigger className="ecs-group-trigger">
        <ChevronDown
          size={17}
          className={(open[path] ?? true) ? '' : 'ecs-closed'}
        />
        <strong>{group.label}</strong>
        <span>{s.count} 台</span>
        <small>
          {s.cpu} vCPU · {s.memory.toLocaleString('zh-CN')} GiB
        </small>
      </CollapsibleTrigger>
      <CollapsibleContent>
        {group.children.length ? (
          <div className="ecs-subgroups">
            {group.children.map((g) => (
              <ResourceGroup
                key={g.key}
                group={g}
                path={`${path}/${g.key}`}
                open={open}
                setOpen={setOpen}
                business={business}
                associations={associations}
                sort={sort}
                setSort={setSort}
              />
            ))}
          </div>
        ) : (
          <ResourceTable rows={group.rows} business={business} associations={associations} sort={sort} setSort={setSort} />
        )}
      </CollapsibleContent>
    </Collapsible>
  );
}
function subscribeUrl(callback: () => void) {
  window.addEventListener('popstate', callback);
  return () => window.removeEventListener('popstate', callback);
}
const readUrl = () => window.location.search;
const serverUrl = () => '';
export default function EcsView({ detail = false, site, tenantId }: { detail?: boolean; site?: CloudSite; tenantId?: string }) {
  const search = useSyncExternalStore(subscribeUrl, readUrl, serverUrl);
  const [editedFilters, setFilters] = useState<Filters | null>(null);
  const [manualTags, setManualTags] = useState<ManualTags>({});
  const [tagsReady, setTagsReady] = useState(false);
  const [tagsError, setTagsError] = useState(false);
  const allInstances = instances.map(row => ({ ...row, manualTags: manualTags[manualIdentity(row)] ?? {}, ecsTags: row.tags, tags: { ...manualTags[manualIdentity(row)], ...row.tags } }));
  const tagKeys = [...new Set(allInstances.flatMap(row => Object.keys(row.tags)))].sort((a, b) => a === 'env' ? -1 : b === 'env' ? 1 : compareText(a, b));
  const filters = editedFilters ?? filtersFromQuery(search, allInstances);
  async function saveTag(row: Instance, key: string, value: string, action: 'add' | 'delete') {
    const response = await fetch('/api/catalog/server-tags', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ resource: manualIdentity(row), key, value, action }) });
    const result = await response.json() as ManualTags & { error?: string };
    if (!response.ok) throw new Error(result.error || '保存失败');
    setManualTags(result);
  }
  useEffect(() => {
    const controller = new AbortController();
    fetch('/api/catalog/server-tags', { cache: 'no-store', signal: controller.signal }).then(async response => { if (!response.ok) throw new Error(); return await response.json() as ManualTags; }).then(result => { setManualTags(result); setTagsReady(true); }).catch(() => { if (!controller.signal.aborted) setTagsError(true); });
    return () => controller.abort();
  }, []);
  const [levels, setLevels] = useState<string[]>([]);
  const [nextLevel, setNextLevel] = useState('');
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [sort, setSort] = useState<ServerSort | null>(null);
  const [businessResult, setBusinessResult] = useState<BusinessResult | null>(null);
  const [businessError, setBusinessError] = useState(false);
  useEffect(() => {
    if (!detail) return;
    const controller = new AbortController();
    fetch('/api/catalog/server-business', { cache: 'no-store', signal: controller.signal })
      .then(async response => { if (!response.ok) throw new Error('catalog'); return await response.json() as BusinessResult; })
      .then(result => setBusinessResult(result))
      .catch(() => { if (!controller.signal.aborted) setBusinessError(true); });
    return () => controller.abort();
  }, [detail]);
  useEffect(() => {
    if (editedFilters) {
      const query = filtersQuery(filters);
      window.history.replaceState(
        null,
        '',
        window.location.pathname + (query ? `?${query}` : ''),
      );
    }
  }, [filters, editedFilters]);
  const scopedInstances = tenantId ? allInstances.filter(row => (row.tenantId || 'legacy-aliyun') === tenantId) : site ? partitionCloud(allInstances, tenantSnapshots)[site] : allInstances;
  const scoped = Boolean(site || tenantId);
  const scopedIds = new Set(tenantId ? [tenantId] : scopedInstances.map(row => row.tenantId || 'legacy-aliyun'));
  const shown = filterInstances(scopedInstances, filters);
  const basePath = tenantId ? serverTenantPath(tenantId) : site ? `/servers/${site}` : '/aliyun/instances';
  const business = classifyServers(instances, businessResult?.evidence ?? [], row => {
    if (!row.projectId) return undefined;
    const name = projects.find(p => p.id === row.projectId && p.tenantId === row.tenantId)?.name;
    return name === `未知项目（${row.projectId}）` ? undefined : name;
  });
  const associations = businessResult?.matchesCatalog && businessResult.ecsFetchedAt === fetchedAt
    ? associateServerApps(instances, inventoryApps, businessResult.evidence, processesForIp, (app, process) => !!matchAppToProcess({ ...app, ports: [] }, [process]))
    : new Map<string, ServerApps>();
  const tree: Group[] = businessResult ? businessCategories(shown, business).map(key => {
    const rows = shown.filter(row => business.get(resourceIdentity(row))?.category === key);
    return { key, label: businessLabel(key), rows,
      children: groupInstances(rows, levels.map(k => `tag:${k}`)) };
  }).filter(group => group.rows.length) : [];
  const set = (patch: Partial<Filters>) =>
    setFilters((current) => ({ ...(current ?? filters), ...patch }));
  const query = filtersQuery(filters);
  const link = (path: string) => path + (query ? `?${query}` : '');
  const totals = summarize(shown);
  const projectStats = projects
    .filter((p) => (!scoped || scopedIds.has(p.tenantId || 'legacy-aliyun')) && (!filters.tenant || (p.tenantId || 'legacy-aliyun') === filters.tenant) &&
      (!filters.project || projectToken(p.id, p.tenantId) === filters.project || JSON.stringify(p.id) === filters.project))
    .map((p) => ({
      ...p,
      ...summarize(shown.filter((r) => r.projectId === p.id && r.tenantId === p.tenantId)),
    }))
    .sort((a, b) => b.count - a.count || compareText(a.name, b.name));
  const envStats = groupInstances(shown, ['tag:env']);
  function expandAll(value: boolean) {
    const next: Record<string, boolean> = {};
    function visit(groups: Group[], parent = '') {
      for (const g of groups) {
        const path = parent ? `${parent}/${g.key}` : g.key;
        next[path] = value;
        visit(g.children, path);
      }
    }
    visit(tree);
    setOpen(next);
  }
  return (
    <Shell active={scoped ? `server-${tenantId || site}` : detail ? 'ecs-list' : 'ecs-summary'}>
      <main className="ecs-page">
        <div className="page-heading">
          <div>
            <div className="eyebrow">ALIYUN · CN-SHANGHAI</div>
            <h1>{detail ? '阿里云资源列表' : '阿里云资源汇总'}</h1>
            <p>
              已配置租户及地域 · ECS 快照 {formatBeijingTime(fetchedAt)}（北京时间）
            </p>
          </div>
          <a
            className="ecs-page-link"
            href={link(scoped ? (detail ? `${basePath}/summary` : basePath) : (detail ? '/aliyun' : '/aliyun/instances'))}
          >
            {detail ? '查看资源汇总' : '查看详细资源'}
            <ArrowUpRight size={17} />
          </a>
        </div>
        {scoped && !scopedInstances.length && <p className="settings-hint">当前快照中没有可归属此账号的 ECS。请核对租户名称和采集状态；无法确认账号的资源可在服务器资源总览中查看。</p>}
        <section className="panel ecs-filters" aria-label="资源筛选">
          {tenantSnapshots.filter(t => (!scoped || scopedIds.has(t.tenantId)) && (!filters.tenant || t.tenantId === filters.tenant)).map(t =>
            <p key={`${t.tenantId}/${t.region}`} className="settings-hint">{t.tenantName} · {t.region} · 采集：{formatBeijingTime(t.collectedAt, '时间未知')}</p>)}
          <div className="ecs-filter-row">
            {!scoped && <TenantFilter rows={instances} value={filters.tenant || ''} onChange={tenant => set({ tenant })} />}
            <label className="ecs-field ecs-search" htmlFor="ecs-search">
              <span>搜索</span>
              <Input
                id="ecs-search"
                aria-label="搜索 ECS"
                placeholder="实例名、ID、IP、操作系统或标签"
                value={filters.query}
                onChange={(e) => set({ query: e.target.value })}
              />
            </label>
            <Picker
              label="项目分组"
              value={filters.project}
              onChange={(value) => set({ project: value })}
            >
              <option value="">全部项目</option>
              {projects.filter(p => !scoped || scopedIds.has(p.tenantId || 'legacy-aliyun')).map((p) => (
                <option key={projectToken(p.id, p.tenantId)} value={projectToken(p.id, p.tenantId)}>
                  {p.tenantName ? `${p.tenantName} · ` : ''}{p.name}
                </option>
              ))}
            </Picker>
            <Picker
              label="实例状态"
              value={filters.status}
              onChange={(value) => set({ status: value })}
            >
              <option value="">全部状态</option>
              {[...new Set(instances.map((r) => r.status))].map((s) => (
                <option key={s} value={s}>
                  {statusName(s)}
                </option>
              ))}
            </Picker>
            <Picker
              label="公网 IP"
              value={filters.publicIp}
              onChange={(value) => set({ publicIp: value })}
            >
              <option value="">全部</option>
              <option value="yes">有公网 IP</option>
              <option value="no">无公网 IP</option>
            </Picker>
            <Button variant="ghost" onClick={() => setFilters(emptyFilters)}>
              清除筛选
            </Button>
          </div>
          {filters.tags.map((t, index) => (
            <div className="ecs-tag-filter" key={index}>
              <Picker
                label={`标签条件 ${index + 1}`}
                value={t.key}
                onChange={(key) =>
                  set({
                    tags: filters.tags.map((item, i) =>
                      i === index ? { key, value: '' } : item,
                    ),
                  })
                }
              >
                <option value="">选择标签</option>
                {tagKeys.map((k) => (
                  <option key={k} value={k}>
                    {k}
                  </option>
                ))}
              </Picker>
              <Picker
                label={`标签值 ${index + 1}`}
                value={t.value}
                onChange={(value) =>
                  set({
                    tags: filters.tags.map((item, i) =>
                      i === index ? { ...item, value } : item,
                    ),
                  })
                }
              >
                <option value="">全部值</option>
                {t.key &&
                  tagValues(t.key, allInstances).map((v) => (
                    <option key={v} value={v}>
                      {tagLabel(v)}
                    </option>
                  ))}
              </Picker>
              <Button
                variant="ghost"
                aria-label={`移除标签条件 ${index + 1}`}
                onClick={() =>
                  set({ tags: filters.tags.filter((_, i) => i !== index) })
                }
              >
                <X size={16} />
              </Button>
            </div>
          ))}
          <div className="ecs-filter-footer">
            <Button
              variant="outline"
              onClick={() =>
                set({ tags: [...filters.tags, { key: 'env', value: '' }] })
              }
            >
              <Plus size={16} />
              添加标签筛选
            </Button>
            <span>多个条件同时满足</span>
            <output>
              匹配 {shown.length} / {scopedInstances.length} 台
            </output>
          </div>
        </section>
        <Metrics rows={shown} />
        {detail && !businessResult && <p className="ecs-business-notice" role="status">{businessError ? '无法读取部署目录，业务分类暂不可用。请检查部署目录后刷新。' : '正在读取服务器业务关联…'}</p>}
        {detail && businessResult && !businessResult.matchesCatalog && <p className="ecs-business-notice" role="status">部署目录与当前快照不同步，业务分类可能过期，应用关联暂待核实。</p>}
        {detail && businessResult && businessResult.ecsFetchedAt !== fetchedAt && <p className="ecs-business-notice" role="status">服务器清单与当前快照不一致，请刷新页面后再核对业务分类及应用关联。</p>}
        {detail && businessResult && <BusinessDistribution rows={shown} business={business} />}
        {detail ? (
          <section className="panel ecs-detail">
            <div className="ecs-section-title">
              <div>
                <h2>分组资源清单</h2>
                <p>默认按业务分类分组。关联应用数按部署目录与 JumpServer 进程匹配结果去重；未采集、未匹配及有歧义的节点不计入，结果仅代表已观察到的部署。</p>
              </div>
              <div className="ecs-actions">
                <Button variant="outline" disabled={!shown.length || !businessResult || !associations.size} onClick={() => exportResources(shown, business, associations)}><Download size={16} />导出 CSV（{shown.length} 台）</Button>
                <Button variant="ghost" onClick={() => expandAll(true)}>
                  全部展开
                </Button>
                <Button variant="ghost" onClick={() => expandAll(false)}>
                  全部收起
                </Button>
              </div>
            </div>
            <div className="ecs-levels">
              <b>业务</b>
              {levels.map((key, index) => (
                <span key={key}>
                  → {key}
                  <Button
                    variant="ghost"
                    aria-label={`移除分组 ${key}`}
                    onClick={() => {
                      setLevels(levels.filter((_, i) => i !== index));
                      setOpen({});
                    }}
                  >
                    <X size={14} />
                  </Button>
                </span>
              ))}
              <Picker
                label="继续按标签分组"
                value={nextLevel}
                onChange={setNextLevel}
              >
                <option value="">选择标签</option>
                {tagKeys
                  .filter((k) => !levels.includes(k))
                  .map((k) => (
                    <option key={k} value={k}>
                      {k}
                    </option>
                  ))}
              </Picker>
              <Button
                variant="outline"
                disabled={!nextLevel}
                onClick={() => {
                  setLevels([...levels, nextLevel]);
                  setNextLevel('');
                  setOpen({});
                }}
              >
                <Plus size={16} />
                添加层级
              </Button>
            </div>
            {tagsError && <p role="alert">人工标签加载失败，请刷新页面重试。</p>}
            <TagsContext.Provider value={{ save: saveTag, ready: tagsReady }}>
            {tree.map((g) => (
              <ResourceGroup
                key={g.key}
                group={g}
                business={business}
                associations={associations}
                sort={sort}
                setSort={setSort}
                path={g.key}
                open={open}
                setOpen={(path, value) =>
                  setOpen((current) => ({ ...current, [path]: value }))
                }
              />
            ))}
            </TagsContext.Provider>
            {businessResult && !shown.length && (
              <div className="ecs-empty">
                没有符合条件的 ECS。
                <Button variant="link" onClick={() => setFilters(emptyFilters)}>
                  清除筛选
                </Button>
              </div>
            )}
          </section>
        ) : (
          <>
          <div className="ecs-summary-grid">
            <section className="panel">
              <div className="ecs-section-title">
                <div>
                  <h2>项目资源分布</h2>
                  <p>
                    {projectStats.filter((p) => p.count > 0).length}{' '}
                    个分组有匹配实例 · 项目目录 {snapshotProjectCount()} 个
                  </p>
                </div>
              </div>
              <Table className="ecs-project-table">
                <TableHeader>
                  <TableRow>
                    {[
                      '项目分组',
                      '实例数',
                      'CPU / vCPU',
                      '内存 / GiB',
                      '公网 IP / 台',
                    ].map((h) => (
                      <TableHead key={h}>{h}</TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {projectStats.map((p) => (
                    <TableRow key={projectToken(p.id, p.tenantId)}>
                      <TableCell>
                        <a
                          href={`${basePath}?${filtersQuery({ ...filters, project: projectToken(p.id, p.tenantId) })}`}
                        >
                          <b>{p.name}</b>
                          <ArrowUpRight size={14} />
                        </a>
                        {p.tenantName && <small>{p.tenantName}</small>}
                        <small>
                          {p.code || (!p.id ? 'ResourceGroupId 为空' : p.id)}
                        </small>
                        <div className="ecs-bar">
                          <i
                            style={{
                              width: `${totals.count ? (p.count / totals.count) * 100 : 0}%`,
                            }}
                          />
                        </div>
                      </TableCell>
                      <TableCell>{p.count}</TableCell>
                      <TableCell>{p.cpu}</TableCell>
                      <TableCell>{p.memory.toLocaleString('zh-CN')}</TableCell>
                      <TableCell>{p.publicIp}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </section>
            <section className="panel ecs-env">
              <div className="ecs-section-title">
                <div>
                  <h2>环境标签分布</h2>
                  <p>按 env 原始值统计</p>
                </div>
              </div>
              {envStats.map((g) => (
                <a
                  key={g.key}
                  href={`${basePath}?${filtersQuery({ ...filters, tags: [...filters.tags, { key: 'env', value: g.key }] })}`}
                >
                  <div>
                    <b>{g.label}</b>
                    <strong>
                      {g.rows.length}
                      <small> 台</small>
                    </strong>
                  </div>
                  <div className="ecs-bar">
                    <i
                      style={{
                        width: `${totals.count ? (g.rows.length / totals.count) * 100 : 0}%`,
                      }}
                    />
                  </div>
                  <p>
                    {summarize(g.rows).cpu} vCPU ·{' '}
                    {summarize(g.rows).memory.toLocaleString('zh-CN')} GiB
                  </p>
                </a>
              ))}
              {!envStats.length && <p className="ecs-empty">没有匹配资源</p>}
              <div className="ecs-note">
                项目来自
                ResourceGroup；未指定项目与默认资源组分别统计。标签缺失显示“未设置”，不会根据实例名推断环境。
              </div>
            </section>
          </div>
          </>
        )}
        <footer className="page-footer">
          <span>
            按实例 ID 计数 · 内存从 MiB 换算为 GiB · CPU、内存为配置容量
          </span>
          <span>已配置租户及地域的 ECS · 版本化快照</span>
        </footer>
      </main>
    </Shell>
  );
}
function snapshotProjectCount() {
  return projects.filter((p) => p.id && !p.name.startsWith('未知项目')).length;
}

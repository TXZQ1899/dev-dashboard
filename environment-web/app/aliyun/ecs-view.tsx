'use client';
import { useEffect, useState, useSyncExternalStore } from 'react';
import {
  Cloud,
  Cpu,
  MemoryStick,
  Server,
  Plus,
  X,
  ArrowUpRight,
  ChevronDown,
} from 'lucide-react';
import { Shell } from '../page';
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
  fetchedAt,
  tagKeys,
  tagValues,
  tagLabel,
  statusName,
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
function ResourceTable({ rows }: { rows: Instance[] }) {
  return (
    <Table className="ecs-table">
      <TableHeader>
        <TableRow>
          {[
            '实例名 / ID',
            'CPU',
            '内存',
            '操作系统名',
            '内网 IP',
            '公网 IP',
            '状态',
            '标签',
          ].map((h) => (
            <TableHead key={h}>{h}</TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((r) => (
          <TableRow key={r.id}>
            <TableCell>
              <b>{r.name}</b>
              <small>{r.id}</small>
              <small>{r.instanceType}</small>
            </TableCell>
            <TableCell>{r.cpu} vCPU</TableCell>
            <TableCell>{r.memoryGiB} GiB</TableCell>
            <TableCell className="ecs-os">{r.os}</TableCell>
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
            <TableCell>
              <span
                className={`ecs-status ${r.status === 'Running' ? 'running' : ''}`}
              >
                {statusName(r.status)}
              </span>
            </TableCell>
            <TableCell>
              <div className="ecs-tags">
                {Object.entries(r.tags).map(([key, value]) => (
                  <span key={key}>
                    {key}: {value || '空值'}
                  </span>
                ))}
                {!Object.keys(r.tags).length && '无标签'}
              </div>
            </TableCell>
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
}: {
  group: Group;
  path: string;
  open: Record<string, boolean>;
  setOpen: (path: string, value: boolean) => void;
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
              />
            ))}
          </div>
        ) : (
          <ResourceTable rows={group.rows} />
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
export default function EcsView({ detail = false }: { detail?: boolean }) {
  const search = useSyncExternalStore(subscribeUrl, readUrl, serverUrl);
  const [editedFilters, setFilters] = useState<Filters | null>(null);
  const filters = editedFilters ?? filtersFromQuery(search);
  const [levels, setLevels] = useState(['env']);
  const [nextLevel, setNextLevel] = useState('');
  const [open, setOpen] = useState<Record<string, boolean>>({});
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
  const shown = filterInstances(instances, filters);
  const tree = groupInstances(shown, [
    'project',
    ...levels.map((k) => `tag:${k}`),
  ]);
  const set = (patch: Partial<Filters>) =>
    setFilters((current) => ({ ...(current ?? filters), ...patch }));
  const query = filtersQuery(filters);
  const link = (path: string) => path + (query ? `?${query}` : '');
  const totals = summarize(shown);
  const projectStats = projects
    .filter((p) => !filters.project || JSON.stringify(p.id) === filters.project)
    .map((p) => ({
      ...p,
      ...summarize(shown.filter((r) => r.projectId === p.id)),
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
    <Shell active={detail ? 'ecs-list' : 'ecs-summary'}>
      <main className="ecs-page">
        <div className="page-heading">
          <div>
            <div className="eyebrow">ALIYUN · CN-SHANGHAI</div>
            <h1>{detail ? '阿里云资源列表' : '阿里云资源汇总'}</h1>
            <p>
              上海区域 · ECS 快照{' '}
              {new Date(fetchedAt).toLocaleString('zh-CN', {
                timeZone: 'Asia/Shanghai',
                hour12: false,
              })}
              （北京时间）
            </p>
          </div>
          <a
            className="ecs-page-link"
            href={link(detail ? '/aliyun' : '/aliyun/instances')}
          >
            {detail ? '查看资源汇总' : '查看详细资源'}
            <ArrowUpRight size={17} />
          </a>
        </div>
        <section className="panel ecs-filters" aria-label="资源筛选">
          <div className="ecs-filter-row">
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
              {projects.map((p) => (
                <option key={p.id} value={JSON.stringify(p.id)}>
                  {p.name}
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
                  tagValues(t.key).map((v) => (
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
              匹配 {shown.length} / {instances.length} 台
            </output>
          </div>
        </section>
        <Metrics rows={shown} />
        {detail ? (
          <section className="panel ecs-detail">
            <div className="ecs-section-title">
              <div>
                <h2>分组资源清单</h2>
                <p>项目固定为第一层，标签按添加顺序逐层分组。</p>
              </div>
              <div className="ecs-actions">
                <Button variant="ghost" onClick={() => expandAll(true)}>
                  全部展开
                </Button>
                <Button variant="ghost" onClick={() => expandAll(false)}>
                  全部收起
                </Button>
              </div>
            </div>
            <div className="ecs-levels">
              <b>项目</b>
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
            {tree.map((g) => (
              <ResourceGroup
                key={g.key}
                group={g}
                path={g.key}
                open={open}
                setOpen={(path, value) =>
                  setOpen((current) => ({ ...current, [path]: value }))
                }
              />
            ))}
            {!shown.length && (
              <div className="ecs-empty">
                没有符合条件的 ECS。
                <Button variant="link" onClick={() => setFilters(emptyFilters)}>
                  清除筛选
                </Button>
              </div>
            )}
          </section>
        ) : (
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
                    <TableRow key={p.id}>
                      <TableCell>
                        <a
                          href={`/aliyun/instances?${filtersQuery({ ...filters, project: JSON.stringify(p.id) })}`}
                        >
                          <b>{p.name}</b>
                          <ArrowUpRight size={14} />
                        </a>
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
                  href={`/aliyun/instances?${filtersQuery({ ...filters, tags: [...filters.tags, { key: 'env', value: g.key }] })}`}
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
        )}
        <footer className="page-footer">
          <span>
            按实例 ID 计数 · 内存从 MiB 换算为 GiB · CPU、内存为配置容量
          </span>
          <span>仅包含上海区域 ECS · 静态快照</span>
        </footer>
      </main>
    </Shell>
  );
}
function snapshotProjectCount() {
  return projects.filter((p) => p.id && !p.name.startsWith('未知项目')).length;
}

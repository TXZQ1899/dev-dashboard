'use client';
import { TenantFilter } from '@/components/tenant-filter';
import { tenantLabel } from '@/lib/tenants';
import { useState } from 'react';
import {
  ArrowUpRight,
  Database,
  KeyRound,
  Network,
  ShieldCheck,
} from 'lucide-react';
import { Shell } from '@/app/page';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { NativeSelect } from '@/components/ui/native-select';
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from '@/components/ui/table';
import { formatBeijingTime } from '@/lib/datetime';
import {
  compareText,
  distinctValues,
  dms,
  emptyFilters,
  filterInstances,
  instances,
  instancesCsv,
  loginStatuses,
  summarize,
  type DmsInstance,
  type Filters,
} from '@/lib/dms';
import './dms.css';

const consoleUrl = 'https://dms.aliyun.com/';
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
    <label className="dms-field">
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
function downloadCsv(rows: DmsInstance[]) {
  const url = URL.createObjectURL(
    new Blob([instancesCsv(rows)], { type: 'text/csv;charset=utf-8;' }),
  );
  const link = document.createElement('a');
  link.href = url;
  link.download = `dms-instances-${(dms.collectedAt ?? '').slice(0, 10)}.csv`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export default function DmsView() {
  const [filters, setFilters] = useState<Filters>(emptyFilters);
  const shown = filterInstances(instances, filters);
  const stats = summarize(shown);
  const duplicated = instances.filter((r) => r.duplicated > 0).length;
  const set = (patch: Partial<Filters>) => setFilters((c) => ({ ...c, ...patch }));
  const cards = [
    {
      label: 'DMS 实例',
      value: stats.count,
      note: `覆盖 ${stats.types} 种数据库类型`,
      icon: Database,
    },
    {
      label: '免登录',
      value: stats.free,
      note: '控制台显示无需登录',
      icon: ShieldCheck,
    },
    {
      label: '未登录',
      value: stats.needLogin,
      note: '本次会话没有登录凭据',
      icon: KeyRound,
    },
    {
      label: '地址已解析 IP',
      value: stats.resolved,
      note: `${stats.unresolved} 个地址无法解析`,
      icon: Network,
    },
  ];
  return (
    <Shell active="dms">
      <main className="dms-page">
        <TenantFilter rows={instances} value={filters.tenant || ''} onChange={tenant => set({ tenant })} />
        {(dms.tenants ?? []).filter(t => !filters.tenant || t.tenantId === filters.tenant).map(t =>
          <p key={t.tenantId} className="settings-hint">{t.tenantName} · 采集：{formatBeijingTime(t.collectedAt, '时间未知')}</p>)}
        <div className="page-heading">
          <div>
            <div className="eyebrow">ALIYUN · DMS</div>
            <h1>阿里云 DMS 数据库实例</h1>
            <p>
              DMS 控制台可见的全部数据库实例 · 快照{' '}
              {formatBeijingTime(dms.collectedAt, '未采集')}（北京时间）
            </p>
          </div>
          <a
            className="dms-page-link"
            href={consoleUrl}
            target="_blank"
            rel="noopener noreferrer"
          >
            打开 DMS 控制台
            <ArrowUpRight size={17} />
          </a>
        </div>
        {!dms.available && (
          <section className="panel dms-empty">
            <h2>该版本尚未采集 DMS 实例</h2>
            <p>
              在 Settings 配置租户凭据并单独同步 DMS，或执行全量同步。
            </p>
            <a href="/settings">打开 Settings</a>
          </section>
        )}
        <div className="dms-stats">
          {cards.map(({ label, value, note, icon: Icon }) => (
            <section className="panel" key={label}>
              <small>
                {label}
                <Icon size={16} />
              </small>
              <strong>{value.toLocaleString('zh-CN')}</strong>
              <em>{note}</em>
            </section>
          ))}
        </div>
        <section className="panel dms-toolbar" aria-label="实例筛选">
          <label className="dms-field dms-search">
            <span>搜索</span>
            <Input
              aria-label="搜索 DMS 实例"
              placeholder="实例名称、地址、IP 或实例 ID"
              value={filters.query}
              onChange={(e) => set({ query: e.target.value })}
            />
          </label>
          <Picker
            label="登录状态"
            value={filters.loginStatus}
            onChange={(value) => set({ loginStatus: value })}
          >
            <option value="">全部状态</option>
            {loginStatuses.map((status) => (
              <option key={status} value={status}>
                {status}
              </option>
            ))}
          </Picker>
          <Picker
            label="数据库类型"
            value={filters.dbType}
            onChange={(value) => set({ dbType: value })}
          >
            <option value="">全部类型</option>
            {distinctValues(instances, 'dbType').map((type) => (
              <option key={type} value={type}>
                {type}
              </option>
            ))}
          </Picker>
          <Picker
            label="实例地区"
            value={filters.region}
            onChange={(value) => set({ region: value })}
          >
            <option value="">全部地区</option>
            {distinctValues(instances, 'region').map((region) => (
              <option key={region} value={region}>
                {region}
              </option>
            ))}
          </Picker>
          <Button variant="ghost" onClick={() => setFilters(emptyFilters)}>
            清除筛选
          </Button>
          <Button
            variant="outline"
            disabled={!shown.length}
            onClick={() => downloadCsv(shown)}
          >
            导出 CSV（{shown.length}）
          </Button>
          <output>
            匹配 {shown.length} / {instances.length} 个实例
          </output>
        </section>
        <section className="panel dms-list">
          <div className="dms-section-title">
            <div>
              <h2>实例清单</h2>
              <p>
                {loginStatuses.join(' / ')} 为控制台登录状态口径，本页不执行数据库登录。
              </p>
            </div>
          </div>
          {!instances.length && <p className="dms-note">当前快照没有 DMS 实例。</p>}
          {instances.length > 0 && !shown.length && (
            <p className="dms-note">没有匹配的实例。</p>
          )}
          {shown.length > 0 && (
            <Table className="dms-table">
              <TableHeader>
                <TableRow>
                  {[
                    '实例名称',
                    '租户',
                    '实例地址',
                    '类型',
                    '实例地区',
                    '实例IP',
                    '登录状态',
                    '实例ID',
                  ].map((head) => (
                    <TableHead key={head}>{head}</TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {shown.map((row) => (
                  <TableRow key={row.resourceKey || `${row.tenantId}:${row.id}:${row.address}`}>
                    <TableCell>
                      {row.name || <span className="dms-missing">未命名</span>}
                    </TableCell>
                    <TableCell>{tenantLabel(row)}<small>{row.accountId}</small></TableCell>
                    <TableCell>
                      <code>{row.address}</code>
                    </TableCell>
                    <TableCell>{row.dbType}</TableCell>
                    <TableCell>{row.region}</TableCell>
                    <TableCell>
                      {row.ip ? (
                        <code>{row.ip}</code>
                      ) : (
                        <span className="dms-missing">未解析</span>
                      )}
                    </TableCell>
                    <TableCell>
                      <span
                        className={
                          row.loginStatus === '免登录'
                            ? 'dms-tag free'
                            : 'dms-tag login'
                        }
                      >
                        {row.loginStatus || '未知'}
                      </span>
                    </TableCell>
                    <TableCell>
                      <code>{row.id}</code>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
          <p className="dms-note">
            免登录按控制台口径判定：控制模式为安全模式或实例启用信任登录时为免登录，其余实例在本次会话没有登录凭据即未登录。 实例 IP
            由实例地址经 AliDNS DoH
            解析，未解析表示该地址只在 VPC 私网内可解析或实例已不存在。
            {duplicated > 0 &&
              ` 另有 ${duplicated} 个实例地址在 DMS 中重复录入，已按实例 ID 与地址合并。`}
          </p>
        </section>
      </main>
    </Shell>
  );
}

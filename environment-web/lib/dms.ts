import snapshot from './dms-snapshot.json';
import type { TenantResource, TenantScope } from './tenants';
// Keep SSR and browser ordering identical across runtime locales.
export const compareText = (a: string, b: string) =>
  a === b ? 0 : a < b ? -1 : 1;
export type DmsInstance = TenantResource & {
  name: string;
  id: string;
  address: string;
  dbType: string;
  region: string;
  ip: string;
  loginStatus: string;
  env: string;
  source: string;
  status: string;
  version: string;
  dmsIds: string[];
  duplicated: number;
};
export type DmsSnapshot = {
  tenants?: TenantScope[];
  available: boolean;
  collectedAt: string | null;
  instances: unknown[];
  totalCount?: number;
  rawRecordCount?: number;
};
export const dms = snapshot as unknown as DmsSnapshot;
const text = (value: unknown) =>
  typeof value === 'string' ? value : value == null ? '' : String(value);
export const instances: DmsInstance[] = (
  (dms.instances ?? []) as Record<string, unknown>[]
).map((row) => ({
  tenantId: text(row.tenantId), tenantName: text(row.tenantName), accountId: text(row.accountId), resourceKey: text(row.resourceKey),
  name: text(row['实例名称']),
  id: text(row['实例ID']),
  address: text(row['实例地址']),
  dbType: text(row['类型'] || row['dbType']),
  region: text(row['实例地区'] || row['regionId']),
  ip: text(row['实例IP']),
  loginStatus: text(row['登录状态']),
  env: text(row['环境']),
  source: text(row['实例来源']),
  status: text(row['实例状态']),
  version: text(row['版本']),
  dmsIds: Array.isArray(row.dmsInstanceIds)
    ? row.dmsInstanceIds.map(text)
    : [text(row['id'])].filter(Boolean),
  duplicated: Number(row['重复记录'] ?? 0),
}));
export const loginStatuses = ['免登录', '未登录'];
export type Filters = {
  tenant?: string;
  query: string;
  loginStatus: string;
  dbType: string;
  region: string;
};
export const emptyFilters: Filters = {
  tenant: '',
  query: '',
  loginStatus: '',
  dbType: '',
  region: '',
};
export function filterInstances(rows: DmsInstance[], filters: Filters) {
  const q = filters.query.trim().toLocaleLowerCase();
  return rows.filter(
    (r) =>
      (!filters.tenant || (r.tenantId || 'legacy-aliyun') === filters.tenant) &&
      (!filters.loginStatus || r.loginStatus === filters.loginStatus) &&
      (!filters.dbType || r.dbType === filters.dbType) &&
      (!filters.region || r.region === filters.region) &&
      (!q ||
        [r.name, r.address, r.region, r.dbType, r.ip, r.id, r.tenantName || '', r.accountId || '', ...r.dmsIds].some(
          (value) => value.toLocaleLowerCase().includes(q),
        )),
  );
}
export function summarize(rows: DmsInstance[]) {
  const free = rows.filter((r) => r.loginStatus === '免登录').length;
  return {
    count: rows.length,
    free,
    needLogin: rows.length - free,
    types: new Set(rows.map((r) => r.dbType).filter(Boolean)).size,
    resolved: rows.filter((r) => r.ip).length,
    unresolved: rows.filter((r) => !r.ip).length,
  };
}
export const distinctValues = (rows: DmsInstance[], key: 'dbType' | 'region') => [
  ...new Set(rows.map((r) => r[key]).filter(Boolean)),
].sort(compareText);
export const csvFields = [
  '实例名称',
  '实例地址',
  '类型',
  '实例地区',
  '实例IP',
  '登录状态',
  '实例ID',
  '租户ID', '租户名称', '阿里云账号UID',
];
const csvCell = (value: unknown) => {
  const text = String(value ?? '');
  return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
};
export function instancesCsv(rows: DmsInstance[]) {
  const lines = rows.map((r) =>
    [
      r.name,
      r.address,
      r.dbType,
      r.region,
      r.ip,
      r.loginStatus,
      r.id,
      r.tenantId || '', r.tenantName || '', r.accountId || '',
    ]
      .map(csvCell)
      .join(','),
  );
  return `\uFEFF${[csvFields.join(','), ...lines].join('\r\n')}\r\n`;
}

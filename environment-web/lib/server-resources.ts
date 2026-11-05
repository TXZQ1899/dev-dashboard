import type { AliyunTenantSummary, TenantResource, TenantScope } from './tenants';

export type CloudSite = 'xiuping' | 'thomascookjv';
type Capacity = TenantResource & { cpu: number; memoryGiB: number };
const cloudSites: CloudSite[] = ['xiuping', 'thomascookjv'];
const resourceTenantId = (row: TenantResource) => row.tenantId || 'legacy-aliyun';
export function siteForName(name: string | undefined): CloudSite | undefined {
  const normalized = (name || '').trim().toLocaleLowerCase().replace(/[\s\-－_]/g, '');
  if (normalized === '修平' || normalized === '上海修平' || normalized === '阿里云上海修平') return 'xiuping';
  if (normalized === 'thomascookjv' || normalized === '阿里云thomascookjv') return 'thomascookjv';
}
// 租户改名后快照名称不再能识别站点。优先使用旧名称证据，其次固定现有租户
// （legacy-aliyun 对应 xiuping）并按配置顺序补齐，保证详情页仍按租户 ID 归属。
export function siteTenantMap(items: TenantResource[]): Partial<Record<CloudSite, string>> {
  const ids: string[] = [];
  const nameSites = new Map<string, Set<CloudSite>>();
  for (const item of items) {
    const id = resourceTenantId(item);
    if (!ids.includes(id)) ids.push(id);
    const site = siteForName(item.tenantName);
    if (site) nameSites.set(id, new Set([...(nameSites.get(id) || []), site]));
  }
  const result: Partial<Record<CloudSite, string>> = {};
  const assigned = new Set<string>();
  for (const id of ids) {
    const matches = nameSites.get(id);
    if (matches?.size !== 1) continue;
    const site = [...matches][0];
    if (!result[site]) { result[site] = id; assigned.add(id); }
  }
  if (!result.xiuping && !assigned.has('legacy-aliyun') && !nameSites.get('legacy-aliyun')?.size) {
    result.xiuping = 'legacy-aliyun';
    assigned.add('legacy-aliyun');
  }
  for (const site of cloudSites) {
    if (result[site]) continue;
    const id = ids.find((value) => !assigned.has(value));
    if (!id) break;
    result[site] = id;
    assigned.add(id);
  }
  return result;
}
export function cloudSiteLabel(site: CloudSite, mapping: Partial<Record<CloudSite, string>>, tenants: AliyunTenantSummary[] | null, failed = false): string {
  if (failed) return '阿里云-租户信息不可用';
  if (tenants === null) return '阿里云-加载中';
  const id = mapping[site];
  const tenant = tenants.find((item) => item.id === id);
  return tenant ? `阿里云-${tenant.name}` : '阿里云-租户未确认';
}
export function partitionCloud<T extends Capacity>(rows: T[], scopes: TenantScope[], tenants: TenantResource[] = []) {
  const mapping = siteTenantMap([...scopes, ...rows, ...tenants]);
  const byTenant = new Map<string, CloudSite>();
  for (const site of cloudSites) {
    const id = mapping[site];
    if (id) byTenant.set(id, site);
  }
  const result: Record<CloudSite | 'unknown', T[]> = { xiuping: [], thomascookjv: [], unknown: [] };
  for (const row of rows) result[byTenant.get(resourceTenantId(row)) ?? 'unknown'].push(row);
  return result;
}
export function capacity(rows: { cpu: number; memoryGiB: number }[]) {
  return { count: rows.length, cpu: rows.reduce((sum, row) => sum + row.cpu, 0), memory: rows.reduce((sum, row) => sum + row.memoryGiB, 0) };
}

export function serverTenantPath(id: string | undefined): string {
  return id ? `/servers/${encodeURIComponent(id)}` : '/aliyun/instances';
}

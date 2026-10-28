import type { TenantResource, TenantScope } from './tenants';

export type CloudSite = 'xiuping' | 'thomascookjv';
type Capacity = TenantResource & { cpu: number; memoryGiB: number };
export function siteForName(name: string | undefined): CloudSite | undefined {
  const normalized = (name || '').trim().toLocaleLowerCase().replace(/[\s\-－_]/g, '');
  if (normalized === '修平' || normalized === '上海修平' || normalized === '阿里云上海修平') return 'xiuping';
  if (normalized === 'thomascookjv' || normalized === '阿里云thomascookjv') return 'thomascookjv';
}
export function partitionCloud<T extends Capacity>(rows: T[], scopes: TenantScope[]) {
  const names = new Map<string, Set<CloudSite>>();
  for (const item of [...scopes, ...rows]) {
    const id = item.tenantId || 'legacy-aliyun';
    const site = siteForName(item.tenantName);
    if (site) names.set(id, new Set([...(names.get(id) || []), site]));
  }
  const result: Record<CloudSite | 'unknown', T[]> = { xiuping: [], thomascookjv: [], unknown: [] };
  for (const row of rows) {
    const matches = names.get(row.tenantId || 'legacy-aliyun');
    const site = matches?.size === 1 ? [...matches][0] : 'unknown';
    result[site].push(row);
  }
  return result;
}
export function capacity(rows: { cpu: number; memoryGiB: number }[]) {
  return { count: rows.length, cpu: rows.reduce((sum, row) => sum + row.cpu, 0), memory: rows.reduce((sum, row) => sum + row.memoryGiB, 0) };
}

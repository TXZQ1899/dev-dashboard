export type TenantResource = {
  tenantId?: string;
  tenantName?: string;
  accountId?: string;
  resourceKey?: string;
};
export type TenantScope = { tenantId: string; tenantName: string; region: string; collectedAt: string | null };
export const resourceTenantId = (row: TenantResource) => row.tenantId || 'legacy-aliyun';
export const tenantLabel = (row: TenantResource) => row.tenantName || (row.tenantId ? row.tenantId : '现有租户（历史数据）');
export const resourceIdentity = (row: TenantResource & { id: string }) => row.resourceKey || JSON.stringify([resourceTenantId(row), row.id]);
export function tenantOptions(rows: TenantResource[]) {
  return [...new Map(rows.map(row => [resourceTenantId(row), { id: resourceTenantId(row), name: tenantLabel(row) }])).values()];
}

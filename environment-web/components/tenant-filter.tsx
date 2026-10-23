'use client';
import { resourceTenantId, tenantOptions, type TenantResource } from '@/lib/tenants';
export { resourceTenantId };
export function TenantFilter({ rows, value, onChange }: {
  rows: TenantResource[]; value: string; onChange: (value: string) => void;
}) {
  return <label className="ecs-field"><span>租户</span>
    <select aria-label="租户筛选" value={value} onChange={event => onChange(event.target.value)}>
      <option value="">全部租户</option>
      {tenantOptions(rows).map(tenant => <option key={tenant.id} value={tenant.id}>{tenant.name}</option>)}
    </select>
  </label>;
}

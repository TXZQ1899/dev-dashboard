'use client';
import { cloudSiteLabel, siteTenantMap, serverTenantPath } from '@/lib/server-resources';
import { instances, tenantSnapshots } from '@/lib/ecs';
import { useAliyunTenants } from '@/lib/aliyun-tenant-cache';
import './server-tabs.css';
export function ServerTabs({ active }: { active: string }) {
  const { tenants, failed } = useAliyunTenants();
  const tenantSites = siteTenantMap([...tenantSnapshots, ...instances]);
  const tabs = [
    { key: 'server-overview', label: '服务器资源总览', href: '/servers' },
    { key: `server-${tenantSites.xiuping}`, label: cloudSiteLabel('xiuping', tenantSites, tenants, failed), href: serverTenantPath(tenantSites.xiuping) },
    { key: `server-${tenantSites.thomascookjv}`, label: cloudSiteLabel('thomascookjv', tenantSites, tenants, failed), href: serverTenantPath(tenantSites.thomascookjv) },
    { key: 'datacenter', label: '本地机房', href: '/datacenter' },
  ];
  return <nav className="server-tabs" aria-label="服务器资源">
    {tabs.map(tab => <a key={tab.key} href={tab.href} aria-current={active === tab.key ? 'page' : undefined}>{tab.label}</a>)}
  </nav>;
}

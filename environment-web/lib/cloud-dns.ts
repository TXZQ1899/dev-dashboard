import snapshot from './cloud-dns-snapshot.json';
import type { DnsRecord } from './dns';
import type { TenantResource, TenantScope } from './tenants';

export type DnsZone = TenantResource & {
  id: string;
  name: string;
  visibility: 'public' | 'private';
  recordCount: number;
  recordsCollected: boolean;
  skipReason: string;
  observedAt?: string;
  records: DnsRecord[];
  raw?: Record<string, unknown>;
};
export const cloudDns = snapshot as unknown as {
  available: boolean;
  collectedAt: string | null;
  tenants: TenantScope[];
  zones: DnsZone[];
};
export const cloudDnsRecords = cloudDns.zones.flatMap(zone => zone.records.map(record => ({
  ...record, nativeId: record.id, id: record.resourceKey || JSON.stringify([zone.tenantId, zone.visibility, zone.id, record.id]),
})));

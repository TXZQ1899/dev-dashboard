import type { Instance } from './ecs';
import type { App } from './inventory';
import { resourceIdentity } from './tenants';
import type { BusinessEvidence } from './server-business';
import type { Process } from './server-processes';

export type ServerApps = { apps: { id: string; name: string }[]; ambiguous: boolean };
export type ServerSort = { key: 'cpu' | 'memory' | 'apps' | 'expiry'; direction: 'asc' | 'desc' };

export function sortServerRows(rows: Instance[], associations: Map<string, ServerApps>, sort: ServerSort | null): Instance[] {
  if (!sort) return rows;
  const value = (row: Instance): number | null => {
    if (sort.key === 'cpu') return Number.isFinite(row.cpu) ? row.cpu : null;
    if (sort.key === 'memory') return Number.isFinite(row.memoryGiB) ? row.memoryGiB : null;
    if (sort.key === 'apps') return associations.get(resourceIdentity(row))?.apps.length ?? 0;
    if (row.chargeType === 'PostPaid' || !row.expiredAt) return null;
    const date = Date.parse(row.expiredAt);
    return Number.isFinite(date) ? date : null;
  };
  return [...rows].sort((a, b) => {
    const left = value(a), right = value(b);
    if (left === null) return right === null ? 0 : 1;
    if (right === null) return -1;
    return (left - right) * (sort.direction === 'asc' ? 1 : -1);
  });
}

// IP is deployment evidence, not proof of ownership. Shared IPs remain unresolved.
export function associateServerApps(instances: Instance[], apps: App[], evidence: BusinessEvidence[], processesForIp: (ip: string) => Process[], matches: (app: App, process: Process) => boolean): Map<string, ServerApps> {
  const byIp = new Map<string, Set<string>>();
  const result = new Map<string, ServerApps>();
  for (const row of instances) {
    const id = resourceIdentity(row);
    result.set(id, { apps: [], ambiguous: false });
    for (const ip of new Set([...row.privateIps, ...row.publicIps].filter(Boolean))) {
      if (!byIp.has(ip)) byIp.set(ip, new Set());
      byIp.get(ip)!.add(id);
    }
  }
  const seen = new Map<string, Set<string>>();
  const appById = new Map(apps.map(app => [String(app.id), app]));
  const byEvidenceIp = new Map<string, Set<string>>();
  for (const item of evidence) {
    if (item.unitType !== 'APPLICATION' || !item.appId || !appById.has(item.appId)) continue;
    const ip = item.ip.trim();
    if (!byEvidenceIp.has(ip)) byEvidenceIp.set(ip, new Set());
    byEvidenceIp.get(ip)!.add(item.appId);
  }
  for (const [ip, appIds] of byEvidenceIp) {
      const ids = byIp.get(ip);
      if (!ids) continue;
      if (ids.size !== 1) {
        for (const id of ids) result.get(id)!.ambiguous = true;
        continue;
      }
      const id = [...ids][0];
      if (!seen.has(id)) seen.set(id, new Set());
      for (const process of processesForIp(ip)) {
        const candidates = [...appIds].map(appId => appById.get(appId)!).filter(app => matches(app, process));
        if (candidates.length > 1) { result.get(id)!.ambiguous = true; continue; }
        const app = candidates[0];
        if (app && !seen.get(id)!.has(app.id)) {
          seen.get(id)!.add(app.id);
          result.get(id)!.apps.push({ id: app.id, name: app.name });
        }
      }
  }
  return result;
}

export function csvCell(value: string): string {
  const safe = /^\s*[=+\-@]/.test(value) ? `'${value}` : value;
  return /[",\r\n]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe;
}

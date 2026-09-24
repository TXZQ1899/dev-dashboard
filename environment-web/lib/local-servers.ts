import { apps, envs, inventoryDate, type App, type Env, type Deployment } from './inventory';
import { instances } from './ecs';
import { specs as jumpserverSpecs, fetchedAt as specsDate, type ServerSpec } from './server-specs';

export type LocalServer = {
  ip: string;
  hostname: string;
  cpu: number;
  memoryMB: number;
  memoryGiB: number;
  os: string;
  specsCollected: boolean;
  loginStatus?: string;
  appCount: number;
  appName: string;
  environments: string[];
};

// Nominal capacity buckets: MemTotal excludes kernel-reserved memory, so
// same-spec machines report slightly different raw values (e.g. nine distinct
// raw values across nominal 16GB hosts). Snap raw MB to the nearest standard
// capacity for display; the raw value stays available as memoryMB.
const CAPACITIES = [2, 4, 6, 8, 12, 16, 24, 32, 48, 64, 96, 128, 192, 256];
export function nominalMemoryGiB(memoryMB: number): number {
  const gib = memoryMB / 1024;
  if (!gib) return 0;
  return CAPACITIES.reduce((best, c) =>
    Math.abs(c - gib) < Math.abs(best - gib) ? c : best,
  );
}

export const compareText = (a: string, b: string) =>
  a === b ? 0 : a < b ? -1 : 1;

// All ECS IPs (private + public) — these are cloud, not local.
const ecsIpSet = new Set<string>();
for (const inst of instances) {
  for (const ip of inst.privateIps) ecsIpSet.add(ip);
  for (const ip of inst.publicIps) ecsIpSet.add(ip);
}

// JumpServer specs keyed by IP for enrichment.
const specsByIp = new Map<string, ServerSpec>();
for (const s of jumpserverSpecs) {
  for (const ip of s.ip.split(/[\s,]+/)) {
    const trimmed = ip.trim();
    if (trimmed) specsByIp.set(trimmed, s);
  }
}

// Build DevOps deployment map: IP -> { apps, environments }
const devopsMap = new Map<string, { apps: Set<string>; environments: Set<string> }>();
for (const app of apps) {
  for (const env of envs) {
    for (const row of (app.envs[env] || []) as Deployment[]) {
      const ip = row.ip?.trim();
      if (!ip) continue;
      if (!devopsMap.has(ip)) devopsMap.set(ip, { apps: new Set(), environments: new Set() });
      const entry = devopsMap.get(ip)!;
      entry.apps.add(app.name);
      entry.environments.add(env);
    }
  }
}

// Local servers = DevOps IPs not in ECS, enriched with specs where available.
export const localServers: LocalServer[] = [...devopsMap.entries()]
  .filter(([ip]) => !ecsIpSet.has(ip))
  .map(([ip, info]) => {
    const specs = specsByIp.get(ip);
    const collected = !!(specs && (specs.specsCollected ?? specs.cpu > 0));
    return {
      ip,
      hostname: specs?.hostname || ip,
      cpu: specs?.cpu ?? 0,
      memoryMB: specs?.memoryMB ?? 0,
      memoryGiB: nominalMemoryGiB(specs?.memoryMB ?? 0),
      os: specs?.os || '未采集',
      specsCollected: collected,
      loginStatus: specs?.loginStatus,
      appCount: info.apps.size,
      appName: [...info.apps].sort(compareText).join(', '),
      environments: [...info.environments].sort(),
    };
  })
  .sort((a, b) =>
    a.appCount !== b.appCount
      ? b.appCount - a.appCount
      : compareText(a.ip, b.ip),
  );

export const fetchedAt = specsDate;
export const inventoryDateStr = inventoryDate;

export function summarize(rows: LocalServer[]) {
  return {
    count: rows.length,
    cpu: rows.reduce((n, r) => n + r.cpu, 0),
    memory: rows.reduce((n, r) => n + r.memoryGiB, 0),
    collected: rows.filter((r) => r.specsCollected).length,
    apps: new Set(rows.flatMap((r) => r.appName.split(', '))).size,
  };
}

export function filterServers(rows: LocalServer[], query: string) {
  const q = query.trim().toLocaleLowerCase();
  if (!q) return rows;
  return rows.filter(
    (r) =>
      r.ip.toLocaleLowerCase().includes(q) ||
      r.hostname.toLocaleLowerCase().includes(q) ||
      r.os.toLocaleLowerCase().includes(q) ||
      r.appName.toLocaleLowerCase().includes(q),
  );
}

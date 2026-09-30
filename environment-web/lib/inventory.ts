import snapshot from './snapshot.json';
export const envs = ['TEST', 'SIMULATION', 'PRODUCT'] as const;
export type Env = (typeof envs)[number];
export const labels = {
  TEST: '测试环境',
  SIMULATION: '仿真环境',
  PRODUCT: '生产环境',
};
export type Deployment = {
  ip: string;
  deploy: string;
  config: string;
  status: string;
  error: string;
  port?: string;
  branch?: string;
  branchSource?: string;
  repository?: string;
  lastPublishedAt?: string;
  publishStatus?: string;
  publishTimeSource?: string;
  pushIn?: {operation?: string; status?: string; startTime?: string | null; endTime?: string | null}[];
};
export type App = {
  repository?: string;
  id: string;
  name: string;
  http: string;
  port: string;
  envs: Record<Env, Deployment[]>;
};
function stableRecord(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableRecord).join(',')}]`;
  if (value !== null && typeof value === 'object') {
    const record = value as Record<string, unknown>;
    return `{${Object.keys(record)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${stableRecord(record[key])}`)
      .join(',')}}`;
  }
  return JSON.stringify(value) ?? 'undefined';
}
export function uniqueDeployments(rows: Deployment[]): Deployment[] {
  const seen = new Set<string>();
  return rows.filter((row) => {
    const key = stableRecord(row);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}
export const apps: App[] = snapshot.apps.map((app) => ({
  ...app,
  envs: Object.fromEntries(
    envs.map((env) => [env, uniqueDeployments(app.envs[env])]),
  ) as Record<Env, Deployment[]>,
}));
export const inventoryDate =
  (snapshot as { collectedAt?: string }).collectedAt ||
  '2026-09-02T15:57:36+08:00';

export function info(app: App, env: Env) {
  const rows = uniqueDeployments(app.envs[env]);
  const ips = [...new Set(rows.map((r) => r.ip).filter(Boolean))];
  const unknown =
    !rows.length || rows.some((r) => r.status !== '成功' || !r.ip);
  return { rows, ips, unknown, single: !unknown && ips.length === 1 };
}
export const stats = envs.map((env) => ({
  env,
  servers: new Set(apps.flatMap((a) => info(a, env).ips)).size,
  instances: apps.reduce(
    (n, a) => n + info(a, env).rows.filter((r) => r.ip).length,
    0,
  ),
  deployed: apps.filter((a) => info(a, env).ips.length).length,
  unknown: apps.filter((a) => info(a, env).unknown).length,
}));
export const singles = apps.filter((a) => info(a, 'PRODUCT').single);

// Count applications by ID within each environment/IP; retain per-server ports.
export function serversFor(inventory: App[], env: Env) {
  const servers = new Map<string, Map<string, App & { ports: string[] }>>();
  for (const app of inventory) {
    for (const row of (env ? app.envs[env] : envs.flatMap(e => app.envs[e]))) {
      if (!row.ip) continue;
      if (!servers.has(row.ip)) servers.set(row.ip, new Map());
      const members = servers.get(row.ip)!;
      if (!members.has(app.id)) members.set(app.id, { ...app, ports: [] });
      const member = members.get(app.id)!;
      const port = row.port?.trim() || app.port?.trim() || '未提供端口';
      if (!member.ports.includes(port)) member.ports.push(port);
    }
  }
  return [...servers.entries()]
    .map(([ip, members]) => ({ ip, apps: [...members.values()] }))
    .sort(
      (a, b) =>
        b.apps.length - a.apps.length ||
        (a.ip < b.ip ? -1 : a.ip > b.ip ? 1 : 0),
    );
}
export const environmentServers = envs.map((env) => ({
  env,
  servers: serversFor(apps, env),
}));
export function sharedServersFor(inventory: App[], env: Env) {
  return serversFor(inventory, env).filter((server) => server.apps.length > 1);
}
export const sharedProductionServers = sharedServersFor(apps, 'PRODUCT');
export const sharedProductionAppIds = new Set(
  sharedProductionServers.flatMap((server) => server.apps.map((app) => app.id)),
);

// Missing configuration is distinct from a failed environment read.
export function environmentPresent(app: App, env: Env): boolean | null {
  const rows = app.envs[env];
  if (rows.some(r => r.ip || r.deploy || r.config)) return true;
  if (rows.length && rows.every(r => r.status === '无环境配置')) return false;
  return null;
}
export function matchesEnvironmentCombination(app: App, selected: Env[] | null) {
  return selected === null || envs.every(e => environmentPresent(app,e) === selected.includes(e));
}
export function latestSuccessfulPushIn(app: App, env?: Env): string {
  const candidates: string[] = [];
  for (const row of (env ? app.envs[env] : envs.flatMap(e => app.envs[e]))) {
    for (const step of row.pushIn || []) {
      if (step.status === 'SUCCESS' && (step.operation || '').replace(/[\s_-]/g,'').toLowerCase() === 'pushin') {
        const value=step.endTime || step.startTime;
        if (value) candidates.push(value);
      }
    }
    if (row.publishStatus === 'SUCCESS' && row.lastPublishedAt) candidates.push(row.lastPublishedAt);
  }
  return candidates.filter(v => Number.isFinite(deploymentTimestamp(v))).sort((a,b)=>deploymentTimestamp(b)-deploymentTimestamp(a))[0] || '';
}
function deploymentTimestamp(value: string) {
  return Date.parse(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(value) ? value.replace(' ','T')+'+08:00' : value);
}
export function overYearUndeployed(app: App, env: Env, reference=inventoryDate) {
  const rows = app.envs[env] || [];
  if (!rows.some(row => row.ip?.trim())) return false;
  const now = Date.parse(reference);
  if (!Number.isFinite(now)) return false;
  const date = latestSuccessfulPushIn(app, env);
  if (!date) return true;
  const deployed = deploymentTimestamp(date);
  if (!Number.isFinite(deployed) || deployed > now) return false;
  const current = new Date(now + 8 * 3600000);
  const day = current.getUTCDate();
  current.setUTCDate(1);
  current.setUTCMonth(current.getUTCMonth() - 12);
  current.setUTCDate(Math.min(day, new Date(Date.UTC(current.getUTCFullYear(), current.getUTCMonth() + 1, 0)).getUTCDate()));
  return deployed < current.getTime() - 8 * 3600000;
}
export function matchesDeploymentPeriod(app: App, env: Env | '', period: string, reference=inventoryDate) {
  if (!env || !period) return true;
  const now=Date.parse(reference);
  if (period === 'older') return overYearUndeployed(app, env, reference);
  const time=deploymentTimestamp(latestSuccessfulPushIn(app,env));
  if (!Number.isFinite(time) || !Number.isFinite(now) || time>now) return false;
  const monthsAgo=(n:number)=>{
    const d=new Date(now+8*3600000),day=d.getUTCDate();
    d.setUTCDate(1);d.setUTCMonth(d.getUTCMonth()-n);
    d.setUTCDate(Math.min(day,new Date(Date.UTC(d.getUTCFullYear(),d.getUTCMonth()+1,0)).getUTCDate()));
    return d.getTime()-8*3600000;
  };
  const thresholds:Record<string,number>={week:now-7*86400000,fortnight:now-15*86400000,month:monthsAgo(1),quarter:monthsAgo(3),half:monthsAgo(6),year:monthsAgo(12)};
  return period in thresholds && time>=thresholds[period];
}

export function repositoryCategory(raw: string | undefined): string {
  if (!raw?.trim()) return '未提供';
  try {
    const address = raw.trim().replace(/^git@([^:]+):/, 'ssh://git@$1/');
    const host = new URL(address).hostname.toLowerCase();
    if (host === 'gitlab.dev.thomascook.com.cn' || host.startsWith('gitlab.')) return 'Local Gitlab';
    if (host === 'codeup.aliyun.com') return '阿里 云效';
    if (host === 'code.aliyun.com') return '地址失效';
  } catch { /* Unrecognized addresses remain unclassified. */ }
  return '其他';
}

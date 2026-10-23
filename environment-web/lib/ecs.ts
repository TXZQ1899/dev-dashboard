import snapshot from './ecs-snapshot.json';
import type { TenantResource, TenantScope } from './tenants';
// Keep SSR and browser ordering identical across runtime locales.
export const compareText = (a: string, b: string) =>
  a === b ? 0 : a < b ? -1 : 1;
export type Instance = TenantResource & {
  id: string;
  name: string;
  projectId: string;
  cpu: number;
  memoryGiB: number;
  os: string;
  privateIps: string[];
  publicIps: string[];
  tags: Record<string, string>;
  status: string;
  region: string;
  zone: string;
  instanceType: string;
};
export const instances: Instance[] = snapshot.instances.map((row) => ({
  ...row,
  tags: row.tags as Record<string, string>,
}));
export const fetchedAt = snapshot.fetchedAt;
export const tenantSnapshots = (snapshot as unknown as { tenants?: TenantScope[] }).tenants ?? [];

// 部署清单中的公网 IP（EIP）需要展示其对应 ECS 的内网 IP。
// 一个公网 IP 只绑定一台实例，优先取该实例的第一个内网 IP。
const publicToPrivate = new Map<string, string>();
for (const instance of instances) {
  const privateIp = instance.privateIps[0];
  if (!privateIp) continue;
  for (const publicIp of instance.publicIps) {
    if (!publicToPrivate.has(publicIp)) publicToPrivate.set(publicIp, privateIp);
  }
}
export function privateIpForPublic(ip: string): string | undefined {
  return publicToPrivate.get(ip);
}
export const projects: (TenantResource & { id: string; name: string; code: string })[] = [...snapshot.projects];
for (const row of instances)
  if (!projects.some((p) => p.id === row.projectId && p.tenantId === row.tenantId))
    projects.push({
      id: row.projectId,
      name: row.projectId ? `未知项目（${row.projectId}）` : '未指定项目',
      code: '',
      tenantId: row.tenantId, tenantName: row.tenantName,
    });
export const projectName = (id: string, tenantId?: string) =>
  projects.find((p) => p.id === id && (!tenantId || p.tenantId === tenantId))?.name ?? `未知项目（${id}）`;
export const projectToken = (id: string, tenantId?: string) => JSON.stringify(tenantId ? [tenantId, id] : id);
export const tagKeys = [
  ...new Set(instances.flatMap((r) => Object.keys(r.tags))),
].sort((a, b) => (a === 'env' ? -1 : b === 'env' ? 1 : compareText(a, b)));
export const statusName = (status: string) =>
  ({
    Running: '运行中',
    Stopped: '已停止',
    Starting: '启动中',
    Stopping: '停止中',
  })[status] ?? status;
export type TagFilter = { key: string; value: string }; // JSON null means absent; JSON string preserves empty values.
export type Filters = {
  tenant?: string;
  query: string;
  project: string;
  status: string;
  publicIp: string;
  tags: TagFilter[];
};
export const emptyFilters: Filters = {
  tenant: '',
  query: '',
  project: '',
  status: '',
  publicIp: '',
  tags: [],
};
export const tagToken = (row: Instance, key: string) =>
  JSON.stringify(Object.hasOwn(row.tags, key) ? row.tags[key] : null);
export const tagLabel = (token: string) => {
  const value = JSON.parse(token);
  return value === null ? '未设置' : value === '' ? '空值' : String(value);
};
export const tagValues = (key: string) =>
  [...new Set(instances.map((r) => tagToken(r, key)))].sort();
export function filterInstances(rows: Instance[], filters: Filters) {
  const q = filters.query.trim().toLocaleLowerCase();
  return rows.filter(
    (r) =>
      (!filters.tenant || (r.tenantId || 'legacy-aliyun') === filters.tenant) &&
      (!filters.project || projectToken(r.projectId, r.tenantId) === filters.project || JSON.stringify(r.projectId) === filters.project) &&
      (!filters.status || r.status === filters.status) &&
      (!filters.publicIp ||
        r.publicIps.length > 0 === (filters.publicIp === 'yes')) &&
      filters.tags.every(
        (t) => !t.key || !t.value || tagToken(r, t.key) === t.value,
      ) &&
      (!q ||
        [
          r.name,
          r.id,
          projectName(r.projectId, r.tenantId), r.tenantName || '', r.accountId || '',
          r.os,
          r.instanceType,
          ...r.privateIps,
          ...r.publicIps,
          ...Object.entries(r.tags).flat(),
        ].some((v) => v.toLocaleLowerCase().includes(q))),
  );
}
export function summarize(rows: Instance[]) {
  return {
    count: rows.length,
    cpu: rows.reduce((n, r) => n + r.cpu, 0),
    memory: rows.reduce((n, r) => n + r.memoryGiB, 0),
    running: rows.filter((r) => r.status === 'Running').length,
    publicIp: rows.filter((r) => r.publicIps.length > 0).length,
  };
}
export type Group = {
  key: string;
  label: string;
  rows: Instance[];
  children: Group[];
};
export function groupInstances(
  rows: Instance[],
  keys: string[],
  depth = 0,
): Group[] {
  if (depth >= keys.length) return [];
  const key = keys[depth];
  const buckets = new Map<string, Instance[]>();
  for (const row of rows) {
    const value =
      key === 'project'
        ? projectToken(row.projectId, row.tenantId)
        : tagToken(row, key.slice(4));
    buckets.set(value, [...(buckets.get(value) ?? []), row]);
  }
  return [...buckets]
    .map(([value, items]) => ({
      key: value,
      label:
        key === 'project'
          ? projectName(items[0].projectId, items[0].tenantId)
          : `${key.slice(4)} · ${tagLabel(value)}`,
      rows: items,
      children: groupInstances(items, keys, depth + 1),
    }))
    .sort(
      (a, b) => b.rows.length - a.rows.length || compareText(a.label, b.label),
    );
}
export function filtersQuery(filters: Filters) {
  const p = new URLSearchParams();
  if (filters.tenant) p.set('tenant', filters.tenant);
  if (filters.query) p.set('q', filters.query);
  if (filters.project) p.set('project', filters.project);
  if (filters.status) p.set('status', filters.status);
  if (filters.publicIp) p.set('publicIp', filters.publicIp);
  for (const t of filters.tags)
    if (t.key && t.value) p.append('tag', JSON.stringify(t));
  return p.toString();
}
export function filtersFromQuery(search: string): Filters {
  const p = new URLSearchParams(search);
  const tags: TagFilter[] = [];
  for (const raw of p.getAll('tag')) {
    try {
      const t = JSON.parse(raw);
      if (tagKeys.includes(t.key) && tagValues(t.key).includes(t.value))
        tags.push(t);
    } catch {
      /* Ignore malformed links. */
    }
  }
  return {
    ...(p.get('tenant') ? { tenant: p.get('tenant')! } : {}),
    query: p.get('q') ?? '',
    project: p.get('project') ?? '',
    status: p.get('status') ?? '',
    publicIp: p.get('publicIp') ?? '',
    tags,
  };
}

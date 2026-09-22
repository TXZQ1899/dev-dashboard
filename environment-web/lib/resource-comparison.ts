import inventory from './snapshot.json';
import cloud from './ecs-snapshot.json';
import jump from './jumpserver-snapshot.json';
import repositories from './repositories.json';
import clbSnapshot from './clb-snapshot.json';
import eipSnapshot from './eip-snapshot.json';
import natSnapshot from './nat-snapshot.json';
import dnsSnapshot from './dns-snapshot.json';

type Deployment = {
  ip?: string | null;
  port?: string | null;
  repository?: string | null;
  lastPublishedAt?: string | null;
};
type Application = {
  id: string;
  name: string;
  port?: string;
  repository?: string;
  envs: Record<string, Deployment[]>;
};
type CloudInstance = {
  id: string;
  projectId: string;
  privateIps: string[];
  publicIps: string[];
};
type JumpGroup = {
  id: string;
  key: string;
  name: string;
  path?: string;
  org_id?: string;
  assetIds: string[];
};
export type ComparisonInput = {
  clbInstances?: { id: string; ip: string; groups?: { servers: { ip: string }[] }[] }[];
  eips?: { ip: string; id: string; name: string; owner: string; notes: { value: string }[] }[];
  natGateways?: { id: string; name: string; entries: { externalIp: string; internalIp: string; id: string }[] }[];
  dnsRecords?: { id: string; name: string; type: string; value: string; status: string }[];
  apps: Application[];
  instances: CloudInstance[];
  projects: { id: string; name: string }[];
  assets: { id: string; ip: string | null }[];
  jumpGroups: JumpGroup[];
  repos: { url: string; groupId: string }[];
  codeGroups: { id: string; name: string; description?: string }[];
};
export type Association = {
  key: string;
  appId: string;
  name: string;
  env: string;
  port: string;
  git: string;
  gitLink: string;
  codeGroup: string;
  pushIn: string;
};
export type ResourceRow = {
  ip: string;
  resourceType: '阿里云' | '阿里云 CLB' | '阿里云 EIP' | 'NAT 映射' | 'CLB 后端' | 'DNS 记录' | '公司机房' | '';
  references: { label: string; href: string }[];
  searchTerms: string[];
  clbInstanceIds: string[];
  classificationConflict: boolean;
  cloudInstanceIds: string[];
  projectNames: string[];
  jumpGroups: string[];
  associations: Association[];
  sources: { devops: boolean; aliyun: boolean; jumpserver: boolean; eip: boolean; nat: boolean; dns: boolean; clbBackend: boolean };
  otherManagedIps: string[];
};
const compare = (a: string, b: string) => (a === b ? 0 : a < b ? -1 : 1);
const unique = (values: string[]) =>
  [...new Set(values.filter(Boolean))].sort(compare);
export function normalizeIp(raw: string | null | undefined) {
  const value = (raw || '').trim();
  if (
    /^\d{1,3}(\.\d{1,3}){3}$/.test(value) &&
    value.split('.').every((p) => Number(p) <= 255)
  )
    return value.split('.').map(Number).join('.');
  return value; // Preserve unusual source values for review rather than silently discard them.
}
export function canonicalGit(raw: string) {
  try {
    let value = raw.trim();
    if (!value.includes('://'))
      value = value.replace(/^(?:[^/@:]+@)?([^/:]+):(.+)$/, 'ssh://$1/$2');
    const url = new URL(value);
    if (
      !['http:', 'https:', 'ssh:', 'git:'].includes(url.protocol) ||
      url.password ||
      url.search ||
      url.hash
    )
      return '';
    if (
      url.port &&
      !(
        (url.protocol === 'ssh:' && url.port === '22') ||
        (url.protocol === 'git:' && url.port === '9418')
      )
    )
      return '';
    const path = decodeURIComponent(url.pathname)
      .replace(/^\/+|\/+$/g, '')
      .replace(/\.git$/, '');
    if (
      path.split('/').length < 2 ||
      path.split('/').some((p) => !p || p === '.' || p === '..') ||
      /[\x00-\x1f]/.test(path)
    )
      return '';
    return `https://${url.hostname.toLowerCase()}/${path.split('/').map(encodeURIComponent).join('/')}.git`;
  } catch {
    return '';
  }
}
export function buildComparison(input: ComparisonInput): ResourceRow[] {
  const rows = new Map<string, ResourceRow>();
  const ensure = (raw: string | null | undefined) => {
    const ip = normalizeIp(raw);
    if (!ip) return undefined;
    if (!rows.has(ip))
      rows.set(ip, {
        ip,
        resourceType: '',
        classificationConflict: false,
        cloudInstanceIds: [],
        clbInstanceIds: [],
        projectNames: [],
        jumpGroups: [],
        associations: [],
        references: [],
        searchTerms: [],
        sources: { devops: false, aliyun: false, jumpserver: false, eip: false, nat: false, dns: false, clbBackend: false },
        otherManagedIps: [],
      });
    return rows.get(ip)!;
  };
  const projects = new Map(
    input.projects.filter((p) => p.id).map((p) => [p.id, p.name]),
  );
  const codeGroups = new Map(input.codeGroups.map((g) => [g.id, g.name]));
  const repoGroups = new Map(
    input.repos
      .map(
        (r) => [canonicalGit(r.url), codeGroups.get(r.groupId) || ''] as const,
      )
      .filter(([url]) => !!url),
  );
  for (const asset of input.assets) {
    const row = ensure(asset.ip);
    if (!row) continue;
    row.sources.jumpserver = true;
    const groups = input.jumpGroups.filter((g) =>
      g.assetIds.includes(asset.id),
    );
    // Remove ancestors per asset, not per IP: two records sharing an IP may have different memberships.
    row.jumpGroups.push(
      ...groups
        .filter(
          (g) =>
            !groups.some(
              (child) =>
                child.org_id === g.org_id && child.key.startsWith(g.key + ':'),
            ),
        )
        .map((g) => g.path || g.name),
    );
  }
  for (const instance of input.instances) {
    const ips = unique(
      [...instance.privateIps, ...instance.publicIps].map(normalizeIp),
    );
    for (const ip of ips) {
      const row = ensure(ip)!;
      row.sources.aliyun = true;
      row.cloudInstanceIds.push(instance.id);
      row.projectNames.push(projects.get(instance.projectId) || '');
      row.otherManagedIps.push(
        ...ips.filter(
          (other) => other !== ip && rows.get(other)?.sources.jumpserver,
        ),
      );
    }
  }
  for (const instance of input.clbInstances ?? []) {
    const row = ensure(instance.ip);
    if (row) {
      row.sources.aliyun = true;
      row.clbInstanceIds.push(instance.id);
    }
    for (const group of instance.groups ?? []) for (const server of group.servers) {
      const backend = ensure(server.ip);
      if (!backend) continue;
      backend.sources.clbBackend = true;
      backend.references.push({ label: 'CLB 后端', href: `/aliyun/clb?view=instances&q=${encodeURIComponent(server.ip)}` });
      backend.searchTerms.push(instance.id);
    }
  }
  const textIps = (text: string) => [...text.matchAll(/(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])/g)].map(m => m[0]).filter(ip => ip.split('.').every(p => Number(p) <= 255));
  for (const record of input.eips ?? []) {
    for (const ip of unique([record.ip, ...record.notes.flatMap(note => textIps(note.value))])) {
      const row = ensure(ip);
      if (!row) continue;
      row.sources.eip = true;
      // Only the actual EIP field establishes a cloud resource; notes are references.
      if (normalizeIp(ip) === normalizeIp(record.ip)) {
        row.sources.aliyun = true;
        row.resourceType = '阿里云 EIP';
      }
      row.references.push({label: ip === record.ip ? 'EIP' : 'EIP 备注', href: `/aliyun/clb?view=eip&q=${encodeURIComponent(record.id)}`});
      row.searchTerms.push(record.id, record.name, record.owner);
    }
  }
  for (const gateway of input.natGateways ?? []) for (const entry of gateway.entries) {
    for (const ip of unique([entry.externalIp, entry.internalIp])) {
      const row = ensure(ip);
      if (!row) continue;
      row.sources.nat = true;
      row.references.push({label:'NAT 映射',href:`/aliyun/clb?view=nat&q=${encodeURIComponent(ip)}`});
      row.searchTerms.push(gateway.id, gateway.name, entry.id);
    }
  }
  for (const record of input.dnsRecords ?? []) {
    const ips = record.type === 'AAAA' ? [record.value.trim()] : textIps(record.value);
    for (const ip of unique(ips)) {
      const row = ensure(ip);
      if (!row) continue;
      // Paused records remain searchable, without claiming they currently route traffic.
      row.sources.dns = true;
      row.references.push({label:`DNS · ${record.name}（${record.status}）`,href:`/aliyun/clb?view=dns&q=${encodeURIComponent(record.id)}`});
      row.searchTerms.push(record.name, record.type, record.status);
    }
  }
  for (const app of input.apps)
    for (const [env, deployments] of Object.entries(app.envs))
      for (const deployment of deployments) {
        const row = ensure(deployment.ip);
        if (!row) continue;
        row.sources.devops = true;
        const port = deployment.port?.trim() || app.port?.trim() || '';
        const git =
          deployment.repository?.trim() || app.repository?.trim() || '';
        const link = canonicalGit(git);
        const key = JSON.stringify([app.id, env, port, link || git]);
        const stamp = deployment.lastPublishedAt?.trim() || '';
        const existing = row.associations.find((a) => a.key === key);
        if (existing) {
          if (stamp > existing.pushIn) existing.pushIn = stamp;
        } else
          row.associations.push({
            key,
            appId: app.id,
            name: app.name,
            env,
            port,
            git,
            gitLink: link,
            codeGroup: link ? repoGroups.get(link) || '' : '',
            pushIn: stamp,
          });
      }
  for (const row of rows.values()) {
    const onPrem =
      /^10\.58\.\d{1,3}\.\d{1,3}$/.test(row.ip) &&
      row.ip.split('.').every((p) => Number(p) <= 255);
    row.classificationConflict = (onPrem && row.sources.aliyun) || (row.clbInstanceIds.length > 0 && row.cloudInstanceIds.length > 0);
    row.cloudInstanceIds = unique(row.cloudInstanceIds);
    row.clbInstanceIds = unique(row.clbInstanceIds);
    row.resourceType = row.clbInstanceIds.length ? '阿里云 CLB' : row.cloudInstanceIds.length ? '阿里云' : row.resourceType || (onPrem ? '公司机房' : row.sources.nat ? 'NAT 映射' : row.sources.clbBackend ? 'CLB 后端' : row.sources.dns ? 'DNS 记录' : '');
    row.references = [...new Map(row.references.map(ref => [ref.href, ref])).values()];
    row.searchTerms = unique(row.searchTerms);
    row.projectNames = unique(row.projectNames);
    row.jumpGroups = unique(row.jumpGroups);
    row.otherManagedIps = unique(row.otherManagedIps);
    row.associations.sort(
      (a, b) =>
        compare(a.name, b.name) ||
        compare(a.appId, b.appId) ||
        compare(a.env, b.env) ||
        compare(a.port, b.port) ||
        compare(a.git, b.git),
    );
  }
  return [...rows.values()].sort((a, b) => {
    const aa = a.ip.split('.').map(Number),
      bb = b.ip.split('.').map(Number);
    if (
      aa.length === 4 &&
      bb.length === 4 &&
      [...aa, ...bb].every(Number.isFinite)
    ) {
      for (let i = 0; i < 4; i++) if (aa[i] !== bb[i]) return aa[i] - bb[i];
    }
    return compare(a.ip, b.ip);
  });
}
export const comparisonRows = buildComparison({
  clbInstances: clbSnapshot.available ? clbSnapshot.instances : [],
  eips: eipSnapshot.records,
  natGateways: natSnapshot.available ? natSnapshot.gateways : [],
  dnsRecords: dnsSnapshot.records,
  apps: inventory.apps,
  instances: cloud.instances,
  projects: cloud.projects,
  assets: jump.assets,
  jumpGroups: jump.groups,
  repos: repositories.repos,
  codeGroups: repositories.groups,
});
export const comparisonDates = {
  clb: clbSnapshot.collectedAt,
  nat: natSnapshot.collectedAt,
  eip: eipSnapshot.snapshotDate,
  devops: inventory.collectedAt,
  aliyun: cloud.fetchedAt,
  jumpserver: jump.collectedAt,
};
export function summarizeComparison(rows: ResourceRow[]) {
  const scope = rows.filter((r) => r.sources.devops || r.sources.aliyun);
  return {
    total: rows.length,
    devops: rows.filter((r) => r.sources.devops).length,
    aliyun: rows.filter((r) => r.sources.aliyun).length,
    jumpserver: rows.filter((r) => r.sources.jumpserver).length,
    scope: scope.length,
    missing: scope.filter((r) => !r.sources.jumpserver).length,
    devopsMissing: rows.filter((r) => r.sources.devops && !r.sources.jumpserver)
      .length,
    aliyunMissing: rows.filter((r) => r.sources.aliyun && !r.sources.jumpserver)
      .length,
    onPrem: rows.filter((r) => r.resourceType === '公司机房').length,
    unknown: rows.filter((r) => !r.resourceType).length,
    alternate: scope.filter(
      (r) => !r.sources.jumpserver && r.otherManagedIps.length,
    ).length,
  };
}
export type ComparisonFilters = {
  query: string;
  coverage: string;
  resource: string;
  source: string;
  project: string;
  group: string;
  pushIn: string;
  pushInSort: string;
};
export const emptyComparisonFilters: ComparisonFilters = {
  query: '',
  coverage: '',
  resource: '',
  source: '',
  project: '',
  group: '',
  pushIn: '',
  pushInSort: '',
};
export function filterComparison(
  rows: ResourceRow[],
  filters: ComparisonFilters,
) {
  const q = filters.query.trim().toLowerCase();
  const matchingRows = rows.flatMap((row) => {
    if (!filters.pushIn) return [row];
    const associations = row.associations.filter((a) =>
      filters.pushIn === 'present' ? Boolean(a.pushIn.trim()) : !a.pushIn.trim(),
    );
    if (!associations.length && (row.associations.length || filters.pushIn === 'present')) return [];
    return [{ ...row, associations }];
  });
  const filtered = matchingRows.filter(
    (r) =>
      (!filters.coverage ||
        (filters.coverage === 'missing'
          ? !r.sources.jumpserver
          : r.sources.jumpserver)) &&
      (!filters.resource ||
        (filters.resource === 'unknown'
          ? !r.resourceType
          : r.resourceType === filters.resource)) &&
      (!filters.source ||
        r.sources[filters.source as keyof ResourceRow['sources']]) &&
      (!filters.project || r.projectNames.includes(filters.project)) &&
      (!filters.group || r.jumpGroups.includes(filters.group)) &&
      (!q ||
        [
          r.ip,
          ...r.cloudInstanceIds,
          ...r.clbInstanceIds,
          ...r.searchTerms,
          ...r.projectNames,
          ...r.jumpGroups,
          ...r.associations.flatMap((a) => [
            a.name,
            a.port,
            a.git,
            a.codeGroup,
            a.env,
          ]),
        ].some((value) => value.toLowerCase().includes(q))),
  );
  if (!filters.pushInSort) return filtered;
  const compareTime = (a: string, b: string) => {
    if (!a.trim()) return b.trim() ? 1 : 0;
    if (!b.trim()) return -1;
    return compare(a, b) * (filters.pushInSort === 'asc' ? 1 : -1);
  };
  const latest = (row: ResourceRow) =>
    row.associations.reduce((time, a) => a.pushIn > time ? a.pushIn : time, '');
  return filtered.map((row) => ({
    ...row,
    associations: [...row.associations].sort((a, b) => compareTime(a.pushIn, b.pushIn)),
  })).sort((a, b) => compareTime(latest(a), latest(b)));
}

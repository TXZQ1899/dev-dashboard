import type { Instance } from './ecs';
import { resourceIdentity } from './tenants';

export type Business = 'group' | 'travel' | 'other' | 'unknown' | `project:${string}`;
export type BusinessEvidence = { ip: string; environment: string; domainName: string | null; unitType: string; appId?: string | null };
export type BusinessResult = { matchesCatalog: boolean; snapshotVersion: string; ecsFetchedAt: string | null; evidence: BusinessEvidence[] };
export type ServerBusiness = { category: Business; categories: Business[]; ambiguous: boolean };
export const businessLabels: Record<'group' | 'travel' | 'other' | 'unknown', string> = {
  group: '集团', travel: '商旅', other: '其它', unknown: '未识别',
};
export const businessOrder: Business[] = ['group', 'travel', 'other', 'unknown'];
export const businessLabel = (key: Business) => key.startsWith('project:') ? key.slice(8) : businessLabels[key as keyof typeof businessLabels];
export function businessCategories(rows: Instance[], business: Map<string, ServerBusiness>): Business[] {
  const projects = [...new Set(rows.map(row => business.get(resourceIdentity(row))?.category).filter((key): key is Business => !!key && key.startsWith('project:')))];
  projects.sort((a, b) => a < b ? -1 : a > b ? 1 : 0);
  return [...businessOrder.slice(0, -1), ...projects, 'unknown'];
}

// A private or public IP must identify only one ECS instance across the complete snapshot.
export function classifyServers(instances: Instance[], evidence: BusinessEvidence[], resolveProject: (row: Instance) => string | undefined = () => undefined): Map<string, ServerBusiness> {
  const byIp = new Map<string, Set<string>>();
  for (const row of instances) {
    const id = resourceIdentity(row);
    for (const ip of new Set([...row.privateIps, ...row.publicIps])) {
      if (!byIp.has(ip)) byIp.set(ip, new Set());
      byIp.get(ip)!.add(id);
    }
  }
  const categories = new Map<string, Set<Business>>();
  for (const row of instances) {
    if (row.name.includes('商旅')) categories.set(resourceIdentity(row), new Set(['travel']));
  }
  const ambiguous = new Set<string>();
  for (const item of evidence) {
    const ids = byIp.get(item.ip);
    if (!ids) continue;
    if (ids.size !== 1) {
      for (const id of ids) ambiguous.add(id);
      continue;
    }
    const id = [...ids][0];
    const domain = item.domainName;
    const category: Business = !domain || domain === '未分类' ? 'other' : domain === '商旅' ? 'travel' : 'group';
    if (!categories.has(id)) categories.set(id, new Set());
    categories.get(id)!.add(category);
  }
  return new Map(instances.map(row => {
    const id = resourceIdentity(row);
    const found = businessOrder.filter(key => categories.get(id)?.has(key));
    const project = resolveProject(row)?.trim();
    if (!found.length && project && project !== '未指定项目' && project !== '默认资源组') {
      found.push(project === '商旅' ? 'travel' : project === '集团' ? 'group' : project === '其它' ? 'other' : `project:${project}`);
    }
    return [id, { category: found[0] ?? 'unknown', categories: found, ambiguous: ambiguous.has(id) }];
  }));
}

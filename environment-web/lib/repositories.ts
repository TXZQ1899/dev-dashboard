import data from './repositories.json';
export type Repository = {
  id: string;
  name: string;
  url: string;
  path?: string;
  groupId: string;
  access: string;
  reason: string;
  match: string;
  apps: { id: string; name: string; branch: string }[];
  inCodeup?: boolean;
  inDevops?: boolean;
  difference?: string;
  branches?: number | null;
  mergeRequests?: number | null;
  commits?: number | null;
  reportedCommits?: number | null;
  commitHistoryComplete?: boolean;
  commitDailyCounts?: Record<string, number>;
  updatedAt?: string;
  lastCommittedAt?: string;
  commitTimeStatus?: string;
  lastCommitBranch?: string;
  groupName?: string;
  source?: 'codeup' | 'local_gitlab';
  possibleCodeupMatches?: { id: string; name: string; url: string; groupName: string; path: string }[];
};
type Snapshot = {
  accessDate: string;
  source: string;
  scope?: string;
  complete?: boolean;
  snapshotDate?: string;
  groups: {
    id: string;
    name: string;
    description: string;
    path: string;
    total: number;
    listed?: boolean;
  }[];
  repos: Repository[];
  invalidReferences?: { appId: string; appName: string; reason: string }[];
};
// This internal GitLab uses HTTP; apply on read so synced and historical snapshots agree.
export function normalizeRepositoryUrl(raw: string): string {
  return raw.replace(/^https:\/\/gitlab\.dev\.thomascook\.com\.cn(?=[:/?#]|$)/i, 'http://gitlab.dev.thomascook.com.cn');
}
export const repositorySnapshot: Snapshot = {
  ...data,
  repos: data.repos.map((repo) => ({ ...repo, url: normalizeRepositoryUrl(repo.url) })),
};
export const isDailySnapshot = repositorySnapshot.scope === 'mine';
const compare = (a: string, b: string) => (a === b ? 0 : a < b ? -1 : 1);
export const differenceLabel = (repo: Repository) =>
  repo.difference === 'codeup_only'
    ? 'DevOps 未使用'
    : repo.difference === 'devops_only'
      ? '仅 DevOps 清单'
      : repo.difference === 'both'
        ? '两边均有'
        : '历史记录 · 未对比';
export const groups = repositorySnapshot.groups.map((group) => {
  const repos = repositorySnapshot.repos.filter(
    (repo) => repo.groupId === group.id,
  );
  const linked = repos.filter(
    (repo) => repo.apps.length > 0 && (!isDailySnapshot || repo.inCodeup),
  ).length;
  const accessible = repos.filter((repo) => repo.access === '是').length;
  const denied = repos.filter((repo) => repo.access === '否').length;
  const unused = repos.filter(
    (repo) => repo.difference === 'codeup_only',
  ).length;
  const devopsOnly = repos.filter(
    (repo) => repo.difference === 'devops_only',
  ).length;
  return {
    ...group,
    repos,
    linked,
    accessible,
    denied,
    unused,
    devopsOnly,
    gap: group.total - linked,
    unknown: Math.max(0, group.total - accessible - denied),
  };
});
export type Group = (typeof groups)[number];
export const unmatched = repositorySnapshot.repos.filter(
  (repo) => !repo.groupId,
);
export const summary = {
  total: groups.reduce((n, g) => n + g.total, 0),
  linked: groups.reduce((n, g) => n + g.linked, 0),
  accessible: groups.reduce((n, g) => n + g.accessible, 0),
  denied: groups.reduce((n, g) => n + g.denied, 0),
  accessibleGroups: groups.filter((g) => g.accessible > 0).length,
  conflicts: groups.filter((g) => g.gap < 0).length,
  unused: repositorySnapshot.repos.filter((r) => r.difference === 'codeup_only')
    .length,
  devopsOnly: repositorySnapshot.repos.filter(
    (r) => r.difference === 'devops_only',
  ).length,
};
// 解析 Git 地址，返回小写的完整路径（group/sub/name）与仓库名（最后一段，去掉 .git）。
// 兼容 HTTPS、SSH（git@host:path）、ssh:// 以及无协议地址；无法解析时返回空串。
function splitGitAddress(raw: string | null | undefined): { path: string; name: string } {
  const empty = { path: '', name: '' };
  if (!raw || typeof raw !== 'string') return empty;
  const value = raw.trim();
  if (!value) return empty;
  let pathname = value;
  const scp = value.match(/^[^/@:]+@([^:[\]]+):(.+)$/);
  if (scp) {
    pathname = scp[2];
  } else {
    try {
      pathname = new URL(value).pathname;
    } catch {
      const slash = value.indexOf('/');
      pathname = slash >= 0 ? value.slice(slash + 1) : '';
    }
  }
  const path = pathname.replace(/\.git\/?$/i, '').replace(/^\/+/, '').toLowerCase();
  const segments = path.split('/').filter(Boolean);
  return { path, name: segments[segments.length - 1] || '' };
}
export function repositoryNameFromUrl(raw: string | null | undefined): string {
  return splitGitAddress(raw).name;
}
// 应用 ID -> 该应用在 DevOps 清单中引用过的代码库。采集端按主机名 + 完整路径精确比对，
// 同时包含应用默认地址与各环境的覆盖地址（如 TEST 单独指向其他仓库）；
// possibleCodeupMatches 仅为改名提示，不属于已确认关联，不进入此索引。
export const appRepositoryIndex: Map<string, Repository[]> = (() => {
  const index = new Map<string, Repository[]>();
  for (const repo of repositorySnapshot.repos) {
    for (const ref of repo.apps) {
      const list = index.get(ref.id);
      if (list) list.push(repo);
      else index.set(ref.id, [repo]);
    }
  }
  return index;
})();
// 按 Git 仓库名/路径筛选应用：优先使用代码库清单中按完整地址确认的关联，
// 对未在清单中建立关联的应用再回退到其自身提供的仓库地址（含各环境行）。
// 关键字大小写不敏感；为空时不过滤。不按同名猜测未确认的关联。
export function appRepositoryNameMatches(
  appId: string,
  repositoryUrls: (string | null | undefined)[],
  query: string,
): boolean {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  const haystack: string[] = [];
  for (const repo of appRepositoryIndex.get(appId) || []) {
    haystack.push(repo.name, repo.path || '');
  }
  for (const raw of repositoryUrls) {
    const parts = splitGitAddress(raw);
    haystack.push(parts.name, parts.path);
  }
  return haystack.some((value) => !!value && value.includes(needle));
}
export function filterGroups(query: string, filter: string, order: string, period = '', pipeline = '') {
  const q = query.trim().toLowerCase();
  return groups
    .map(g => ({...g, repos:g.repos.filter(r => matchesRepository(r,period,pipeline) &&
      (filter !== 'unused' || r.difference === 'codeup_only') &&
      (filter !== 'devops_only' || r.difference === 'devops_only'))}))
    .filter(
      (g) =>
        ((!period && !pipeline) || g.repos.length > 0) &&
        (!q ||
          [
            g.name,
            g.description,
            g.path,
            ...g.repos.flatMap((r) => [
              r.name,
              r.url,
              ...r.apps.map((a) => a.name),
            ]),
          ]
            .join(' ')
            .toLowerCase()
            .includes(q)) &&
        (filter === 'all' ||
          (filter === 'accessible'
            ? g.accessible > 0
            : filter === 'gap'
              ? g.gap > 0
              : filter === 'unused'
                ? g.unused > 0
                : filter === 'devops_only'
                  ? g.devopsOnly > 0
                  : g.gap < 0)),
    )
    .sort((a, b) =>
      order === 'name'
        ? compare(a.name, b.name)
        : order === 'gap'
          ? b.gap - a.gap
          : b.total - a.total,
    );
}
export function formatRepoTime(value?: string) {
  if (!value) return '未采集';
  return new Date(value).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai',hour12:false});
}
export function matchesRepository(repo: Repository, period: string, pipeline: string, reference = repositorySnapshot.accessDate) {
  const linked = repo.apps.length > 0;
  if ((pipeline === 'linked' && !linked) || (pipeline === 'unlinked' && linked)) return false;
  if (!period) return true;
  if (repo.commitHistoryComplete && period !== 'unknown' && period !== 'older') return (periodCommitCount(repo,period,reference) ?? 0) > 0;
  const stamp = Date.parse(repo.lastCommittedAt || ''), now = Date.parse(reference);
  if (period === 'unknown') return !Number.isFinite(stamp);
  if (!Number.isFinite(stamp) || !Number.isFinite(now) || stamp > now) return false;
  const monthsAgo = (months: number) => {
    const date = new Date(now), day = date.getUTCDate();
    date.setUTCDate(1); date.setUTCMonth(date.getUTCMonth()-months);
    const last = new Date(Date.UTC(date.getUTCFullYear(),date.getUTCMonth()+1,0)).getUTCDate();
    date.setUTCDate(Math.min(day,last)); return date.getTime();
  };
  if (period === 'week') return stamp >= now-7*86400000;
  if (period === 'month') return stamp >= monthsAgo(1);
  if (period === 'quarter') return stamp >= monthsAgo(3);
  if (period === 'half') return stamp >= monthsAgo(6);
  if (period === 'lastYear') return stamp >= monthsAgo(12);
  if (period === 'year') return stamp < monthsAgo(6) && stamp >= monthsAgo(12);
  if (period === 'twoYears') return stamp < monthsAgo(12) && stamp >= monthsAgo(24);
  if (period === 'older') return stamp < monthsAgo(24);
  return false;
}

export const periodLabels: Record<string,string> = {'':'全部时间',week:'最近 1 星期',month:'最近 1 个月',quarter:'最近 3 个月',half:'最近半年',lastYear:'最近 1 年',year:'半年至 1 年前',twoYears:'1 至 2 年前',older:'2 年前',unknown:'无提交或未采集'};
export function periodCommitCount(repo: Repository, period: string, reference = repositorySnapshot.accessDate): number | null {
  if (!repo.commitHistoryComplete || !repo.commitDailyCounts || period === 'unknown') return null;
  const stamp = Date.parse(reference);
  if (!Number.isFinite(stamp)) return null;
  const end = new Date(stamp+8*3600000).toISOString().slice(0,10);
  const month = (n:number) => {const d=new Date(end+'T00:00:00Z'),day=d.getUTCDate();d.setUTCDate(1);d.setUTCMonth(d.getUTCMonth()-n);d.setUTCDate(Math.min(day,new Date(Date.UTC(d.getUTCFullYear(),d.getUTCMonth()+1,0)).getUTCDate()));return d.toISOString().slice(0,10);};
  const week=new Date(Date.parse(end+'T00:00:00Z')-6*86400000).toISOString().slice(0,10);
  const bounds:Record<string,[string,string]>={'':['',end],week:[week,end],month:[month(1),end],quarter:[month(3),end],half:[month(6),end],lastYear:[month(12),end],year:[month(12),month(6)],twoYears:[month(24),month(12)],older:['',month(24)]};
  if (!bounds[period]) return null;
  const [from,to]=bounds[period],exclusive=['year','twoYears','older'].includes(period);
  return Object.entries(repo.commitDailyCounts).reduce((sum,[day,n])=>sum+(day>=from&&(exclusive?day<to:day<=to)?n:0),0);
}
export function repositoryCsv(repos: Repository[], period: string, conditions: string, reference=repositorySnapshot.accessDate) {
  const cell=(v:unknown)=>{let text=String(v??'');if (/^[\s]*[=+@-]/.test(text)||/^[\t\r\n]/.test(text)) text="'"+text;return '"'+text.replace(/"/g,'""')+'"';};
  const header=['代码组','仓库名称','Git 地址','是否关联 DevOps 流水线','关联应用','对比结果','分支数','合并请求数','全部提交数','时间范围','范围内提交数','最近代码提交时间','统计基准时间','筛选条件'];
  const rows=repos.map(r=>[r.groupName||repositorySnapshot.groups.find(g=>g.id===r.groupId)?.name||'',r.name,r.url,r.apps.length?'是':'否',r.apps.map(a=>a.name).join('；'),differenceLabel(r),r.branches,r.mergeRequests,r.commits,periodLabels[period]??period,periodCommitCount(r,period,reference),r.lastCommittedAt||'',reference,conditions]);
  return '\uFEFF'+[header,...rows].map(r=>r.map(cell).join(',')).join('\r\n')+'\r\n';
}

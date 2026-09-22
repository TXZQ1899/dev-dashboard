import type { Repository } from './repositories';
import { envs, labels, latestSuccessfulPushIn, uniqueDeployments, type App } from './inventory';

export function gitKey(raw: string): string {
  try {
    const value = raw.trim().replace(/\\_/g, '_');
    const url = new URL(value.includes('://') ? value : value.replace(/^git@([^:]+):(.+)$/, 'ssh://git@$1/$2'));
    if (!['http:', 'https:', 'ssh:', 'git:'].includes(url.protocol) || url.password || url.search || url.hash) return '';
    const path = url.pathname.replace(/\/+$/, '').replace(/\.git$/, '');
    if (path.split('/').filter(Boolean).length < 2) return '';
    return url.hostname.toLowerCase() + (url.port && url.port !== '22' ? ':' + url.port : '') + path;
  } catch { return ''; }
}
export function parseGitList(text: string) {
  const inputs: { input: string; key: string }[] = [];
  const invalid: string[] = [];
  const seen = new Set<string>();
  for (const line of text.split(/\r?\n/)) {
    const clean = line.trim();
    if (!clean || /^[|\s:-]+$/.test(clean)) continue;
    const links = [...clean.matchAll(/\[[^\]]*\]\(([^)]+)\)/g)].map(m => m[1]);
    const tokens = links.length ? links : clean.split(/[\s|,;，；]+/).filter(Boolean);
    for (const input of tokens) {
      const key = gitKey(input.replace(/^<|>$/g, ''));
      if (!key) { invalid.push(input); continue; }
      if (!seen.has(key)) { seen.add(key); inputs.push({ input, key }); }
    }
  }
  return { inputs, invalid: [...new Set(invalid)] };
}
export function matchGitList(text: string, repos: Repository[]) {
  const parsed = parseGitList(text);
  return { ...parsed, results: parsed.inputs.map(input => ({ ...input, repos: repos.filter(repo => gitKey(repo.url) === input.key) })) };
}
export function batchDetailCsv(batch: ReturnType<typeof matchGitList>, apps: App[], groups: { id: string; name: string }[], repositoryDate: string, inventoryDate: string) {
  const header = ['输入 Git 地址','匹配状态','仓库 ID','代码组','仓库名称','Git 地址','数据来源','访问权限','权限说明','对比结果','分支数','合并请求数','提交数','提交历史完整','最近代码提交时间','应用 ID','应用名称','应用 HTTP','应用默认端口','仓库关联分支','应用最新成功 Push In','环境','环境最新成功 Push In','IP','端口','部署分支','部署 ID','配置 ID','采集状态','采集错误','部署最新成功 Push In','发布状态','Push In 步骤明细','代码库快照时间','应用快照时间'];
  type Cell = string | number | null | undefined;
  const rows: Cell[][] = [];
  const add = (values: Cell[]) => { const padded = [...values]; while (padded.length < header.length - 2) padded.push(''); rows.push([...padded, repositoryDate, inventoryDate]); };
  for (const result of batch.results) {
    if (!result.repos.length) { add([result.input, '未匹配代码库']); continue; }
    for (const repo of result.repos) {
      const base = [result.input, result.repos.length > 1 ? '同地址多条仓库记录' : '已匹配', repo.id, repo.groupName || groups.find(g => g.id === repo.groupId)?.name || '', repo.name, repo.url, repo.source || '', repo.access, repo.reason, repo.difference || '', repo.branches, repo.mergeRequests, repo.commits, repo.commitHistoryComplete == null ? '未采集' : repo.commitHistoryComplete ? '是' : '否', repo.lastCommittedAt || ''];
      if (!repo.apps.length) { add([...base, '', '无关联应用']); continue; }
      for (const linked of new Map(repo.apps.map(app => [app.id, app])).values()) {
        const app = apps.find(app => app.id === linked.id);
        if (!app) { add([...base, linked.id, linked.name, '', '', linked.branch, '', '', '', '', '', '', '', '', '应用详情未采集']); continue; }
        for (const env of envs) {
          const deployments = uniqueDeployments(app.envs[env] || []);
          for (const deployment of deployments.length ? deployments : [null]) {
            const single = deployment ? { ...app, envs: { TEST: [], SIMULATION: [], PRODUCT: [], [env]: [deployment] } } : null;
            add([...base, app.id, app.name, app.http, app.port, linked.branch, latestSuccessfulPushIn(app), labels[env], latestSuccessfulPushIn(app, env), deployment?.ip, deployment?.port || app.port, deployment?.branch, deployment?.deploy, deployment?.config, deployment?.status || '无环境记录', deployment?.error, single ? latestSuccessfulPushIn(single) : '', deployment?.publishStatus, deployment?.pushIn ? JSON.stringify(deployment.pushIn) : '']);
          }
        }
      }
    }
  }
  for (const invalid of batch.invalid) add([invalid, '无效 Git 地址']);
  const cell = (value: Cell) => { let text = String(value ?? ''); if (/^[\s]*[=+@-]/.test(text)) text = "'" + text; return '"' + text.replace(/"/g, '""') + '"'; };
  return '\uFEFF' + [header, ...rows].map(row => row.map(cell).join(',')).join('\r\n') + '\r\n';
}

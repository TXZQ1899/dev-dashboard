import type { Listener } from './clb';
import type { Asset, Route } from './server-processes';

const normalize = (value: string) => value.trim().toLowerCase().replace(/\.$/, '');
function domainMatches(pattern: string, domain: string) {
  const p = normalize(pattern), d = normalize(domain);
  return p === d || (p.startsWith('*.') && d.endsWith(p.slice(1)) && d.length > p.length - 1);
}

/** DNS does not specify request port or URI, so retain all domain-matching rules and possible default fallback. */
export function dnsListenerRoutes(listener: Listener, domain: string) {
  const rules = listener.rules.filter(rule => !rule.domain || rule.domain.split(',').some(part => domainMatches(part, domain)));
  return { rules, defaultPossible: !rules.some(rule => !rule.path) };
}

function matchesPort(route: Route, port: number) {
  return route.listen.some(value => {
    const match = value.trim().match(/^(?:\[.*\]:|[^\s:]+:)?(\d+)(?:\s|$)/);
    return !!match && Number(match[1]) === port;
  });
}

export function nginxRoutesForBackend(assets: Asset[], ip: string, port: number | null, domain: string) {
  if (!ip || port == null) return { status: 'unknown' as const, routes: [] as Route[], assetId: '', version: '' };
  const matches = assets.filter(asset => asset.ip === ip);
  if (matches.length !== 1) return { status: 'unknown' as const, routes: [] as Route[], assetId: '', version: '' };
  const inspection = matches[0].inspection;
  if (!inspection) return { status: 'unknown' as const, routes: [] as Route[], assetId: '', version: '' };
  const routes = (inspection.nginxRoutes || []).filter(route => matchesPort(route, port) && route.domains.some(name => domainMatches(name, domain)));
  return { status: inspection.nginxStatus, routes, assetId: matches[0].id, version: inspection.configurationVersion || '' };
}

export type ArchivedConfig = { path: string; instance: string; content: string };
export function nginxServerNames(files: ArchivedConfig[], routes: Route[], domain: string) {
  const instances = new Set(routes.map(route => route.instance).filter(Boolean));
  if (instances.size !== 1) return [];
  const instance = [...instances][0];
  const sourcePaths = new Set(routes.map(route => route.file).filter(Boolean));
  return files.filter(file => file.instance === instance && sourcePaths.has(file.path)).flatMap(file => {
    const content = file.content.split('\n').map(line => line.replace(/\s+#.*$/, '')).join('\n');
    return [...content.matchAll(/(?:^|[;{}\n])\s*server_name\s+([^;{}]+);/g)]
      .map(match => match[1].trim().split(/\s+/).map(name => name.replace(/^['"]|['"]$/g, '')))
      .filter(names => names.some(name => domainMatches(name, domain)))
      .map(names => ({ path: file.path, names }));
  });
}
/** File-level references, not proof every include belongs to a particular server block. */
export function nginxFileIncludes(files: ArchivedConfig[], routes: Route[]) {
  const instances = new Set(routes.map(route => route.instance).filter(Boolean));
  const sourcePaths = [...new Set(routes.map(route => route.file).filter((p): p is string => !!p))].sort();
  if (instances.size !== 1) return { sourcePaths, edges: [] as { from: string; to: string }[], warnings: ['Nginx 实例不唯一，无法确定 include 引用。'] };
  const instance = [...instances][0];
  const scoped = files.filter(file => file.instance === instance);
  const roots = scoped.filter(file => /\/nginx\.conf$/.test(file.path));
  const prefix = roots.length === 1 ? roots[0].path.slice(0, roots[0].path.lastIndexOf('/')) : '';
  const warnings: string[] = [];
  const edges: { from: string; to: string }[] = [];
  const seen = new Set<string>(), queue = [...sourcePaths];
  const patternFor = (raw: string) => {
    if (raw.includes('$') || (!raw.startsWith('/') && !prefix) || /[?\[\]{}]/.test(raw) || raw.split('/').includes('..')) return null;
    const pattern = raw.startsWith('/') ? raw : `${prefix}/${raw}`;
    const escaped = pattern.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '[^/]*');
    return new RegExp(`^${escaped}$`);
  };
  // Link the main configuration to relevant route files without expanding its unrelated wildcard entries.
  if (roots.length === 1) for (const line of roots[0].content.split('\n')) {
    const match = line.match(/^\s*include\s+([^;#\s]+)\s*;/);
    if (!match) continue;
    const pattern = patternFor(match[1].replace(/^['"]|['"]$/g, ''));
    if (pattern) for (const path of sourcePaths) if (pattern.test(path)) edges.push({ from: roots[0].path, to: path });
  }
  while (queue.length) {
    const path = queue.shift()!;
    if (seen.has(path)) continue;
    seen.add(path);
    const file = scoped.find(item => item.path === path);
    if (!file) { warnings.push(`未归档配置：${path}`); continue; }
    // Only simple literal include directives are resolved; complex syntax stays unresolved.
    for (const line of file.content.split('\n')) {
      const match = line.match(/^\s*include\s+([^;#\s]+)\s*;/);
      if (!match) { if (/^\s*include\b/.test(line)) warnings.push(`无法解析 include：${path}`); continue; }
      const raw = match[1].replace(/^['"]|['"]$/g, '');
      const pattern = patternFor(raw);
      if (!pattern) { warnings.push(`无法确定引用：${path} → ${raw}`); continue; }
      const found = scoped.filter(item => pattern.test(item.path));
      if (!found.length) { warnings.push(`引用未归档或未匹配：${path} → ${raw}`); continue; }
      for (const target of found) { edges.push({ from: path, to: target.path }); if (!seen.has(target.path)) queue.push(target.path); }
    }
  }
  return { sourcePaths, edges: edges.filter((edge, index) => edges.findIndex(item => item.from === edge.from && item.to === edge.to) === index), warnings: [...new Set(warnings)] };
}

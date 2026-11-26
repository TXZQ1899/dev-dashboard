/** Query-time, read-only Kong Admin observations. No state is persisted in topology. */
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { fileURLToPath } from 'node:url';
import type { PathQuery, RequestEnvironment } from './query.ts';
import type { Evidence } from '../types.ts';
import { resolveEndpointNodes } from './endpoint.ts';
import type { PathResolver, ResolverContext, ResolutionResult } from './resolver.ts';

export type KongGateway = { environment: RequestEnvironment; ip: string; proxyPort: number; adminPort: number; viaJumpserver?: boolean };
export const KONG_GATEWAYS: readonly KongGateway[] = [
  { environment: 'PRODUCT', ip: '10.179.4.220', proxyPort: 8000, adminPort: 8001 },
  { environment: 'PRODUCT', ip: '10.179.4.73', proxyPort: 8000, adminPort: 8001 },
  { environment: 'PRODUCT', ip: '10.58.16.5', proxyPort: 8000, adminPort: 8001, viaJumpserver: true },
  { environment: 'PRODUCT', ip: '10.58.6.145', proxyPort: 8000, adminPort: 8001, viaJumpserver: true },
  { environment: 'TEST', ip: '10.179.2.144', proxyPort: 8000, adminPort: 8001 },
];
export const KONG_NO_TARGET = 'kong:no-target';
export const KONG_NO_ROUTE = 'kong:no-route';
type Row = Record<string, any>; // Admin API fields are validated before use.
export type KongData = { gateway: KongGateway; observedAt: string; routes: Row[]; services: Record<string, Row>; targets: Record<string, Row[]>; serviceErrors?: Record<string, string>; error?: string };

export function matchesKongGateway(node: { type: string; identity: Record<string, unknown> }, gateway: KongGateway): boolean {
  return node.type === 'ENDPOINT' && String(node.identity.ip) === gateway.ip && Number(node.identity.port) === gateway.proxyPort;
}

/** Report gateways reached under a different (or unspecified) environment. */
export function kongEnvironmentWarnings(paths: { nodes: { type: string; identity: Record<string, unknown> }[] }[], environment?: RequestEnvironment | null): string[] {
  return KONG_GATEWAYS.flatMap(gateway => {
    if (gateway.environment === environment) return [];
    const reached = paths.some(path => path.nodes.some(node => matchesKongGateway(node, gateway)));
    if (!reached) return [];
    return [`Kong 网关 ${gateway.ip}:${gateway.proxyPort} 属于 ${gateway.environment} 环境；当前查询环境为 ${environment || '未指定'}。请切换到 ${gateway.environment} 后重新查询，当前环境不会访问该网关的 Admin API。`];
  });
}

function possible(route: Row, query: PathQuery): boolean {
  const paths = route.paths;
  if (!Array.isArray(paths) || paths.length === 0) return true;
  return paths.some((p: unknown) => typeof p !== 'string' || p.startsWith('~') || /[\^$\[\](){}?+*\\]/.test(p) || query.path.startsWith(p));
}

function supportedRoute(route: Row, query: PathQuery): boolean {
  return Array.isArray(route.paths) && route.paths.length > 0 &&
    !route.paths.some((p: unknown) => typeof p !== 'string' || p.startsWith('~') || /[\^$\[\](){}?+*\\]/.test(p)) &&
    !['hosts', 'methods', 'headers', 'snis', 'sources', 'destinations'].some(k => route[k] && (Array.isArray(route[k]) ? route[k].length : Object.keys(route[k]).length)) &&
    !(Array.isArray(route.protocols) && !route.protocols.includes(query.scheme));
}

function openAdmin(gateway: KongGateway, deadline: AbortSignal, sourceVersion = '') {
  const base = `http://${gateway.ip}:${gateway.adminPort}`;
  if (!gateway.viaJumpserver) return {
    base,
    async read(url: URL): Promise<Row> {
      const response = await fetch(url, { signal: AbortSignal.any([deadline, AbortSignal.timeout(4000)]), redirect: 'error' });
      if (!response.ok) throw new Error(`Admin API HTTP ${response.status}`);
      return await response.json() as Row;
    },
    close() {},
  };

  const helper = fileURLToPath(new URL('../../../runtime/kong_admin_via_jumpserver.py', import.meta.url));
  const child = spawn('python3', [helper, gateway.ip, sourceVersion], { stdio: ['pipe', 'pipe', 'ignore'], signal: deadline });
  child.on('error', () => {});
  child.stdin.on('error', () => {});
  const stdout = createInterface({ input: child.stdout });
  const replies = stdout[Symbol.asyncIterator]();
  // All Admin requests for this gateway share one authorized terminal. Serial
  // reads preserve request order even if callers load several services at once.
  let pending: Promise<void> = Promise.resolve();
  return {
    base,
    read(url: URL): Promise<Row> {
      const result = pending.then(async () => {
        deadline.throwIfAborted();
        child.stdin.write(`${JSON.stringify(url.pathname + url.search)}\n`);
        const reply = await replies.next();
        if (reply.done) throw new Error('JumpServer Admin 会话已关闭');
        const { data, error } = JSON.parse(reply.value);
        if (error) throw new Error(error);
        return data as Row;
      });
      pending = result.then(() => {}, () => {});
      return result;
    },
    close() {
      child.stdin.end();
      stdout.close();
    },
  };
}

type AdminReader = ReturnType<typeof openAdmin>;

async function list(admin: AdminReader, path: string): Promise<Row[]> {
  const rows: Row[] = [];
  let next: string | null = path;
  for (let page = 0; next && page < 20; page++) {
    const url = new URL(next, admin.base);
    if (url.origin !== admin.base) throw new Error('invalid pagination origin');
    const data = await admin.read(url);
    if (!Array.isArray(data.data)) throw new Error('invalid Admin API list');
    rows.push(...data.data);
    if (rows.length > 10000) throw new Error('Admin API list too large');
    next = typeof data.next === 'string' && data.next ? data.next : null;
  }
  if (next) throw new Error('Admin API pagination limit exceeded');
  return rows;
}
export async function loadKongData(gateway: KongGateway | undefined, queries: PathQuery[]): Promise<KongData | null> {
  if (!gateway || !queries.length) return null;
  const data: KongData = { gateway, observedAt: new Date().toISOString(), routes: [], services: {}, targets: {} };
  const admin = openAdmin(gateway, AbortSignal.timeout(gateway.viaJumpserver ? 45_000 : 15_000));
  try {
    data.routes = await list(admin, '/routes?size=1000');
    const ids = [...new Set(data.routes.filter(r => queries.some(q => possible(r, q))).map(r => r.service?.id).filter((id): id is string => typeof id === 'string' && /^[a-z0-9-]+$/i.test(id)))];
    if (ids.length > 20) throw new Error('too many possible Kong services');
    for (const id of ids) {
      const service = await admin.read(new URL(`/services/${encodeURIComponent(id)}`, admin.base));
      data.services[id] = service;
      if (typeof service.host !== 'string' || !/^[a-z0-9._-]+$/i.test(service.host)) continue;
      try { data.targets[service.host] = await list(admin, `/upstreams/${encodeURIComponent(service.host)}/targets?size=1000`); }
      catch (error) { throw new Error(`upstream ${service.host}: ${error instanceof Error ? error.message : 'unavailable'}`); }
    }
  } catch (error) { data.error = error instanceof Error ? error.message : 'Admin API unavailable'; }
  finally { admin.close(); }
  return data;
}

/** Batch export needs only services for routes the request resolver can select.
 * Keep failures local to the affected service so one unavailable upstream does
 * not obscure evidence from the rest of the gateway. */
export async function loadKongExportData(gateway: KongGateway, queries: PathQuery[], sourceVersion?: string): Promise<KongData | null> {
  if (!gateway || !queries.length) return null;
  const data: KongData = { gateway, observedAt: new Date().toISOString(), routes: [], services: {}, targets: {}, serviceErrors: {} };
  const admin = openAdmin(gateway, AbortSignal.timeout(gateway.viaJumpserver ? 65_000 : 45_000), sourceVersion);
  try {
    data.routes = await list(admin, '/routes?size=1000');
  } catch (error) {
    data.error = error instanceof Error ? error.message : 'Admin API unavailable';
    admin.close();
    return data;
  }
  const ids = new Set<string>();
  for (const query of queries) {
    if (query.domainOnly) continue;
    const candidates = data.routes.filter(route => possible(route, query));
    const route = candidates.length === 1 ? candidates[0] : null;
    const id = route?.service?.id;
    if (route && supportedRoute(route, query) && typeof id === 'string' && /^[a-z0-9-]+$/i.test(id)) ids.add(id);
  }
  const pending = [...ids];
  const upstreams = new Map<string, Promise<Row[]>>();
  async function loadServices() {
    for (let id = pending.shift(); id; id = pending.shift()) {
      try {
        const service = await admin.read(new URL(`/services/${encodeURIComponent(id)}`, admin.base));
        data.services[id] = service;
        if (typeof service.host !== 'string' || !/^[a-z0-9._-]+$/i.test(service.host)) continue;
        if (!upstreams.has(service.host)) {
          upstreams.set(service.host, list(admin, `/upstreams/${encodeURIComponent(service.host)}/targets?size=1000`));
        }
        data.targets[service.host] = await upstreams.get(service.host)!;
      } catch (error) {
        data.serviceErrors![id] = error instanceof Error ? error.message : 'Admin API unavailable';
      }
    }
  }
  try {
    await Promise.all(Array.from({ length: Math.min(5, pending.length) }, loadServices));
  } finally {
    admin.close();
  }
  return data;
}

export class KongResolver implements PathResolver {
  readonly name = 'KongResolver';
  private readonly observations: readonly KongData[];
  constructor(data: KongData | KongData[]) { this.observations = Array.isArray(data) ? data : [data]; }
  private forEndpoint(context: ResolverContext): KongData | undefined {
    const endpoint = context.current;
    return endpoint ? this.observations.find(data => matchesKongGateway(endpoint, data.gateway)) : undefined;
  }
  canResolve(context: ResolverContext): boolean {
    return Boolean(this.forEndpoint(context));
  }
  resolve(context: ResolverContext): ResolutionResult {
    const fail = (message: string, confidence: ResolutionResult['confidence'] = 'UNKNOWN'): ResolutionResult => ({ rule: 'kong:route', confidence, moves: [], warnings: [message] });
    const data = this.forEndpoint(context);
    if (!data) return fail('No Kong observation for this proxy endpoint.');
    if (data.error) return fail(`Kong Admin API unavailable or incomplete: ${data.error}`);
    const candidates = data.routes.filter(r => possible(r, context.query));
    // Host headers, methods, regex and other request dimensions may be unknown;
    // never pick a route when any competing route could match.
    if (!candidates.length) return {
      ...fail(`Kong has 0 possible routes for ${context.query.path}; routing remains unresolved.`),
      stopReason: KONG_NO_ROUTE,
    };
    if (candidates.length > 1) return fail(`Kong has ${candidates.length} possible routes for ${context.query.path}; routing remains unresolved.`, 'AMBIGUOUS');
    const route = candidates[0];
    if (!supportedRoute(route, context.query)) {
      return fail('Kong route depends on unsupported or unknown request criteria; routing remains unresolved.');
    }
    const serviceId = route.service?.id;
    const error = data.serviceErrors?.[serviceId];
    if (error) return fail(`Kong Admin API service or upstream query failed: ${error}`);
    const service = data.services[serviceId];
    if (!service || service.enabled === false || !['http', 'https'].includes(service.protocol)) return fail('Kong service is unavailable, disabled, or has an unsupported protocol.');
    const targets = data.targets[service.host];
    if (!Array.isArray(targets)) return fail('Kong upstream targets are unavailable.');
    const evidence: Evidence[] = [{ source: 'kong', sourceId: String(route.id), reference: `http://${data.gateway.ip}:${data.gateway.adminPort}`, detail: `route ${route.name || route.id} → service ${service.name || service.id} → upstream ${service.host}`, observedAt: data.observedAt }];
    if (!targets.length) return {
      ...fail(`Kong ${evidence[0].detail}，Admin API 返回空的 Target 清单。`),
      stopReason: KONG_NO_TARGET,
      evidence,
    };
    const moves = [];
    for (const target of targets) {
      if (!(Number(target.weight) > 0) || typeof target.target !== 'string') continue;
      const match = target.target.match(/^(\d{1,3}(?:\.\d{1,3}){3}):(\d{1,5})$/);
      if (!match || match[1].split('.').some((x: string) => Number(x) > 255) || Number(match[2]) < 1 || Number(match[2]) > 65535) continue;
      const targetEvidence: Evidence = { ...evidence[0], sourceId: String(target.id || target.target), detail: `${evidence[0].detail} → target ${target.target} (weight ${target.weight})` };
      const resolved = resolveEndpointNodes(context.index, match[1], match[2], service.protocol, [targetEvidence], 'internal');
      for (const node of resolved.nodes.length ? resolved.nodes : resolved.synthesized ? [resolved.synthesized] : []) moves.push({ node });
      evidence.push(targetEvidence);
    }
    if (!moves.length) return fail('Kong upstream has no supported positive-weight IP targets.');
    return { rule: 'kong:targets', moves, confidence: 'INFERRED', evidence, warnings: [`Kong route ${route.name || route.id} → service ${service.name || service.id} → upstream ${service.host}; target health and uncollected plugins are not verified.`] };
  }
}

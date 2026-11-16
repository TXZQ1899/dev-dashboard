/** Query-time, read-only Kong Admin observations. No state is persisted in topology. */
import type { PathQuery, RequestEnvironment } from './query.ts';
import type { Evidence } from '../types.ts';
import { resolveEndpointNodes } from './endpoint.ts';
import type { PathResolver, ResolverContext, ResolutionResult } from './resolver.ts';

export const KONG_GATEWAYS: Partial<Record<RequestEnvironment, { ip: string; proxyPort: number; adminPort: number }>> = {
  PRODUCT: { ip: '10.179.4.220', proxyPort: 8000, adminPort: 8001 },
  TEST: { ip: '10.179.2.144', proxyPort: 8000, adminPort: 8001 },
};
type Row = Record<string, any>; // Admin API fields are validated before use.
export type KongData = { gateway: NonNullable<(typeof KONG_GATEWAYS)[RequestEnvironment]>; observedAt: string; routes: Row[]; services: Record<string, Row>; targets: Record<string, Row[]>; error?: string };

function possible(route: Row, query: PathQuery): boolean {
  const paths = route.paths;
  if (!Array.isArray(paths) || paths.length === 0) return true;
  return paths.some((p: unknown) => typeof p !== 'string' || p.startsWith('~') || /[\^$\[\](){}?+*\\]/.test(p) || query.path.startsWith(p));
}

async function list(base: string, path: string, deadline: AbortSignal): Promise<Row[]> {
  const rows: Row[] = [];
  let next: string | null = path;
  for (let page = 0; next && page < 20; page++) {
    const url = new URL(next, base);
    if (url.origin !== base) throw new Error('invalid pagination origin');
    const response = await fetch(url, { signal: AbortSignal.any([deadline, AbortSignal.timeout(4000)]), redirect: 'error' });
    if (!response.ok) throw new Error(`Admin API HTTP ${response.status}`);
    const data = await response.json() as Row;
    if (!Array.isArray(data.data)) throw new Error('invalid Admin API list');
    rows.push(...data.data);
    if (rows.length > 10000) throw new Error('Admin API list too large');
    next = typeof data.next === 'string' && data.next ? data.next : null;
  }
  if (next) throw new Error('Admin API pagination limit exceeded');
  return rows;
}
async function get(base: string, path: string, deadline: AbortSignal): Promise<Row> {
  const response = await fetch(base + path, { signal: AbortSignal.any([deadline, AbortSignal.timeout(4000)]), redirect: 'error' });
  if (!response.ok) throw new Error(`Admin API HTTP ${response.status}`);
  return await response.json() as Row;
}

export async function loadKongData(environment: RequestEnvironment, queries: PathQuery[]): Promise<KongData | null> {
  const gateway = KONG_GATEWAYS[environment];
  if (!gateway || !queries.length) return null;
  const base = `http://${gateway.ip}:${gateway.adminPort}`;
  const data: KongData = { gateway, observedAt: new Date().toISOString(), routes: [], services: {}, targets: {} };
  const deadline = AbortSignal.timeout(15000);
  try {
    data.routes = await list(base, '/routes?size=1000', deadline);
    const ids = [...new Set(data.routes.filter(r => queries.some(q => possible(r, q))).map(r => r.service?.id).filter((id): id is string => typeof id === 'string' && /^[a-z0-9-]+$/i.test(id)))];
    if (ids.length > 20) throw new Error('too many possible Kong services');
    for (const id of ids) {
      const service = await get(base, `/services/${encodeURIComponent(id)}`, deadline);
      data.services[id] = service;
      if (typeof service.host !== 'string' || !/^[a-z0-9._-]+$/i.test(service.host)) continue;
      try { data.targets[service.host] = await list(base, `/upstreams/${encodeURIComponent(service.host)}/targets?size=1000`, deadline); }
      catch (error) { throw new Error(`upstream ${service.host}: ${error instanceof Error ? error.message : 'unavailable'}`); }
    }
  } catch (error) { data.error = error instanceof Error ? error.message : 'Admin API unavailable'; }
  return data;
}

export class KongResolver implements PathResolver {
  readonly name = 'KongResolver';
  private readonly data: KongData;
  constructor(data: KongData) { this.data = data; }
  canResolve(context: ResolverContext): boolean {
    const n = context.current;
    return n?.type === 'ENDPOINT' && String(n.identity.ip) === this.data.gateway.ip && Number(n.identity.port) === this.data.gateway.proxyPort;
  }
  resolve(context: ResolverContext): ResolutionResult {
    const fail = (message: string): ResolutionResult => ({ rule: 'kong:route', moves: [], warnings: [message] });
    if (this.data.error) return fail(`Kong Admin API unavailable or incomplete: ${this.data.error}`);
    const candidates = this.data.routes.filter(r => possible(r, context.query));
    // Host headers, methods, regex and other request dimensions may be unknown;
    // never pick a route when any competing route could match.
    if (candidates.length !== 1) return fail(`Kong has ${candidates.length} possible routes for ${context.query.path}; routing remains unresolved.`);
    const route = candidates[0];
    if (!Array.isArray(route.paths) || !route.paths.length || route.paths.some((p: unknown) => typeof p !== 'string' || p.startsWith('~') || /[\^$\[\](){}?+*\\]/.test(p)) ||
        ['hosts', 'methods', 'headers', 'snis', 'sources', 'destinations'].some(k => route[k] && (Array.isArray(route[k]) ? route[k].length : Object.keys(route[k]).length)) ||
        (Array.isArray(route.protocols) && !route.protocols.includes(context.query.scheme))) {
      return fail('Kong route depends on unsupported or unknown request criteria; routing remains unresolved.');
    }
    const service = this.data.services[route.service?.id];
    if (!service || service.enabled === false || !['http', 'https'].includes(service.protocol)) return fail('Kong service is unavailable, disabled, or has an unsupported protocol.');
    const targets = this.data.targets[service.host];
    if (!targets) return fail('Kong upstream targets are unavailable.');
    const evidence: Evidence[] = [{ source: 'kong', sourceId: String(route.id), reference: `http://${this.data.gateway.ip}:${this.data.gateway.adminPort}`, detail: `route ${route.name || route.id} → service ${service.name || service.id} → upstream ${service.host}`, observedAt: this.data.observedAt }];
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

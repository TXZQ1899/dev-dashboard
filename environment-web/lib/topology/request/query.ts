/**
 * Request-level query model for the Request Resolver framework (TASK-01).
 *
 * This is intentionally distinct from the Path Explorer `PathQuery`
 * (`../path-explorer.ts`), which starts from an already-resolved topology node
 * kind (domain/application/host). A request query describes an incoming HTTP
 * request before any topology node is known.
 */

export type RequestScheme = 'http' | 'https';

export type RequestEnvironment = 'TEST' | 'SIMULATION' | 'PRODUCT';

/** Accepted input. Either `raw` (a URL or domain-only string) or structured fields. */
export type PathQueryInput = {
  /** Original user input, retained for UI display. */
  raw?: string;
  scheme?: string;
  host?: string;
  port?: number;
  path?: string;
  method?: string;
  environment?: string;
};

/** Normalized request query used by the resolution engine. */
export type PathQuery = {
  /** Original input when supplied; never derived, kept verbatim for UI display. */
  raw?: string;
  scheme: RequestScheme;
  /** Lowercase hostname without a trailing root dot. */
  host: string;
  /** Always populated: 80 for http, 443 for https unless explicitly overridden. */
  port: number;
  /** Always starts with `/`; never carries a query string or fragment. */
  path: string;
  /**
   * True when the caller supplied a host without any URL path (domain-only
   * query, e.g. `api.example.com` or `{ host }`). Path-dependent routing must
   * not be guessed from the implicit `/`; resolvers surface a
   * "URI required to continue routing" warning instead (TASK-06 Step 4).
   */
  domainOnly?: boolean;
  method?: string;
  environment?: RequestEnvironment;
};

const DEFAULT_PORTS: Record<RequestScheme, number> = { http: 80, https: 443 };
const ENVIRONMENTS: readonly RequestEnvironment[] = ['TEST', 'SIMULATION', 'PRODUCT'];

/**
 * Normalize a request query.
 *
 * Rules (V1 infrastructure routing):
 * - hostname lowercase, trailing root dot removed
 * - http defaults to port 80, https to port 443
 * - path defaults to `/`, always rooted; URL query string is stripped
 * - fragments are stripped
 * - `raw` is preserved verbatim for UI display
 *
 * Throws when no host can be determined or when an unsupported scheme/port is given.
 */
export function normalizeRequestQuery(input: PathQueryInput | string): PathQuery {
  const source: PathQueryInput = typeof input === 'string' ? { raw: input } : { ...input };

  let parsed: URL | null = null;
  if (typeof source.raw === 'string' && source.raw.trim()) {
    parsed = parseRaw(source.raw.trim());
  }

  // Domain-only input (raw or structured) carries no scheme; http is the V1 default.
  const scheme = normalizeScheme(source.scheme ?? parsed?.protocol.slice(0, -1) ?? 'http');
  const host = normalizeHost(source.host ?? parsed?.hostname ?? '');
  if (!host) throw new Error('PathQuery requires a host.');

  const port = normalizePort(source.port ?? (parsed && parsed.port !== '' ? Number(parsed.port) : undefined), scheme);
  const path = normalizePath(source.path ?? parsed?.pathname ?? '/');
  const method = normalizeMethod(source.method);
  const environment = normalizeEnvironment(source.environment);

  // Domain-only input: a host was supplied without any URL path information
  // (no explicit `path` field and no path in `raw` beyond the default `/`).
  const domainOnly = source.path === undefined && (parsed === null || parsed.pathname === '/');

  const normalized: PathQuery = { scheme, host, port, path };
  if (source.raw !== undefined) normalized.raw = source.raw;
  if (domainOnly) normalized.domainOnly = true;
  if (method !== undefined) normalized.method = method;
  if (environment !== undefined) normalized.environment = environment;
  return normalized;
}

/**
 * Parse a raw string that may be a full URL or a domain-only / host+path string
 * without a scheme.
 */
function parseRaw(raw: string): URL {
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(raw)) return new URL(raw);
  // Scheme-less input: prepend a placeholder scheme so URL parsing applies
  // (domain-only, host:port and host/path forms are all accepted).
  return new URL(`http://${raw}`);
}

function normalizeScheme(value: unknown): RequestScheme {
  const raw = typeof value === 'string' ? value.trim().toLowerCase() : '';
  if (raw === 'http' || raw === 'https') return raw;
  throw new Error(`Unsupported PathQuery scheme: ${String(value)}; only http and https are supported.`);
}

function normalizeHost(value: unknown): string {
  const raw = typeof value === 'string' ? value.trim().replace(/^\[|\]$/g, '') : '';
  return raw.replace(/\.$/, '').toLowerCase();
}

function normalizePort(value: number | undefined, scheme: RequestScheme): number {
  if (value === undefined || (typeof value === 'number' && Number.isNaN(value))) return DEFAULT_PORTS[scheme];
  if (!Number.isInteger(value) || value < 1 || value > 65535) {
    throw new Error(`Invalid PathQuery port: ${String(value)}; expected an integer between 1 and 65535.`);
  }
  return value;
}

function normalizePath(value: unknown): string {
  let path = typeof value === 'string' ? value.trim() : '';
  // Query string and fragment never participate in V1 infrastructure routing.
  path = path.split(/[?#]/, 1)[0];
  if (!path) return '/';
  return path.startsWith('/') ? path : `/${path}`;
}

function normalizeMethod(value: unknown): string | undefined {
  const method = typeof value === 'string' ? value.trim().toUpperCase() : '';
  return method || undefined;
}

function normalizeEnvironment(value: unknown): RequestEnvironment | undefined {
  const raw = typeof value === 'string' ? value.trim().toUpperCase() : '';
  return (ENVIRONMENTS as readonly string[]).includes(raw) ? raw as RequestEnvironment : undefined;
}

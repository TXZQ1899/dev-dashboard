'use client';
import { useState } from 'react';
import { Shell } from '@/app/page';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { AlertTriangle, Network, Route, Search } from 'lucide-react';

type Kind = 'domain' | 'application' | 'host';
type Mode = 'chain' | 'paths' | 'request';
type NodeRef = { id: string; type: string; label?: string; status?: string };
type Evidence = { source: string; reference?: string; detail?: string; observedAt?: string };
type EdgeRef = {
  id: string;
  type: string;
  confidence: string;
  environment: string;
  evidence: Evidence[];
};
type Step = { direction: 'forward' | 'reverse'; edge: EdgeRef; to: NodeRef };
type TraversalPath = {
  nodes: NodeRef[];
  edges: EdgeRef[];
  steps: Step[];
  length: number;
  confidence: string;
  weakEdges: string[];
  environments: string[];
  unresolved: boolean;
};
type Payload = {
  topology: { id: string; generatedAt: string };
  result: {
    query: { kind: Kind; query: string; to: string[]; environment: string | null; maxDepth: number };
    resolution: { status: string; candidates: NodeRef[]; matchedBy: string[] };
    status: 'resolved' | 'ambiguous' | 'unresolved';
    paths: TraversalPath[];
    gaps: string[];
    reachableTypes: string[];
  };
};

// ---- Domain landing chain (staged projection, mirrors domain-chain.ts) ----
type ChainNode = NodeRef & {
  identity?: Record<string, unknown>;
  attributes?: Record<string, unknown>;
};
type ChainAppMatch = {
  application: ChainNode;
  deployment: ChainNode;
  listenerEndpoint: ChainNode;
  environment: string;
  confidence: string;
  protocolMismatch: boolean;
};
type ChainHostCandidate = { application: ChainNode; deployment: ChainNode; port: string; environment: string };
type ChainBackend = {
  endpoint: ChainNode;
  host: ChainNode | null;
  status: 'exact' | 'ambiguous' | 'unresolved';
  matches: ChainAppMatch[];
  sameHostCandidates: ChainHostCandidate[];
};
type ChainGroup = {
  upstream: ChainNode;
  kind: 'proxy' | 'static' | 'external';
  routeIds: string[];
  uris: string[];
  uriCount: number;
  directives: string[];
  targets: string[];
  backends: ChainBackend[];
};
type ChainNginxHost = {
  assetId: string;
  host: ChainNode | null;
  nginxStatus: string;
  configurationVersion: string;
  listen: string[];
  context: string[];
  routeCount: number;
  totalRouteCount: number;
  routeIds: string[];
};
type ChainDnat = { rule: ChainNode; external: ChainNode | null; internal: ChainNode | null; internalHost: ChainNode | null };
type ChainClbBackend = { listener: ChainNode; group: ChainNode; endpoint: ChainNode; host: ChainNode | null };
type ChainEntry = {
  resolveEdge: EdgeRef;
  target: ChainNode;
  targetKind: 'eip' | 'clb' | 'host';
  binding: { edge: EdgeRef; kind: 'nat-gateway' | 'ecs-host' | 'clb'; gateway: ChainNode | null; host: ChainNode | null; clb: ChainNode | null } | null;
  dnat: ChainDnat[];
  clbBackends: ChainClbBackend[];
};
type ChainCnameHop = { edge: EdgeRef; domain: ChainNode };
type DomainChain = {
  query: { domain: string; normalizedDomain: string; environment: string | null };
  resolution: { status: string; candidates: ChainNode[] };
  status: 'resolved' | 'ambiguous' | 'unresolved';
  cnameChain: ChainCnameHop[];
  entries: ChainEntry[];
  nginxHosts: ChainNginxHost[];
  groups: ChainGroup[];
  connected: boolean;
  stats: {
    routeCount: number;
    upstreamCount: number;
    backendCount: number;
    exactBackendCount: number;
    ambiguousBackendCount: number;
    unresolvedBackendCount: number;
    applicationIds: string[];
  };
  gaps: string[];
};
type ChainPayload = { topology: { id: string; generatedAt: string }; result: DomainChain };

// ---- Request-aware end-to-end trace (staged projection, mirrors request/index.ts) ----
type RequestStep = {
  resolver: string;
  inputNodeIds: string[];
  outputNodeIds: string[];
  rule: string;
  confidence: string;
  evidence: Evidence[];
  warnings: string[];
};
type RequestNode = NodeRef & {
  identity?: Record<string, unknown>;
  attributes?: Record<string, unknown>;
};
type RequestPath = {
  steps: RequestStep[];
  nodes: RequestNode[];
  terminalNodeId: string | null;
  confidence: string;
  status: 'RESOLVED' | 'PARTIAL' | 'AMBIGUOUS' | 'UNRESOLVED';
  stoppedAt?: string;
  reason?: string;
  warnings: string[];
};
type RequestPayload = {
  topology: { id: string; generatedAt: string };
  result: {
    query: {
      scheme: string; host: string; port: number; path: string;
      raw?: string; domainOnly?: boolean; method?: string; environment?: string;
    };
    paths: RequestPath[];
    status: RequestPath['status'];
    warnings: string[];
  };
};

const KINDS: [Kind, string][] = [
  ['domain', '域名 Domain'],
  ['application', '应用 Application'],
  ['host', '主机 Host'],
];
const PLACEHOLDERS: Record<Kind, string> = {
  domain: 'api.example.com',
  application: 'order-service 或应用 ID',
  host: '10.179.1.10 或实例名',
};
const TARGETS = ['', 'APPLICATION', 'HOST', 'ENDPOINT', 'DOMAIN', 'DEPLOYMENT', 'NGINX_ROUTE', 'UPSTREAM', 'CLB'];
const DEFAULT_TARGETS: Record<Kind, string> = {
  domain: 'APPLICATION',
  application: 'HOST',
  host: 'APPLICATION',
};
const ENVIRONMENTS = ['', 'PRODUCT', 'TEST', 'SIMULATION'];
const STATUS_LABELS: Record<string, string> = {
  resolved: '已解析',
  ambiguous: '存在多条路径',
  unresolved: '未解析',
};
const CONFIDENCE_LABELS: Record<string, string> = {
  EXACT: '确认',
  INFERRED: '推断',
  AMBIGUOUS: '待确认',
  UNKNOWN: '未知',
};

function confidenceLabel(value: string) {
  return CONFIDENCE_LABELS[value] ?? value;
}

const QUERY_MODES: [Mode, string][] = [
  ['chain', '域名落点链路（推荐域名）'],
  ['request', '请求链路（URL 端到端）'],
  ['paths', '通用路径遍历'],
];
const REQUEST_STATUS_LABELS: Record<string, string> = {
  RESOLVED: '已解析',
  PARTIAL: '部分解析',
  AMBIGUOUS: '存在多条解释',
  UNRESOLVED: '未解析',
};
const REQUEST_STOP_REASONS: Record<string, string> = {
  'no-resolver-entry': '没有 Resolver 可以继续',
  'no-candidate': '没有候选目标',
  'cycle-guard': '环路保护',
  'max-depth': '达到最大深度',
  'max-paths': '达到最大路径数',
};
const CHAIN_KIND_LABELS: Record<string, string> = {
  proxy: '代理转发',
  external: '外部主机名目标',
  static: '静态/其他',
};
const NGINX_STATUS_LABELS: Record<string, string> = {
  complete: '配置采集完整（有 Nginx 进程）',
  partial: '降级采集（配置部分可用）',
  failed: '采集失败',
  not_running: '未发现运行中的 Nginx',
};
function chainHostLabel(host: ChainNode | null): string {
  if (!host) return '';
  const ecs = host.attributes?.ecs as { name?: string; id?: string } | undefined;
  if (ecs?.name) return `${ecs.name}（${ecs.id || host.id}）`;
  const jump = host.attributes?.jumpserver as { hostname?: string } | undefined;
  return jump?.hostname || host.id;
}

function endpointLabel(node: ChainNode | null): string {
  if (!node?.identity) return node?.id ?? '?';
  const { ip, port, protocol } = node.identity as { ip?: string; port?: string; protocol?: string };
  return `${ip}:${port}/${protocol}`;
}

export default function TopologyPathPage() {
  const [mode, setMode] = useState<Mode>('chain');
  const [kind, setKind] = useState<Kind>('domain');
  const [query, setQuery] = useState('');
  const [target, setTarget] = useState('');
  const [environment, setEnvironment] = useState('');
  const [maxDepth, setMaxDepth] = useState('8');
  const [data, setData] = useState<Payload | null>(null);
  const [chainData, setChainData] = useState<ChainPayload | null>(null);
  const [requestData, setRequestData] = useState<RequestPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  function switchMode(next: Mode) {
    setMode(next);
    setData(null);
    setChainData(null);
    setRequestData(null);
    setError('');
    if (next === 'chain') setKind('domain');
    // request 模式默认 PRODUCT 环境，避免遍历所有环境导致路径爆炸（maxPaths 提前触发）
    if (next === 'request' && (!environment || environment === 'GLOBAL')) {
      setEnvironment('PRODUCT');
    }
  }

  async function search(event: { preventDefault: () => void }) {
    event.preventDefault();
    if (!query.trim()) {
      setError(
        mode === 'chain'
          ? '请输入要查询的域名'
          : mode === 'request'
            ? '请输入要查询的 URL 或域名'
            : '请输入要查询的域名、应用名或 IP',
      );
      return;
    }
    setLoading(true);
    setError('');
    try {
      if (mode === 'chain') {
        const params = new URLSearchParams({ q: query.trim() });
        if (environment) params.set('env', environment);
        const response = await fetch('/api/topology/domain-chain?' + params.toString(), { cache: 'no-store' });
        const body = (await response.json()) as ChainPayload & { error?: string };
        if (!response.ok) throw new Error(body.error || '查询失败');
        setData(null);
        setRequestData(null);
        setChainData(body);
        return;
      }
      if (mode === 'request') {
        const params = new URLSearchParams({ q: query.trim() });
        // request 模式必须指定环境，否则遍历所有环境导致路径爆炸；默认 PRODUCT
        const effectiveEnv = environment && environment !== 'GLOBAL' ? environment : 'PRODUCT';
        params.set('env', effectiveEnv);
        const response = await fetch('/api/topology/request-path?' + params.toString(), { cache: 'no-store' });
        const body = (await response.json()) as RequestPayload & { error?: string };
        if (!response.ok) throw new Error(body.error || '查询失败');
        setData(null);
        setChainData(null);
        setRequestData(body);
        return;
      }
      const params = new URLSearchParams({ kind, q: query.trim(), maxDepth });
      if (target) params.set('to', target);
      if (environment) params.set('env', environment);
      const response = await fetch('/api/topology/path?' + params.toString(), { cache: 'no-store' });
      const body = (await response.json()) as Payload & { error?: string };
      if (!response.ok) throw new Error(body.error || '查询失败');
      setChainData(null);
      setRequestData(null);
      setData(body);
    } catch (e) {
      setData(null);
      setChainData(null);
      setRequestData(null);
      setError(e instanceof Error ? e.message : '查询失败');
    } finally {
      setLoading(false);
    }
  }

  const result = data?.result;
  const chain = chainData?.result;
  const request = requestData?.result;

  return (
    <Shell active="topology">
      <main>
        <div className="page-heading">
          <div>
            <div className="eyebrow">TOPOLOGY PATH EXPLORER</div>
            <h1>拓扑路径查询</h1>
            <p>
              从域名、应用或主机出发，沿着 Topology
              中的真实连边回溯完整链路，用于定位访问入口与部署落点。
            </p>
          </div>
          <span className="subtle-tag">基于当前激活的 Topology 版本</span>
        </div>

        <section className="panel">
          <form className="resource-filters" onSubmit={search}>
            <select
              aria-label="查询模式"
              value={mode}
              onChange={(e) => switchMode(e.target.value as Mode)}
            >
              {QUERY_MODES.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
            <select
              aria-label="查询起点类型"
              value={kind}
              disabled={mode !== 'paths'}
              onChange={(e) => setKind(e.target.value as Kind)}
            >
              {KINDS.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
            <Input
              type="search"
              aria-label={mode === 'chain' ? '域名' : mode === 'request' ? 'URL 或域名' : '域名、应用名或 IP'}
              placeholder={mode === 'chain' ? 'apis.folidaymall.com' : mode === 'request' ? 'https://api.example.com/order/123' : PLACEHOLDERS[kind]}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            {mode === 'paths' && (
              <select
                aria-label="目标节点类型"
                value={target}
                onChange={(e) => setTarget(e.target.value)}
              >
                <option value="">目标：{DEFAULT_TARGETS[kind]}（默认）</option>
                {TARGETS.filter(Boolean).map((value) => (
                  <option key={value} value={value}>
                    目标：{value}
                  </option>
                ))}
              </select>
            )}
            <select
              aria-label="环境过滤"
              value={environment}
              onChange={(e) => setEnvironment(e.target.value)}
            >
              {ENVIRONMENTS.filter((value) => mode !== 'request' || value !== '').map((value) => (
                <option key={value || 'all'} value={value}>
                  {value ? `环境：${value}` : '环境：全部'}
                </option>
              ))}
            </select>
            {mode === 'paths' && (
              <select
                aria-label="最大深度"
                value={maxDepth}
                onChange={(e) => setMaxDepth(e.target.value)}
              >
                {['4', '6', '8', '10', '12', '16'].map((value) => (
                  <option key={value} value={value}>
                    最长 {value} 段
                  </option>
                ))}
              </select>
            )}
            <Button type="submit" disabled={loading}>
              <Search />
              {loading ? '查询中…' : mode === 'chain' ? '查询链路' : mode === 'request' ? '解析请求' : '查询路径'}
            </Button>
          </form>
          <p className="settings-hint">
            {mode === 'chain'
              ? '域名落点链路按 5 层展开：DNS → EIP（NAT/ECS/CLB）→ Nginx → Upstream 后端 IP+端口 → DevOps 应用；后端严格按 IP+端口匹配，同机其他端口只作为排查提示，不构成匹配。'
              : mode === 'request'
                ? '请求链路按完整 URL 端到端解析，按 5 层落点展示：DNS 解析 → 入口绑定 (NAT) → CLB 端口转发 → Nginx 转发规则 → 后端落点 (IP+端口) 与应用。必须选择环境（默认 PRODUCT），否则跨环境遍历会命中路径上限；仅输入域名时无法匹配 location 路由。'
                : '未指定目标类型时按默认方向查询：域名 → 应用、应用 → 主机、主机 → 应用。GLOBAL 连边在任何环境过滤下都会保留，环境过滤只排除 TEST / PRODUCT / SIMULATION 专属连边。'}
          </p>
          {error && (
            <p role="alert" className="settings-error">
              {error}
            </p>
          )}
        </section>

        {request && requestData && (
          <RequestView request={request} topology={requestData.topology} />
        )}

        {chain && chainData && (
          <ChainView chain={chain} topology={chainData.topology} />
        )}

        {result && (
          <>
            <section className="panel" aria-live="polite">
              <div className="panel-title">
                <div>
                  <h2>
                    查询结果{' '}
                    <span className="count-pill">{result.paths.length} 条路径</span>
                  </h2>
                  <p>
                    {result.query.kind.toUpperCase()}「{result.query.query}」→{' '}
                    {result.query.to.join(' / ')}
                    {result.query.environment ? ` · 环境 ${result.query.environment}` : ' · 全部环境'} ·
                    最长 {result.query.maxDepth} 段
                  </p>
                </div>
                <span className="subtle-tag">{STATUS_LABELS[result.status] ?? result.status}</span>
              </div>

              <p className="settings-version">
                起点解析：{STATUS_LABELS[result.resolution.status] ?? result.resolution.status} ·
                {result.resolution.candidates.length
                  ? ` ${result.resolution.candidates.map((node) => node.id).join('、')}`
                  : ' 未匹配到节点'}
              </p>
              <p className="settings-hint">
                Topology 版本：{data.topology.id} · 生成时间：
                {new Date(data.topology.generatedAt).toLocaleString('zh-CN', {
                  timeZone: 'Asia/Shanghai',
                  hour12: false,
                })}
              </p>

              {result.gaps.length > 0 && (
                <div className="rounded-lg border p-3">
                  <p className="flex items-center gap-2 text-sm font-medium">
                    <AlertTriangle size={16} />
                    诊断提示
                  </p>
                  <ul className="mt-2 list-disc pl-5 text-sm">
                    {result.gaps.map((gap) => (
                      <li key={gap}>{gap}</li>
                    ))}
                  </ul>
                  {result.reachableTypes.length > 0 && (
                    <p className="settings-hint">
                      从起点出发在深度范围内可达的节点类型：{result.reachableTypes.join('、')}
                    </p>
                  )}
                </div>
              )}
            </section>

            {result.paths.map((path, index) => (
              <section className="panel" key={path.edges.map((edge) => edge.id).join('>')}>
                <div className="panel-title">
                  <div>
                    <h2>
                      <Route size={17} /> 路径 {index + 1}
                    </h2>
                    <p>
                      {path.length} 段 · 链路置信度 {confidenceLabel(path.confidence)} ·
                      环境 {path.environments.join(' / ')}
                    </p>
                  </div>
                  {path.unresolved && <span className="subtle-tag">包含未解析节点</span>}
                </div>
                <ol className="flex flex-col gap-2 text-sm">
                  <li>
                    <code>{path.nodes[0].id}</code> <span>[{path.nodes[0].type}]</span>
                  </li>
                  {path.steps.map((step, position) => (
                    <li key={`${step.edge.id}-${position}`}>
                      <span className="subtle-tag">
                        {step.direction === 'forward' ? '→' : '←'} {step.edge.type}
                      </span>{' '}
                      <span>
                        {confidenceLabel(step.edge.confidence)} · {step.edge.environment} ·{' '}
                        {step.edge.evidence.length} 条证据
                      </span>
                      <br />
                      <code>{step.to.id}</code> <span>[{step.to.type}]</span>
                    </li>
                  ))}
                </ol>
                <details className="mt-3">
                  <summary>查看逐段证据（{path.edges.length} 段）</summary>
                  <ul className="mt-2 list-disc pl-5 text-sm">
                    {path.edges.map((edge) => (
                      <li key={edge.id}>
                        <code>{edge.id}</code> {edge.type}
                        <ul className="list-disc pl-5">
                          {edge.evidence.map((item, position) => (
                            <li key={`${edge.id}-${position}`}>
                              {item.detail || item.source}
                              {item.reference ? ` · ${item.reference}` : ''}
                            </li>
                          ))}
                        </ul>
                      </li>
                    ))}
                  </ul>
                </details>
              </section>
            ))}
          </>
        )}
      </main>
    </Shell>
  );
}

function RequestView({ request, topology }: { request: RequestPayload['result']; topology: RequestPayload['topology'] }) {
  const q = request.query;
  const input = q.raw || `${q.scheme}://${q.host}:${q.port}${q.path}`;

  // Collect all nodes from all paths for cross-path lookup.
  const nodeMap = new Map<string, RequestNode>();
  for (const path of request.paths) {
    for (const node of path.nodes) {
      if (!nodeMap.has(node.id)) nodeMap.set(node.id, node);
    }
  }

  // Primary path: most steps (furthest progress), then highest confidence.
  const primary = [...request.paths].sort((a, b) => {
    if (b.steps.length !== a.steps.length) return b.steps.length - a.steps.length;
    const rank: Record<string, number> = { EXACT: 3, INFERRED: 2, AMBIGUOUS: 1, UNKNOWN: 0 };
    return (rank[b.confidence] ?? 0) - (rank[a.confidence] ?? 0);
  })[0];

  const statusLabel = REQUEST_STATUS_LABELS[request.status] ?? request.status;
  const stopNode = primary && !primary.terminalNodeId && primary.stoppedAt ? nodeMap.get(primary.stoppedAt) : null;

  // Helper accessors
  const nodeById = (id: string) => nodeMap.get(id);
  const out0 = (step: RequestStep | undefined) => step ? nodeById(step.outputNodeIds[0]) : undefined;
  const in0 = (step: RequestStep | undefined) => step ? nodeById(step.inputNodeIds[0]) : undefined;
  const str = (v: unknown, fallback = '?') => {
    if (typeof v === 'string') return v;
    if (typeof v === 'number' || typeof v === 'boolean') return String(v);
    return fallback;
  };
  const ipOf = (node: RequestNode | undefined) => str(node?.identity?.ip);
  const portOf = (node: RequestNode | undefined) => {
    const port = node?.identity?.port;
    if (!port || port === 'unknown') return '';
    return `:${str(port)}`;
  };
  const hostLabel = (node: RequestNode | undefined) =>
    (node?.attributes?.jumpserver as { hostname?: string } | undefined)?.hostname ?? ipOf(node);

  const steps = primary?.steps ?? [];
  const find = (predicate: (s: RequestStep) => boolean) => steps.find(predicate);

  // Layer steps
  const dnsEntry = find(s => s.resolver === 'DNSResolver' && s.rule === 'dns:entry');
  const dnsRecord = find(s => s.resolver === 'DNSResolver' && s.rule === 'dns:records');
  const natExt = find(s => s.resolver === 'NatResolver' && s.rule === 'nat:external-endpoint');
  const natDnat = find(s => s.resolver === 'NatResolver' && s.rule === 'nat:dnat');
  const clbSteps = steps.filter(s => s.resolver === 'ClbResolver');
  const nginxSteps = steps.filter(s => s.resolver === 'NginxResolver');
  const nginxHostStep = nginxSteps.find(s => s.rule === 'nginx:host');
  const nginxRouteSteps = nginxSteps.filter(s => s.rule.startsWith('nginx:route'));
  const nginxUpstreamStep = nginxSteps.find(s => s.rule === 'nginx:upstream');
  const nginxBackendStep = nginxSteps.find(s => s.rule === 'nginx:backend' || s.rule === 'nginx:backend-derived');
  const deploySteps = steps.filter(s => s.resolver === 'DeploymentResolver');
  const repoStep = steps.find(s => s.resolver === 'RepositoryResolver');

  // Key warnings (primary path only, skip noise + already-shown)
  const skipPatterns = ['evidence truncated', 'Step evidence truncated', 'No rule on listener', 'falling back to the default', 'No nginx route on host', 'falling back to port-agnostic', 'equally match host', 'Resolver(s)'];
  const keyWarnings = (primary?.warnings ?? []).filter((w, i, arr) => arr.indexOf(w) === i && !skipPatterns.some(p => w.includes(p)));

  return (
    <>
      <section className="panel" aria-live="polite">
        <div className="panel-title">
          <div>
            <h2>
              <Route size={17} /> 请求链路{' '}
              {request.paths.length > 1 && <span className="count-pill">{request.paths.length} 条路径，已取最远</span>}
            </h2>
            <p>
              <code>{input}</code>
              {q.environment ? ` · 环境 ${q.environment}` : ' · 全部环境'}
            </p>
          </div>
          <span className="subtle-tag">{statusLabel}</span>
        </div>
        <p className="settings-hint">
          Topology 版本：{topology.id} · 生成时间：
          {new Date(topology.generatedAt).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })}
        </p>
        {stopNode && (
          <p className="settings-hint">
            停止于 {hostLabel(stopNode) || stopNode.id}
            {primary?.reason ? `（${REQUEST_STOP_REASONS[primary.reason] ?? primary.reason}）` : ''}
          </p>
        )}
      </section>

      {/* ① DNS 解析 */}
      {(dnsEntry || dnsRecord) && (
        <section className="panel">
          <h2>① DNS 解析</h2>
          <p className="text-sm">
            {dnsEntry ? str(out0(dnsEntry)?.identity?.name, q.host) : q.host}
            {dnsRecord && out0(dnsRecord) && (
              <> → <code>{ipOf(out0(dnsRecord))}</code> [{out0(dnsRecord)!.type}] ({confidenceLabel(dnsRecord.confidence)})</>
            )}
          </p>
        </section>
      )}

      {/* ② 入口绑定 (NAT) */}
      {(natExt || natDnat) && (
        <section className="panel">
          <h2>② 入口绑定 (NAT)</h2>
          <div className="text-sm space-y-1">
            {natExt && (
              <p>EIP <code>{ipOf(in0(natExt))}</code> → <code>{ipOf(out0(natExt))}{portOf(out0(natExt))}</code></p>
            )}
            {natDnat && (
              <p>DNAT <code>{ipOf(in0(natDnat))}{portOf(in0(natDnat))}</code> → <code>{ipOf(out0(natDnat))}{portOf(out0(natDnat))}</code></p>
            )}
          </div>
        </section>
      )}

      {/* ③ CLB 端口转发 */}
      {clbSteps.length > 0 && (
        <section className="panel">
          <h2>③ CLB 端口转发</h2>
          <div className="text-sm space-y-1">
            {(() => {
              const clbStep = clbSteps.find(s => s.rule === 'clb:endpoint-ip');
              const listenerStep = clbSteps.find(s => s.rule === 'clb:listener');
              const groupStep = clbSteps.find(s => s.rule === 'clb:default-group' || s.rule === 'clb:rule');
              const backendStep = clbSteps.find(s => s.rule === 'clb:backend');
              return (
                <>
                  {clbStep && out0(clbStep) && <p>CLB {out0(clbStep)!.label ?? out0(clbStep)!.id}</p>}
                  {listenerStep && out0(listenerStep) && (
                    <p>listener {str(out0(listenerStep)!.identity?.protocol)}:{str(out0(listenerStep)!.identity?.port)}</p>
                  )}
                  {groupStep && out0(groupStep) && (
                    <p>→ {str(out0(groupStep)!.identity?.serverGroupId, out0(groupStep)!.label ?? '?')}{groupStep.rule === 'clb:default-group' ? ' (默认服务器组)' : ''}</p>
                  )}
                  {backendStep && out0(backendStep) && (
                    <p>→ <code>{ipOf(out0(backendStep))}{portOf(out0(backendStep))}</code></p>
                  )}
                  {groupStep?.warnings.find(w => w.includes('No rule on listener') || w.includes('falling back')) && (
                    <p className="text-yellow-600">⚠ {groupStep.warnings.find(w => w.includes('No rule on listener') || w.includes('falling back'))}</p>
                  )}
                </>
              );
            })()}
          </div>
        </section>
      )}

      {/* ④ Nginx 转发规则 */}
      {(nginxHostStep || nginxRouteSteps.length > 0) && (
        <section className="panel">
          <h2>④ Nginx 转发规则</h2>
          <div className="text-sm space-y-1">
            {nginxHostStep && out0(nginxHostStep) && (
              <p>主机 {hostLabel(out0(nginxHostStep))} ({confidenceLabel(nginxHostStep.confidence)})</p>
            )}
            {nginxRouteSteps.length > 0 && nginxRouteSteps[0].outputNodeIds.map(routeId => {
              const route = nodeById(routeId);
              if (!route) return null;
              const domains = (route.identity?.domains as string[]) ?? [];
              const uri = (route.identity?.uri as string) ?? '/';
              const target = (route.identity?.target as string) ?? '';
              return (
                <p key={routeId}>
                  server_name {domains.join(', ') || '(默认)'} · location {uri}{' '}
                  {target ? <>→ <code>{target}</code></> : '(无 proxy_pass)'}
                </p>
              );
            })}
            {nginxRouteSteps.length > 0 && nginxRouteSteps[0].outputNodeIds.length > 1 && (
              <p className="text-yellow-600">
                ({nginxRouteSteps[0].outputNodeIds.length} 条 route 匹配，{confidenceLabel(nginxRouteSteps[0].confidence)})
              </p>
            )}
            {nginxUpstreamStep && out0(nginxUpstreamStep) && (
              <p>upstream {str(out0(nginxUpstreamStep)!.identity?.name)}</p>
            )}
            {nginxRouteSteps.flatMap(s => s.warnings).find(w => w.includes('port-agnostic') || w.includes('falling back to port')) && (
              <p className="text-yellow-600">
                ⚠ {nginxRouteSteps.flatMap(s => s.warnings).find(w => w.includes('port-agnostic') || w.includes('falling back to port'))}
              </p>
            )}
          </div>
        </section>
      )}

      {/* ⑤ 后端落点 */}
      {(nginxBackendStep || deploySteps.length > 0) && (
        <section className="panel">
          <h2>⑤ 后端落点</h2>
          <div className="text-sm space-y-1">
            {nginxBackendStep && out0(nginxBackendStep) && (
              <p>→ <code>{ipOf(out0(nginxBackendStep))}{portOf(out0(nginxBackendStep))}</code></p>
            )}
            {deploySteps.flatMap(s => s.outputNodeIds).map(id => {
              const node = nodeById(id);
              if (node?.type !== 'APPLICATION') return null;
              return <p key={id}>→ 应用 {node.label} ({str(node.identity?.devopsAppId)})</p>;
            })}
            {repoStep && out0(repoStep) && (
              <p>→ 仓库 {out0(repoStep)!.label ?? str(out0(repoStep)!.identity?.url)}</p>
            )}
          </div>
        </section>
      )}

      {keyWarnings.length > 0 && (
        <section className="panel">
          <div className="flex items-center gap-2 text-sm font-medium">
            <AlertTriangle size={16} />
            提示
          </div>
          <ul className="mt-2 list-disc pl-5 text-sm">
            {keyWarnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}

function ChainView({ chain, topology }: { chain: DomainChain; topology: ChainPayload['topology'] }) {
  const s = chain.stats;
  return (
    <>
      <section className="panel" aria-live="polite">
        <div className="panel-title">
          <div>
            <h2>
              <Network size={17} /> 域名落点链路{' '}
              <span className="count-pill">{s.applicationIds.length} 个应用</span>
            </h2>
            <p>
              {chain.query.normalizedDomain}
              {chain.query.environment ? ` · 环境 ${chain.query.environment}` : ' · 全部环境'} ·
              起点解析：{STATUS_LABELS[chain.resolution.status] ?? chain.resolution.status}
            </p>
          </div>
          <span className="subtle-tag">{STATUS_LABELS[chain.status] ?? chain.status}</span>
        </div>
        <p className="settings-version">
          {s.routeCount} 个 Nginx location · {chain.nginxHosts.length} 台 Nginx · {s.upstreamCount}{' '}
          个 Upstream · {s.backendCount} 个后端端点：精确 {s.exactBackendCount} / 待确认{' '}
          {s.ambiguousBackendCount} / 未匹配 {s.unresolvedBackendCount}
        </p>
        <p className="settings-hint">
          Topology 版本：{topology.id} · 生成时间：
          {new Date(topology.generatedAt).toLocaleString('zh-CN', {
            timeZone: 'Asia/Shanghai',
            hour12: false,
          })}
        </p>
        {!chain.connected && (
          <p role="alert" className="settings-error">
            入口层（DNS/EIP）与 Nginx 接入层在拓扑中没有连通证据：DNAT/CLB/前置代理证据缺失，
            公网入口到 Nginx 主机这一跳未解析（见 ⑤ 缺口）。
          </p>
        )}
      </section>

      <section className="panel">
        <div className="panel-title">
          <h2>① DNS 解析</h2>
        </div>
        <ol className="flex flex-col gap-2 text-sm">
          <li>
            <code>{chain.query.normalizedDomain}</code> <span>[DOMAIN]</span>
          </li>
          {chain.cnameChain.map((hop) => (
            <li key={hop.edge.id}>
              <span className="subtle-tag">CNAME · {confidenceLabel(hop.edge.confidence)}</span>
              <br />
              <code>{hop.domain.label || hop.domain.id}</code> <span>[DOMAIN]</span>
            </li>
          ))}
          {chain.entries.map((entry) => (
            <li key={entry.resolveEdge.id}>
              <span className="subtle-tag">
                {entry.resolveEdge.type} · {confidenceLabel(entry.resolveEdge.confidence)}
              </span>
              <br />
              <code>{entry.target.label || entry.target.id}</code>{' '}
              <span>[{entry.target.type}]</span>
            </li>
          ))}
          {!chain.entries.length && (
            <li className="settings-hint">未找到 A 记录 / RESOLVES_TO 证据。</li>
          )}
        </ol>
      </section>

      <section className="panel">
        <div className="panel-title">
          <h2>② EIP 入口绑定（NAT 网关 / ECS / CLB）</h2>
        </div>
        {chain.entries.length === 0 && <p className="settings-hint">无入口目标可展开。</p>}
        {chain.entries.map((entry) => (
          <div className="rounded-lg border p-3" key={entry.target.id}>
            <p className="text-sm font-medium">
              <code>{entry.target.label || entry.target.id}</code>{' '}
              <span className="subtle-tag">{entry.targetKind.toUpperCase()}</span>
            </p>
            {!entry.binding && (
              <p className="settings-error mt-2">未找到 BOUND_TO 绑定证据，入口落点未知。</p>
            )}
            {entry.binding?.kind === 'nat-gateway' && (
              <p className="mt-2 text-sm">
                → 绑定 <strong>NAT 网关</strong> <code>{entry.binding.gateway?.id}</code>
              </p>
            )}
            {entry.binding?.kind === 'ecs-host' && (
              <p className="mt-2 text-sm">
                → <strong>直绑 ECS</strong> {chainHostLabel(entry.binding.host)}{' '}
                <span className="subtle-tag">
                  {confidenceLabel(entry.binding.edge.confidence)}
                </span>
              </p>
            )}
            {entry.binding?.kind === 'clb' && (
              <p className="mt-2 text-sm">
                → 绑定 <strong>CLB 负载均衡</strong>{' '}
                <code>{entry.binding.clb?.label || entry.binding.clb?.id}</code>
              </p>
            )}
            {entry.binding?.kind === 'nat-gateway' && entry.dnat.length === 0 && (
              <p className="settings-hint">该 NAT 网关下没有端口级 DNAT 映射证据。</p>
            )}
            {entry.dnat.map((d) => (
              <p className="mt-1 text-sm" key={d.rule.id}>
                DNAT {d.rule.label || d.rule.id}：
                <code>{d.external ? endpointLabel(d.external) : '?'}</code> →{' '}
                <code>{d.internal ? endpointLabel(d.internal) : '?'}</code>
                {d.internalHost ? `（${chainHostLabel(d.internalHost)}）` : ''}
              </p>
            ))}
            {entry.clbBackends.map((b) => (
              <p className="mt-1 text-sm" key={`${b.listener.id}>${b.endpoint.id}`}>
                CLB 监听 <code>{b.listener.label || b.listener.id}</code> · 服务器组{' '}
                <code>{b.group.label || b.group.id}</code> →{' '}
                <code>{endpointLabel(b.endpoint)}</code>
                {b.host ? `（${chainHostLabel(b.host)}）` : ''}
              </p>
            ))}
            {entry.binding?.kind === 'ecs-host' && entry.dnat.length === 0 && (
              <p className="settings-hint">直绑 ECS 且无端口级证据，暴露的服务端口未知。</p>
            )}
          </div>
        ))}
      </section>

      <section className="panel">
        <div className="panel-title">
          <h2>③ Nginx 接入层</h2>
          {chain.nginxHosts.length > 0 && (
            <span className="subtle-tag">{s.routeCount} 个 location</span>
          )}
        </div>
        {chain.nginxHosts.length === 0 && (
          <p className="settings-hint">未发现服务该域名的 Nginx 配置节点（无 SERVED_BY 证据）。</p>
        )}
        {chain.nginxHosts.map((nh) => (
          <div className="rounded-lg border p-3" key={nh.assetId}>
            <p className="text-sm font-medium">
              {nh.host ? chainHostLabel(nh.host) : `未关联主机（asset ${nh.assetId}）`}
            </p>
            <p className="settings-version">
              进程/采集状态：{NGINX_STATUS_LABELS[nh.nginxStatus] ?? nh.nginxStatus} ·{' '}
              {nh.routeCount} 个 location{nh.totalRouteCount > nh.routeCount ? `（资产共 ${nh.totalRouteCount} 个，已按域名过滤）` : ''} · listen {nh.listen.join('、') || '?'} · 配置版本{' '}
              {nh.configurationVersion || '?'}
            </p>
            {nh.context.length > 0 && (
              <p className="settings-hint">配置上下文：{nh.context.join('、')}</p>
            )}
          </div>
        ))}
      </section>

      <section className="panel">
        <div className="panel-title">
          <h2>④ Upstream 后端落点（IP+端口 → DevOps 应用）</h2>
          <span className="subtle-tag">
            {chain.groups.length} 组 · 精确 {s.exactBackendCount} / 未匹配{' '}
            {s.unresolvedBackendCount}
          </span>
        </div>
        <div className="flex flex-col gap-3">
          {chain.groups.map((g) => (
            <ChainGroupBlock key={g.upstream.id} group={g} />
          ))}
        </div>
      </section>

      {chain.gaps.length > 0 && (
        <section className="panel">
          <div className="panel-title">
            <h2>
              <AlertTriangle size={17} /> ⑤ 拓扑缺口 / 提示
            </h2>
          </div>
          <ul className="list-disc pl-5 text-sm">
            {chain.gaps.map((gap) => (
              <li key={gap}>{gap}</li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}

function ChainGroupBlock({ group }: { group: ChainGroup }) {
  const upstreamName =
    (group.upstream.identity as { name?: string } | undefined)?.name || group.upstream.id;
  const shownUris = group.uris.slice(0, 4);
  return (
    <div className="rounded-lg border p-3">
      <p className="text-sm font-medium">
        <span className="subtle-tag">{CHAIN_KIND_LABELS[group.kind] ?? group.kind}</span>{' '}
        <code>{upstreamName}</code>
      </p>
      <p className="settings-hint">
        {group.directives[0] || group.targets[0] || ''} · {group.uriCount} 个 location：
        {shownUris.join('、')}
        {group.uris.length > shownUris.length ? ' …' : ''}
      </p>
      {group.uris.length > shownUris.length && (
        <details className="settings-hint">
          <summary>查看全部 {group.uriCount} 个 location</summary>
          <ul className="mt-1 list-disc pl-5">
            {group.uris.map((u) => (
              <li key={u}>{u}</li>
            ))}
          </ul>
        </details>
      )}
      {group.backends.length === 0 && (
        <p className="settings-hint">无具体 IP 后端：主机名/动态目标或静态指令。</p>
      )}
      <ul className="mt-2 list-disc pl-5 text-sm">
        {group.backends.map((b) => (
          <li key={b.endpoint.id}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '0.5rem' }}>
              <span>
                <code>{endpointLabel(b.endpoint)}</code>{' '}
                {b.host ? <span className="subtle-tag">{chainHostLabel(b.host)}</span> : null}
              </span>
              {b.status === 'unresolved' && (
                <span className="settings-error">未匹配到同 IP+端口的 DevOps 部署</span>
              )}
            </div>
            {b.status === 'exact' &&
              b.matches.map((m) => (
                <span key={m.application.id}>
                  {' '}
                  → ✓ <strong>{m.application.label}</strong>（应用{' '}
                  {(m.application.identity as { devopsAppId?: string } | undefined)?.devopsAppId ??
                    ''}
                  ）[{m.environment}] {confidenceLabel(m.confidence)}
                  {m.protocolMismatch ? '（http/unknown 协议标签已按 IP+端口归一）' : ''}
                </span>
              ))}
            {b.status === 'ambiguous' && (
              <span>
                {' '}
                → ⚠ 同一 IP+端口存在多个部署候选，需人工确认：
                {b.matches.map(
                  (m) =>
                    ` ${m.application.label}(${
                      (m.application.identity as { devopsAppId?: string }).devopsAppId
                    })[${m.environment}]`,
                )}
              </span>
            )}
            {b.status === 'unresolved' && b.sameHostCandidates.length > 0 && (
              <p className="settings-hint">
                同机其他端口（仅供排查，不构成匹配）：
                {b.sameHostCandidates
                  .map(
                    (c) =>
                      `${c.port}=${c.application.label}(${(
                        c.application.identity as { devopsAppId?: string }
                      ).devopsAppId})[${c.environment}]`,
                  )
                  .join('；')}
              </p>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
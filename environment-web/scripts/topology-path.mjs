#!/usr/bin/env node
import { readFile } from 'node:fs/promises';
import { once } from 'node:events';
import path from 'node:path';
import process from 'node:process';
import { NODE_TYPE_BY_KIND, buildGraphIndex, explorePaths } from '../lib/topology/path-explorer.ts';
import { exploreDomainChain } from '../lib/topology/domain-chain.ts';
import { createDefaultResolverRegistry, resolveRequestPathWithIndex } from '../lib/topology/request/index.ts';

// stdout is a pipe whenever this script is driven by the runtime service or any
// other program. console.log is fire-and-forget and process.exit() then drops
// whatever is still buffered, so large JSON payloads arrive truncated: write
// with backpressure and let the process exit on its own.
async function write(stream, text) {
  if (!stream.write(`${text}\n`)) await once(stream, 'drain');
}
const out = text => write(process.stdout, text);
const err = text => write(process.stderr, text);

function usage(message) {
  return message ? { status: 2, lines: [message, '', USAGE] } : { status: 0, lines: [USAGE] };
}

const KINDS = ['domain', 'application', 'host'];
const NODE_TYPES = ['DOMAIN', 'EIP', 'NAT_GATEWAY', 'DNAT_RULE', 'CLB', 'CLB_LISTENER', 'SERVER_GROUP', 'HOST', 'ENDPOINT', 'NGINX_ROUTE', 'UPSTREAM', 'APPLICATION', 'DEPLOYMENT', 'REPOSITORY'];
const ENVIRONMENTS = ['PRODUCT', 'TEST', 'SIMULATION', 'GLOBAL', 'UNKNOWN'];
// The request resolver speaks the query's own environment, which is one of the
// deployment environments; GLOBAL/UNKNOWN are Path-Explorer-only filters.
const REQUEST_ENVIRONMENTS = ['PRODUCT', 'TEST', 'SIMULATION'];

const USAGE = `Usage: npm run topology:path -- <command> [args] [options]

Commands:
  request <url|domain>   request-aware end-to-end resolution
                         (DNS -> NAT/CLB -> nginx -> endpoint -> application ->
                         repository), e.g.
                         npm run topology:path -- request https://api.example.com/order/123

  domain-chain <name>    staged domain landing report (DNS -> EIP -> NAT/ECS/CLB
                         -> nginx host -> upstream backends -> applications)
                         e.g. npm run topology:path -- domain-chain api.example.com

  <kind> <name|ip>       generic path enumeration (all simple paths to the target)
    domain <name>          e.g. npm run topology:path -- domain api.example.com
    application <name|id>  e.g. npm run topology:path -- application order-service
    host <ip|name>         e.g. npm run topology:path -- host 10.179.1.10

Options:
  --to <type[,type]>     target node type(s): ${NODE_TYPES.join(', ')}
                         (default: domain->APPLICATION, application->HOST, host->APPLICATION)
  --env <env>            environment filter: ${ENVIRONMENTS.join(', ')} (GLOBAL edges always stay traversable;
                         request mode accepts ${REQUEST_ENVIRONMENTS.join(', ')})
  --max-depth <n>        maximum edges per path (default 8)
  --max-paths <n>        maximum paths to enumerate (default 50)
  --file <path>          topology JSON file (default: the active version in $ENVSCOPE_DATA,
                         otherwise outputs/topology/topology.json)
  --json                 print the full machine-readable result
  --help                 show this message`;

function parseArgs(argv) {
  const positional = [];
  const options = { to: [], json: false, help: false };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--json') options.json = true;
    else if (arg === '--help' || arg === '-h') options.help = true;
    else if (arg === '--to') options.to.push(...String(argv[++i] ?? '').split(',').map(item => item.trim()).filter(Boolean));
    else if (arg === '--env') options.env = String(argv[++i] ?? '').trim().toUpperCase();
    else if (arg === '--max-depth') options.maxDepth = Number(argv[++i]);
    else if (arg === '--max-paths') options.maxPaths = Number(argv[++i]);
    else if (arg === '--file') options.file = String(argv[++i] ?? '');
    else if (arg.startsWith('-')) throw usage(`Unknown option: ${arg}`);
    else positional.push(arg);
  }
  return { positional, options };
}

// Inside the container the topology lives in the data volume, not in the repo:
// follow the same activation pointer the runtime service uses so a query can
// never silently answer from a stale checked-in snapshot.
async function resolveTopologyFile(explicit) {
  if (explicit) return explicit;
  const data = process.env.ENVSCOPE_DATA;
  if (!data) return path.join('outputs', 'topology', 'topology.json');
  const latest = path.join(data, 'topology', 'latest.json');
  try {
    return path.join(data, 'topology', String(JSON.parse(await readFile(latest, 'utf8')).id), 'topology.json');
  } catch (error) {
    throw usage(`Cannot read ${latest}: ${error.message}. Generate a Topology first or pass --file.`);
  }
}

async function main() {
  const { positional, options } = parseArgs(process.argv.slice(2));
  if (options.help) {
    for (const line of usage().lines) await out(line);
    return 0;
  }
  const chainMode = positional[0] === 'domain-chain';
  const requestMode = positional[0] === 'request';
  if (chainMode && positional.length < 2) throw usage('domain-chain requires a domain name.');
  if (requestMode && positional.length < 2) throw usage('request requires a URL or domain name.');
  if (!chainMode && !requestMode && positional.length < 2) throw usage('Expected a kind and a name/IP query.');
  const [rawKind, ...queryParts] = chainMode || requestMode ? positional.slice(1) : positional;
  const kind = chainMode ? 'domain' : rawKind.toLowerCase();
  if (!chainMode && !requestMode && !KINDS.includes(rawKind.toLowerCase())) throw usage(`Unsupported kind "${rawKind}". Expected one of: ${KINDS.join(', ')}`);
  if (requestMode && options.to.length > 0) throw usage('request mode walks the full resolver chain; --to is not applicable.');
  const query = chainMode || requestMode ? positional.slice(1).join(' ') : queryParts.join(' ');
  const file = await resolveTopologyFile(options.file);

  if (options.env && !ENVIRONMENTS.includes(options.env)) throw usage(`Unsupported environment "${options.env}".`);
  if (requestMode && options.env && !REQUEST_ENVIRONMENTS.includes(options.env)) {
    throw usage(`request mode accepts environments: ${REQUEST_ENVIRONMENTS.join(', ')}.`);
  }
  const targets = options.to.map(item => item.toUpperCase());
  for (const target of targets) if (!NODE_TYPES.includes(target)) throw usage(`Unsupported target node type "${target}".`);
  if (!chainMode && !requestMode && targets.includes(NODE_TYPE_BY_KIND[kind])) throw usage(`Target type ${NODE_TYPE_BY_KIND[kind]} equals the entry kind ${kind}; nothing could be traversed.`);

  let topology;
  try {
    topology = JSON.parse(await readFile(file, 'utf8'));
  } catch (error) {
    throw usage(`Cannot read topology file ${file}: ${error.message}. Run "npm run topology" first or pass --file.`);
  }

  const index = buildGraphIndex(topology);
  if (requestMode) {
    let trace;
    try {
      trace = resolveRequestPathWithIndex(index, query, {
        registry: createDefaultResolverRegistry(),
        environment: options.env || undefined,
        maxDepth: Number.isFinite(options.maxDepth) ? options.maxDepth : undefined,
        maxPaths: Number.isFinite(options.maxPaths) ? options.maxPaths : undefined,
      });
    } catch (error) {
      throw usage(`Invalid request query "${query}": ${error.message}`);
    }
    if (options.json) {
      await out(JSON.stringify(trace, null, 2));
      return 0;
    }
    for (const line of renderRequestTrace(trace)) await out(line);
    return 0;
  }
  if (chainMode) {
    const chain = exploreDomainChain(index, { domain: query, environment: options.env });
    if (options.json) {
      await out(JSON.stringify(chain, null, 2));
      return 0;
    }
    for (const line of renderDomainChain(chain)) await out(line);
    return 0;
  }
  const result = explorePaths(index, {
    kind,
    query,
    to: targets.length ? targets : undefined,
    environment: options.env,
    maxDepth: Number.isFinite(options.maxDepth) ? options.maxDepth : undefined,
    maxPaths: Number.isFinite(options.maxPaths) ? options.maxPaths : undefined,
  });

  if (options.json) {
    await out(JSON.stringify(result, null, 2));
    return 0;
  }

  const { query: echo, resolution } = result;
  await out(`Query      : ${echo.kind.toUpperCase()} "${echo.query}" -> ${echo.to.join('/')}${echo.environment ? ` (env=${echo.environment})` : ''}`);
  await out(`Resolution : ${resolution.status} (${resolution.candidates.length} candidate${resolution.candidates.length === 1 ? '' : 's'}${resolution.candidates.length ? `: ${resolution.candidates.map(node => node.id).join(', ')}` : ''})`);
  await out(`Status     : ${result.status}`);
  await out(`Paths      : ${result.paths.length}`);
  await out('');

  for (const [position, found] of result.paths.entries()) {
    await out(`Path ${position + 1}: ${found.length} edge(s), confidence ${found.confidence}${found.unresolved ? ', UNRESOLVED' : ''}, environments ${found.environments.join('/')}`);
    await out(`  ${found.nodes[0].id} [${found.nodes[0].type}]`);
    for (const step of found.steps) {
      const arrow = step.direction === 'forward' ? '->' : '<-';
      await out(`  ${arrow} ${step.edge.type} (${step.edge.confidence}, ${step.edge.environment}, ${step.edge.evidence.length} evidence) ${step.to.id} [${step.to.type}]`);
    }
    await out('');
  }

  if (result.gaps.length) {
    await out('Gaps:');
    for (const gap of result.gaps) await out(`  - ${gap}`);
  }
  if (result.reachableTypes.length) await out(`Reachable node types: ${result.reachableTypes.join(', ')}`);
  return 0;
}

const CHAIN_STATUS_LABEL = { resolved: '已解析', ambiguous: '待确认', unresolved: '未解析' };
const CONFIDENCE_LABEL = { EXACT: '确认', INFERRED: '推断', AMBIGUOUS: '待确认', UNKNOWN: '未知' };
const KIND_LABEL = { proxy: '代理', external: '外部目标', static: '静态/其他' };

const REQUEST_STATUS_LABEL = { RESOLVED: '已解析', PARTIAL: '部分解析', AMBIGUOUS: '存在多条解释', UNRESOLVED: '未解析' };
const STOP_REASON_LABEL = {
  'no-resolver-entry': '没有 Resolver 可以继续',
  'no-candidate': '没有候选目标',
  'cycle-guard': '环路保护',
  'max-depth': '达到最大深度',
  'max-paths': '达到最大路径数',
};

/**
 * Short human-readable label for a topology node, used in stop-reason lines.
 */
function nodeBrief(node) {
  if (!node) return '?';
  switch (node.type) {
    case 'DOMAIN': return node.identity?.name ?? node.id;
    case 'EIP': return `EIP ${node.identity?.ip ?? '?'}`;
    case 'ENDPOINT': return `${node.identity?.ip ?? '?'}:${node.identity?.port ?? '?'}`;
    case 'HOST': return node.attributes?.jumpserver?.hostname ?? node.identity?.ip ?? node.id;
    case 'CLB': return node.label ?? node.id;
    case 'NGINX_ROUTE': return `route ${node.identity?.domains?.join(',') ?? ''} ${node.identity?.uri ?? '/'}`;
    case 'UPSTREAM': return `upstream ${node.identity?.name ?? '?'}`;
    case 'APPLICATION': return node.label ?? node.id;
    default: return node.label ?? node.id;
  }
}

/**
 * Concise 5-layer linear "landing" report for a request-aware trace.
 *
 * Instead of dumping every hop with resolver/rule/evidence noise, the primary
 * path (furthest progress, then highest confidence) is grouped into the five
 * layers the operator cares about:
 *
 *   ① DNS 解析         domain → IP (EIP / 内网 IP)
 *   ② 入口绑定 (NAT)    EIP → DNAT 端口映射 → 内网端点
 *   ③ CLB 端口转发      CLB listener → server group → backend IP:port
 *   ④ Nginx 转发规则    host → server_name + location → proxy_pass target
 *   ⑤ 后端落点          backend IP:port → application / repository
 *
 * Unresolved paths surface a stop-reason line; key warnings (minus evidence
 * truncation noise) are listed at the bottom.
 */
function renderRequestTrace(trace) {
  const lines = [];
  const q = trace.query;
  const input = q.raw || `${q.scheme}://${q.host}:${q.port}${q.path}`;

  lines.push(`请求链路: ${input}`);

  if (!trace.paths.length) {
    lines.push(`状态: ${REQUEST_STATUS_LABEL[trace.status] ?? trace.status}`);
    lines.push('');
    lines.push('（没有解析路径）');
    return lines;
  }

  // Collect all nodes from all paths for cross-path lookup (e.g. showing all
  // matched nginx routes even when the primary path only follows one branch).
  const nodeMap = new Map();
  for (const path of trace.paths) {
    for (const node of path.nodes) {
      if (!nodeMap.has(node.id)) nodeMap.set(node.id, node);
    }
  }

  // Primary path: most steps (furthest progress), then highest confidence.
  const primary = [...trace.paths].sort((a, b) => {
    if (b.steps.length !== a.steps.length) return b.steps.length - a.steps.length;
    const rank = { EXACT: 3, INFERRED: 2, AMBIGUOUS: 1, UNKNOWN: 0 };
    return rank[b.confidence] - rank[a.confidence];
  })[0];

  const statusLabel = REQUEST_STATUS_LABEL[trace.status] ?? trace.status;
  const stopNode = !primary.terminalNodeId && primary.stoppedAt ? nodeMap.get(primary.stoppedAt) : null;
  if (stopNode) {
    lines.push(`状态: ${statusLabel} · 停止于 ${nodeBrief(stopNode)}`);
  } else {
    lines.push(`状态: ${statusLabel}`);
  }
  if (trace.paths.length > 1) {
    lines.push(`       ${trace.paths.length} 条路径，已取最远路径`);
  }
  lines.push('');

  const steps = primary.steps;
  const find = predicate => steps.find(predicate);
  const nodeById = id => nodeMap.get(id);
  const out0 = step => nodeById(step.outputNodeIds[0]);
  const in0 = step => nodeById(step.inputNodeIds[0]);

  // ① DNS 解析
  const dnsEntry = find(s => s.resolver === 'DNSResolver' && s.rule === 'dns:entry');
  const dnsRecord = find(s => s.resolver === 'DNSResolver' && s.rule === 'dns:records');
  if (dnsEntry || dnsRecord) {
    lines.push('① DNS 解析');
    const domainName = dnsEntry ? out0(dnsEntry)?.identity?.name : q.host;
    lines.push(`  ${domainName ?? q.host}`);
    if (dnsRecord) {
      const target = out0(dnsRecord);
      if (target) {
        lines.push(`  → ${target.identity?.ip ?? '?'} [${target.type}] (${CONFIDENCE_LABEL[dnsRecord.confidence] ?? dnsRecord.confidence})`);
      }
    }
    lines.push('');
  }

  // ② 入口绑定 (NAT)
  const natExt = find(s => s.resolver === 'NatResolver' && s.rule === 'nat:external-endpoint');
  const natDnat = find(s => s.resolver === 'NatResolver' && s.rule === 'nat:dnat');
  if (natExt || natDnat) {
    lines.push('② 入口绑定 (NAT)');
    if (natExt) {
      const eip = in0(natExt);
      const ep = out0(natExt);
      lines.push(`  EIP ${eip?.identity?.ip ?? '?'} → ${ep?.identity?.ip ?? '?'}:${ep?.identity?.port ?? '?'}`);
    }
    if (natDnat) {
      const from = in0(natDnat);
      const to = out0(natDnat);
      lines.push(`  DNAT ${from?.identity?.ip ?? '?'}:${from?.identity?.port ?? '?'} → ${to?.identity?.ip ?? '?'}:${to?.identity?.port ?? '?'}`);
    }
    lines.push('');
  }

  // ③ CLB 端口转发
  const clbSteps = steps.filter(s => s.resolver === 'ClbResolver');
  if (clbSteps.length) {
    lines.push('③ CLB 端口转发');
    const clbStep = clbSteps.find(s => s.rule === 'clb:endpoint-ip');
    const listenerStep = clbSteps.find(s => s.rule === 'clb:listener');
    const groupStep = clbSteps.find(s => s.rule === 'clb:default-group' || s.rule === 'clb:rule');
    const backendStep = clbSteps.find(s => s.rule === 'clb:backend');
    if (clbStep) {
      const clb = out0(clbStep);
      lines.push(`  CLB ${clb?.label ?? clb?.id ?? '?'}`);
    }
    if (listenerStep) {
      const lis = out0(listenerStep);
      lines.push(`  listener ${lis?.identity?.protocol ?? '?'}:${lis?.identity?.port ?? '?'}`);
    }
    if (groupStep) {
      const grp = out0(groupStep);
      const isDefault = groupStep.rule === 'clb:default-group';
      lines.push(`  → ${grp?.identity?.serverGroupId ?? grp?.label ?? '?'}${isDefault ? ' (默认服务器组)' : ''}`);
    }
    if (backendStep) {
      const be = out0(backendStep);
      const port = be?.identity?.port;
      lines.push(`  → ${be?.identity?.ip ?? '?'}${port && port !== 'unknown' ? `:${port}` : ''}`);
    }
    const urlWarning = groupStep?.warnings?.find(w => w.includes('No rule on listener') || w.includes('falling back'));
    if (urlWarning) lines.push(`  ⚠ ${urlWarning}`);
    lines.push('');
  }

  // ④ Nginx 转发规则
  const nginxSteps = steps.filter(s => s.resolver === 'NginxResolver');
  const nginxHostStep = nginxSteps.find(s => s.rule === 'nginx:host');
  const nginxRouteSteps = nginxSteps.filter(s => s.rule.startsWith('nginx:route'));
  const nginxUpstreamStep = nginxSteps.find(s => s.rule === 'nginx:upstream');
  if (nginxHostStep || nginxRouteSteps.length) {
    lines.push('④ Nginx 转发规则');
    if (nginxHostStep) {
      const host = out0(nginxHostStep);
      const hostname = host?.attributes?.jumpserver?.hostname ?? host?.identity?.ip ?? '?';
      lines.push(`  主机 ${hostname} (${CONFIDENCE_LABEL[nginxHostStep.confidence] ?? nginxHostStep.confidence})`);
    }
    if (nginxRouteSteps.length) {
      const routeStep = nginxRouteSteps[0];
      // Show ALL matched routes from the step's outputNodeIds (the primary
      // path only carries the branch it followed, but the step records every
      // candidate that was kept).
      for (const routeId of routeStep.outputNodeIds) {
        const route = nodeById(routeId);
        if (!route) continue;
        const domains = route.identity?.domains ?? [];
        const uri = route.identity?.uri ?? '/';
        const target = route.identity?.target ?? '';
        const targetLabel = target ? `→  ${target}` : '(无 proxy_pass)';
        lines.push(`  server_name ${domains.join(', ') || '(默认)'}  location ${uri}  ${targetLabel}`);
      }
      if (routeStep.outputNodeIds.length > 1) {
        lines.push(`  (${routeStep.outputNodeIds.length} 条 route 匹配，${CONFIDENCE_LABEL[routeStep.confidence] ?? routeStep.confidence})`);
      }
    }
    if (nginxUpstreamStep) {
      const up = out0(nginxUpstreamStep);
      lines.push(`  upstream ${up?.identity?.name ?? '?'}`);
    }
    const portWarn = nginxRouteSteps.flatMap(s => s.warnings).find(w => w.includes('port-agnostic') || w.includes('falling back to port'));
    if (portWarn) lines.push(`  ⚠ ${portWarn}`);
    lines.push('');
  }

  // ⑤ 后端落点
  const nginxBackendStep = nginxSteps.find(s => s.rule === 'nginx:backend' || s.rule === 'nginx:backend-derived');
  const deploySteps = steps.filter(s => s.resolver === 'DeploymentResolver');
  const repoStep = steps.find(s => s.resolver === 'RepositoryResolver');
  if (nginxBackendStep || deploySteps.length) {
    lines.push('⑤ 后端落点');
    if (nginxBackendStep) {
      const be = out0(nginxBackendStep);
      lines.push(`  → ${be?.identity?.ip ?? '?'}:${be?.identity?.port ?? '?'}`);
    }
    for (const step of deploySteps) {
      for (const id of step.outputNodeIds) {
        const node = nodeById(id);
        if (node?.type === 'APPLICATION') {
          lines.push(`  → 应用 ${node.label} (${node.identity?.devopsAppId ?? '?'})`);
        }
      }
    }
    if (repoStep) {
      const repo = out0(repoStep);
      lines.push(`  → 仓库 ${repo?.label ?? repo?.identity?.url ?? '?'}`);
    }
    lines.push('');
  }

  // Stop reason
  if (!primary.terminalNodeId && primary.stoppedAt) {
    const reason = STOP_REASON_LABEL[primary.reason] ?? primary.reason ?? '未知原因';
    lines.push(`停止原因: ${reason}`);
    lines.push('');
  }

  // Key warnings: only from the primary path, skip evidence truncation noise
  // and warnings already surfaced inline in the layers above.
  const skipPatterns = [
    'evidence truncated',
    'Step evidence truncated',
    'No rule on listener',           // shown in ③
    'falling back to the default server group',
    'No nginx route on host',        // shown in ④
    'falling back to port-agnostic',
    'equally match host',            // shown in ④
    'Resolver(s)',                   // too technical, stop reason covers it
  ];
  const seenWarn = new Set();
  const keyWarnings = [];
  for (const w of primary.warnings) {
    if (seenWarn.has(w)) continue;
    seenWarn.add(w);
    if (skipPatterns.some(p => w.includes(p))) continue;
    keyWarnings.push(w);
  }
  if (keyWarnings.length) {
    lines.push('提示:');
    for (const w of keyWarnings) lines.push(`  - ${w}`);
  }

  return lines;
}

function hostName(host) {
  if (!host) return '';
  const ecs = host.attributes?.ecs;
  return ecs?.name ? `${ecs.name} (${ecs.id})` : host.attributes?.jumpserver?.hostname || '';
}

function endpointText(node) {
  return node ? `${node.identity.ip}:${node.identity.port}/${node.identity.protocol}` : '?';
}

function renderDomainChain(chain) {
  const lines = [];
  const q = chain.query;
  const s = chain.stats;
  lines.push(`查询  : 域名落点链路 "${q.normalizedDomain}"${q.environment ? ` (env=${q.environment})` : ''}`);
  lines.push(`状态  : ${CHAIN_STATUS_LABEL[chain.status] ?? chain.status} · ${s.exactBackendCount}/${s.backendCount} 个后端端点精确匹配到应用 · ${s.applicationIds.length} 个应用 · ${chain.gaps.length} 个缺口/提示`);
  lines.push('');

  lines.push(`① DNS 解析${chain.cnameChain.length ? `（CNAME ${chain.cnameChain.length} 跳）` : ''}`);
  for (const hop of chain.cnameChain) {
    lines.push(`  CNAME ${hop.edge.confidence} -> ${hop.domain.id}${hop.domain.status === 'external' || hop.domain.status === 'unresolved' ? ` [${hop.domain.status}]` : ''}`);
  }
  if (!chain.entries.length) {
    lines.push('  （拓扑中没有该域名的 A/AAAA RESOLVES_TO 记录）');
  }
  for (const entry of chain.entries) {
    const ip = entry.target.identity.ip;
    lines.push(`  A     ${ip} [${entry.targetKind.toUpperCase()}] (${CONFIDENCE_LABEL[entry.resolveEdge.confidence] ?? entry.resolveEdge.confidence})`);
  }

  lines.push('');
  lines.push('② EIP 入口绑定（NAT 网关 / ECS / CLB）');
  if (!chain.entries.length) lines.push('  （无）');
  for (const entry of chain.entries) {
    const ip = entry.target.identity.ip;
    if (entry.targetKind === 'host') {
      lines.push(`  ${ip} 直接解析到主机 ${entry.target.id}${hostName(entry.target) ? ` · ${hostName(entry.target)}` : ''}`);
      continue;
    }
    if (entry.targetKind === 'clb') {
      lines.push(`  ${ip} [CLB] ${entry.target.id}`);
      for (const backend of entry.clbBackends) {
        lines.push(`    listener :${backend.listener.identity.port ?? ''}/${backend.listener.identity.protocol ?? ''} -> ${backend.group.id} -> ${endpointText(backend.endpoint)}${backend.host ? ` (${backend.host.id})` : ''}`);
      }
      continue;
    }
    if (!entry.binding) {
      lines.push(`  EIP ${ip} 未绑定到任何 NAT 网关 / CLB / ECS`);
      continue;
    }
    const b = entry.binding;
    if (b.kind === 'nat-gateway') {
      lines.push(`  EIP ${ip} -> NAT 网关 ${b.gateway.id} (${CONFIDENCE_LABEL[b.edge.confidence] ?? b.edge.confidence})`);
      if (!entry.dnat.length) lines.push('    （该 NAT 网关上没有暴露此 EIP 的 DNAT 规则，内网 IP:端口未知）');
      for (const rule of entry.dnat) {
        lines.push(`    DNAT ${endpointText(rule.external)} -> ${endpointText(rule.internal)}${rule.internalHost ? ` (${rule.internalHost.id})` : ''}`);
      }
    } else if (b.kind === 'clb') {
      lines.push(`  EIP ${ip} -> CLB ${b.clb.id} (${CONFIDENCE_LABEL[b.edge.confidence] ?? b.edge.confidence})`);
      for (const backend of entry.clbBackends) {
        lines.push(`    listener -> ${backend.group.id} -> ${endpointText(backend.endpoint)}${backend.host ? ` (${backend.host.id})` : ''}`);
      }
    } else {
      lines.push(`  EIP ${ip} -> 直绑 ECS ${b.host.id}${hostName(b.host) ? ` · ${hostName(b.host)}` : ''} (${CONFIDENCE_LABEL[b.edge.confidence] ?? b.edge.confidence})`);
      lines.push('    （无端口级 DNAT 映射，暴露的服务端口缺少证据）');
    }
  }

  lines.push('');
  lines.push(`③ Nginx 接入层（${s.routeCount} 个 location，${chain.nginxHosts.length} 台采集到配置的 Nginx；入口层与 Nginx 层${chain.connected ? '有图证据连通' : '未连通'}）`);
  for (const host of chain.nginxHosts) {
    const name = hostName(host.host);
    lines.push(`  ${host.host ? host.host.id : `(资产 ${host.assetId} 不在主机清单)`}${name ? ` · ${name}` : ''}`);
    lines.push(`    nginx=${host.nginxStatus} · routes=${host.routeCount}${host.totalRouteCount ? `（资产共 ${host.totalRouteCount} 个，已按域名过滤）` : ''} · listen=${host.listen.join(', ') || '?'}${host.configurationVersion ? ` · config=${host.configurationVersion}` : ''}`);
  }

  lines.push('');
  const proxy = chain.groups.filter(group => group.kind === 'proxy').length;
  const external = chain.groups.filter(group => group.kind === 'external').length;
  const staticGroups = chain.groups.filter(group => group.kind === 'static').length;
  lines.push(`④ Upstream 后端落点（${s.upstreamCount} 组：代理 ${proxy} / 外部目标 ${external} / 静态 ${staticGroups}；${s.backendCount} 个端点：精确 ${s.exactBackendCount} / 待确认 ${s.ambiguousBackendCount} / 未匹配 ${s.unresolvedBackendCount}）`);
  chain.groups.forEach((group, index) => {
    const name = group.upstream.identity.name || group.upstream.id;
    const directive = group.directives.join(',');
    const target = group.targets[0] || '';
    lines.push(`  [${index + 1}] ${name} [${KIND_LABEL[group.kind]}] ${directive}${target ? ` ${target}` : ''} · ${group.uriCount} location${group.uriCount > 1 ? 's' : ''}${group.uriCount ? `: ${group.uris.slice(0, 4).join(', ')}${group.uriCount > 4 ? ' …' : ''}` : ''}`);
    for (const backend of group.backends) {
      const ep = backend.endpoint.identity;
      const base = `      -> ${ep.ip}:${ep.port}`;
      if (backend.status === 'unresolved') {
        lines.push(`${base}  => 未匹配到 DevOps 部署`);
        if (backend.sameHostCandidates.length) {
          lines.push(`         同机其他端口（仅供排查，不构成匹配）: ${backend.sameHostCandidates.map(candidate => `${candidate.port}=${candidate.application.label}(${candidate.application.identity.devopsAppId})[${candidate.environment}]`).join('; ')}`);
        }
      } else {
        for (const match of backend.matches) {
          const tag = backend.status === 'exact' ? '✓' : '?';
          const proto = match.protocolMismatch ? `（协议标签 ${backend.endpoint.identity.protocol}/${match.listenerEndpoint.identity.protocol} 已按 IP+端口归一）` : '';
          lines.push(`${base}  => ${tag} ${match.application.label} (应用 ${match.application.identity.devopsAppId}) [${match.environment}] (${CONFIDENCE_LABEL[match.confidence] ?? match.confidence})${proto}`);
        }
      }
    }
    if (group.kind !== 'proxy') lines.push('      （无具体 IP 后端：主机名/动态目标或静态指令）');
  });

  if (chain.gaps.length) {
    lines.push('');
    lines.push('⑤ 拓扑缺口 / 提示');
    for (const gap of chain.gaps) lines.push(`  - ${gap}`);
  }
  return lines;
}

try {
  process.exitCode = await main();
} catch (error) {
  if (error && Array.isArray(error.lines)) {
    for (const line of error.lines) await err(line);
    process.exitCode = error.status;
  } else {
    await err(`Unexpected failure: ${error?.message ?? error}`);
    process.exitCode = 1;
  }
}
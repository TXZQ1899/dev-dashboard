#!/usr/bin/env node
import { readFile } from 'node:fs/promises';
import { once } from 'node:events';
import path from 'node:path';
import process from 'node:process';
import { NODE_TYPE_BY_KIND, buildGraphIndex, explorePaths } from '../lib/topology/path-explorer.ts';
import { exploreDomainChain } from '../lib/topology/domain-chain.ts';

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

const USAGE = `Usage: npm run topology:path -- <command> [args] [options]

Commands:
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
  --env <env>            environment filter: ${ENVIRONMENTS.join(', ')} (GLOBAL edges always stay traversable)
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
  if (chainMode && positional.length < 2) throw usage('domain-chain requires a domain name.');
  if (!chainMode && positional.length < 2) throw usage('Expected a kind and a name/IP query.');
  const [rawKind, ...queryParts] = chainMode ? positional.slice(1) : positional;
  const kind = chainMode ? 'domain' : rawKind.toLowerCase();
  if (!chainMode && !KINDS.includes(rawKind.toLowerCase())) throw usage(`Unsupported kind "${rawKind}". Expected one of: ${KINDS.join(', ')}`);
  const query = chainMode ? positional.slice(1).join(' ') : queryParts.join(' ');
  const file = await resolveTopologyFile(options.file);

  if (options.env && !ENVIRONMENTS.includes(options.env)) throw usage(`Unsupported environment "${options.env}".`);
  const targets = options.to.map(item => item.toUpperCase());
  for (const target of targets) if (!NODE_TYPES.includes(target)) throw usage(`Unsupported target node type "${target}".`);
  if (!chainMode && targets.includes(NODE_TYPE_BY_KIND[kind])) throw usage(`Target type ${NODE_TYPE_BY_KIND[kind]} equals the entry kind ${kind}; nothing could be traversed.`);

  let topology;
  try {
    topology = JSON.parse(await readFile(file, 'utf8'));
  } catch (error) {
    throw usage(`Cannot read topology file ${file}: ${error.message}. Run "npm run topology" first or pass --file.`);
  }

  const index = buildGraphIndex(topology);
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
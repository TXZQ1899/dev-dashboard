import { isIP } from 'node:net';
import { normalizeDomain, normalizeIp, sha256Short } from './topology/common.ts';
import { buildGraphIndex } from './topology/path-explorer.ts';
import { ClbResolver, DnsResolver, NatResolver, NginxResolver, ResolverRegistry, matchDnatRules,
  parseListenPort, resolveEndpointNodes, resolveRequestPathWithIndex } from './topology/request/index.ts';
import { KONG_GATEWAYS, KONG_NO_ROUTE, KONG_NO_TARGET, KongResolver, loadKongExportData } from './topology/request/kong.ts';
import { createAssetInventory, createEndpointProcessMatcher, frontendPath, frontendRow, isInternalApplicationLocation, loopbackHost } from './nginx-export.mjs';

export const backendHeaders = [
  'Nginx服务器IP', '主机名', '域名', 'Listen端口', 'URI', '后端URL', '服务IP+端口', '应用匹配说明', '应用ID', '应用名',
  'Upstream名', 'proxy_pass目标', 'Nginx转发后端IP', 'Nginx转发后端端口', '解析方式',
  '经Kong转发', 'Kong Service名', 'Kong Service目标', 'Kong最终Target', 'Kong路由路径', 'Kong Admin',
  'DNS解析状态', 'DNS解析IP', 'CNAME链', 'EIP绑定关系', 'ECS实例', 'CLB/ALB', 'NAT网关',
  'Hostname链最终后端IP', 'Hostname链最终后端端口', '完整代理链', '链路状态', '链路置信度',
  '应用匹配置信度', '进程监听证据', '停止原因与说明', '来源快照', '配置文件', 'Nginx实例', 'Kong查询时间',
];

const gateways = new Map(KONG_GATEWAYS.map(gateway => [
  `${gateway.ip}:${gateway.proxyPort}`, gateway,
]));
const DNS_NO_RECORD = 'dns:no-record';
const NGINX_NON_PROXY_LOCATION = 'nginx:non-proxy-location';
const EIP_BOUND_ECS_MISSING = 'eip:bound-ecs-missing';
const CLB_LISTENER_PORT_MISSING = 'clb:listener-port-missing';
const REQUEST_URI_NEEDED = 'request:uri-needed';
const KONG_MANAGEMENT = 'kong:management';
const KONG_INTERNAL = 'Kong内部或管理流量';
const OFFLINE_ASSET = '资产已下线';
const INVALID_PROXY_BACKEND = 'Proxy Pass后端配置失效';
const stopLabels = new Map([
  [DNS_NO_RECORD, 'DNS待核实'],
  [KONG_NO_TARGET, 'Kong网关无Target'],
  [KONG_NO_ROUTE, 'Kong网关无此路由'],
  [CLB_LISTENER_PORT_MISSING, INVALID_PROXY_BACKEND],
  [KONG_MANAGEMENT, KONG_INTERNAL],
  [REQUEST_URI_NEEDED, '请求URI待核实'],
  ['nginx:no-routes', '下游Nginx配置采集不足'],
  ['nginx:no-port', '下游Nginx监听配置待核实'],
  ['nginx:no-server', '下游Host未匹配'],
  ['nginx:regex', 'Nginx正则或重写待核实'],
  ['nginx:request-path', '请求URI待核实'],
]);
const fail = reason => ({ rule: 'export:unresolved', confidence: 'UNKNOWN', moves: [], warnings: reason ? [reason] : [] });
const missingDnsWarning = host => `当前快照没有可用于此链路的 ${host} DNS 地址或 CNAME 记录`;
const ipOf = node => normalizeIp(node?.identity.ip);
const validPort = value => Number.isInteger(Number(value)) && Number(value) > 0 && Number(value) <= 65535;
const endpointLabel = (ip, port) => `${isIP(ip) === 6 ? `[${ip}]` : ip}:${port}`;
const routeUri = uri => {
  const path = String(uri ?? '').trim().replace(/^(?:=|\^~)\s*/, '');
  return path.startsWith('/') && !/[\\*?\[\](){}$#]/.test(path) ? path : undefined;
};
const concreteHost = value => /^(?:[a-z\d](?:[a-z\d.-]*[a-z\d])?\.?|\[[a-f\d:]+\])$/i.test(value) ? normalizeDomain(value) : '';
const splitIps = asset => String(asset.ip ?? '').split(/[\s,]+/).map(normalizeIp).filter(isIP);
const isStaticRoot = route => route.context === 'http' && route.uri === '/' && route.directive === 'static/other';
const fallbackKey = (routeId, requestPath) => JSON.stringify([routeId, requestPath === '/']);
const staticLocationKey = (endpointId, query) => JSON.stringify([endpointId, query.host, query.path]);
const requestHintKey = (asset, route) => JSON.stringify([asset.id, route.instance, route.file, route.listen, route.domains, route.uri, route.target]);
const isKongInternal = (asset, route) => route.target === '$upstream_scheme://kong_upstream$upstream_uri' &&
  splitIps(asset).some(ip => KONG_GATEWAYS.some(gateway => gateway.ip === ip));
const kongAdminEndpoints = new Set(KONG_GATEWAYS.map(gateway => endpointLabel(gateway.ip, gateway.adminPort)));
const clbScopeKey = (tenantId, region) => JSON.stringify([tenantId || '', region || '']);

function completeClbListeners(snapshot) {
  const byId = new Map();
  if (snapshot?.available !== true || snapshot.complete !== true || snapshot.partial === true) return byId;
  const scopes = new Set((snapshot.tenants ?? []).filter(slice => slice.complete === true).map(slice =>
    clbScopeKey(slice.tenantId, slice.region)));
  const scoped = snapshot.schemaVersion === 2 || Boolean(snapshot.tenants?.length);
  for (const instance of snapshot.instances ?? []) {
    const scope = clbScopeKey(instance.tenantId, instance.region || snapshot.region);
    if (scoped && !scopes.has(scope)) continue;
    if (!Array.isArray(instance.listeners) || !instance.listeners.every(listener => validPort(listener.port))) continue;
    byId.set(JSON.stringify([instance.id, scope]), new Set(instance.listeners.map(listener => Number(listener.port))));
  }
  return byId;
}

function retiredEipBindings(index, jumpserver, eip, ecs) {
  const retired = new Map();
  if (!eip?.region || !Array.isArray(eip.records) || ecs?.available === false || !Array.isArray(ecs?.instances)) return retired;

  const instances = new Map(ecs.instances.map(instance => [instance.id, instance]));
  const scoped = ecs.schemaVersion === 2 || Boolean(ecs.tenants?.length);
  if (!scoped && (ecs.complete !== true || ecs.region !== eip.region)) return retired;
  const regionSlices = (ecs.tenants ?? []).filter(slice => slice.region === eip.region);
  const completeTenants = new Set(regionSlices.filter(slice => slice.tenantId && slice.complete === true).map(slice => slice.tenantId));

  // Existing instance IDs from the same historical CSV establish which ECS
  // inventory can be used to check the missing bindings from that source.
  const bySource = new Map();
  const addressCounts = new Map();
  const ecsBindings = eip.records.filter(record => /^(?:ecs(?:\s*实例|\s+instance)?|云服务器\s*ecs)$/i.test(String(record.bindingType).trim()) &&
    /^i-[a-z0-9]+$/i.test(record.bindingId));
  for (const record of eip.records) {
    const ip = normalizeIp(record.ip);
    addressCounts.set(ip, (addressCounts.get(ip) ?? 0) + 1);
  }
  for (const record of ecsBindings) {
    const instance = instances.get(record.bindingId);
    if (!record.source || !instance) continue;
    const covered = scoped ? instance.region === eip.region && completeTenants.has(instance.tenantId) : true;
    bySource.set(record.source, covered && (bySource.get(record.source) ?? true));
  }

  const claimedIps = new Set(ecs.instances.flatMap(instance =>
    [...(instance.privateIps ?? []), ...(instance.publicIps ?? [])].map(normalizeIp)));
  for (const asset of jumpserver.assets ?? []) {
    if (asset.inspection?.loginStatus === 'can_login') for (const ip of splitIps(asset)) claimedIps.add(ip);
  }
  for (const node of index.topology.nodes) {
    if (node.type === 'CLB') claimedIps.add(ipOf(node));
    if (node.type === 'DNAT_RULE') claimedIps.add(normalizeIp(node.identity.externalIp));
  }

  for (const record of ecsBindings) {
    const ip = normalizeIp(record.ip);
    if (!isIP(ip) || addressCounts.get(ip) !== 1 || instances.has(record.bindingId) ||
        claimedIps.has(ip) || !bySource.get(record.source)) continue;
    retired.set(ip, `EIP ${ip} 的历史绑定 ECS ${record.bindingId} 未出现在当前快照对应地域的完整 ECS 清单中`);
  }
  return retired;
}

function confirmedPrivateDnsAnswers(cloudDns, confirmations) {
  const answers = new Map();
  const zones = cloudDns?.available === true ? cloudDns.zones ?? [] : [];
  for (const { sourceAssetId, zoneId, hostname, ip: address } of confirmations) {
    const host = concreteHost(hostname);
    const ip = normalizeIp(address);
    if (!sourceAssetId || !zoneId || !host || !isIP(ip) || loopbackHost(ip)) continue;
    const zone = zones.find(item => item.id === zoneId && item.visibility === 'private');
    const record = zone?.records?.find(item => item.status === '启用' &&
      item.type === (isIP(ip) === 4 ? 'A' : 'AAAA') && normalizeDomain(item.name) === host && normalizeIp(item.value) === ip);
    if (!record) continue;
    const key = JSON.stringify([sourceAssetId, host]);
    if (answers.has(key) && answers.get(key).ip !== ip) {
      throw new Error(`Conflicting PrivateZone DNS confirmations for ${sourceAssetId} / ${host}`);
    }
    answers.set(key, { ip, evidence: {
      source: 'dns', sourceId: record.resourceKey || record.id,
      reference: `cloud-dns-snapshot.json (private/${zone.id})`,
      detail: `PrivateZone ${record.type} ${host} -> ${ip}, confirmed for source Nginx asset ${sourceAssetId}`,
      observedAt: record.observedAt || zone.observedAt, dnsAddress: ip,
    } });
  }
  return answers;
}

function missingBackendDomain(index, backend) {
  if (backend.resolution !== 'hostname' || !validPort(backend.port) || loopbackHost(backend.host)) return '';
  const host = concreteHost(backend.host);
  return host && !isIP(normalizeIp(host)) && !index.lookup.domain.has(host) ? host : '';
}

// Only apply the literal proxy_pass URI replacement to a concrete location.
// The representative path and Host are inferred when a downstream L7 hop uses them.
function forwardedRequest(route, domain, path, backend, hint) {
  const target = String(route.target ?? '');
  const scheme = /^https:\/\//i.test(target) ? 'https' : 'http';
  const stream = route.context === 'stream';
  const host = hint?.host || (backend.resolution === 'hostname' ? normalizeDomain(backend.host) : concreteHost(domain));
  const query = { host: host || 'unknown.invalid', scheme, port: Number(backend.port) };
  if (!path || stream || /[?$#]/.test(target)) return query;
  const replacement = /^https?:\/\/[^/]+(\/.*)$/i.exec(target)?.[1];
  const location = routeUri(route.uri);
  if (replacement && (!location || !path.startsWith(location))) return query;
  return { ...query, path: replacement ? replacement + path.slice(location.length) : path };
}

function backendNodes(index, backend, protocol, evidence, sourceAsset, route, privateDnsAnswers) {
  if (!validPort(backend.port)) return [];
  const loopback = loopbackHost(backend.host);
  if (loopback) {
    const ips = [...new Set(splitIps(sourceAsset))].filter(ip => !loopbackHost(ip));
    if (ips.length !== 1) return [];
    const [ip] = ips;
    const pid = Number(/^\/proc\/(\d+)\/exe$/.exec(route.instance ?? '')?.[1]);
    const inspection = sourceAsset.inspection ?? {};
    const nginxProcess = (inspection.processes ?? []).find(process => Number(process.pid) === pid);
    const podProcess = (inspection.kubernetes?.processes ?? []).some(process => Number(process.pid) === pid);
    const containerProcess = (inspection.docker?.containers ?? []).some(container => {
      const initPid = Number(container.initPid);
      return initPid > 0 && (initPid === pid || initPid === Number(nginxProcess?.ppid));
    });
    const node = endpoint(index, ip, backend.port, protocol, evidence);
    return [{ ...node, id: JSON.stringify(['nginx-loopback', sourceAsset.id, ip, loopback, Number(backend.port), protocol]),
      attributes: { ...node.attributes, nginxLoopback: loopback, isolatedNginx: podProcess || containerProcess },
    }];
  }
  if (backend.resolution === 'hostname') {
    const host = normalizeDomain(backend.host);
    const key = JSON.stringify([sourceAsset.id, host]);
    const answer = privateDnsAnswers.get(key);
    if (answer) return [{
      id: `nginx-private-dns:${key}`, type: 'DOMAIN', label: host,
      identity: { name: host }, status: 'active', environment: 'GLOBAL', evidence: [answer.evidence],
      attributes: { confirmedPrivateDns: { ip: answer.ip, protocol, sourceIp: sourceAsset.ip } },
    }];
    return (index.lookup.domain.get(host) ?? []).filter(node => node.status !== 'paused');
  }
  if (backend.resolution !== 'ip' || !isIP(normalizeIp(backend.host))) return [];
  return [endpoint(index, normalizeIp(backend.host), backend.port, protocol, evidence)];
}

function endpoint(index, ip, port, protocol, evidence) {
  const result = resolveEndpointNodes(index, ip, port, protocol, evidence, 'internal');
  // Protocol aliases on one address/port are representations of the same
  // listener. Keep the requested protocol and use topology address indexes.
  if (result.exact) return result.nodes[0];
  return {
    ...(result.synthesized ?? result.nodes[0]),
    id: `endpoint:${ip}:${Number(port)}:${protocol}`,
    identity: { ip, port: String(Number(port)), protocol },
  };
}

function routeKey(asset, route) {
  const domains = (route.domains ?? []).map(normalizeDomain).filter(Boolean);
  const upstream = route.upstream || route.target || `${route.directive}:${route.target || ''}`;
  return `nginx-route:${asset.id}:${sha256Short(`${asset.id}|${domains.join(',')}|${route.uri}|${route.directive}|${route.target || ''}|${upstream}`)}`;
}

function archiveRoute(id, asset, route) {
  const inspection = asset.inspection;
  return { id, assetId: asset.id, version: inspection.configurationVersion, checkedAt: inspection.checkedAt,
    instance: route.instance, file: route.file, domains: route.domains, listen: route.listen, uri: route.uri,
    directive: route.directive, target: route.target || '' };
}

function alternateProxyUri(original, forwarded, detail) {
  if (!original || !forwarded) return '';
  const candidateSet = new Set();
  const segments = path => path.replace(/[-/]+/g, '/').replace(/\/$/, '');
  for (const location of detail?.proxyLocations ?? []) {
    const path = routeUri(location);
    if (!path || path === '/' || path === forwarded || forwarded.startsWith(path)) continue;
    if ((original !== forwarded && original === path) || segments(path) === segments(forwarded)) candidateSet.add(path);
  }
  return candidateSet.size === 1 ? [...candidateSet][0] : '';
}

function nginxStopExplanation(query, stop, detail) {
  const request = `${query.host}${query.domainOnly ? '' : query.path}`;
  if (stop.origins.length > 1) return `下游 Nginx ${request} 对应多个配置来源，无法确认命中的规则（${stop.origins.join('、')}）`;
  const location = detail ? (detail.location ? `location ${stop.uri}` : 'server 默认处理') : `配置条目 ${stop.uri || '（无 URI）'}`;
  const actions = (detail?.actions ?? []).map(([code, conditional]) =>
    `HTTP ${code} ${conditional ? '条件' : ''}${[301, 302, 303, 307, 308].includes(code) ? '重定向' : '直接响应'}`);
  if (stop.directive === 'static/other' && stop.target) actions.push(`静态目录 ${stop.target}`);
  if (detail?.phpFallback) actions.push(`try_files 找不到文件或目录时内部转发到 ${detail.phpFallback}`);
  if (detail?.conditionalLogging) actions.push('按请求条件配置访问日志');
  if (stop.directive !== 'static/other') actions.push(`配置指令 ${stop.directive}`);
  const handling = actions.length ? `配置了${actions.join('、')}` : '快照未确认该条目的响应方式';
  return `下游 Nginx ${request} 命中 ${location}，${handling}${stop.file ? `（${stop.file}）` : ''}`;
}

function buildResolver(index, jumpserver, kongData, encounter, privateDnsAnswers, possibleFallbacks,
  possibleStaticLocations, nginxStops, retiredEips, collectedClbListeners, requestHints) {
  const dns = new DnsResolver();
  const nat = new NatResolver();
  const clb = new ClbResolver();
  const nginx = new NginxResolver();
  const routeSources = new Map();
  const staticOrigins = new Map();
  const assetById = new Map((jumpserver.assets ?? []).map(asset => [asset.id, asset]));
  const nginxPorts = new Map();
  const kongPorts = new Set();
  const natGateways = new Map();
  const eipByIp = new Map();
  const eipBindings = new Map();
  for (const node of index.topology.nodes) {
    if (node.type === 'EIP' && node.status !== 'paused') {
      const eips = eipByIp.get(ipOf(node)) ?? [];
      eips.push(node);
      eipByIp.set(ipOf(node), eips);
      eipBindings.set(node.id, (index.adjacency.get(node.id) ?? []).flatMap(step => {
        const host = index.nodeById.get(step.neighborId);
        return step.direction === 'forward' && step.edge.type === 'BOUND_TO' && host?.type === 'HOST'
          ? [{ host, edge: step.edge }] : [];
      }));
    }
    if (node.type === 'DNAT_RULE') {
      for (const step of index.adjacency.get(node.id) ?? []) {
        const gateway = index.nodeById.get(step.neighborId);
        if (gateway?.type === 'NAT_GATEWAY') natGateways.set(node.id, gateway.label);
      }
    }
  }
  for (const asset of jumpserver.assets ?? []) {
    for (const route of asset.inspection?.nginxRoutes ?? []) {
      const key = routeKey(asset, route);
      const definitions = routeSources.get(key) ?? new Map();
      // Preserve distinct backend definitions and listeners when the graph
      // has coalesced otherwise identical routes from different Nginx instances.
      // Identical loopback routes in multiple Nginx instances can refer to different namespaces.
      const loopbackInstance = (route.backends ?? []).some(backend => loopbackHost(backend.host)) ? route.instance : '';
      definitions.set(JSON.stringify([route.listen, route.context, route.backends, loopbackInstance]), { route, asset });
      routeSources.set(key, definitions);
      if (route.directive === 'static/other') {
        const origins = staticOrigins.get(key) ?? new Set();
        origins.add(JSON.stringify([route.instance, route.file]));
        staticOrigins.set(key, origins);
      }
      for (const ip of splitIps(asset)) {
        for (const listen of route.listen ?? []) {
          const port = parseListenPort(listen);
          if (port) nginxPorts.set(endpointLabel(ip, port), true);
        }
      }
    }
    for (const listener of asset.inspection?.appPorts ?? []) {
      if (/^nginx$/i.test(listener.kind || listener.app)) {
        for (const ip of splitIps(asset)) nginxPorts.set(endpointLabel(ip, listener.port), true);
      }
      if (/^kong$/i.test(listener.kind || '') || /^kong$/i.test(listener.app || '')) {
        for (const ip of splitIps(asset)) kongPorts.add(endpointLabel(ip, listener.port));
      }
    }
  }

  function noteStaticLocation(endpointContext, host, port, selected, defaultServer) {
    const { current, query } = endpointContext;
    if (!possibleStaticLocations || query.domainOnly || selected.moves.length !== 1 || selected.confidence === 'AMBIGUOUS') return;
    const matched = selected.moves[0].node;
    const definitions = routeSources.get(matched.id);
    if (definitions?.size !== 1 || staticOrigins.get(matched.id)?.size !== 1) return;
    const { route, asset } = definitions.values().next().value;
    if (route.directive !== 'static/other' || route.context !== 'http' || frontendPath(route.uri) === null ||
        !/^\/[^$\s]*$/.test(route.target) || !route.file || !route.instance) return;
    const inspection = asset.inspection;
    if (!inspection.checkedAt || host.identity.jumpserverAssetIds?.length !== 1) return;
    let mainConfig = '';
    if (inspection.nginxStatus !== 'complete') {
      const masters = (inspection.processes ?? []).filter(process => /^nginx: master process\b/.test(process.command ?? ''));
      mainConfig = /\s-c\s+(\/\S+)(?:\s|$)/.exec(masters[0]?.command ?? '')?.[1];
      if (inspection.nginxStatus !== 'partial' || masters.length !== 1 || !mainConfig ||
          inspection.portStatus !== 'complete' || !(inspection.appPorts ?? []).some(listener => Number(listener.port) === port)) return;
    }
    const id = staticLocationKey(current.id, query);
    const inferredDefault = selected.rule.startsWith('nginx:route:default:') &&
      !route.listen.some(listen => /(?:^|\s)default_server(?:\s|$)/.test(listen));
    possibleStaticLocations.set(id, {
      ...archiveRoute(id, asset, route), routeId: matched.id, defaultServer,
      requireSoleServer: defaultServer || inferredDefault, mainConfig, port, rootRequest: query.path === '/',
      endpoint: endpointLabel(ipOf(current), port),
    });
  }

  function noteImplicitStaticLocation(endpointContext, host, port) {
    if (!possibleStaticLocations || endpointContext.query.domainOnly) return;
    const assets = host.identity.jumpserverAssetIds ?? [];
    if (assets.length !== 1) return;
    const routes = (assetById.get(assets[0])?.inspection?.nginxRoutes ?? []).filter(route =>
      route.context === 'http' && (route.listen ?? []).some(listen => parseListenPort(listen) === port));
    if (new Set(routes.map(route => JSON.stringify([route.domains, route.listen, route.instance]))).size !== 1) return;
    const name = routes[0].domains?.map(concreteHost).find(Boolean);
    if (!name) return;
    const candidate = nginx.resolveRoutes({ ...endpointContext, query: { ...endpointContext.query, host: name } }, host, port);
    noteStaticLocation(endpointContext, host, port, candidate, true);
  }

  return {
    name: 'NginxExportResolver',
    canResolve: ({ current }) => Boolean(current),
    resolve(context) {
      const { current: node, query } = context;
      const ip = ipOf(node);
      if ((node.type === 'EIP' || node.type === 'ENDPOINT') && retiredEips.has(ip)) {
        return { ...fail(retiredEips.get(ip)), stopReason: EIP_BOUND_ECS_MISSING };
      }
      if (node.status === 'paused') return fail('目标已暂停');
      if (node.type === 'DOMAIN') {
        const confirmed = node.attributes.confirmedPrivateDns;
        if (confirmed) return {
          rule: 'dns:records', confidence: 'INFERRED', evidence: node.evidence,
          moves: [{ node: endpoint(index, confirmed.ip, query.port, confirmed.protocol, node.evidence) }],
          warnings: [`来源 Nginx ${confirmed.sourceIp} 已核实 PrivateZone 记录 ${node.label} → ${confirmed.ip}`],
        };
        const result = dns.resolve(context);
        if (result.moves.length || result.warnings?.length) return result;
        return { ...result, stopReason: DNS_NO_RECORD, warnings: [missingDnsWarning(node.label)] };
      }
      if (node.type === 'EIP') {
        if (clb.canResolve(context)) return clb.resolve(context);
        const bindings = eipBindings.get(node.id) ?? [];
        if (bindings.length && !matchDnatRules(index, ip, query.port, 'tcp').length) {
          const moves = new Map();
          for (const { host, edge } of bindings) {
            for (const privateIp of host.attributes.ecs?.privateIps ?? []) {
              if (isIP(privateIp) && privateIp !== ip) {
                const target = endpoint(index, normalizeIp(privateIp), query.port, query.scheme, edge.evidence);
                moves.set(target.id, { node: target });
              }
            }
          }
          if (moves.size) return {
            rule: 'export:eip-ecs', moves: [...moves.values()],
            confidence: moves.size === 1 && bindings.every(({ edge }) => edge.confidence === 'EXACT') ? 'INFERRED' : 'AMBIGUOUS',
            evidence: bindings.flatMap(({ edge }) => edge.evidence),
          };
        }
        return nat.resolve(context);
      }
      if (clb.canResolve(context) && node.type !== 'ENDPOINT') {
        if (node.type === 'CLB_LISTENER' && query.domainOnly &&
            ['http', 'https'].includes(String(node.identity.protocol).toLowerCase())) {
          return { ...fail('缺少具体请求 URI，无法确定 CLB 规则'), stopReason: REQUEST_URI_NEEDED };
        }
        const result = clb.resolve(context);
        if (node.type === 'CLB' && !result.moves.length && validPort(query.port)) {
          const scope = clbScopeKey(node.identity.tenantId, node.attributes.region);
          const ports = collectedClbListeners.get(JSON.stringify([node.identity.clbId, scope]));
          if (ports && !ports.has(Number(query.port))) {
            return { ...result, stopReason: CLB_LISTENER_PORT_MISSING,
              warnings: [...(result.warnings ?? []), `完整 CLB 清单中 ${node.label}（${ip}）未配置 ${query.port} 监听端口`] };
          }
        }
        return result;
      }
      if (node.type === 'HOST') {
        // A logical host can merge private and public addresses. Use the DNS
        // answer that led to this host, with the forwarded request port.
        const domain = context.trail.at(-1)?.inputNodeIds[0];
        const evidence = (index.adjacency.get(domain) ?? [])
          .filter(step => step.direction === 'forward' && step.neighborId === node.id && step.edge.type === 'RESOLVES_TO')
          .flatMap(step => step.edge.evidence);
        const ips = [...new Set(evidence.map(item => normalizeIp(item.dnsAddress)).filter(isIP))];
        if (!ips.length) return fail('DNS 落点没有可确认的地址');
        return { rule: 'export:dns-host', confidence: 'EXACT', evidence,
          moves: ips.map(address => ({ node: endpoint(index, address, query.port, query.scheme, evidence) })) };
      }
      if (node.type === 'NGINX_ROUTE') {
        const definitions = routeSources.get(node.id);
        if (!definitions?.size) return fail('缺少该 Nginx 路由的快照定义');
        if (definitions.size > 1) return {
          ...fail('多个 Nginx 实例上的路由定义不一致'), confidence: 'AMBIGUOUS',
        };
        const { route, asset } = definitions.values().next().value;
        if (route.directive !== 'proxy_pass') {
          const inspection = asset.inspection;
          const origins = [...(staticOrigins.get(node.id) ?? [])].map(origin => JSON.parse(origin)[1] || route.file || '未知');
          nginxStops.set(node.id, { ...archiveRoute(node.id, asset, route), origins });
          if (isStaticRoot(route) && !query.domainOnly &&
              (route.domains ?? []).some(name => normalizeDomain(name) === query.host) &&
              inspection.nginxStatus === 'complete' && inspection.checkedAt && route.file && route.instance &&
              staticOrigins.get(node.id)?.size === 1) {
            const id = fallbackKey(node.id, query.path);
            possibleFallbacks.set(id, { ...archiveRoute(id, asset, route), rootRequest: query.path === '/' });
          }
          return { ...fail(), stopReason: NGINX_NON_PROXY_LOCATION };
        }
        const moves = [];
        const warnings = [];
        for (const backend of route.backends ?? []) {
          const forwarded = forwardedRequest(route, query.host, query.domainOnly ? undefined : query.path, backend,
            requestHints[requestHintKey(asset, route)]);
          const targets = backendNodes(index, backend, route.context === 'stream' ? 'tcp' : forwarded.scheme, node.evidence, asset, route, privateDnsAnswers);
          for (const target of targets) {
            moves.push({ node: target, query: { ...forwarded, path: forwarded.path ?? '/', domainOnly: !forwarded.path } });
          }
          if (targets.length === 0) {
            const label = String(backend.host || route.target || '未知');
            const missingDomain = missingBackendDomain(index, backend);
            if (!missingDomain) warnings.push(`下游后端 ${label} 缺少可解析的地址或端口`);
            moves.push({ node: {
              id: JSON.stringify(['unresolved-backend', node.id, label, backend.port]), type: 'UPSTREAM',
              label, identity: {}, attributes: missingDomain ? { missingDomain } : {},
              status: 'unresolved', environment: 'GLOBAL', evidence: node.evidence,
            } });
          }
        }
        return moves.length
          ? { rule: 'export:nginx-backend', confidence: 'INFERRED', moves, evidence: node.evidence, warnings }
          : fail('下游 Nginx 路由没有采集到后端');
      }
      if (node.type === 'UPSTREAM' && node.attributes.missingDomain) return {
        ...fail(missingDnsWarning(node.attributes.missingDomain)),
        stopReason: DNS_NO_RECORD,
      };
      if (node.type !== 'ENDPOINT' || !isIP(ip) || !validPort(node.identity.port)) return fail('缺少可确认的后端地址或端口');

      const port = Number(node.identity.port);
      const address = endpointLabel(ip, port);
      if (kongAdminEndpoints.has(address)) return {
        ...fail(`${address} 是 Kong Admin 管理端点`), stopReason: KONG_MANAGEMENT,
      };
      if (node.attributes.nginxLoopback) return {
        rule: 'export:nginx-loopback', confidence: 'INFERRED', moves: [{ node, terminal: true }],
        evidence: node.evidence,
        warnings: [`Nginx 转发到本机回环 ${endpointLabel(node.attributes.nginxLoopback, port)}，清单以 Nginx 资产地址 ${address} 展示。`],
      };
      const gateway = gateways.get(address);
      if (gateway) {
        encounter(address, query);
        const data = kongData.get(address);
        if (!data) return fail('尚未取得该 Kong 网关的实时查询证据');
        if (data.error) return fail(`Kong Admin 查询失败：${data.error}`);
        if (query.domainOnly) return { ...fail('缺少具体请求 URI，无法确定 Kong 路由'), stopReason: REQUEST_URI_NEEDED };
        const result = new KongResolver(data).resolve(context);
        // The existing resolver verifies route and Target addresses. Later L7
        // routing needs the rewritten request, which it does not provide.
        return { ...result, moves: result.moves.map(move => ({
          ...move,
          // KongResolver only accepts HTTP(S) services. A compatible topology
          // node can still carry DevOps' unspecified protocol. Keep the TCP
          // transport proved by the Kong hop for listener correlation.
          node: move.node.identity.protocol === 'unknown'
            ? endpoint(index, ipOf(move.node), move.node.identity.port, 'tcp', result.evidence ?? [])
            : move.node,
          query: { ...query, domainOnly: true },
        })) };
      }
      if (kongPorts.has(address)) return fail('快照观察到 Kong 监听，但拓扑尚未配置该代理端点的 Admin 地址');
      const transport = String(node.identity.protocol).toLowerCase();
      const dnat = transport !== 'udp' ? matchDnatRules(index, ip, port, 'tcp') : [];
      const endpointContext = { ...context, query: { ...query, port } };
      if (dnat.length) {
        const result = nat.resolve({ ...endpointContext, current: { ...node, attributes: { ...node.attributes, natSide: 'external' } } });
        const gateways = [...new Set(dnat.map(rule => natGateways.get(rule.rule.id)).filter(Boolean))];
        return { ...result, warnings: [...(result.warnings ?? []), ...gateways.map(name => `NAT 网关 ${name}`)] };
      }
      if (clb.canResolve(endpointContext)) {
        const result = clb.resolve(endpointContext);
        return { ...result, moves: result.moves.map(move => ({ ...move, query: endpointContext.query })) };
      }
      // A direct IP target may be an EIP bound to a CLB whose identity uses a
      // different address. Use the existing BOUND_TO relationship.
      const boundEips = (eipByIp.get(ip) ?? []).filter(eip =>
        clb.canResolve({ ...endpointContext, current: eip }) || eipBindings.get(eip.id)?.length);
      if (boundEips.length) return {
        rule: 'export:eip', confidence: boundEips.length === 1 ? 'EXACT' : 'AMBIGUOUS',
        moves: boundEips.map(eip => ({ node: eip, query: endpointContext.query })),
      };
      if (nginxPorts.has(address)) {
        const hosts = nginx.resolve(endpointContext);
        const moves = [];
        const warnings = [...(hosts.warnings ?? [])];
        let confidence = hosts.confidence;
        const issues = new Set();
        for (const { node: host } of hosts.moves) {
          const hostContext = { ...endpointContext, current: host };
          const stream = query.domainOnly && nginx.resolveRoutes(hostContext, host, port, true);
          const selected = stream && stream.moves.length ? stream : nginx.resolveRoutes(hostContext, host, port, false,
            { requireRequestPath: true, allowSoleHttpServer: true });
          warnings.push(...(selected.warnings ?? []));
          if (selected.issue) issues.add(selected.issue);
          if (selected.confidence === 'AMBIGUOUS') confidence = 'AMBIGUOUS';
          if (hosts.moves.length === 1) {
            if (selected.moves.length) noteStaticLocation(endpointContext, host, port, selected, false);
            else noteImplicitStaticLocation(endpointContext, host, port);
          }
          for (const move of selected.moves) moves.push({ ...move, query: endpointContext.query });
        }
        if (moves.length) return {
          rule: 'export:nginx-route', moves, confidence: confidence === 'AMBIGUOUS' ? confidence : 'INFERRED',
          evidence: hosts.evidence, warnings,
        };
        return { ...fail('无法确定下游 Nginx 路由'), warnings,
          ...(issues.size === 1 && hosts.moves.length === 1 ? { stopReason: `nginx:${[...issues][0]}` } : {}) };
      }
      if (node.attributes.natSide === 'external' || (eipByIp.has(ip) && !index.lookup.host.has(ip))) {
        return fail('公网端点缺少可确认的转发规则');
      }
      return { rule: 'export:service-endpoint', confidence: 'EXACT', moves: [{ node, terminal: true,
        query: endpointContext.query }] };
    },
  };
}

function describePath(path, initialBackend, kongData) {
  const nodes = path?.nodes ?? [];
  const dns = nodes.filter(node => node.type === 'DOMAIN').map(node => node.label);
  const details = type => [...new Set(nodes.filter(node => node.type === type).map(node => node.label))].join(' → ');
  const dnsAnswer = path?.steps?.find(step => step.rule === 'dns:records' &&
    nodes.some(node => node.type !== 'DOMAIN' && step.outputNodeIds.includes(node.id)));
  const dnsNode = dnsAnswer && nodes.find(node => node.type !== 'DOMAIN' && dnsAnswer.outputNodeIds.includes(node.id));
  const dnsIp = dnsNode?.type === 'HOST' ? ipOf(nodes[nodes.indexOf(dnsNode) + 1]) : ipOf(dnsNode);
  const kongNode = nodes.find(node => node.type === 'ENDPOINT' && gateways.has(endpointLabel(ipOf(node), node.identity.port)));
  const address = kongNode && endpointLabel(ipOf(kongNode), kongNode.identity.port);
  const gateway = address && gateways.get(address);
  const observation = address && kongData.get(address);
  const kongStep = path?.steps?.find(step => step.rule === 'kong:targets' && step.inputNodeIds.includes(kongNode?.id));
  const kongRoute = kongStep && observation?.routes.find(route => String(route.id) === kongStep.evidence[0]?.sourceId);
  const kongService = kongRoute && observation.services[kongRoute.service?.id];
  const kongTarget = kongStep && nodes[nodes.indexOf(kongNode) + 1];
  const nat = path?.steps?.flatMap(step => step.warnings.filter(warning => warning.startsWith('NAT 网关 '))) ?? [];
  return [
    gateway ? 'YES' : '',
    kongService?.name || kongService?.id || '',
    kongService?.host ? `${kongService.host}${kongService.port ? ':' + kongService.port : ''}${kongService.path || ''}` : '',
    kongTarget?.type === 'ENDPOINT' ? endpointLabel(ipOf(kongTarget), kongTarget.identity.port) : '',
    kongRoute?.paths?.join('|') || '',
    gateway ? `${gateway.ip}:${gateway.adminPort}` : '',
    loopbackHost(initialBackend.host) ? '本机回环' : initialBackend.resolution === 'hostname' ? dnsIp ? 'RESOLVED' : 'UNRESOLVED' : initialBackend.resolution || '',
    dnsIp,
    dns.length > 1 ? dns.join(' → ') : '',
    details('EIP'),
    nodes.filter(node => node.type === 'HOST' && node.attributes.ecs).map(node => node.label).join(' → '),
    details('CLB'),
    [...new Set(nat)].join(' → '),
    observation?.observedAt || '',
  ];
}

function mergeValues(values) {
  const distinct = new Map();
  for (const value of values) {
    if (value !== '' && value != null && !distinct.has(String(value))) distinct.set(String(value), value);
  }
  const unique = [...distinct.values()];
  return unique.length < 2 ? unique[0] ?? '' : unique.join(' | ');
}

export async function backendRows(topology, jumpserver, devops, sourceVersion,
  { ecs, eip, clb: clbSnapshot, cloudDns, privateDnsConfirmations = [], invalidProxyEndpoints = [],
    loadKong = loadKongExportData, loadServerFallbacks, loadStaticLocations, loadNginxStops, loadRequestHints } = {}) {
  const index = buildGraphIndex(topology);
  const retiredEips = retiredEipBindings(index, jumpserver, eip, ecs);
  const privateDnsAnswers = confirmedPrivateDnsAnswers(cloudDns, privateDnsConfirmations);
  const kongData = new Map();
  const requested = new Map();
  const reached = new Map();
  const possibleFallbacks = new Map();
  const possibleStaticLocations = loadStaticLocations && new Map();
  const nginxStops = new Map();
  const hintCandidates = (jumpserver.assets ?? []).flatMap(asset => (asset.inspection?.nginxRoutes ?? []).filter(route =>
    route.directive === 'proxy_pass' && route.context === 'http' && !isInternalApplicationLocation(route.uri) &&
    asset.inspection.checkedAt && route.file && route.instance &&
    (String(route.target).includes('$') || !(route.domains ?? []).some(concreteHost)))
    .map(route => ({ ...archiveRoute(requestHintKey(asset, route), asset, route), version: asset.inspection.configurationVersion || sourceVersion })));
  const requestHints = loadRequestHints && hintCandidates.length ? await loadRequestHints(hintCandidates) : {};
  const encounter = (address, query) => {
    const queries = reached.get(address) ?? new Map();
    queries.set(JSON.stringify(query), query);
    reached.set(address, queries);
  };
  const registry = new ResolverRegistry().register(buildResolver(index, jumpserver, kongData, encounter, privateDnsAnswers,
    possibleFallbacks, possibleStaticLocations, nginxStops, retiredEips, completeClbListeners(clbSnapshot), requestHints));
  const inventory = createAssetInventory(jumpserver, ecs);
  const matchProcess = createEndpointProcessMatcher(jumpserver, devops, ecs, inventory);
  const confirmedInvalid = new Set(invalidProxyEndpoints.flatMap(({ ip: address, port }) => {
    const ip = normalizeIp(address);
    if (!isIP(ip) || !validPort(port)) return [];
    const listeners = (inventory.byIp.get(ip) ?? []).flatMap(asset => asset.inspection?.appPorts ?? []);
    return listeners.some(listener => Number(listener.port) === Number(port)) ? [] : [endpointLabel(ip, Number(port))];
  }));
  const infrastructureIps = new Set(KONG_GATEWAYS.map(gateway => gateway.ip));
  for (const node of topology.nodes) {
    if (node.type === 'CLB' || node.type === 'EIP') infrastructureIps.add(ipOf(node));
    if (node.type === 'DNAT_RULE') infrastructureIps.add(normalizeIp(node.identity.externalIp));
  }
  const tasks = [];
  for (const asset of jumpserver.assets ?? []) {
    for (const route of asset.inspection?.nginxRoutes ?? []) {
      if (route.directive !== 'proxy_pass' || isInternalApplicationLocation(route.uri)) continue;
      const uri = routeUri(route.uri);
      const hint = requestHints[requestHintKey(asset, route)];
      const derivedBackends = hint?.backends?.length ? hint.backends : null;
      for (const domain of route.domains?.length ? route.domains : ['']) {
        const kongTargetsByEndpoint = new Map();
        for (const backend of derivedBackends ?? (route.backends?.length ? route.backends : [{}])) {
          const query = forwardedRequest(route, domain, uri, backend, hint);
          const startNodes = backendNodes(index, backend, route.context === 'stream' ? 'tcp' : query.scheme, [], asset, route, privateDnsAnswers);
          tasks.push({ asset, route, uri, domain, backend, query, startNodes, kongTargetsByEndpoint, hint, derivedBackend: Boolean(derivedBackends) });
        }
      }
    }
  }
  const traceTask = ({ query, startNodes }) => startNodes.flatMap(start => {
    const trace = resolveRequestPathWithIndex(index, query, { registry, start });
    const cap = trace.warnings.find(warning => warning.startsWith('Path enumeration stopped'));
    return cap ? [...trace.paths, { warnings: [cap] }] : trace.paths;
  });
  let traces = tasks.map(traceTask);
  // Resolve the snapshot first and query only the gateways actually reached.
  // Re-run if a returned Target exposes another known Kong gateway.
  for (let round = 0; round <= gateways.size; round++) {
    const pending = [...reached].filter(([address, queries]) => requested.get(address) !== queries.size);
    if (!pending.length) break;
    await Promise.all(pending.map(async ([address, queries]) => {
      requested.set(address, queries.size);
      const data = await loadKong(gateways.get(address), [...queries.values()], sourceVersion);
      if (data) kongData.set(address, data);
    }));
    traces = tasks.map(traceTask);
  }

  const confirmedFallbacks = loadServerFallbacks && possibleFallbacks.size
    ? new Set(await loadServerFallbacks([...possibleFallbacks.values()].map(item => ({ ...item, version: item.version || sourceVersion }))))
    : new Set();
  const confirmedStaticLocations = possibleStaticLocations?.size
    ? new Set(await loadStaticLocations([...possibleStaticLocations.values()].map(item => ({ ...item, version: item.version || sourceVersion }))))
    : new Set();
  const stopCandidates = [...nginxStops.values()].filter(item => item.directive === 'static/other' &&
    item.origins.length === 1 && item.checkedAt && item.file && item.instance);
  const nginxStopDetails = loadNginxStops && stopCandidates.length
    ? await loadNginxStops(stopCandidates.map(item => ({ ...item, version: item.version || sourceVersion }))) : {};
  const results = [];
  const proxiedFrontend = new Map();
  for (const [i, task] of tasks.entries()) {
    const { asset, route, uri, domain, backend, kongTargetsByEndpoint, hint, derivedBackend } = task;
    const paths = traces[i];
    const sourceEipWarning = splitIps(asset).map(ip => retiredEips.get(ip)).find(Boolean);
    for (const path of paths.length ? paths : [null]) {
      const last = path?.nodes?.at(-1);
      const terminal = last?.type === 'ENDPOINT' && last.id === path.terminalNodeId;
      const frontendUri = frontendPath(route.uri);
      if (!sourceEipWarning && !terminal && frontendUri && path?.effectiveRequest) {
        const reachedEndpoint = last?.type === 'NGINX_ROUTE' ? path.nodes.at(-2) : last;
        const id = reachedEndpoint?.type === 'ENDPOINT' && staticLocationKey(reachedEndpoint.id, path.effectiveRequest);
        const location = confirmedStaticLocations.has(id) && possibleStaticLocations.get(id);
        if (location && (location.defaultServer ? last === reachedEndpoint : last.id === location.routeId) &&
            !path.steps.some(step => step.confidence === 'AMBIGUOUS')) {
          const row = frontendRow(asset, route, domain, frontendUri, location.target, location.endpoint);
          proxiedFrontend.set(JSON.stringify(row), row);
          continue;
        }
      }
      const chainConfidence = derivedBackend && path?.confidence === 'EXACT' ? 'INFERRED' : path?.confidence ?? 'UNKNOWN';
      const protocol = String(last?.identity.protocol ?? '').toLowerCase();
      const finalIp = ipOf(last);
      const businessEndpoint = terminal && (!infrastructureIps.has(finalIp) ||
        inventory.byIp.has(finalIp) || inventory.ecsAddresses.has(finalIp));
      const inventoryMatch = businessEndpoint && inventory.missingJumpServerAsset(finalIp);
      const applicationStatus = chainConfidence === 'AMBIGUOUS' ? 'AMBIGUOUS' : 'UNKNOWN';
      const confirmedInvalidBackend = businessEndpoint && ['http', 'https', 'tcp'].includes(protocol) &&
        confirmedInvalid.has(endpointLabel(finalIp, last.identity.port));
      const app = terminal && last.attributes.isolatedNginx
        ? { status: 'UNKNOWN', reason: 'Nginx 位于容器或 Pod，快照未确认其网络命名空间内的监听进程' }
        : confirmedInvalidBackend ? { status: applicationStatus, matchExplanation: INVALID_PROXY_BACKEND,
          reason: `用户确认后端 ${endpointLabel(finalIp, last.identity.port)} 未监听` }
        : inventoryMatch?.assetStatus ? { ...inventoryMatch, status: applicationStatus }
        : businessEndpoint && ['http', 'https', 'tcp'].includes(protocol) &&
        ['EXACT', 'INFERRED'].includes(chainConfidence)
        ? matchProcess(finalIp, last.identity.port, last.attributes.nginxLoopback)
        : { status: applicationStatus,
          reason: terminal ? '最终端点的协议或路径置信度不足，无法据此匹配进程' : undefined };
      const chainStatus = chainConfidence === 'AMBIGUOUS' || path?.status === 'AMBIGUOUS' ? 'AMBIGUOUS'
        : terminal ? 'RESOLVED' : (path?.nodes?.length ?? 0) > 0 ? 'PARTIAL' : 'UNRESOLVED';
      const missingDomain = !path && missingBackendDomain(index, backend);
      const nginxStop = path?.reason === NGINX_NON_PROXY_LOCATION && nginxStops.get(last.id);
      const fallback = nginxStop && possibleFallbacks.get(fallbackKey(last.id, path.effectiveRequest.path));
      const missingRoute = fallback && chainStatus === 'PARTIAL' && confirmedFallbacks.has(fallback.id) &&
        !path.steps.some(step => step.confidence === 'AMBIGUOUS');
      const stopDetail = nginxStop ? nginxStopDetails[last.id] : undefined;
      const unmatchedProxyUri = nginxStop && (missingRoute || (nginxStop.uri === '/' && !nginxStop.target && stopDetail?.loggingOnly)) &&
        !path.steps.some(step => step.confidence === 'AMBIGUOUS');
      const slashRedirect = unmatchedProxyUri && stopDetail?.proxyLocations?.some(location =>
        !location.startsWith('=') && routeUri(location) === `${path.effectiveRequest.path}/`);
      const alternateUri = unmatchedProxyUri && !slashRedirect ? alternateProxyUri(uri, path.effectiveRequest.path, stopDetail) : '';
      const kongInternal = isKongInternal(asset, route);
      const dynamic = !path && (backend.resolution === 'dynamic' || String(route.target).includes('$'));
      const dynamicNote = dynamic && !kongInternal && `代理目标依赖运行时变量 ${[
        ...new Set(String(route.target).match(/\$[A-Za-z_]\w*/g) ?? []),
      ].join('、')}${hint?.conditions?.length ? `，相关请求条件 ${hint.conditions.join('、')}` : ''}`;
      const explanations = path?.warnings ?? [
        missingDomain ? missingDnsWarning(missingDomain)
          : loopbackHost(backend.host) ? '本机回环转发缺少可确认的 Nginx 资产地址或后端端口'
          : backend.resolution === 'hostname' ? '快照中没有可使用的后端域名、DNS 记录或端口'
          : '后端地址或端口缺失，或需要运行时变量、Unix socket 等快照外证据',
      ];
      const chain = [`${asset.ip} ${domain || ''} ${route.uri || ''}`.trim(),
        ...(path?.nodes ?? []).map(node => node.type === 'ENDPOINT'
          ? endpointLabel(ipOf(node), node.identity.port)
          : node.type === 'EIP' && retiredEips.has(ipOf(node)) ? ipOf(node) : node.label)].join(' → ');
      const components = describePath(path, backend, kongData);
      const observedAt = components.pop();
      const stopLabel = kongInternal ? KONG_INTERNAL
        : slashRedirect ? '重定向或条件响应'
        : alternateUri ? 'URI配置错误'
        : missingRoute ? '后端路由不存在'
        : nginxStop?.origins?.length > 1 ? '下游Nginx配置有歧义'
        : stopDetail?.actions?.length ? '重定向或条件响应'
        : stopDetail?.phpFallback ? '下游PHP回退'
        : dynamic ? '运行时变量代理待核实'
        : stopLabels.get(missingDomain ? DNS_NO_RECORD : path?.reason) || (terminal && !app.id && !app.assetStatus &&
          !app.matchExplanation ? '进程证据不足' : '');
      const offlineEip = Boolean(sourceEipWarning) || path?.reason === EIP_BOUND_ECS_MISSING;
      const nginxExplanation = slashRedirect
        ? `下游 Nginx ${path.effectiveRequest.host} 对 ${path.effectiveRequest.path} 返回 HTTP 301，补全末尾斜杠（${nginxStop.file}）`
        : missingRoute
        ? `下游 Nginx ${path.effectiveRequest.host} 未找到匹配 ${path.effectiveRequest.path} 的 location，落到 server 默认处理 /（${fallback.file}）`
        : nginxStop && nginxStopExplanation(path.effectiveRequest, nginxStop, stopDetail);
      const branch = { backend, last, terminal, app, chain, chainStatus, chainConfidence, components, observedAt, stopLabel, offlineEip,
        explanations: [...explanations, nginxExplanation, sourceEipWarning, dynamicNote,
          kongInternal && 'Kong 代理监听的内部变量转发，业务链路通过网关路由解析',
          alternateUri && `转发后的 URI ${path.effectiveRequest.path} 与下游代理路径 ${alternateUri} 不匹配`,
          derivedBackend && '归档中变量的默认和条件 upstream 成员一致，按配置列出候选后端'].filter(Boolean) };

      // Only combine independently resolved routes through Kong that reached
      // the same endpoint. Keep incomplete branches as their own evidence.
      const key = chainStatus === 'RESOLVED' && ['EXACT', 'INFERRED'].includes(chainConfidence) && components[3]
        ? JSON.stringify([ipOf(last), Number(last.identity.port), protocol]) : null;
      const previous = key && kongTargetsByEndpoint.get(key);
      if (previous) {
        previous.push(branch);
      } else {
        const branches = [branch];
        results.push({ task, branches });
        if (key) kongTargetsByEndpoint.set(key, branches);
      }
    }
  }

  const counts = { backendRows: results.length, endpointRows: 0, matchedRows: 0, unresolvedRows: 0, ambiguousRows: 0,
    kongRows: 0, kongTargetRows: 0, unclassifiedRows: 0, explanationCounts: {} };
  const rows = results.map(({ task: { asset, route, uri, domain }, branches }) => {
    const { last, terminal, app, chainStatus, components, stopLabel, offlineEip } = branches[0];
    const merge = select => mergeValues(branches.map(select));
    const chainConfidence = branches.some(branch => branch.chainConfidence === 'INFERRED')
      ? 'INFERRED' : branches[0].chainConfidence;
    if (terminal) counts.endpointRows++;
    else counts.unresolvedRows++;
    if (chainStatus === 'AMBIGUOUS' || app.status === 'AMBIGUOUS') counts.ambiguousRows++;
    if (app.id) counts.matchedRows++;
    if (components[0]) counts.kongRows++;
    if (components[3]) counts.kongTargetRows++;
    const explanation = offlineEip ? OFFLINE_ASSET : app.assetStatus || app.matchExplanation || (app.id ? '进程匹配成功' : stopLabel) || '';
    if (explanation) counts.explanationCounts[explanation] = (counts.explanationCounts[explanation] ?? 0) + 1;
    else if (!app.id) counts.unclassifiedRows++;
    return [
      asset.ip || '', asset.hostname || '', domain, (route.listen ?? []).join('|'), route.uri || '',
      uri && concreteHost(domain) ? `${domain}${uri}` : '',
      terminal ? endpointLabel(ipOf(last), last.identity.port) : '',
      explanation, app.id || '', app.name || '',
      route.upstream || '', route.target || '',
      merge(branch => branch.backend.host), merge(branch => branch.backend.port), merge(branch => branch.backend.resolution),
      ...components.map((_, col) => merge(branch => branch.components[col])),
      terminal && branches.some(branch => branch.backend.resolution === 'hostname' && !loopbackHost(branch.backend.host)) ? ipOf(last) : '',
      terminal && branches.some(branch => branch.backend.resolution === 'hostname' && !loopbackHost(branch.backend.host)) ? String(last.identity.port) : '',
      merge(branch => branch.chain), chainStatus, chainConfidence, app.status, app.evidence || '',
      mergeValues([...branches.flatMap(branch => branch.explanations), app.reason, app.assetReason]), sourceVersion,
      route.file || '', route.instance || '', merge(branch => branch.observedAt),
    ];
  });
  return { rows, counts, frontend: [...proxiedFrontend.values()] };
}

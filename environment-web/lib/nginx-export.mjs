import { isIP } from 'node:net';
import { normalizeIp } from './topology/common.ts';

// Transform normalized snapshot routes without altering their source evidence.
export const routeHeaders = ['nginxServerIp', 'hostname', 'domains', 'listen', 'uri', 'directive', 'upstream', 'target', 'context', 'backendHost', 'backendPort', 'backendResolution'];
export const frontendHeaders = ['Nginx服务器IP', '主机名', '域名', 'Listen端口', 'URI', '前端URL', '本地部署目录', '静态文件服务器IP+端口'];
const str = value => value == null ? '' : String(value);
const join = value => Array.isArray(value) ? value.map(str).join('|') : str(value);
const internalApplicationLocations = new Set(['@unbuffered', '@unbuffered_request', '@unbuffered_response', '= /kong_buffered_http']);

export function isInternalApplicationLocation(uri) {
  return internalApplicationLocations.has(str(uri).trim().replace(/\s+/g, ' '));
}

export function routeRows(snapshot) {
  const rows = [];
  for (const asset of snapshot?.assets ?? []) {
    for (const route of asset.inspection?.nginxRoutes ?? []) {
      const backends = Array.isArray(route.backends) && route.backends.length ? route.backends : [{}];
      for (const backend of backends) rows.push([
        str(asset.ip), str(asset.hostname), join(route.domains), join(route.listen), str(route.uri),
        str(route.directive), str(route.upstream), str(route.target), str(route.context),
        str(backend.host), str(backend.port), str(backend.resolution),
      ]);
    }
  }
  return rows;
}

// Regex and named locations do not identify a concrete application URL.
export function frontendPath(uri) {
  let path = str(uri).trim();
  if (/^(?:~\*?|@)/.test(path)) return null;
  path = path.replace(/^(?:=|\^~)\s*/, '');
  if (!path.startsWith('/') || /[\\*?\[\](){}$]/.test(path)) return null;
  if (/\.[a-z\d]{1,10}$/i.test(path.split('/').pop() || '')) return null;
  return path;
}

export function frontendRow(asset, route, domain, path, directory, staticEndpoint = '') {
  return [
    str(asset.ip), str(asset.hostname), str(domain), join(route.listen), str(route.uri),
    domain && domain !== '_' && !String(domain).includes('*') ? `${domain}${path}` : '', str(directory), staticEndpoint,
  ];
}

export function frontendRows(snapshot) {
  const rows = [];
  for (const asset of snapshot?.assets ?? []) {
    for (const route of asset.inspection?.nginxRoutes ?? []) {
      if (route.directive !== 'static/other' || !str(route.target).trim() || isInternalApplicationLocation(route.uri)) continue;
      const path = frontendPath(route.uri);
      if (path === null) continue;
      const domains = Array.isArray(route.domains) && route.domains.length ? route.domains : [''];
      for (const domain of domains) rows.push(frontendRow(asset, route, domain, path, route.target));
    }
  }
  return rows;
}

const assetAddresses = asset => str(asset.ip).split(/[\s,]+/).filter(Boolean).map(normalizeIp);

export function loopbackHost(value) {
  const host = normalizeIp(value).replace(/\.$/, '');
  return host === 'localhost' || host === '::1' || (isIP(host) === 4 && host.startsWith('127.')) ? host : '';
}

function isExplicitLoginDenial(reason) {
  // The collector also emits combined messages such as “SSH 认证失败或账号无权限”.
  // Those do not identify permission as the cause of the failure.
  return /^(?:没有授权\s+SSH\s+账号|无权限访问|(?:SSH:\s*)?permission denied(?:\s*\([^)]*\))?)[。.]?$/i.test(str(reason).trim());
}

export function createAssetInventory(jumpserver, ecs) {
  const ecsAddresses = new Set((ecs?.instances ?? []).flatMap(instance =>
    [...(instance.privateIps ?? []), ...(instance.publicIps ?? [])].map(normalizeIp)));
  const ecsComplete = Array.isArray(ecs?.instances) && ecs.available !== false && ecs.complete !== false &&
    (ecs.tenants?.length ? ecs.tenants.every(tenant => tenant.complete === true) : ecs.schemaVersion !== 2);
  const byIp = new Map();
  for (const asset of jumpserver?.assets ?? []) {
    for (const ip of new Set(assetAddresses(asset))) {
      const assets = byIp.get(ip) ?? [];
      assets.push(asset);
      byIp.set(ip, assets);
    }
  }
  function missingJumpServerAsset(ip) {
    if (byIp.has(ip)) return undefined;
    const reason = '快照中没有该地址的 JumpServer 资产';
    if (ecsAddresses.has(ip)) return {
      status: 'UNKNOWN', reason, assetStatus: 'JumpServer未收录', assetReason: '当前 ECS 清单存在该地址',
    };
    if (ecsComplete && Array.isArray(jumpserver?.assets) && jumpserver.available !== false) return {
      status: 'UNKNOWN', reason, assetStatus: '资产已下线', assetReason: '当前 ECS 清单也未找到该地址，按清单口径标记',
    };
    return { status: 'UNKNOWN', reason };
  }
  return { byIp, ecsAddresses, ecsComplete, missingJumpServerAsset };
}

// Require both a directly observed listener/PID and a matching deployment;
// address-only correlation or an ambiguous process can never establish an ID.
export function createEndpointProcessMatcher(jumpserver, devops, ecs, inventory = createAssetInventory(jumpserver, ecs)) {
  const { byIp, ecsAddresses, ecsComplete, missingJumpServerAsset } = inventory;
  const norm = value => str(value).trim().toLowerCase().replace(/\.(?:jar|war)$/i, '').replace(/[-_.]/g, '');
  const deploymentsByIp = new Map();
  for (const app of devops?.apps ?? []) {
    for (const deployments of Object.values(app.envs ?? {})) {
      for (const deployment of Array.isArray(deployments) ? deployments : []) {
        const ip = normalizeIp(deployment.ip);
        const port = Number(deployment.port || app.port);
        if (!ip || !str(app.id)) continue;
        const apps = deploymentsByIp.get(ip) ?? new Map();
        const candidate = apps.get(str(app.id)) ?? { app, ports: new Set() };
        candidate.ports.add(port || null);
        apps.set(str(app.id), candidate);
        deploymentsByIp.set(ip, apps);
      }
    }
  }

  const cache = new Map();
  function match(ip, port, loopback) {
    const assets = byIp.get(ip) ?? [];
    if (!assets.length) return missingJumpServerAsset(ip);
    if (assets.length > 1) return { status: 'AMBIGUOUS', reason: '该地址对应多个 JumpServer 资产' };
    const inspection = assets[0].inspection ?? {};
    const portNotCollected = inspection.portStatus === 'failed' || inspection.portStatus === 'not_collected';
    if (inspection.loginStatus === 'cannot_login') {
      const reason = portNotCollected ? 'TCP 监听采集未完成' : '没有可确认的进程监听证据';
      if (ecsComplete && !assetAddresses(assets[0]).some(address => ecsAddresses.has(address))) {
        return { status: 'UNKNOWN', reason, assetStatus: '资产已下线', assetReason: 'JumpServer 无法登录，当前 ECS 清单未找到该资产地址，按清单口径标记' };
      }
      if (isExplicitLoginDenial(inspection.reason)) {
        return { status: 'UNKNOWN', reason, assetStatus: '无权限访问', assetReason: 'JumpServer 无法登录，检查明确记录缺少 SSH 授权或访问被拒' };
      }
      // Keep the existing collection diagnostics for all other failures.
    }
    if (portNotCollected) {
      return { status: 'UNKNOWN', reason: 'TCP 监听采集未完成' };
    }
    const processes = new Map((inspection.processes ?? []).map(process => [Number(process.pid), process]));
    const address = loopback || ip;
    const observedAddresses = address === 'localhost' ? ['127.0.0.1', '::1'] : [address];
    const compatibleBindings = new Set(['*', '::', ...observedAddresses, ...(address.includes(':') ? [] : ['0.0.0.0'])]);
    const portListeners = (inspection.appPorts ?? []).filter(listener => Number(listener.port) === port &&
      !(listener.addresses?.length && listener.addresses.every(address => {
        return !compatibleBindings.has(normalizeIp(address));
      })));
    if (!portListeners.length && inspection.portStatus === 'complete') {
      return { status: 'UNKNOWN', reason: '未观察到该地址和端口的 TCP 监听', matchExplanation: '端口未监听' };
    }
    const listeners = portListeners.filter(listener =>
      !listener.ownershipConfidence && listener.addresses?.length &&
      Array.isArray(listener.pids) && listener.pids.length > 0 && listener.pids.every(pid => processes.has(Number(pid))) &&
      str(listener.app).trim());
    if (!listeners.length) return { status: 'UNKNOWN', reason: '没有可确认的进程监听证据' };
    const evidence = listeners.map(listener => `${listener.app} · PID ${listener.pids.join(', ')} · ${listener.addresses.join(', ')}:${port}`).join(' / ') +
      (inspection.checkedAt ? ` · ${inspection.checkedAt}` : '');
    const apps = [...(deploymentsByIp.get(ip)?.values() ?? [])];
    const matches = new Map();
    let unmatched = false;
    for (const listener of listeners) {
      for (const pid of listener.pids) {
        const process = processes.get(Number(pid));
        const { names, artifactNames } = processNames(listener, process);
        const identifiers = names.map(norm);
        let candidates = apps.filter(({ app }) => identifiers.includes(norm(app.name)) || identifiers.includes(norm(app.id)));
        const nameFromArtifact = !candidates.length;
        if (nameFromArtifact) {
          // Deployment instance and artifact suffixes are weaker name evidence.
          // Require the observed listener to match the declared port as well.
          const artifacts = artifactNames.map(norm);
          candidates = apps.filter(({ app, ports }) => ports.has(port) && artifacts.includes(norm(app.name)));
        }
        const exactPort = candidates.filter(candidate => candidate.ports.has(port));
        const eligible = exactPort.length ? exactPort : candidates.filter(candidate => candidate.ports.has(null));
        if (!eligible.length) unmatched = true;
        for (const candidate of eligible) {
          const id = str(candidate.app.id);
          matches.set(id, { ...candidate, nameFromArtifact: matches.get(id)?.nameFromArtifact || nameFromArtifact });
        }
      }
    }
    if (listeners.length !== portListeners.length || matches.size > 1 || (matches.size && unmatched)) {
      return { status: 'AMBIGUOUS', reason: '监听进程或应用存在无法合并的候选', evidence };
    }
    if (!matches.size) return {
      status: 'UNKNOWN', reason: '未找到与进程及端口一致的应用', evidence, matchExplanation: '未能匹配应用',
    };
    const { app, ports, nameFromArtifact } = matches.values().next().value;
    const reason = nameFromArtifact ? '进程实例名或包名对应应用，监听端口与部署声明一致'
      : ports.has(port) ? '进程监听与部署声明一致' : '按实际进程监听确认端口，DevOps 未声明端口';
    return { status: 'INFERRED', id: str(app.id), name: str(app.name), reason, evidence };
  }

  return (ip, port, loopback = '') => {
    const address = normalizeIp(ip);
    const key = JSON.stringify([address, Number(port), loopback]);
    if (!cache.has(key)) cache.set(key, match(address, Number(port), loopback));
    return cache.get(key);
  };
}

function processNames(listener, process) {
  const generic = /^(?:java|tomcat|nginx|node(?:\.js)?|python|未知进程)$/i;
  const names = [listener.app].filter(name => !generic.test(name));
  const artifactNames = [];
  const command = str(process.command);
  const identity = /(?:^|\s)(?:-Dappid|--appid|-Dspring\.application\.name)=([\w.-]+)(?=\s|$)/g;
  for (const match of command.matchAll(identity)) names.push(match[1]);
  const jar = /(?:^|\s)-jar\s+(?:"([^"\n]+)"|'([^'\n]+)'|(\S+))/.exec(command);
  if (jar) {
    const filename = (jar[1] || jar[2] || jar[3]).split('/').pop();
    names.push(filename);
    const artifact = /^(.+?)(?:-exec|-\d+(?:\.\d+)+(?:-SNAPSHOT)?)\.(?:jar|war)$/i.exec(filename);
    if (artifact) artifactNames.push(artifact[1]);
  }
  const catalinaBase = /(?:^|\s)-Dcatalina\.base=([^\s]+)/i.exec(command)?.[1];
  const catalinaHome = /(?:^|\s)-Dcatalina\.home=([^\s]+)/i.exec(command)?.[1];
  const catalina = /\/(?:apache-)?tomcat[_-]([^/\s]+)\/*$/i.exec(catalinaBase || catalinaHome || '');
  if (catalina) {
    names.push(catalina[1]);
    const instance = /^(.+)_\d{4,5}$/.exec(catalina[1]);
    if (instance) artifactNames.push(instance[1]);
    if (catalina[1].endsWith('_' + listener.port)) names.push(catalina[1].slice(0, -String(listener.port).length - 1));
  }
  return { names: names.filter(Boolean), artifactNames };
}

export function matchEndpointProcess(jumpserver, devops, ip, port) {
  return createEndpointProcessMatcher(jumpserver, devops)(ip, port);
}

export function csv(rows) {
  const cell = value => {
    const text = str(value);
    return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };
  return '\ufeff' + rows.map(row => row.map(cell).join(',')).join('\r\n') + '\r\n';
}

// Transform normalized snapshot routes without altering their source evidence.
export const routeHeaders = ['nginxServerIp', 'hostname', 'domains', 'listen', 'uri', 'directive', 'upstream', 'target', 'context', 'backendHost', 'backendPort', 'backendResolution'];
export const frontendHeaders = ['Nginx服务器IP', '主机名', '域名', 'Listen端口', 'URI', '前端URL', '本地部署目录'];
const str = value => value == null ? '' : String(value);
const join = value => Array.isArray(value) ? value.map(str).join('|') : str(value);

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
  path = path.replace(/^(?:=|\^~)\s+/, '');
  if (!path.startsWith('/') || /[\\*?\[\](){}$]/.test(path)) return null;
  if (/\.[a-z\d]{1,10}$/i.test(path.split('/').pop() || '')) return null;
  return path;
}

export function frontendRows(snapshot) {
  const rows = [];
  for (const asset of snapshot?.assets ?? []) {
    for (const route of asset.inspection?.nginxRoutes ?? []) {
      if (route.directive !== 'static/other') continue;
      const path = frontendPath(route.uri);
      if (path === null) continue;
      const domains = Array.isArray(route.domains) && route.domains.length ? route.domains : [''];
      for (const domain of domains) rows.push([
        str(asset.ip), str(asset.hostname), str(domain), join(route.listen), str(route.uri),
        domain && domain !== '_' && !String(domain).includes('*') ? `${domain}${path}` : '', str(route.target),
      ]);
    }
  }
  return rows;
}

// Require both a directly observed listener/PID and a matching deployment;
// address-only correlation or an ambiguous process can never establish an ID.
export function matchEndpointProcess(jumpserver, devops, ip, port) {
  const assets = (jumpserver?.assets ?? []).filter(asset => str(asset.ip).split(/[\s,]+/).includes(str(ip)));
  if (assets.length !== 1) return { status: assets.length ? 'AMBIGUOUS' : 'UNKNOWN', reason: '资产地址不唯一或未采集' };
  const inspection = assets[0].inspection ?? {};
  const processes = new Set((inspection.processes ?? []).map(process => Number(process.pid)));
  const portListeners = (inspection.appPorts ?? []).filter(listener => Number(listener.port) === Number(port) && Number(port) > 0);
  const listeners = portListeners.filter(listener =>
    !['AMBIGUOUS', 'UNKNOWN'].includes(listener.ownershipConfidence) &&
    Array.isArray(listener.pids) && listener.pids.length > 0 && listener.pids.every(pid => processes.has(Number(pid))) &&
    str(listener.app).trim());
  if (!listeners.length) return { status: 'UNKNOWN', reason: '没有可确认的进程监听证据' };
  if (listeners.length !== portListeners.length) return { status: 'AMBIGUOUS', reason: '存在无法确认归属的监听记录' };
  const norm = value => str(value).trim().toLowerCase();
  const names = new Set(listeners.map(listener => norm(listener.app)));
  const candidates = (devops?.apps ?? []).filter(app => names.has(norm(app.name)) &&
    Object.values(app.envs ?? {}).some(deployments => Array.isArray(deployments) && deployments.some(deployment =>
      str(deployment.ip).trim() === str(ip) && str(deployment.port || app.port).trim() === str(port))));
  const unique = [...new Map(candidates.map(app => [str(app.id), app])).values()].filter(app => str(app.id));
  if (names.size !== 1 || unique.length > 1) return { status: 'AMBIGUOUS', reason: '监听进程或应用存在多个候选' };
  if (!unique.length) return { status: 'UNKNOWN', reason: '未找到与进程及端口一致的应用' };
  return { status: 'INFERRED', id: str(unique[0].id), name: str(unique[0].name), reason: '进程监听与部署声明一致' };
}

export function csv(rows) {
  const cell = value => {
    const text = str(value);
    return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };
  return '\ufeff' + rows.map(row => row.map(cell).join(',')).join('\r\n') + '\r\n';
}

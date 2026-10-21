import { jumpserver } from '@/lib/jumpserver';
import { jarName, type Process, type Asset, type Webapp, type AppPort } from '@/lib/server-processes';
import { apps, envs, inventoryDate, type App, type Env } from '@/lib/inventory';
import { specs as serverSpecs, type ServerSpec } from '@/lib/server-specs';
import { instances } from '@/lib/ecs';
import { nominalMemoryGiB } from '@/lib/local-servers';

export type AppComparison = {
  app: App & { ports: string[] };
  matched: boolean;
  process?: Process;
  supplementedPort?: string;
  // 进程未匹配时回退到 Webapps 应用目录匹配；命中时填充该 Webapp。
  webapp?: Webapp;
};

export type ServerComparison = {
  ip: string;
  available: boolean;
  loginStatus?: string;
  processStatus?: string;
  processes: Process[];
  apps: AppComparison[];
  extraProcesses: Process[];
};

function tokenizeArgs(command: string): string[] {
  return command.match(/"(?:\\.|[^"\\])*"|'[^']*'|[^\s]+/g)?.map((s) => s.replace(/^(['"])(.*)\1$/, '$2')) || [];
}

function jarBaseName(command: string): string | undefined {
  const tokens = tokenizeArgs(command);
  const i = tokens.indexOf('-jar');
  if (i < 0 || !tokens[i + 1]) return undefined;
  const file = tokens[i + 1].split('/').pop() || '';
  return file.replace(/\.(jar|war)$/i, '').toLowerCase();
}

function normalize(value: string): string {
  return value.toLowerCase().replace(/[-_.]/g, '');
}

const PORT_PATTERNS: RegExp[] = [
  /--server\.port[=\s]+(\d{2,5})\b/,
  /-Dserver\.port=(\d{2,5})\b/,
  /--port[=\s]+(\d{2,5})\b/,
  /-p\s+(\d{2,5})\b/,
];

export function extractPortFromCommand(command: string): string | undefined {
  for (const pattern of PORT_PATTERNS) {
    const match = command.match(pattern);
    if (match) {
      const port = parseInt(match[1], 10);
      if (port >= 1 && port <= 65535) return match[1];
    }
  }
  return undefined;
}

function findAssetByIp(ip: string): Asset | undefined {
  return (jumpserver.assets as Asset[]).find((a) => a.ip === ip);
}

export function processesForIp(ip: string): Process[] {
  const asset = findAssetByIp(ip);
  if (!asset?.inspection || asset.inspection.loginStatus !== 'can_login') return [];
  return asset.inspection.processes || [];
}

// 取该 IP 上已采集的 Webapps 应用目录列表（仅在可登录服务器上有效）。
// absent/not_collected/未采集时返回空数组。
export function webappsForIp(ip: string): Webapp[] {
  const asset = findAssetByIp(ip);
  const webapps = asset?.inspection?.webapps;
  if (!webapps) return [];
  if (webapps.status === 'absent' || webapps.status === 'not_collected') return [];
  return webapps.apps || [];
}

function inspectionInfo(ip: string): { loginStatus?: string; processStatus?: string } {
  const asset = findAssetByIp(ip);
  if (!asset?.inspection) return {};
  return { loginStatus: asset.inspection.loginStatus, processStatus: asset.inspection.processStatus };
}

function processIdentifier(process: Process): { jarName?: string; name: string; kind: string } {
  return { jarName: jarBaseName(process.command), name: process.name.toLowerCase(), kind: process.kind };
}

function appMatchesProcess(appName: string, process: Process): boolean {
  const appNorm = normalize(appName);
  if (appNorm.length < 2) return false;
  const { jarName, name, kind } = processIdentifier(process);
  // 1. Jar name match (java -jar app.jar)
  if (jarName && (jarName === appNorm || jarName.includes(appNorm) || appNorm.includes(jarName))) return true;
  // 2. Nginx app + Nginx process
  if (kind === 'Nginx' && (appName.toLowerCase() === 'nginx' || appName.toLowerCase().includes('nginx'))) return true;
  const cmdLower = process.command.toLowerCase();
  const appLower = appName.toLowerCase();
  // 3. -Dappid=APP_NAME (Spring Boot convention used by this project)
  if (new RegExp('-Dappid=' + escapeRegex(appLower) + '\\b').test(cmdLower)) return true;
  // 4. App name as a path segment (/webapps/APP_NAME/ or /APP_NAME/) — skip common dirs
  if (appLower.length >= 3 && !COMMON_PATH_WORDS.has(appLower) && new RegExp('/' + escapeRegex(appLower) + '[/.]').test(cmdLower)) return true;
  // 5. Tomcat base path convention: tomcat_APP_NAME_ or tomcat_APP_NAME/
  if (appLower.length >= 3 && new RegExp('tomcat[_/]' + escapeRegex(appLower) + '[_/.]').test(cmdLower)) return true;
  // 6. Generic command substring (only for names long enough to avoid false positives, skip common words)
  if (appLower.length >= 6 && !COMMON_PATH_WORDS.has(appLower) && cmdLower.includes(appLower)) return true;
  // 7. Non-generic process name match
  if (name === appNorm && name.length >= 4 && !isGenericProcessName(name)) return true;
  return false;
}

function escapeRegex(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function isGenericProcessName(name: string): boolean {
  return ['java', 'node', 'python', 'python3', 'redis-server', 'mysqld', 'postgres'].includes(name);
}

const COMMON_PATH_WORDS = new Set(['www', 'app', 'apps', 'var', 'opt', 'lib', 'libs', 'bin', 'sbin', 'etc', 'tmp', 'srv', 'home', 'usr', 'local', 'conf', 'config', 'static', 'webapps', 'share', 'data', 'log', 'logs']);

export function matchAppToProcess(app: App & { ports: string[] }, processes: Process[]): Process | undefined {
  for (const process of processes) {
    if (appMatchesProcess(app.name, process)) return process;
  }
  return undefined;
}

// 进程未匹配时，与 /var/www/webapps 一级子目录名做精确（忽略大小写与分隔符）匹配。
// 仅过滤过短（<2）的名称；webapps 目录名本身就是应用名（如 static、www），
// 不再套用进程匹配用的 COMMON_PATH_WORDS 通用目录词过滤。
export function matchAppToWebapp(appName: string, webapps: Webapp[]): Webapp | undefined {
  const appNorm = normalize(appName);
  if (appNorm.length < 2) return undefined;
  for (const webapp of webapps) {
    const nameNorm = normalize(webapp.name);
    if (nameNorm.length < 2) continue;
    if (appNorm === nameNorm) return webapp;
  }
  return undefined;
}

export function compareServerApps(ip: string, apps: Array<App & { ports: string[] }>): ServerComparison {
  const processes = processesForIp(ip);
  const webapps = webappsForIp(ip);
  const info = inspectionInfo(ip);
  const usedProcesses = new Set<Process>();
  const appComparisons: AppComparison[] = apps.map((app) => {
    const process = matchAppToProcess(app, processes);
    let supplementedPort: string | undefined;
    let webapp: Webapp | undefined;
    if (process) {
      usedProcesses.add(process);
      const hasEmptyPort = app.ports.every((p) => !p || p === '未提供端口');
      if (hasEmptyPort) supplementedPort = extractPortFromCommand(process.command);
    } else {
      // 进程未匹配时回退到 Webapps 应用目录匹配；命中视为匹配，不再标红。
      webapp = matchAppToWebapp(app.name, webapps);
    }
    return { app, matched: !!process || !!webapp, process: process, supplementedPort, webapp };
  });
  const extraProcesses = processes.filter((p) => !usedProcesses.has(p));
  return {
    ip,
    available: processes.length > 0,
    loginStatus: info.loginStatus,
    processStatus: info.processStatus,
    processes,
    apps: appComparisons,
    extraProcesses,
  };
}

export function serverComparisonAvailable(ip: string): boolean {
  const asset = findAssetByIp(ip);
  return !!(asset?.inspection && asset.inspection.loginStatus === 'can_login');
}

export function appHasProcessOnIp(appName: string, ip: string): boolean {
  const processes = processesForIp(ip);
  if (!processes.length) return false;
  for (const process of processes) {
    if (appMatchesProcess(appName, process)) return true;
  }
  return false;
}

// 进程实际监听端口：优先 ss 采集的 appPorts（按 PID 关联，可能多个），
// 其次解析进程命令行中的端口参数，最后回退调用方给的清单声明端口；均无返回空串。
export function processListeningPort(
  process: Process,
  ip: string,
  fallbackPort?: string,
): string {
  const appPorts = findAssetByIp(ip)?.inspection?.appPorts;
  const listened = (appPorts || [])
    .filter((ap: AppPort) => ap.pids.includes(process.pid))
    .map((ap) => String(ap.port));
  if (listened.length) return [...new Set(listened)].join('、');
  const fromCommand = extractPortFromCommand(process.command);
  if (fromCommand) return fromCommand;
  return fallbackPort?.trim() || '';
}

// Tomcat 实例目录：优先取 -Dcatalina.base/home，其次从命令行中出现的
// apache-tomcat_* 路径提取（如 logging.config.file 指向的目录）。
function tomcatBase(command: string): string | undefined {
  const tokens = tokenizeArgs(command);
  for (const key of ['-Dcatalina.base=', '-Dcatalina.home=']) {
    const token = tokens.find((t) => t.startsWith(key));
    if (token) return token.slice(key.length).replace(/\/+$/, '');
  }
  const match = command.match(/(\/[\w./~-]*apache-tomcat[\w.-]*)/i);
  return match?.[1].replace(/\/+$/, '');
}

// 进程业务名：java 只是运行时名，不具备业务意义。优先取 -jar 的 jar 包名，
// 其次 Tomcat 实例目录（如 /opt/apache-tomcat_phoenix-open-web_37864），
// 都没有时回退进程名（nginx/node/redis-server 等本身即有标识意义）。
export function processBusinessName(process: Process): string {
  return jarName(process) || tomcatBase(process.command) || process.name;
}

// Tomcat 实例业务名：取实例目录（-Dcatalina.base/home 或命令行中的 apache-tomcat_*
// 路径）的 basename 并去掉 tomcat_/apache-tomcat_ 前缀。
// 如 /opt/apache-tomcat_dolphin-webhook → dolphin-webhook；
// 目录名仅为通用 tomcat 名（无业务名段）或无法提取实例目录时返回 undefined。
export function tomcatInstanceName(command: string): string | undefined {
  const base = tomcatBase(command);
  if (!base) return undefined;
  const leaf = base.split('/').filter(Boolean).pop() || '';
  // 必须带分隔符后再跟业务名段；apache-tomcat / tomcat 这类通用目录名返回 undefined。
  const match = leaf.match(/^(?:apache-)?tomcat[_-](.+)$/i);
  return match?.[1]?.trim() || undefined;
}

// Java -jar 进程的展示业务名：-jar 参数的 jar 文件名去掉 .jar/.war 后缀，
// 保留原始大小写；非 `-jar` 启动的进程返回 undefined。
export function jarBusinessName(command: string): string | undefined {
  const tokens = tokenizeArgs(command);
  const i = tokens.indexOf('-jar');
  if (i < 0 || !tokens[i + 1]) return undefined;
  const file = tokens[i + 1].split('/').pop() || '';
  const stripped = file.replace(/\.(jar|war)$/i, '');
  return stripped.trim() || undefined;
}

// 进程关联的 DevOps 应用：仅在该 IP 有部署记录（任一环境）的应用中按既有
// 进程匹配规则（matchAppToProcess）查找；多个候选时优先与进程业务名规范化后
// 完全相同的应用，否则取首个候选（与 IP 集查询的关联口径一致）；无候选返回 undefined。
function resolveProcessApp(
  ip: string,
  process: Process,
  businessName: string | undefined,
  appList: App[],
): App | undefined {
  const target = ip.trim();
  const candidates = appList.filter(
    (a) =>
      envs.some((e) => (a.envs[e] || []).some((r) => (r.ip || '').trim() === target)) &&
      matchAppToProcess({ ...a, ports: [] }, [process]),
  );
  if (!candidates.length) return undefined;
  if (businessName) {
    const exact = candidates.find((a) => normalize(a.name) === normalize(businessName));
    if (exact) return exact;
  }
  return candidates[0];
}

// Tomcat 进程关联的 DevOps 应用（业务名取 catalina 实例目录，含 tomcat_APP_NAME
// 目录约定）。
export function tomcatProcessApp(
  ip: string,
  process: Process,
  appList: App[] = apps,
): App | undefined {
  return resolveProcessApp(ip, process, tomcatInstanceName(process.command), appList);
}

// Java Jar 进程关联的 DevOps 应用（业务名取 -jar 的 jar 文件名，去 .jar/.war 后缀）。
export function jarProcessApp(
  ip: string,
  process: Process,
  appList: App[] = apps,
): App | undefined {
  return resolveProcessApp(ip, process, jarBusinessName(process.command), appList);
}

export type AppMatchNote =
  | { kind: 'process'; pid: number; processName: string; port: string }
  | { kind: 'webapp'; webappName: string };

// 应用在指定 IP 上的匹配说明（应用列表展开明细「说明」列使用）：
// 先匹配 JumpServer 进程（PID/进程名/监听端口），进程未命中再回退 webapps 同名目录，
// 都未命中返回 undefined，由调用方展示原有错误/占位文案。
export function appMatchOnIp(
  app: App,
  ip: string,
  fallbackPort?: string,
): AppMatchNote | undefined {
  const process = matchAppToProcess(
    { ...app, ports: [] },
    processesForIp(ip),
  );
  if (process) {
    return {
      kind: 'process',
      pid: process.pid,
      processName: processBusinessName(process),
      port: processListeningPort(process, ip, fallbackPort),
    };
  }
  const webapp = matchAppToWebapp(app.name, webappsForIp(ip));
  if (webapp) return { kind: 'webapp', webappName: webapp.name };
  return undefined;
}

// 解析批量 IP 输入：支持换行、空白、逗号、分号分隔，去重并保留首次出现顺序。
export function parseIpList(text: string): string[] {
  const seen = new Set<string>();
  const ips: string[] = [];
  for (const token of text.split(/[\s,;]+/)) {
    const ip = token.trim();
    if (!ip || seen.has(ip)) continue;
    seen.add(ip);
    ips.push(ip);
  }
  return ips;
}

export type IpSetAppHit = {
  appId: string;
  appName: string;
  appType: string;
  // 该 IP 在此应用的哪些环境中出现。
  envs: Env[];
};

export type IpSetQueryRow = {
  ip: string;
  matched: boolean;
  hits: IpSetAppHit[];
};

// 按一组 IP 精确匹配关联应用：逐 IP、逐应用、逐环境比对部署记录中的 IP，
// 使用精确相等（trim 后），不做子串匹配，避免 10.0.0.1 误匹配 10.0.0.10。
// 返回行顺序与输入 IP 顺序一致；未命中的 IP 返回 matched:false、空 hits。
export function queryAppsByIpSet(
  ips: string[],
  inventory: App[],
): IpSetQueryRow[] {
  return ips.map((ip) => {
    const hits: IpSetAppHit[] = [];
    for (const app of inventory) {
      const hitEnvs = envs.filter((e) =>
        (app.envs[e] || []).some((r) => (r.ip || '').trim() === ip),
      );
      if (hitEnvs.length) {
        hits.push({
          appId: app.id,
          appName: app.name,
          appType: app.appType || '',
          envs: [...hitEnvs],
        });
      }
    }
    return { ip, matched: hits.length > 0, hits };
  });
}

// 该 IP 在 DevOps 部署记录中出现于哪些环境（按 TEST/SIMULATION/PRODUCT
// 固定顺序返回）；任一应用在该环境有该 IP 的部署记录即计入。
export function ipDeployEnvironments(ip: string, appList: App[] = apps): Env[] {
  const target = ip.trim();
  return envs.filter((e) =>
    appList.some((a) => (a.envs[e] || []).some((r) => (r.ip || '').trim() === target)),
  );
}

// 指定应用在指定 IP 上部署于哪些环境（按 TEST/SIMULATION/PRODUCT 顺序返回）。
export function appDeployEnvironments(app: App, ip: string): Env[] {
  const target = ip.trim();
  return envs.filter((e) =>
    (app.envs[e] || []).some((r) => (r.ip || '').trim() === target),
  );
}

// 服务器 IP 是否被 JumpServer 收录（不论是否可登录/是否采集到进程）。
export function ipInJumpServer(ip: string): boolean {
  return (jumpserver.assets as Asset[]).some((a) => a.ip === ip);
}

// server-specs 按 IP 索引（一条记录可能以空白/逗号分隔包含多个 IP）。
const specByIp = new Map<string, ServerSpec>();
for (const s of serverSpecs) {
  for (const ip of s.ip.split(/[\s,]+/)) {
    const trimmed = ip.trim();
    if (trimmed) specByIp.set(trimmed, s);
  }
}

// ECS 实例按 IP 索引（内网 + 公网）。
const ecsByIp = new Map<string, (typeof instances)[number]>();
for (const inst of instances) {
  for (const ip of [...inst.privateIps, ...inst.publicIps]) ecsByIp.set(ip, inst);
}

export type IpServerState = 'not_collected' | 'can_login' | 'cannot_login';

// 按 IP 判定 JumpServer 收录与登录状态：
// - not_collected：JumpServer 资产中不存在该 IP；
// - can_login：server-specs 登录状态为 can_login（旧格式缺 loginStatus 时回退 cpu>0 推断），
//   或 JumpServer 巡检可登录；
// - cannot_login：已收录但两处数据均无成功登录证据。
export function ipServerState(ip: string): IpServerState {
  if (!ipInJumpServer(ip)) return 'not_collected';
  const spec = specByIp.get(ip);
  if (spec) {
    if (spec.loginStatus === 'can_login') return 'can_login';
    if (spec.loginStatus === 'cannot_login') return 'cannot_login';
    if (spec.loginStatus === undefined && spec.cpu > 0) return 'can_login';
  }
  const loginStatus = findAssetByIp(ip)?.inspection?.loginStatus;
  if (loginStatus === 'can_login') return 'can_login';
  return 'cannot_login';
}

// 服务器规格文本，如 "2C 4G"：本地服务器取 server-specs（内存取标称容量），
// 云服务器取 ECS 实例规格；无规格数据返回空串。
export function ipSpecSummary(ip: string): string {
  const spec = specByIp.get(ip);
  const collected = !!(spec && (spec.specsCollected ?? spec.cpu > 0));
  if (spec && collected && spec.cpu > 0)
    return `${spec.cpu}C ${nominalMemoryGiB(spec.memoryMB)}G`;
  const inst = ecsByIp.get(ip);
  if (inst && inst.cpu > 0) return `${inst.cpu}C ${inst.memoryGiB}G`;
  return '';
}

// 汇总应用在所有环境的部署 IP 与 JumpServer 进程的对比结果。
// - hasUnmatched: 任意环境存在「IP 已被 JumpServer 收录但未匹配到该应用进程」
// - hasNotCollected: 任意环境存在「IP 未被 JumpServer 收录」
export function appProcessStatusSummary(app: App): {
  hasUnmatched: boolean;
  hasNotCollected: boolean;
} {
  let hasUnmatched = false;
  let hasNotCollected = false;
  for (const rows of Object.values(app.envs)) {
    for (const row of rows) {
      const ip = row.ip?.trim();
      if (!ip) continue;
      if (!ipInJumpServer(ip)) {
        hasNotCollected = true;
      } else if (!appHasProcessOnIp(app.name, ip)) {
        hasUnmatched = true;
      }
    }
  }
  return { hasUnmatched, hasNotCollected };
}

// 按应用单个环境的部署 IP 汇总收录/登录/进程匹配情况，供导出按
// 「应用 × 环境」一行区分「未收录」「未登录」与真实的「进程未发现」
// （仅可登录服务器才能核实进程）。
export type ServerVerdicts = {
  notCollected: boolean;
  cannotLogin: boolean;
  canLogin: boolean;
  matched: boolean;
  unmatched: boolean;
};

export function envServerVerdicts(app: App, env: Env): ServerVerdicts {
  let notCollected = false;
  let cannotLogin = false;
  let canLogin = false;
  let matched = false;
  let unmatched = false;
  const seen = new Set<string>();
  for (const row of app.envs[env] || []) {
    const ip = row.ip?.trim();
    if (!ip || seen.has(ip)) continue;
    seen.add(ip);
    const state = ipServerState(ip);
    if (state === 'not_collected') {
      notCollected = true;
    } else if (state === 'cannot_login') {
      cannotLogin = true;
    } else {
      canLogin = true;
      if (appHasProcessOnIp(app.name, ip)) matched = true;
      else unmatched = true;
    }
  }
  return { notCollected, cannotLogin, canLogin, matched, unmatched };
}

// 解析部署时间字符串为时间戳；「YYYY-MM-DD HH:MM:SS」按 +08:00 处理，
// 其余（含 ISO）交给 Date.parse。
function deploymentTimestamp(value: string) {
  return Date.parse(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(value) ? value.replace(' ', 'T') + '+08:00' : value);
}

// 统一复用 lib/datetime 的北京时间格式化（所有界面时间展示走同一实现）。
export { formatBeijingTime } from './datetime';

// 应用在指定环境（或全部环境）的最近部署时间：
// 遍历该应用部署 IP，匹配 JumpServer 进程（startedAt）与 webapps 目录（mtime），
// 取两者中最新的一条；都未命中返回空串（页面展示「无成功记录」）。
// 用途：替代 DevOps Push In 时间，作为应用列表「最近成功 Push In」列的数据来源。
export function latestAppDeploymentTime(app: App, env?: Env): string {
  const candidates: string[] = [];
  const rows = env ? app.envs[env] : envs.flatMap(e => app.envs[e]);
  const seenIps = new Set<string>();
  for (const row of rows) {
    const ip = row.ip?.trim();
    if (!ip || seenIps.has(ip)) continue;
    seenIps.add(ip);
    const process = matchAppToProcess({ ...app, ports: [] }, processesForIp(ip));
    if (process?.startedAt) candidates.push(process.startedAt);
    const webapp = matchAppToWebapp(app.name, webappsForIp(ip));
    if (webapp?.mtime) candidates.push(webapp.mtime);
  }
  return candidates.filter(v => Number.isFinite(deploymentTimestamp(v))).sort((a, b) => deploymentTimestamp(b) - deploymentTimestamp(a))[0] || '';
}

// 当前环境存在部署服务器但最近部署时间超过一年（含无成功记录）。
export function appOverYearUndeployed(app: App, env: Env, reference = inventoryDate): boolean {
  const rows = app.envs[env] || [];
  if (!rows.some(row => row.ip?.trim())) return false;
  const now = Date.parse(reference);
  if (!Number.isFinite(now)) return false;
  const date = latestAppDeploymentTime(app, env);
  if (!date) return true;
  const deployed = deploymentTimestamp(date);
  if (!Number.isFinite(deployed) || deployed > now) return false;
  const current = new Date(now + 8 * 3600000);
  const day = current.getUTCDate();
  current.setUTCDate(1);
  current.setUTCMonth(current.getUTCMonth() - 12);
  current.setUTCDate(Math.min(day, new Date(Date.UTC(current.getUTCFullYear(), current.getUTCMonth() + 1, 0)).getUTCDate()));
  return deployed < current.getTime() - 8 * 3600000;
}

// 按部署时间区间筛选：未选环境或未选区间时放行；「超过一年」走 appOverYearUndeployed；
// 其余区间以 latestAppDeploymentTime 为基准。
export function appMatchesDeploymentPeriod(app: App, env: Env | '', period: string, reference = inventoryDate): boolean {
  if (!env || !period) return true;
  const now = Date.parse(reference);
  if (period === 'older') return appOverYearUndeployed(app, env, reference);
  const time = deploymentTimestamp(latestAppDeploymentTime(app, env));
  if (!Number.isFinite(time) || !Number.isFinite(now) || time > now) return false;
  const monthsAgo = (n: number) => {
    const d = new Date(now + 8 * 3600000), day = d.getUTCDate();
    d.setUTCDate(1); d.setUTCMonth(d.getUTCMonth() - n);
    d.setUTCDate(Math.min(day, new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 0)).getUTCDate()));
    return d.getTime() - 8 * 3600000;
  };
  const thresholds: Record<string, number> = { week: now - 7 * 86400000, fortnight: now - 15 * 86400000, month: monthsAgo(1), quarter: monthsAgo(3), half: monthsAgo(6), year: monthsAgo(12) };
  return period in thresholds && time >= thresholds[period];
}

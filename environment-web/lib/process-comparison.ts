import { jumpserver } from '@/lib/jumpserver';
import type { Process, Asset } from '@/lib/server-processes';
import type { App, Env } from '@/lib/inventory';
import { specs as serverSpecs, type ServerSpec } from '@/lib/server-specs';
import { instances } from '@/lib/ecs';
import { nominalMemoryGiB } from '@/lib/local-servers';

export type AppComparison = {
  app: App & { ports: string[] };
  matched: boolean;
  process?: Process;
  supplementedPort?: string;
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

export function compareServerApps(ip: string, apps: Array<App & { ports: string[] }>): ServerComparison {
  const processes = processesForIp(ip);
  const info = inspectionInfo(ip);
  const usedProcesses = new Set<Process>();
  const appComparisons: AppComparison[] = apps.map((app) => {
    const process = matchAppToProcess(app, processes);
    let supplementedPort: string | undefined;
    if (process) {
      usedProcesses.add(process);
      const hasEmptyPort = app.ports.every((p) => !p || p === '未提供端口');
      if (hasEmptyPort) supplementedPort = extractPortFromCommand(process.command);
    }
    return { app, matched: !!process, process: process, supplementedPort };
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

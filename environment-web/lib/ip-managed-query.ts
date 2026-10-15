import { jumpserver } from '@/lib/jumpserver';
import { jarName, processType, processTypes, type Asset, type Process, type Webapp, type ServerUsage, type AppPort } from '@/lib/server-processes';
import { envs, latestSuccessfulPushIn, type App } from '@/lib/inventory';
import {
  ipServerState,
  ipSpecSummary,
  processesForIp,
  webappsForIp,
  matchAppToProcess,
  matchAppToWebapp,
  formatBeijingTime,
  type IpServerState,
} from '@/lib/process-comparison';

export const MANAGED_YES = '已纳管';
export const MANAGED_NO = '未纳管';
export const DASH = '-';

export type ManagedRowSource =
  | 'unmanaged'
  | 'unreachable'
  | 'process'
  | 'webapp'
  | 'nginx'
  | 'empty';

// 一行 = 一个进程 / 一个 webapps 目录 / 一个 Nginx 配置文件；
// 未纳管、无法登录或采集为空的 IP 也占一行。
export type IpManagedRow = {
  ip: string;
  managed: string;
  // 服务器规格文本（如 "4C 64G"）；同一 IP 的所有行一致，无规格数据为 "-"。
  spec: string;
  source: ManagedRowSource;
  appType: string;
  detail: string;
  appId: string;
  appName: string;
  pushIn: string;
  pid?: number;
  // 进程完整启动命令（仅进程行携带；webapp/Nginx/兜底行为 undefined）。
  command?: string;
  // 内存占用信息（仅进程行携带；旧快照缺这些字段时展示 "-"）。
  memPercent?: number;
  rssKB?: number;
  vszKB?: number;
  // 进程监听端口（仅进程行携带；未监听或旧快照缺字段时为 undefined）。
  ports?: number[];
};

export type NginxConfigRef = { path: string };
export type NginxConfigResult = { files: NginxConfigRef[]; error?: string };

export type ManagedQueryDeps = {
  state(ip: string): IpServerState;
  // 服务器规格摘要（如 "4C 64G"），无数据返回空串。
  spec(ip: string): string;
  processes(ip: string): Process[];
  webapps(ip: string): Webapp[];
  matchProcess(app: App, processes: Process[]): Process | undefined;
  matchWebapp(appName: string, webapps: Webapp[]): Webapp | undefined;
  appPushIn(app: App): string;
  // 多个候选时间取最新一条并格式化为北京时间；无有效时间返回空串。
  latestTime(values: string[]): string;
};

function tokenize(command: string): string[] {
  return command.match(/"(?:\\.|[^"\\])*"|'[^']*'|[^\s]+/g)?.map((s) => s.replace(/^(['"])(.*)\1$/, '$2')) || [];
}

function timestampOf(value: string): number {
  return Date.parse(
    /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(value)
      ? value.replace(' ', 'T') + '+08:00'
      : value,
  );
}

// 取候选时间中最新的一条，统一格式化为 "YYYY-MM-DD HH:mm:ss"（北京时间）。
export function latestFormattedTime(values: string[]): string {
  const valid = values.filter((v) => v && Number.isFinite(timestampOf(v)));
  if (!valid.length) return '';
  valid.sort((a, b) => timestampOf(b) - timestampOf(a));
  return formatBeijingTime(valid[0]);
}

// 内存占用率列文本：如 "12.3%"；无数据返回 "-"。
export function formatMemPercent(value: number | undefined): string {
  return value === undefined ? DASH : `${value.toFixed(1)}%`;
}

// 实际内存列文本：RSS KB 人类可读（KB/MB/GB）；无数据返回 "-"。
export function formatMemorySize(kb: number | undefined): string {
  if (kb === undefined) return DASH;
  if (kb < 1024) return `${kb} KB`;
  const mb = kb / 1024;
  if (mb < 1024) return `${Math.round(mb)} MB`;
  return `${(mb / 1024).toFixed(2)} GB`;
}

// 监听端口列文本：如 "80, 443"；无监听或非进程行返回 "-"。
export function formatPorts(ports: number[] | undefined): string {
  return ports && ports.length ? ports.join(', ') : DASH;
}

const defaultDeps: ManagedQueryDeps = {
  state: ipServerState,
  spec: ipSpecSummary,
  processes: processesForIp,
  webapps: webappsForIp,
  matchProcess: (app, ps) => matchAppToProcess({ ...app, ports: [] }, ps),
  matchWebapp: matchAppToWebapp,
  appPushIn: latestSuccessfulPushIn,
  latestTime: latestFormattedTime,
};

type ProcessKind = { type: string; middleware: boolean };

// 进程归类为导出用应用类型；middleware 为 true 的类型（MySQL/Nginx/Redis/MQ
// 等共享中间件）不关联 DevOps 应用，应用 ID/名称/Push In 统一为 "-"。
// Tomcat 虽属中间件，但实例目录命名包含业务名（如 apache-tomcat_APPNAME_PID），
// 可与 DevOps 应用按 process-comparison.ts 的 tomcat_APP_NAME_ 规则匹配，
// 故归类为 middleware:false，尝试关联部署应用；未命中仍回退为 "-"。
function classifyProcess(p: Process): ProcessKind {
  switch (processType(p)) {
    case '系统进程':
      // 系统进程保留在导出中但单独标注；不关联 DevOps 应用（应用 ID/名称/Push In 为 "-"）。
      return { type: '系统进程', middleware: true };
    case 'Tomcat':
      return { type: 'Tomcat', middleware: false };
    case 'Nginx':
      return { type: 'Nginx', middleware: true };
    case 'Kafka':
      return { type: 'MQ', middleware: true };
    case 'Redis':
      return { type: 'Redis', middleware: true };
    case 'MySQL':
      return { type: 'MySQL', middleware: true };
    case 'PostgreSQL':
      return { type: 'PostgreSQL', middleware: true };
    case 'Java Jar':
    case '其他 Java':
      if (/\b(?:org\.apache\.rocketmq|rocketmq|\brabbitmq\b|activemq)\b/i.test(p.command))
        return { type: 'MQ', middleware: true };
      return { type: 'Java', middleware: false };
    case 'Node':
      return { type: 'Node', middleware: false };
    case 'Python':
      return { type: 'Python', middleware: false };
    default:
      if (/rabbitmq/i.test(p.command)) return { type: 'MQ', middleware: true };
      return { type: '其它', middleware: false };
  }
}

// Tomcat 实例目录：优先 -Dcatalina.base/home，其次命令行中的 apache-tomcat_* 路径。
function tomcatBase(command: string): string | undefined {
  const tokens = tokenize(command);
  for (const key of ['-Dcatalina.base=', '-Dcatalina.home=']) {
    const token = tokens.find((t) => t.startsWith(key));
    if (token) return token.slice(key.length).replace(/\/+$/, '');
  }
  const match = command.match(/(\/[\w./~-]*apache-tomcat[\w.-]*)/i);
  return match?.[1].replace(/\/+$/, '');
}

// node 启动脚本：第一个看起来是脚本文件（.js/.mjs/.cjs）的非选项参数。
function nodeScript(command: string): string {
  const tokens = tokenize(command).slice(1);
  return (
    tokens.find((t) => !t.startsWith('-') && /\.[cm]?js$/i.test(t)) ||
    tokens.find((t) => !t.startsWith('-')) ||
    ''
  );
}

// 进程/应用详细列：jar 包名、Tomcat 实例目录、node 脚本路径、其余为进程名+PID。
function processDetail(p: Process, type: string): string {
  if (type === 'Java') {
    return jarName(p) || `java (PID ${p.pid})`;
  }
  if (type === 'Tomcat') {
    return tomcatBase(p.command) || `Tomcat (PID ${p.pid})`;
  }
  if (type === 'Node') {
    const script = nodeScript(p.command);
    return script ? `${script} (PID ${p.pid})` : `node (PID ${p.pid})`;
  }
  return `${p.name} (PID ${p.pid})`;
}

// DevOps 部署记录中包含该 IP 的应用（任一环境）。
function appsDeployedOnIp(ip: string, apps: App[]): App[] {
  return apps.filter((app) =>
    envs.some((e) => (app.envs[e] || []).some((r) => (r.ip || '').trim() === ip)),
  );
}

function dashRow(
  ip: string,
  managed: string,
  spec: string,
  source: ManagedRowSource,
  detail: string,
): IpManagedRow {
  return {
    ip,
    managed,
    spec,
    source,
    appType: DASH,
    detail,
    appId: DASH,
    appName: DASH,
    pushIn: DASH,
  };
}

// 同 IP 下进程详细重名（如同一 jar 启动多实例）时追加 PID 区分。
function disambiguate(rows: IpManagedRow[]): IpManagedRow[] {
  const counts = new Map<string, number>();
  for (const row of rows) {
    if (row.source === 'process') counts.set(row.detail, (counts.get(row.detail) || 0) + 1);
  }
  return rows.map((row) =>
    row.source === 'process' && row.pid !== undefined && (counts.get(row.detail) || 0) > 1
      ? { ...row, detail: `${row.detail} (PID ${row.pid})` }
      : row,
  );
}

// ─── 按IP汇总行 ───
// 每个 IP 一行：规格、CPU/内存使用率、监听端口数、进程分类信息。
export type IpSummaryRow = {
  ip: string;
  managed: string;
  spec: string;
  cpuPercent?: number;
  memPercent?: number;
  memTotalMB?: number;
  memUsedMB?: number;
  // null 表示无端口数据（旧快照），0 表示有数据但无监听端口。
  portCount: number | null;
  portList: number[];
  // 进程分类 [类型, 数量] 列表，如 [["Tomcat", 2], ["Java Jar", 1]]。
  categories: [string, number][];
};

export type SummaryQueryDeps = {
  state(ip: string): IpServerState;
  spec(ip: string): string;
  processes(ip: string): Process[];
  serverUsage(ip: string): ServerUsage | undefined;
  appPorts(ip: string): AppPort[] | undefined;
};

// 进程分类汇总：合并 Nginx worker 为 1 个，排除系统进程；
// 按 processTypes 顺序返回 [类型, 数量] 列表。
function summarizeProcessCategories(processes: Process[]): [string, number][] {
  const counts = new Map<string, number>();
  for (const p of processes) {
    if (p.system) continue;
    const type = processType(p);
    if (type === '系统进程' || type === '全部进程') continue;
    if (type === 'Nginx' && /worker process/i.test(p.command)) continue;
    counts.set(type, (counts.get(type) || 0) + 1);
  }
  return processTypes
    .filter((t) => t !== '全部进程' && t !== '系统进程')
    .map((t) => [t as string, counts.get(t) || 0] as [string, number])
    .filter(([, n]) => n > 0);
}

// 监听端口：优先 appPorts，回退到进程端口并集；无数据返回 null。
function listeningPortsForIp(
  processes: Process[],
  appPorts: AppPort[] | undefined,
): number[] | null {
  if (appPorts) return [...new Set(appPorts.map((p) => p.port))].sort((a, b) => a - b);
  const ports = new Set<number>();
  let collected = false;
  for (const p of processes) {
    if (p.ports && p.ports.length) {
      collected = true;
      for (const port of p.ports) ports.add(port);
    }
  }
  return collected ? [...ports].sort((a, b) => a - b) : null;
}

const defaultSummaryDeps: SummaryQueryDeps = {
  state: ipServerState,
  spec: ipSpecSummary,
  processes: processesForIp,
  serverUsage: (ip) =>
    (jumpserver.assets as Asset[]).find((a) => a.ip === ip)?.inspection?.serverUsage,
  appPorts: (ip) =>
    (jumpserver.assets as Asset[]).find((a) => a.ip === ip)?.inspection?.appPorts,
};

// 为一组 IP 构建按IP汇总行：每个 IP 一行，包含规格、CPU/内存使用率、
// 监听端口数与进程分类信息。未纳管 IP 仅显示"未纳管"；
// 已纳管但无法登录的 IP 仅显示规格，无进程/端口/分类数据。
export function buildIpSummaryRows(
  ips: string[],
  deps: SummaryQueryDeps = defaultSummaryDeps,
): IpSummaryRow[] {
  return ips.map((ip) => {
    const spec = deps.spec(ip) || DASH;
    const state = deps.state(ip);
    if (state === 'not_collected') {
      return { ip, managed: MANAGED_NO, spec, portCount: null, portList: [], categories: [] };
    }
    if (state === 'cannot_login') {
      return { ip, managed: MANAGED_YES, spec, portCount: null, portList: [], categories: [] };
    }
    const processes = deps.processes(ip);
    const usage = deps.serverUsage(ip);
    const ports = listeningPortsForIp(processes, deps.appPorts(ip));
    const categories = summarizeProcessCategories(processes);
    return {
      ip,
      managed: MANAGED_YES,
      spec,
      cpuPercent: usage?.cpuPercent,
      memPercent: usage?.memPercent,
      memTotalMB: usage?.memTotalMB,
      memUsedMB: usage?.memUsedMB,
      portCount: ports ? ports.length : null,
      portList: ports || [],
      categories,
    };
  });
}

// CPU/内存使用率文本：如 "12.3%"；无数据返回 "-"。
export function formatUsagePercent(value: number | undefined): string {
  return value === undefined ? DASH : `${value}%`;
}

// 进程分类文本：如 "Tomcat 2, Java Jar 1, Nginx 1"；无数据返回 "-"。
export function formatCategories(categories: [string, number][]): string {
  return categories.length
    ? categories.map(([t, n]) => `${t} ${n}`).join(', ')
    : DASH;
}

export const SUMMARY_CSV_HEADER = [
  'IP',
  '是否纳管',
  '服务器规格',
  'CPU%',
  '内存%',
  '监听端口数',
  '进程分类',
];

export function summaryRowsCsv(rows: IpSummaryRow[]): string {
  const lines = [SUMMARY_CSV_HEADER.map(csvCell).join(',')];
  for (const row of rows) {
    lines.push(
      [
        row.ip,
        row.managed,
        row.spec,
        formatUsagePercent(row.cpuPercent),
        formatUsagePercent(row.memPercent),
        row.portCount === null ? DASH : String(row.portCount),
        formatCategories(row.categories),
      ]
        .map(csvCell)
        .join(','),
    );
  }
  return lines.join('\r\n');
}

// 为一组 IP 构建纳管查询导出行。
// - not_collected：单行"未纳管"；
// - cannot_login：单行"无法登录，未采集"；
// - can_login：每个业务进程一行（Nginx worker/master 不出行，改由配置文件代表）、
//   每个 /var/www/webapps 一级目录一行、每个 Nginx 原始配置文件一行；
//   配置文件缺失但存在 Nginx 进程时回退一条主进程行；三类数据都没有时单行兜底。
export function buildIpManagedRows(
  ips: string[],
  apps: App[],
  nginxByIp?: Map<string, NginxConfigResult>,
  deps: ManagedQueryDeps = defaultDeps,
): IpManagedRow[] {
  const result: IpManagedRow[] = [];
  for (const ip of ips) {
    // 规格是服务器级属性，同一 IP 的所有行展示同一个值。
    const spec = deps.spec(ip) || DASH;
    const state = deps.state(ip);
    if (state === 'not_collected') {
      result.push(dashRow(ip, MANAGED_NO, spec, 'unmanaged', DASH));
      continue;
    }
    if (state === 'cannot_login') {
      result.push(dashRow(ip, MANAGED_YES, spec, 'unreachable', '无法登录，未采集'));
      continue;
    }

    const before = result.length;
    const processes = deps.processes(ip);
    const webapps = deps.webapps(ip);
    const deployed = appsDeployedOnIp(ip, apps);
    const nginxProcs = processes.filter((p) => classifyProcess(p).type === 'Nginx');

    // 业务进程（Nginx 进程由配置文件行代表）。
    const procRows: IpManagedRow[] = processes
      .filter((p) => classifyProcess(p).type !== 'Nginx')
      .sort((a, b) => a.pid - b.pid)
      .map((p) => {
        const kind = classifyProcess(p);
        const app = kind.middleware
          ? undefined
          : deployed.find((a) => deps.matchProcess(a, [p]));
        if (!app) {
          return {
            ip,
            managed: MANAGED_YES,
            spec,
            source: 'process' as const,
            appType: kind.type,
            detail: processDetail(p, kind.type),
            appId: DASH,
            appName: DASH,
            pushIn: DASH,
            pid: p.pid,
            command: p.command,
            memPercent: p.memPercent,
            rssKB: p.rssKB,
            vszKB: p.vszKB,
            ports: p.ports,
          };
        }
        return {
          ip,
          managed: MANAGED_YES,
          spec,
          source: 'process' as const,
          appType: kind.type,
          detail: processDetail(p, kind.type),
          appId: app.id,
          appName: app.name,
          pushIn:
            deps.latestTime([deps.appPushIn(app), p.startedAt || '']) || DASH,
          pid: p.pid,
          command: p.command,
          memPercent: p.memPercent,
          rssKB: p.rssKB,
          vszKB: p.vszKB,
          ports: p.ports,
        };
      });
    result.push(...disambiguate(procRows));

    // /var/www/webapps 应用目录。
    for (const webapp of [...webapps].sort((a, b) => a.name.localeCompare(b.name))) {
      const app = deployed.find((a) => deps.matchWebapp(a.name, [webapp]));
      result.push({
        ip,
        managed: MANAGED_YES,
        spec,
        source: 'webapp',
        appType: app?.appType?.trim() || '其它',
        detail: `/var/www/webapps/${webapp.name}`,
        appId: app ? app.id : DASH,
        appName: app ? app.name : DASH,
        pushIn: app
          ? deps.latestTime([deps.appPushIn(app), webapp.mtime || '']) || DASH
          : DASH,
      });
    }

    // Nginx 原始配置文件：每个文件一行；拉取失败或未采集但存在 Nginx 进程时兜底。
    const nginxResult = nginxByIp?.get(ip);
    if (nginxResult && nginxResult.files.length) {
      for (const file of [...nginxResult.files]
        .map((f) => f.path)
        .sort((a, b) => a.localeCompare(b))) {
        result.push({
          ip,
          managed: MANAGED_YES,
          spec,
          source: 'nginx',
          appType: 'Nginx',
          detail: file,
          appId: DASH,
          appName: DASH,
          pushIn: DASH,
        });
      }
    } else if (nginxProcs.length) {
      const masters = nginxProcs
        .filter((p) => /master process/i.test(p.command))
        .sort((a, b) => a.pid - b.pid);
      const chosen = masters.length ? masters : [nginxProcs.sort((a, b) => a.pid - b.pid)[0]];
      const suffix = nginxResult?.error
        ? '，原始配置读取失败'
        : '，配置未采集';
      for (const p of chosen) {
        result.push({
          ip,
          managed: MANAGED_YES,
          spec,
          source: 'nginx',
          appType: 'Nginx',
          detail: `nginx 主进程 (PID ${p.pid})${suffix}`,
          appId: DASH,
          appName: DASH,
          pushIn: DASH,
        });
      }
    } else if (nginxResult?.error) {
      result.push({
        ip,
        managed: MANAGED_YES,
        spec,
        source: 'nginx',
        appType: 'Nginx',
        detail: `Nginx 原始配置读取失败（${nginxResult.error}）`,
        appId: DASH,
        appName: DASH,
        pushIn: DASH,
      });
    }

    // 可登录但三类数据均为空（采集不完整等场景）。
    if (result.length === before) {
      result.push(
        dashRow(ip, MANAGED_YES, spec, 'empty', '已登录但未发现进程 / Webapps / Nginx 配置'),
      );
    }
  }
  return result;
}

// 该 IP 拉取 Nginx 原始配置所需的版本/资产参数；无需拉取（非可登录、
// 无已采集配置、缺版本号）时返回 undefined。
export function nginxConfigRequest(
  ip: string,
): { asset: string; version: string } | undefined {
  const asset = (jumpserver.assets as Asset[]).find(
    (a) =>
      a.ip === ip &&
      a.inspection?.loginStatus === 'can_login' &&
      (a.inspection.configurationCount ?? 0) > 0 &&
      !!a.inspection.configurationVersion,
  );
  if (!asset?.inspection?.configurationVersion) return undefined;
  return { asset: asset.id, version: asset.inspection.configurationVersion };
}

function csvCell(value: string): string {
  return /[",\n\r]/.test(value) ? '"' + value.replace(/"/g, '""') + '"' : value;
}

export const MANAGED_CSV_HEADER = [
  'IP',
  '是否纳管',
  '服务器规格',
  '应用类型',
  '进程ID',
  '进程/应用详细',
  '进程命令',
  '监听端口',
  '内存占用率',
  '实际内存',
  'DevOps应用ID',
  'DevOps应用名称',
  '最新Push In时间',
];

export function managedRowsCsv(rows: IpManagedRow[]): string {
  const lines = [MANAGED_CSV_HEADER.map(csvCell).join(',')];
  for (const row of rows) {
    lines.push(
      [
        row.ip,
        row.managed,
        row.spec,
        row.appType,
        row.pid !== undefined ? String(row.pid) : DASH,
        row.detail,
        row.command ?? DASH,
        formatPorts(row.ports),
        formatMemPercent(row.memPercent),
        formatMemorySize(row.rssKB),
        row.appId,
        row.appName,
        row.pushIn,
      ]
        .map(csvCell)
        .join(','),
    );
  }
  return lines.join('\r\n');
}

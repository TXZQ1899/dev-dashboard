export type Process = { pid: number; ppid: number; user: string; name: string; kind: string; startedAt: string; elapsedSeconds: number; command: string };
export type Route = { domains: string[]; listen: string[]; uri: string; directive: string; target: string; upstream: string; instance?: string; backends: {host: string; port: string | null; resolution: string}[] };
export type Inspection = { sudoStatus?: string; configurationCount?: number; configurationVersion?: string; checkedAt: string; loginStatus: string; reason: string; account?: string; processStatus: string; processes: Process[]; nginxStatus: string; nginxRoutes: Route[]; warnings: string[]; attempts?: {account: string; reason: string}[] };
export type Asset = { id: string; ip: string; hostname: string; inspection?: Inspection };
export const processTypes = ['全部进程','Tomcat','Java Jar','其他 Java','Nginx','Kafka','Node','Redis','MySQL','PostgreSQL','Python','其他进程'] as const;
export type ProcessType = typeof processTypes[number];
export const durations = [
  ['all','全部运行时间'],['week','未超过1星期（≤7天）'],['fortnight','未超过2星期（≤14天）'],
  ['month','未超过1个月（≤30天）'],['two_months','未超过2个月（≤60天）'],
  ['half_year','2个月到半年（>60且≤180天）'],['year','半年到一年（>180且≤365天）'],['over_year','超过一年（>365天）'],
] as const;
export type Duration = typeof durations[number][0];
function args(command: string) { return command.match(/"(?:\\.|[^"\\])*"|'[^']*'|[^\s]+/g)?.map(s=>s.replace(/^(['"])(.*)\1$/,'$2')) || []; }
export function jarName(process: Process) {
  const tokens=args(process.command);const i=tokens.indexOf('-jar');
  return i>=0 && tokens[i+1] ? tokens[i+1].split('/').pop() : undefined;
}
export function processType(process: Process): ProcessType {
  const command=process.command;const executable=(args(command)[0]||'').split('/').pop()||'';
  const java=/^(?:java|jsvc)$/i.test(process.name)||/^(?:java|jsvc)$/i.test(executable);
  if(java) {
    if(/\b(?:org\.apache\.catalina\.|-Dcatalina\.(?:base|home)=)/.test(command))return 'Tomcat';
    if(/\b(?:kafka\.(?:Kafka|server\.|tools\.)|org\.apache\.kafka\.)/.test(command))return 'Kafka';
    return jarName(process)?'Java Jar':'其他 Java';
  }
  if(/^nginx(?::|$)/i.test(executable)||process.name==='nginx')return 'Nginx';
  if(/^(node|nodejs)$/.test(executable)||/^(node|nodejs)$/.test(process.name))return 'Node';
  if(/^redis-server/.test(executable)||process.name==='redis-server')return 'Redis';
  if(/^(mysqld|mariadbd)$/.test(executable)||/^(mysqld|mariadbd)$/.test(process.name))return 'MySQL';
  if(/^postgres(?::|$)/.test(executable)||process.name==='postgres')return 'PostgreSQL';
  if(/^python[\d.]*$/.test(executable)||/^python[\d.]*$/.test(process.name))return 'Python';
  return '其他进程';
}
export function runningSeconds(process: Process, checkedAt: string, now: number): number {
  const observed=Date.parse(checkedAt);
  const elapsed=Number.isFinite(process.elapsedSeconds)?Math.max(0,process.elapsedSeconds):0;
  return elapsed+(Number.isFinite(observed)?Math.max(0,Math.floor((now-observed)/1000)):0);
}
export function matchesDuration(seconds: number, filter: Duration) {
  const day=86400;
  switch(filter) {
    case 'week':return seconds<=7*day;
    case 'fortnight':return seconds<=14*day;
    case 'month':return seconds<=30*day;
    case 'two_months':return seconds<=60*day;
    case 'half_year':return seconds>60*day&&seconds<=180*day;
    case 'year':return seconds>180*day&&seconds<=365*day;
    case 'over_year':return seconds>365*day;
    default:return true;
  }
}
export function formatDuration(seconds: number) {
  return `${Math.floor(seconds/86400)}天 ${Math.floor(seconds%86400/3600)}小时 ${Math.floor(seconds%3600/60)}分钟`;
}
export type ProcessRow = { asset: Asset; process: Process; type: ProcessType; name: string; seconds: number };
export function processRows(assets: Asset[], now: number): ProcessRow[] {
  return assets.filter(a=>a.inspection?.loginStatus==='can_login').flatMap(asset=>asset.inspection!.processes.map(process=>({asset,process,type:processType(process),name:jarName(process)||process.name,seconds:runningSeconds(process,asset.inspection!.checkedAt,now)})));
}
export function filterProcesses(rows: ProcessRow[], query: string, duration: Duration, type: ProcessType='全部进程') {
  const words=query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  return rows.filter(r=> (type==='全部进程'||r.type===type) && matchesDuration(r.seconds,duration) && words.every(word=>`${r.asset.ip} ${r.asset.hostname} ${r.process.pid} ${r.name} ${r.process.command}`.toLowerCase().includes(word)))
    .sort((a,b)=>b.seconds-a.seconds||a.asset.ip.localeCompare(b.asset.ip)||a.process.pid-b.process.pid||a.asset.id.localeCompare(b.asset.id));
}
export function paginate<T>(rows: T[], page: number, size: number) {
  const pages=Math.max(1,Math.ceil(rows.length/size));const current=Math.max(1,Math.min(page,pages));
  return {rows:rows.slice((current-1)*size,current*size),page:current,pages,total:rows.length};
}

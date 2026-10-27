export type InferredPort = { port: number; address: string; protocol: 'tcp'; confidence: 'INFERRED'; basis: string };
export type Process = { pid: number; ppid: number; user: string; name: string; kind: string; startedAt: string; elapsedSeconds: number; command: string; cpuPercent?: number; memPercent?: number; rssKB?: number; vszKB?: number; ports?: number[]; inferredPorts?: InferredPort[]; system?: boolean };
export type Route = { domains: string[]; listen: string[]; uri: string; directive: string; target: string; upstream: string; file?: string; instance?: string; backends: {host: string; port: string | null; resolution: string}[] };
export type AppPort = { app: string; kind: string; port: number; addresses: string[]; pids: number[]; candidatePids?: number[]; ownershipConfidence?: 'INFERRED' | 'AMBIGUOUS' | 'UNKNOWN'; basis?: string };
export type Webapp = { name: string; mtime: string | null };
export type Webapps = { status: string; apps: Webapp[] };
export type ServerUsage = { cpuPercent: number; memPercent: number; memTotalMB: number; memUsedMB: number };
export type Inspection = { sudoStatus?: string; configurationCount?: number; configurationVersion?: string; checkedAt: string; loginStatus: string; reason: string; account?: string; processStatus: string; processes: Process[]; appPorts?: AppPort[]; portStatus?: 'complete' | 'failed' | 'not_collected'; webapps?: Webapps; nginxStatus: string; nginxRoutes: Route[]; warnings: string[]; serverUsage?: ServerUsage; attempts?: {account: string; reason: string}[] };
export type Asset = { id: string; ip: string; hostname: string; inspection?: Inspection };
// 把 location 修饰符翻译成给非 Nginx 读者看的匹配说明；无法识别时返回空串。
export function locationHint(uri: string): string {
  if(uri==='(TCP/UDP)')return '四层 stream 转发，按监听端口匹配';
  if(uri==='/')return '匹配所有请求（默认入口）';
  const parts=uri.split(' ');
  if(parts.length>1){
    const path=parts.slice(1).join(' ');
    if(parts[0]==='=')return `仅精确匹配 ${path}`;
    if(parts[0]==='^~')return `匹配所有以 ${path} 开头的请求`;
    if(parts[0]==='~')return `正则匹配（区分大小写）：${path}`;
    if(parts[0]==='~*')return `正则匹配（不区分大小写）：${path}`;
  }
  if(uri.startsWith('@'))return '命名 location，仅供内部跳转（如 try_files、error_page）';
  if(uri.startsWith('/'))return `匹配所有以 ${uri} 开头的请求`;
  return '';
}
// 在原始配置文本中定位 uri 对应的 location 块，返回 [起始行, 结束行]（0 起、含端点）；无法定位时返回 null。
// uri 为采集器归一化后的 location 参数（如 /fotour、^~ /api、= /login、~ .php$），这里按任意空白还原成原文匹配。
export function findLocationBlock(content: string, uri: string): {start: number; end: number} | null {
  const tokens=uri.trim().split(/\s+/).filter(Boolean);
  if(!tokens.length)return null;
  const escaped=tokens.map(t=>t.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'));
  // location 与参数、参数之间允许任意空白；参数后必须是块起始 {、注释或行尾，避免匹配到更长路径。
  const directive=new RegExp('^[ \\t]*location[ \\t]+'+escaped.join('[ \\t]+')+'(?:[ \\t]*\\{|[ \\t]*#|[ \\t]*$)');
  const lines=content.split(/\r?\n/);
  for(let i=0;i<lines.length;i++){
    if(!directive.test(lines[i]))continue;
    // 从该行起统计花括号，第一次回到 0 即为 location 块结束；块未闭合则放弃定位，避免误高亮。
    let depth=0,opened=false;
    for(let j=i;j<lines.length;j++){
      for(const ch of lines[j]){if(ch==='{'){depth++;opened=true;}else if(ch==='}')depth--;}
      if(opened&&depth<=0)return {start:i,end:j};
    }
    return null;
  }
  return null;
}
export const processTypes = ['全部进程','Tomcat','Java Jar','其他 Java','Nginx','Kafka','Node','Redis','MySQL','PostgreSQL','Python','其他进程','系统进程'] as const;
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
  if(process.system)return '系统进程';
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
// 中间件进程：数据库/缓存/消息队列/搜索等共享基础服务，通常不归属任何 DevOps
// 应用，但同样处于业务链路中，需要与业务应用一样提供“检测上下游”入口。
// 已单独归类的中间件按类型判断；MongoDB/Elasticsearch/原生 RabbitMQ 等未归类的
// 中间件按可执行文件名或命令行关键字识别。
const middlewareTypes=new Set<ProcessType>(['MySQL','PostgreSQL','Redis','Kafka']);
export function isMiddlewareProcess(process: Process): boolean {
  if(process.system)return false;
  if(middlewareTypes.has(processType(process)))return true;
  const executable=(args(process.command)[0]||'').split('/').pop()||'';
  const name=process.name||'';
  if(/^(?:mongod|mongos)$/i.test(executable)||/^(?:mongod|mongos)$/i.test(name))return true;
  if(/^(?:rabbitmq-server|rabbitmq)$/i.test(executable)||/^rabbitmq/i.test(name))return true;
  if(/elasticsearch/i.test(name)||/elasticsearch/i.test(process.command))return true;
  return /\b(?:rabbitmq|rocketmq|activemq)\b/i.test(process.command);
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
// 表格中超过该字符数的进程命令折叠展示，完整内容放到弹窗中查看。
export const COMMAND_COLLAPSE_THRESHOLD = 256;
// 把单行启动命令格式化为便于阅读的多行文本：按空白拆成参数（引号内空白保留），
// 每个参数占一行；超长的冒号分隔参数（如 classpath）按冒号拆行并缩进续行。
export function formatCommand(command: string): string {
  const tokens=command.match(/"[^"]*"|'[^']*'|\S+/g)??[];
  const lines:string[]=[];
  for(const token of tokens) {
    if(token.length>120 && token.includes(':/') && !token.includes('://')) {
      const segments=token.split(':');
      lines.push(segments[0]+':');
      segments.slice(1).forEach((segment,i)=>lines.push('  '+segment+(i<segments.length-2?':':'')));
    } else {
      lines.push(token);
    }
  }
  return lines.join('\n');
}
export type ProcessRow = { asset: Asset; process: Process; type: ProcessType; name: string; seconds: number; workerCount?: number };
function isNginxWorker(process: Process): boolean {
  return processType(process)==='Nginx' && /worker process/i.test(process.command);
}
export function processRows(assets: Asset[], now: number): ProcessRow[] {
  return assets.filter(a=>a.inspection?.loginStatus==='can_login').flatMap(asset=>{
    const processes=asset.inspection!.processes;
    const workers=processes.filter(isNginxWorker);
    const rows: ProcessRow[]=processes.filter(p=>!isNginxWorker(p)).map(process=>({asset,process,type:processType(process),name:jarName(process)||process.name,seconds:runningSeconds(process,asset.inspection!.checkedAt,now)}));
    if(workers.length) {
      // 同一台服务器的所有 Nginx worker 进程合并为一条记录，提示 worker 数量；
      // 用 PID 最小的 worker 作为代表（通常最早启动），合并各 worker 的监听端口。
      const rep=workers.slice().sort((a,b)=>a.pid-b.pid)[0];
      const ports=[...new Set(workers.flatMap(w=>w.ports||[]))].sort((a,b)=>a-b);
      rows.push({asset,process:{...rep,ports},type:'Nginx',name:rep.name,seconds:runningSeconds(rep,asset.inspection!.checkedAt,now),workerCount:workers.length});
    }
    return rows;
  });
}
export function filterProcesses(rows: ProcessRow[], query: string, duration: Duration, type: ProcessType='全部进程') {
  const words=query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  return rows.filter(r=> (type==='全部进程'||r.type===type) && matchesDuration(r.seconds,duration) && words.every(word=>`${r.asset.ip} ${r.asset.hostname} ${r.process.pid} ${r.name} ${r.process.command}`.toLowerCase().includes(word)))
    .sort((a,b)=>b.seconds-a.seconds||a.asset.ip.localeCompare(b.asset.ip)||a.process.pid-b.process.pid||a.asset.id.localeCompare(b.asset.id));
}
// 服务器详情框内的进程搜索：按 PID、进程名（含业务名）、监听端口与详细命令匹配；
// 多个关键词以空格分隔，需全部命中（AND）。空查询命中所有行。
export function matchesProcessQuery(row: ProcessRow, query: string) {
  const words=query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if(!words.length)return true;
  const ports=[...(row.process.ports||[]),...(row.process.inferredPorts||[]).map(p=>p.port)].join(' ');
  const haystack=`${row.process.pid} ${row.process.name} ${row.name} ${ports} ${row.process.command}`.toLowerCase();
  return words.every(word=>haystack.includes(word));
}
export function paginate<T>(rows: T[], page: number, size: number) {
  const pages=Math.max(1,Math.ceil(rows.length/size));const current=Math.max(1,Math.min(page,pages));
  return {rows:rows.slice((current-1)*size,current*size),page:current,pages,total:rows.length};
}

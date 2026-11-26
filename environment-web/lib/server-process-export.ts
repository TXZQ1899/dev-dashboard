import { createTwoSheetWorkbook, type Cell } from './xlsx';
import { hideOperationalProcess, jvmMemory, formatDuration, type Asset, type ProcessRow } from './server-processes';
import type { Instance } from './ecs';
import type { ExportApp } from './server-process-export-associations';

type Group = {asset: Asset; rows: ProcessRow[]};
type Options = {
  spec: (ip: string) => string;
  ports: (asset: Asset) => number[] | null;
  categories: (rows: ProcessRow[]) => [string, number][];
  name: (row: ProcessRow) => string;
  date: (value: string) => string;
  instance?: (asset: Asset) => Instance | undefined;
  application?: (asset: Asset, row: ProcessRow) => ExportApp | undefined;
  canAssociate?: (asset: Asset) => boolean;
  allRows?: ProcessRow[];
};

// The export uses the page's global filters and all pages, not per-host tabs/search.
export function serverProcessExportRows(groups: Group[], options: Options): {summary: Cell[][]; detail: Cell[][]} {
  const summary: Cell[][]=[['实例名称','实例 ID','租户','IP','规格','CPU%','内存%','监听端口数','进程分类','部署应用数','部署应用名称','服务器到期时间','进程采集状态']];
  const detail: Cell[][]=[['IP','实例名称','进程ID','进程名','应用ID','应用名称','进程分类','用户','CPU%','内存%','JVM 配置','实际 RSS MiB','RSS/XMX 百分比','监听端口','进程启动时间（北京时间）','运行时间（推算至导出时）','详细命令','采集状态','采集时间（北京时间）','容器名称','容器镜像','容器启动命令','Kubernetes 信息','Pod TCP 监听']];
  const allByAsset=new Map<string,ProcessRow[]>();
  for(const row of options.allRows||groups.flatMap(group=>group.rows)) {
    const list=allByAsset.get(row.asset.id)||[];list.push(row);allByAsset.set(row.asset.id,list);
  }
  for(const {asset,rows} of groups) {
    const inspection=asset.inspection!;
    const nonSystem=rows.filter(r=>r.type!=='系统进程');
    const visible=nonSystem.filter(r=>!hideOperationalProcess(r.process));
    const system=rows.filter(r=>r.type==='系统进程'&&!hideOperationalProcess(r.process));
    const sum=nonSystem.reduce((n,r)=>n+(r.process.cpuPercent??0),0);
    const warning=system.length>0&&(inspection.serverUsage?.cpuPercent??0)-sum>5;
    const categories=options.categories(visible).map(([type,count])=>`${type} ${count}`);
    if(warning)categories.push(`系统进程 ${system.length}`);
    const ports=options.ports(asset);
    const instance=options.instance?.(asset);
    const found=new Map<string,ExportApp>();
    for(const row of allByAsset.get(asset.id)||[]) {
      const app=options.application?.(asset,row);if(app)found.set(app.id,app);
    }
    const applications=[...found.values()].sort((a,b)=>a.name.localeCompare(b.name,'zh-CN')||a.id.localeCompare(b.id));
    const count=(options.canAssociate&&!options.canAssociate(asset))?'—':inspection.processStatus==='complete'||applications.length?applications.length:'—';
    summary.push([instance?.name||'—',instance?.id||'—',instance?.tenantName||instance?.tenantId||'—',asset.ip,options.spec(asset.ip)||'—',inspection.serverUsage?.cpuPercent??null,inspection.serverUsage?.memPercent??null,ports===null?null:ports.length,categories.join('、')||'—',count,applications.map(a=>`${a.name}（${a.id}）`).join('，')||'—',instance?.expiredAt?options.date(instance.expiredAt):'—',inspection.processStatus]);
    for(const row of visible) {
      const p=row.process, jvm=row.workerCount?{config:'',rssToXmxPercent:null}:jvmMemory(p);
      const direct=p.ports||[];
      const inferred=[...new Set((p.inferredPorts||[]).filter(port=>!direct.includes(port.port)).map(port=>port.port))].sort((a,b)=>a-b);
      const portText=[direct.length?direct.join(', '):'',inferred.length?`${inferred.join(', ')}（推断）`:''].filter(Boolean).join('；')||'—';
      const pod=row.kubernetes?`${row.kubernetes.namespace||'命名空间未知'} / ${row.kubernetes.podName||'Pod 名称未知'} / ${row.kubernetes.containerName||'容器名称未知'}`:'—';
      const podPorts=row.kubernetes?(row.kubernetes.listeners.length?row.kubernetes.listeners.map(p=>`${p.address}:${p.port}`).join(', '):row.kubernetes.listenerStatus==='observed'?'未观察到 PID 监听':'未能采集'):'—';
      const app=options.application?.(asset,row);
      const type=row.typeBasis?`${row.type}（容器推断）`:row.type;
      detail.push([asset.ip,instance?.name||'—',row.workerCount?`×${row.workerCount}`:p.pid,options.name(row),app?.id||'—',app?.name||'—',type,p.user,p.cpuPercent??null,p.memPercent??null,jvm.config||'—',!row.workerCount&&p.rssKB!=null&&Number.isFinite(p.rssKB)&&p.rssKB>=0?Math.round(p.rssKB/1024*10)/10:null,jvm.rssToXmxPercent===null?null:Math.round(jvm.rssToXmxPercent*10)/10,portText,row.workerCount?'—':options.date(p.startedAt),row.workerCount?'—':formatDuration(row.seconds),row.workerCount?`nginx: worker process（共 ${row.workerCount} 个）`:p.command,inspection.processStatus,options.date(inspection.checkedAt),row.docker?.container.name||'—',row.docker?.container.image||'—',row.docker?.container.command||'—',pod,podPorts]);
    }
  }
  return {summary,detail};
}

export function createServerProcessWorkbook(data: {summary: Cell[][]; detail: Cell[][]}): Uint8Array {
  return createTwoSheetWorkbook(
    { name: '服务器清单', rows: data.summary, widths: [32,28,22,20,18,14,14,16,45,16,70,25,18] },
    { name: '进程明细', rows: data.detail, widths: [20,32,14,32,18,32,18,18,14,14,28,18,20,28,25,26,85,18,25,30,40,85,50,38] },
  );
}

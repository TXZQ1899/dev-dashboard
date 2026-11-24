import { strToU8, zipSync } from 'fflate';
import { hideOperationalProcess, jvmMemory, formatDuration, type Asset, type ProcessRow } from './server-processes';
import type { Instance } from './ecs';
import type { ExportApp } from './server-process-export-associations';

type Cell = string | number | null;
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

function xml(value: string): string {
  // Excel cells are limited to 32,767 UTF-16 units; retain a visible marker if shortened.
  const clean=value.replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\uFFFE\uFFFF]/g,'');
  const text=clean.length>32767?clean.slice(0,32755)+'…（已截断）':clean;
  return text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&apos;');
}
function col(i: number): string { let result=''; for(let n=i+1;n>0;n=Math.floor((n-1)/26))result=String.fromCharCode(65+(n-1)%26)+result; return result; }
function sheet(rows: Cell[][], widths: number[]): string {
  const body=rows.map((row,i)=>`<row r="${i+1}">${row.map((v,j)=>{
    if(v===null||v===undefined)return '';
    const ref=`${col(j)}${i+1}`, style=i===0?' s="1"':'';
    return typeof v==='number'&&Number.isFinite(v)?`<c r="${ref}"${style}><v>${v}</v></c>`:`<c r="${ref}" t="inlineStr"${style}><is><t xml:space="preserve">${xml(String(v))}</t></is></c>`;
  }).join('')}</row>`).join('');
  const columns=widths.map((w,i)=>`<col min="${i+1}" max="${i+1}" width="${w}" customWidth="1"/>`).join('');
  return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><cols>${columns}</cols><sheetData>${body}</sheetData><autoFilter ref="A1:${col(widths.length-1)}${rows.length}"/></worksheet>`;
}
export function createServerProcessWorkbook(data: {summary: Cell[][]; detail: Cell[][]}): Uint8Array {
  const files: Record<string,string>={
    '[Content_Types].xml':'<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>',
    '_rels/.rels':'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
    'xl/workbook.xml':'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="服务器清单" sheetId="1" r:id="rId1"/><sheet name="进程明细" sheetId="2" r:id="rId2"/></sheets></workbook>',
    'xl/_rels/workbook.xml.rels':'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>',
    'xl/styles.xml':'<?xml version="1.0" encoding="UTF-8"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="11"/><name val="Arial"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Arial"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF244769"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="2"><xf xfId="0"/><xf fontId="1" fillId="2" xfId="0" applyFont="1" applyFill="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>',
    'xl/worksheets/sheet1.xml':sheet(data.summary,[32,28,22,20,18,14,14,16,45,16,70,25,18]),
    'xl/worksheets/sheet2.xml':sheet(data.detail,[20,32,14,32,18,32,18,18,14,14,28,18,20,28,25,26,85,18,25,30,40,85,50,38]),
  };
  return zipSync(Object.fromEntries(Object.entries(files).map(([name,text])=>[name,strToU8(text)])),{level:6});
}

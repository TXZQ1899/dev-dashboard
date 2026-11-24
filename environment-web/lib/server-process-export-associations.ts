import { buildJumpserverOverview } from './jumpserver-overview';
import { matchAppToProcess, jarBusinessName, tomcatInstanceName } from './process-comparison';
import { envs, type App } from './inventory';
import { type Instance } from './ecs';
import { hideOperationalProcess, type Asset, type ProcessRow } from './server-processes';

export type ExportApp = {id: string; name: string};
export function buildExportAssociations(assets: Asset[], instances: Instance[], apps: App[]) {
  // Evaluate against the full asset inventory, not just the filtered page, so
  // filtering cannot turn an ambiguous ECS association into a unique one.
  const overview=buildJumpserverOverview(assets,instances);
  const instancesByAsset=new Map(overview.jump.map(row=>[row.asset.id,row.instance]));
  const ambiguous=new Set(overview.jump.filter(row=>row.location==='ambiguous').map(row=>row.asset.id));
  const ipCounts=new Map<string,number>();
  for(const asset of assets)for(const ip of new Set(asset.ip.split(/[\s,]+/).filter(Boolean)))ipCounts.set(ip,(ipCounts.get(ip)||0)+1);
  function canAssociate(asset: Asset): boolean {
    const ips=asset.ip.split(/[\s,]+/).filter(Boolean);
    return !ambiguous.has(asset.id)&&ips.length>0&&ips.every(ip=>(ipCounts.get(ip)||0)===1);
  }
  function match(asset: Asset, row: ProcessRow): ExportApp | undefined {
    if(!canAssociate(asset)||row.workerCount||row.docker||row.kubernetes||row.type==='系统进程'||hideOperationalProcess(row.process))return undefined;
    const ips=asset.ip.split(/[\s,]+/).filter(Boolean);
    const instance=instancesByAsset.get(asset.id);
    const addresses=new Set([...ips,...(instance?.privateIps||[]),...(instance?.publicIps||[])]);
    const ports=new Set((row.process.ports||[]).map(String));
    const candidates=apps.filter(app=>{
      const deployments=envs.flatMap(env=>app.envs[env]||[]).filter(d=>addresses.has((d.ip||'').trim()));
      if(!deployments.length||!matchAppToProcess({...app,ports:[]},[row.process]))return false;
      const declared=deployments.map(d=>(d.port||app.port||'').trim()).filter(Boolean);
      return !ports.size||!declared.length||declared.some(port=>ports.has(port));
    });
    if(candidates.length===1)return {id:candidates[0].id,name:candidates[0].name};
    const name=row.type==='Tomcat'?tomcatInstanceName(row.process.command):jarBusinessName(row.process.command);
    const norm=(v: string)=>v.toLowerCase().replace(/[-_.]/g,'');
    const exact=name?candidates.filter(app=>norm(app.name)===norm(name)):[];
    return exact.length===1?{id:exact[0].id,name:exact[0].name}:undefined;
  }
  return {instance:(asset: Asset)=>instancesByAsset.get(asset.id),canAssociate,match};
}

"""Import loopback Redis after live PID-file or legacy-version corroboration."""
import json, os
from collections import Counter
from datetime import datetime,timedelta,timezone
from pathlib import Path
try:
    import deployment_catalog as db
    import redis_loopback_catalog as base
except ModuleNotFoundError:
    from runtime import deployment_catalog as db, redis_loopback_catalog as base


def plan(audit,deep,discovery,legacy,legacy_cluster,loopback,sentinels):
    version=audit.get('sourceVersion');reports=(deep,discovery,legacy,legacy_cluster,loopback,sentinels)
    if not version or any(r.get('sourceVersion')!=version for r in reports):raise ValueError('核查报告版本不一致')
    def idx(report):
        rows=report.get('results',[]);counts=Counter((r.get('assetId'),r.get('pid')) for r in rows)
        return {(r.get('assetId'),r.get('pid')):r for r in rows if counts[(r.get('assetId'),r.get('pid'))]==1}
    dm,cm,lm,bm=map(idx,(deep,discovery,legacy,loopback));lc={r.get('ip'):r for r in legacy_cluster.get('results',[])}
    sent=sentinels.get('results',[])
    sentinel_coverage=(len(sent)==9 and all(r.get('status')=='OBSERVED' and (r.get('info') or {}).get('status')=='OBSERVED' for r in sent))
    sent_ips=set()
    for s in sent:
        for master in (s.get('info') or {}).get('masters',[]):
            sent_ips.add(master.get('ip'))
            if isinstance(master.get('replicas'),list):sent_ips.update(x.get('ip') for x in master['replicas'])
            else:sentinel_coverage=False
    accepted=[];unresolved=[]
    for row in audit.get('results',[]):
        key=(row.get('assetId'),row.get('pid'));d=dm.get(key) or {};c=cm.get(key) or {};l=lm.get(key) or {};b=bm.get(key) or {};ip=row.get('ip')
        if not b:continue
        lines=row.get('lines') or [];meta={};app=row.get('applicationCandidates') or {};apps2=d.get('applicationCandidates') or {}
        for line in lines:
            p=line.split('\t')
            if len(p)>=3 and p[0]=='META':meta[p[1]]=p[2]
        common=(sentinel_coverage and ip not in sent_ips and row.get('status')=='OBSERVED' and d.get('status')=='OBSERVED' and b.get('status')=='OBSERVED'
            and row.get('startTicks') and row['startTicks']==d.get('startTicks')==b.get('startTicks') and all(x.get('observedAt') for x in (row,d,b))
            and row.get('container') is None and row.get('containerMatches')==0 and row.get('dockerWarnings')==[]
            and meta.get('netns') and meta['netns']==meta.get('hostnetns') and meta.get('exe')=='redis-server'
            and row.get('frames',0)>=2 and d.get('frames',0)>=2 and b.get('frames',0)>=2 and not row.get('warnings') and not d.get('warnings') and not b.get('warnings')
            and b.get('connectionCount')==0 and b.get('connections')==[] and len(b.get('listeners') or [])==1
            and b['listeners'][0].get('ip')=='127.0.0.1' and b['listeners'][0].get('protocol')=='tcp'
            and set(app)==set(apps2)=={'TEST','SIMULATION','PRODUCT'} and not any(app.values()) and not any(apps2.values()))
        if not common:unresolved.append({'ip':ip,'reason':'实时监听、TCP、Sentinel 或应用关联证据不足'});continue
        port=b['listeners'][0].get('port');config=None;unit=meta.get('service');clusterev=None
        if c.get('status')=='OBSERVED' and (c.get('detail') or {}).get('status')=='OBSERVED':
            detail=c['detail'];runtime=d.get('runtime') or {};sections=runtime.get('sections') or {};server=sections.get('server') or {};rep=sections.get('replication') or {};cluster=sections.get('cluster') or {};cfg=runtime.get('config') or {};clients=runtime.get('clients') or {};service=detail.get('service') or {}
            files=[f for f in detail.get('files',[]) if f.get('pidfileMatches') and not f.get('unparsed')]
            processes=detail.get('processes') or []
            if c.get('startTicks')==row['startTicks'] and len(files)==1 and len(processes)==1 and processes[0].get('pid')==row['pid'] and service.get('unit')==unit and unit:
                item=files[0];values=item.get('directives') or [];vals={k:v for k,v in values};cwd=detail.get('cwd') or ''
                filedir=vals.get('dir');filedir=os.path.normpath(os.path.join(cwd,filedir)) if filedir else None
                if (runtime.get('status')=='OBSERVED' and server.get('redis_mode')=='standalone' and server.get('run_id') and rep.get('role')=='master' and rep.get('connected_slaves')=='0' and cluster.get('cluster_enabled')=='0'
                    and clients.get('replicaCount')==0 and cfg.get('replicaof','')=='' and cfg.get('slaveof','')=='' and cfg.get('bind')=='127.0.0.1' and cfg.get('port')==str(port)
                    and vals.get('bind')=='127.0.0.1' and vals.get('port')==str(port) and filedir==cfg.get('dir') and vals.get('dbfilename')==cfg.get('dbfilename')
                    and not any(k in ('include','slaveof','replicaof') or v=='UNPARSED' for k,v in values) and vals.get('cluster-enabled','no')=='no'):
                    config=item['path'];clusterev='INFO_CLUSTER_DISABLED'
        elif l.get('status')=='OBSERVED':
            lines2=l.get('lines') or [];runtime={};info={};client=None
            for line in d.get('runtimeLines',[]):
                p=line.split('\t')
                if len(p)>=3 and p[0]=='INFO':info[p[1]]=p[2]
                elif len(p)>=3 and p[0]=='CONFIG':runtime[p[1]]=p[2]
                elif len(p)>=3 and p[0]=='CLIENTS':client=p[2]
            refs=[x.split('\t')[2] for x in lines2 if x.startswith('REFERENCE\t') and len(x.split('\t'))>=4 and x.split('\t')[3]=='DIRECT']
            confs=[x.split('\t')[1] for x in lines2 if x.startswith('FILE\t') and x.endswith('\tREADABLE')];processes=[x for x in lines2 if x.startswith('PROCESS\t')]
            directives=[x.split('\t') for x in lines2 if x.startswith('DIRECTIVE\t')];pairs=[(x[2],x[3]) for x in directives if len(x)>=4];vals={k:v for k,v in pairs};livecluster=lc.get(ip) or {}
            if (l.get('startTicks')==row['startTicks'] and len(confs)==1 and confs[0] in refs and processes==['PROCESS\t'+str(row['pid'])+'\tredis-server']
                and info.get('redis_version')=='2.8.19' and info.get('redis_mode')=='standalone' and info.get('run_id') and info.get('role')=='master' and info.get('connected_slaves')=='0'
                and client=='0' and runtime.get('bind')=='127.0.0.1' and runtime.get('port')==str(port) and runtime.get('slaveof','')==''
                and vals.get('bind')=='127.0.0.1' and vals.get('port')==str(port) and not any(k in ('include','slaveof','replicaof','cluster-enabled') for k,v in pairs)
                and not any(x.startswith(('DIRECTIVE_UNPARSED\t','RENAME_CLUSTER\t')) for x in lines2)
                and livecluster.get('status')=='OBSERVED' and livecluster.get('startTicks')==row['startTicks'] and livecluster.get('clusterStatus')=='UNKNOWN_COMMAND'):
                config=confs[0];clusterev='REDIS_2_8_UNKNOWN_CLUSTER_COMMAND'
        if config:
            accepted.append({'audit':row,'startup':{'detail':{'startup':{'unit':unit}}},'configPath':config,'port':port,'clusterEvidence':clusterev})
        else:unresolved.append({'ip':ip,'reason':'配置文件、运行状态或旧版能力未充分确认'})
    return {'accepted':sorted(accepted,key=lambda x:x['audit']['ip']),'unresolved':unresolved}


def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    data=Path(os.environ.get('ENVSCOPE_DATA','/data'));root=data/'checks'/'redis-review';read=lambda p:json.loads(p.read_text())
    names=('config-audit.json','deep-12.json','config-discovery.json','legacy-startup.json','legacy-cluster-extra.json','loopback-two.json','sentinels-all.json')
    reports=[read(root/name) for name in names];version=read(data/'current.json')['id']
    for value in reports[1:]:
        t=datetime.fromisoformat(value['completedAt'].replace('Z','+00:00'))
        if t>datetime.now(timezone.utc)+timedelta(minutes=5) or datetime.now(timezone.utc)-t>timedelta(hours=2):raise ValueError('核查证据已过期')
    p=plan(*reports)
    if version!=reports[0]['sourceVersion']:raise ValueError('当前快照已切换')
    if not args.apply:
        print(json.dumps({'candidates':[{'ip':x['audit']['ip'],'port':x['port'],'configPath':x['configPath'],'clusterEvidence':x['clusterEvidence']} for x in p['accepted']],'unresolved':p['unresolved']},ensure_ascii=False,indent=2));return
    evidence=root/'evidence';evidence.mkdir(parents=True,exist_ok=True)
    for value in reports:
        payload=db.encode(value);path=evidence/(db.digest(payload)+'.json')
        if path.exists() and path.read_text()!=payload:raise ValueError('核查证据校验失败')
        if not path.exists():
            tmp=path.with_suffix('.tmp');tmp.write_text(payload);tmp.chmod(0o600);os.replace(tmp,path)
    result=base.import_report(db.Catalog.from_environment(),*reports[:4],version,can_apply=lambda:read(data/'current.json').get('id')==version,prepared=p,extra_reports=reports[4:],mapper_version='redis-loopback-later-v1',source_path='checks/redis-review/deep-12.json')
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()

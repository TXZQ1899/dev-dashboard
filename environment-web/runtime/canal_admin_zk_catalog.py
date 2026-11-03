"""Import Canal Admin groups corroborated by current ZooKeeper membership."""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import ipaddress
import json
import os
from pathlib import Path
import re
try:
    import canal_admin_catalog as admin
    import deployment_catalog as db
except ModuleNotFoundError:
    from runtime import canal_admin_catalog as admin
    from runtime import deployment_catalog as db

RULE='canal-admin-zk-v2'


def report_from_sources(data):
    data=Path(data);raw=admin.report_from_sources(data);v=raw['sourceVersion'];path=data/'checks'/'canal-review'/('zk-members-'+v+'.json')
    zk=json.loads(path.read_text())
    if zk.get('sourceVersion')!=v:raise ValueError('ZooKeeper 报告来源版本不一致')
    raw.update(ruleVersion=RULE,zkEvidence=zk,reportFiles=raw['reportFiles']+[path.name]);return raw


def plan(report):
    if not report.get('sourceVersion') or report.get('ruleVersion')!=RULE:raise ValueError('来源或规则版本无效')
    members=report.get('adminMembers') or {};configs=report.get('adminConfigs') or {};zk=report.get('zkEvidence') or {}
    if any(x.get('sourceVersion')!=report['sourceVersion'] for x in (members,configs,zk)):raise ValueError('证据来源版本不一致')
    admins={x.get('admin'):x for x in members.get('results',[])};config_by_admin={x.get('admin'):x for x in configs.get('results',[])};zks={x.get('zkHosts'):x for x in zk.get('results',[])}
    if any(len(m)!=len(xs) for m,xs in ((admins,members.get('results',[])),(config_by_admin,configs.get('results',[])),(zks,zk.get('results',[])))):raise ValueError('来源证据重复')
    rows=report.get('results',[]);counts=Counter((r.get('assetId'),r.get('pid')) for r in rows);groups=[];unresolved=[]
    for r in rows:
        i=r.get('initial') or {};manager=(i.get('fields') or {}).get('canal.admin.manager','');reason=None
        try:ipaddress.ip_address(r.get('ip'))
        except (ValueError,TypeError):reason='资产地址无效'
        if manager.startswith('127.0.0.1:'):manager=r.get('ip','')+manager[len('127.0.0.1'):]
        a=admins.get(manager) or {};cfg=config_by_admin.get(manager) or {}
        if not r.get('assetId') or r.get('processStatus')!='complete' or counts[(r.get('assetId'),r.get('pid'))]!=1:reason=reason or '进程采集不完整或重复'
        path=i.get('configArgument') or '';proc=i.get('before');m=re.fullmatch(r'(\d+) \(java\) (\d+)',str(proc or ''))
        if not re.fullmatch(r'/[A-Za-z0-9_./-]+/conf/canal\.properties',path) or i.get('configArgumentMatch')!='MATCH' or i.get('configReadable')!='YES' or not i.get('configOutputComplete') or not m or int(m[1])!=r.get('pid') or i.get('after')!=proc:reason=reason or '启动配置或进程身份不一致'
        nodes=(a.get('nodes') or {}).get('items') or [];matches=[x for x in nodes if x.get('ip')==r.get('ip') and x.get('adminPort')==11110];node=matches[0] if len(matches)==1 else {};cid=node.get('clusterId')
        if a.get('status')!='COMPLETE' or (a.get('nodes') or {}).get('status')!='COMPLETE' or (a.get('instances') or {}).get('status')!='COMPLETE' or len(matches)!=1 or node.get('status')!='1':reason=reason or 'Admin Server 视图不完整或冲突'
        peers=[x for x in nodes if x.get('clusterId')==cid] if cid is not None else []
        if cid is not None and (len(peers)!=1 or peers[0].get('id')!=node.get('id')):reason=reason or 'Admin 集群有其他成员或成员不唯一'
        cluster_matches=[x for x in a.get('clusters',[]) if x.get('id')==cid];cluster=cluster_matches[0] if len(cluster_matches)==1 else {}
        matches_cfg=[x for x in cfg.get('configs',[]) if x.get('ip')==r.get('ip') and x.get('nodeId')==node.get('id') and x.get('clusterId')==cid];config=matches_cfg[0] if len(matches_cfg)==1 else {};cf=config.get('fields') or {};zkhosts=cf.get('canal.zkServers');view=zks.get(zkhosts) or {}
        if cfg.get('status')!='COMPLETE' or config.get('status')!='OBSERVED' or len(matches_cfg)!=1 or not zkhosts or (cid is not None and (len(cluster_matches)!=1 or cluster.get('zkHosts')!=zkhosts)):reason=reason or 'Admin 配置与协调地址不一致'
        endpoint=str(r.get('ip'))+':'+str(node.get('tcpPort'))
        registry=view.get('serverRegistry') or {}
        if cid is None:
            # For an Admin standalone node sharing a namespace, all registered
            # endpoints must be explained by current Admin nodes with the same
            # effective coordination address. Destination membership is checked below.
            same_namespace={x.get('nodeId') for x in cfg.get('configs',[]) if x.get('status')=='OBSERVED' and (x.get('fields') or {}).get('canal.zkServers')==zkhosts}
            expected_endpoints={str(x.get('ip'))+':'+str(x.get('tcpPort')) for x in nodes if x.get('id') in same_namespace and x.get('status')=='1'}
            if view.get('status')!='OBSERVED' or registry.get('code')!=0 or endpoint not in expected_endpoints or set(registry.get('children') or [])!=expected_endpoints:reason=reason or '共享 ZooKeeper 注册无法完整对应 Admin 节点'
        elif view.get('status')!='OBSERVED' or registry.get('code')!=0 or registry.get('children')!=[endpoint]:reason=reason or 'ZooKeeper Server 注册不唯一或不可用'
        if not any(re.search(r'(?:\*|\[?::\]?)?:11110\s',x) and 'pid='+str(r.get('pid'))+',' in x for x in i.get('listeners') or []):reason=reason or 'Admin 监听归属未确认'
        try:
            changed=datetime.strptime(config['modifiedTime'],'%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone(timedelta(hours=8)));started=datetime.fromisoformat(r['startedAt'].replace('Z','+00:00'))
            if changed>started:reason=reason or 'Admin 配置在进程启动后修改'
        except (KeyError,ValueError,TypeError,AttributeError):reason=reason or 'Admin 配置时间未知'
        if admin.host_apps_unresolved(report,r):reason=reason or '同机 DevOps 应用归属待核实'
        all_instances=(a.get('instances') or {}).get('items',[])
        assigned=[x for x in all_instances if x.get('clusterId')==cid] if cid is not None else [x for x in all_instances if x.get('clusterId') is None and x.get('serverId')==node.get('id')]
        ds=view.get('destinations') or [];destmap={x.get('name'):x for x in ds};names=[x.get('name') for x in assigned]
        if not assigned or len(names)!=len(set(names)) or len(destmap)!=len(ds) or view.get('destinationCount')!=len(ds):reason=reason or 'destination 清单不完整或重复'
        active=0
        for x in assigned:
            z=destmap.get(x.get('name')) or {};regs=z.get('members') or [];running=z.get('running');status=str(x.get('runningStatus'))
            if status=='1':
                active+=1
                if regs!=[endpoint] or z.get('clusterCode')!=0 or z.get('runningCode')!=0 or not isinstance(running,dict) or running.get('active') is not True or running.get('address')!=endpoint:reason=reason or '运行中的 destination 选主视图不一致'
            elif status=='0':
                if regs or (isinstance(running,dict) and running.get('active') is True):reason=reason or '停用 destination 与协调视图冲突'
            else:reason=reason or 'destination 运行状态未知'
        if not active:reason=reason or '没有可交叉核对的运行中 destination'
        if any(endpoint in (x.get('members') or []) and x.get('name') not in names for x in ds):reason=reason or 'ZooKeeper 存在未在 Admin 编组中的活动 destination'
        if cid is None and any(x.get('name') in names and x.get('id') not in {y.get('id') for y in assigned} for x in all_instances):reason=reason or '同名 destination 在其他 Admin 编组中重复'
        for stamp in (i.get('checkedAt'),a.get('observedAt'),cfg.get('observedAt'),view.get('observedAt')):
            try:
                age=datetime.now(timezone.utc)-datetime.fromisoformat(stamp.replace('Z','+00:00'))
                if age>timedelta(hours=2) or age< -timedelta(minutes=5):reason=reason or '实时核查已过期'
            except (ValueError,TypeError,AttributeError):reason=reason or '核查时间无效'
        if reason:unresolved.append({'ip':r.get('ip'),'pid':r.get('pid'),'reason':reason});continue
        groups.append({'key':{'assetId':r['assetId'],'configPath':path},'row':r,'destinations':sorted(names),'serverMode':cf.get('canal.serverMode'),'admin':manager,'adminNodeId':node['id'],'adminClusterId':cid,'groupingBasis':'ADMIN_CLUSTER_ZK_AGREEMENT' if cid is not None else 'ADMIN_STANDALONE_ZK_DISJOINT'})
    return {'groups':sorted(groups,key=lambda x:x['row']['ip']),'unresolved':unresolved}


def main():
    p=argparse.ArgumentParser();p.add_argument('--apply',action='store_true');args=p.parse_args();data=Path(os.environ.get('ENVSCOPE_DATA','/data'));report=report_from_sources(data);prepared=plan(report)
    if not args.apply:
        print(json.dumps({'groups':[{'ip':x['row']['ip'],'admin':x['admin'],'clusterId':x['adminClusterId'],'destinationCount':len(x['destinations'])} for x in prepared['groups']],'unresolved':prepared['unresolved']},ensure_ascii=False,indent=2));return
    result=admin.import_report(db.Catalog.from_environment(),report,report['sourceVersion'],can_apply=lambda:json.loads((data/'current.json').read_text()).get('id')==report['sourceVersion'],planner=plan)
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()

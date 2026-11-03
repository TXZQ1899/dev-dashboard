"""Create a user-requested Canal node with explicitly unresolved grouping."""
import argparse
from datetime import datetime,timedelta,timezone
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

RULE='canal-user-pending-v1'


def report_from_sources(data):
    data=Path(data);report=admin.report_from_sources(data);v=report['sourceVersion'];path=data/'checks'/'canal-review'/('user-canal-pending-'+v+'.json')
    confirmation=json.loads(path.read_text())
    if confirmation.get('sourceVersion')!=v:raise ValueError('用户确认来源版本不一致')
    report.update(ruleVersion=RULE,pendingConfirmation=confirmation,reportFiles=report['reportFiles']+[path.name]);return report


def plan(report):
    if not report.get('sourceVersion') or report.get('ruleVersion')!=RULE:raise ValueError('来源或规则版本无效')
    c=report.get('pendingConfirmation') or {};v=report['sourceVersion']
    if c.get('sourceVersion')!=v or c.get('kind')!='USER_PER_NODE_PENDING' or not c.get('confirmedAt') or not c.get('assetId') or not c.get('ip'):raise ValueError('缺少明确的单节点待确认指令')
    candidates=[r for r in report.get('results',[]) if r.get('assetId')==c['assetId'] and r.get('ip')==c['ip']]
    if len(candidates)!=1:raise ValueError('用户指定资产的 Server 候选不唯一')
    r=candidates[0];i=r.get('initial') or {};fields=i.get('fields') or {};reason=None
    path=i.get('configArgument') or '';pid=r.get('pid');proc=i.get('before');m=re.fullmatch(r'(\d+) \(java\) (\d+)',str(proc or ''))
    if r.get('processStatus')!='complete' or not re.fullmatch(r'/[A-Za-z0-9_./-]+/conf/canal\.properties',path) or i.get('configArgumentMatch')!='MATCH' or i.get('configReadable')!='YES' or not m or int(m[1])!=pid or i.get('after')!=proc:reason='进程身份或启动配置未确认'
    manager=fields.get('canal.admin.manager','')
    if manager.startswith('127.0.0.1:'):manager=r['ip']+manager[len('127.0.0.1'):]
    admins=(report.get('adminMembers') or {}).get('results',[]);matches=[x for x in admins if x.get('admin')==manager]
    if len(matches)!=1 or matches[0].get('status')!='UNKNOWN' or not matches[0].get('error'):reason=reason or 'Admin 不可达证据未确认'
    if (report.get('adminMembers') or {}).get('sourceVersion')!=v:reason=reason or 'Admin 证据版本不一致'
    if not any(re.search(r'(?:\*|\[?::\]?)?:11110\s',x) and 'pid='+str(pid)+',' in x for x in i.get('listeners') or []):reason=reason or 'Server 监听归属未确认'
    if admin.host_apps_unresolved(report,r):reason=reason or '同机 DevOps 应用归属未确认'
    for stamp in (i.get('checkedAt'),matches[0].get('observedAt') if matches else None,c.get('confirmedAt')):
        try:
            age=datetime.now(timezone.utc)-datetime.fromisoformat(stamp.replace('Z','+00:00'))
            if age>timedelta(hours=2) or age< -timedelta(minutes=5):reason=reason or '核查或用户确认已过期'
        except (ValueError,TypeError,AttributeError):reason=reason or '核查或用户确认时间无效'
    if reason:return {'groups':[],'unresolved':[{'ip':r.get('ip'),'pid':pid,'reason':reason}]}
    return {'groups':[{'key':{'assetId':r['assetId'],'configPath':path},'row':r,'destinations':None,'serverMode':None,'admin':manager,'adminNodeId':None,'adminClusterId':None,'groupingBasis':'USER_PER_NODE_PENDING'}],'unresolved':[]}


def correct_legacy_unknown(store,report,can_apply=lambda:True):
    """Append a correction to the early pending observation that used an empty list."""
    prepared=plan(report)
    if len(prepared['groups'])!=1:raise db.CatalogError('待确认节点不符合修正条件')
    item=prepared['groups'][0];key=item['key'];source=store.source_key+':canal'
    with store.connection() as conn,store.database_lock(conn,'component-canal'):
        conn.begin()
        try:
            rows=store.execute(conn,"SELECT i.id AS instance_id,i.current_observation_id,o.import_run_id,o.observed_at,o.source_attributes_json,o.evidence_json,s.evidence_json AS source_evidence FROM deployment_unit_source s JOIN deployment_unit_environment e ON e.unit_id=s.unit_id JOIN deployment_instance_source ds ON ds.unit_environment_id=e.id AND ds.source_instance_key=s.source_instance_key AND ds.native_key_hash=s.native_key_hash JOIN deployment_instance i ON i.id=ds.instance_id JOIN catalog_observation o ON o.id=i.current_observation_id WHERE s.source_instance_key=%s AND s.object_kind='CANAL_SERVER' AND s.native_key_hash=%s FOR UPDATE",(source,db.digest(key)))
            if len(rows)!=1:raise db.CatalogError('待确认节点目录身份不唯一')
            row=rows[0];attrs=db.decode(row['source_attributes_json'],{});source_ev=db.decode(row['source_evidence'],{})
            if source_ev.get('groupingBasis')!='USER_PER_NODE_PENDING' or attrs.get('groupingState')!='PENDING' or attrs.get('groupingConfidence')!='UNKNOWN':raise db.CatalogError('目录单元不是用户指定的待确认节点')
            if attrs.get('destinations') is None and attrs.get('destinationsState')=='UNKNOWN':conn.rollback();return False
            if attrs.get('destinations')!=[]:raise db.CatalogError('destination 状态不是可安全修正的旧空列表')
            before={'destinations':attrs['destinations'],'destinationsState':attrs.get('destinationsState')}
            attrs.update(destinations=None,destinationsState='UNKNOWN')
            locator={**key,'record':'instance-destination-unknown-correction'};oid=db.identifier();run=row['import_run_id']
            store.insert(conn,'catalog_observation',{'id':oid,'import_run_id':run,'record_kind':'DEPLOYMENT','instance_id':row['instance_id'],'source_locator_json':db.encode(locator),'source_locator_hash':db.digest(locator),'observed_at':row['observed_at'],'time_basis':'LIVE_CHECK','source_presence':'PRESENT','confidence':'INFERRED','matching_basis':'CANAL_USER_PENDING_PROCESS','source_attributes_json':db.encode(attrs),'evidence_json':db.encode(db.decode(row['evidence_json'],{})),'semantic_hash':db.digest(attrs)})
            store.update(conn,'deployment_instance',row['instance_id'],{'current_observation_id':oid})
            store.insert(conn,'catalog_change',{'id':db.identifier(),'change_batch_id':run,'object_type':'INSTANCE','object_id':row['instance_id'],'action':'UPDATE','before_json':db.encode(before),'after_json':db.encode({'destinations':None,'destinationsState':'UNKNOWN'}),'actor_type':'IMPORT','import_run_id':run,'changed_at':db.utcnow(),'reason':'将未核实的 destination 从空列表修正为未知'})
            if not can_apply():raise db.CatalogError('核查来源版本已切换，本次未修正')
            conn.commit();return True
        except Exception:conn.rollback();raise


def main():
    p=argparse.ArgumentParser();p.add_argument('--apply',action='store_true');args=p.parse_args();data=Path(os.environ.get('ENVSCOPE_DATA','/data'));report=report_from_sources(data);prepared=plan(report)
    if not args.apply:
        print(json.dumps({'groups':[{'ip':x['row']['ip'],'groupingState':'PENDING','groupingConfidence':'UNKNOWN','admin':x['admin']} for x in prepared['groups']],'unresolved':prepared['unresolved']},ensure_ascii=False,indent=2));return
    store=db.Catalog.from_environment();current=lambda:json.loads((data/'current.json').read_text()).get('id')==report['sourceVersion']
    result=admin.import_report(store,report,report['sourceVersion'],can_apply=current,planner=plan)
    result['correctedUnknownDestination']=correct_legacy_unknown(store,report,can_apply=current)
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()

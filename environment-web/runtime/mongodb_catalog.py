"""Conservative import of a MongoDB standalone proven by config and live observation."""
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import deployment_catalog as db
except ModuleNotFoundError:
    from runtime import deployment_catalog as db

RULE = 'mongodb-config-standalone-v1'
TARGET = ('90c0b5c0-2e86-4242-bfc8-850a609fbe29', '10.58.1.42', 27571)
PROFILES = {
    '10.58.1.42': (TARGET, '10.58.1.42,127.0.0.1', 'config-three-'),
    '10.179.4.76': (('1adc25fa-2cd5-4536-80be-657f0b291d77', '10.179.4.76', 1467), '0.0.0.0', 'config-two-'),
    '10.58.10.92': (('9080aeaf-d384-484a-9d5f-3d14b9d72666', '10.58.10.92', 1066), '0.0.0.0', 'config-two-'),
    '10.58.16.4': (('bd12b0d5-501e-403f-b480-62dc9ad615b0', '10.58.16.4', 79868), 'EXPLICIT_ARGV', 'config-cli-'),
    '10.179.2.14': (('6e9b4297-0914-4a21-978a-f071de6960aa', '10.179.2.14', 1222), 'EXPLICIT_ARGV', 'config-cli-'),
    '10.58.1.112': (('3f01adcc-0d35-416a-a50b-afd3d5103f64', '10.58.1.112', 255551), '0.0.0.0', 'config-three-'),
    '10.58.9.81': (('8e60a67b-1c46-4d78-8b5f-21466f263766', '10.58.9.81', 1107), 'EXPLICIT_ARGV', 'config-cli-'),
    '10.179.2.152': (('6bf5a5a0-8d54-4433-8cd9-0f2fae0fc2a7', '10.179.2.152', 23214), 'DEVOPS_CONFIG', 'devops-standalone-'),
    '10.179.2.156': (('f6246393-75de-456d-8213-3cfaca28c336', '10.179.2.156', 5000), 'DEVOPS_CONFIG', 'devops-standalone-'),
    '10.58.6.4': (('030b1a7a-d970-4427-982b-c87daf4158f7', '10.58.6.4', 6852), 'USER_SINGLE', 'config-user-single-'),
}


def plan(snapshot, live, config, version, now=None, replica=None, confirmation=None):
    if any(x.get('sourceVersion') != version for x in (snapshot, live, config)):
        raise ValueError('核查来源版本不一致')
    def one(report, key):
        rows = [x for x in report.get(key, []) if (x.get('assetId'), x.get('ip'), x.get('pid')) == TARGET]
        if len(rows) != 1:
            raise ValueError('候选身份不唯一')
        return rows[0]
    row, process, review = one(snapshot, 'candidates'), one(live, 'results'), one(config, 'results')
    now = now or datetime.now(timezone.utc)
    checked = datetime.fromisoformat(review['checkedAt'].replace('Z', '+00:00'))
    if not timedelta(minutes=-5) <= now - checked <= timedelta(hours=2):
        raise ValueError('配置核查已过期')
    if snapshot.get('devopsComplete') is not True or snapshot.get('devopsScope') != 'all':
        raise ValueError('应用快照不完整')
    bind = PROFILES[TARGET[1]][1]
    port = 9998 if bind == 'EXPLICIT_ARGV' else 27017
    if bind == 'USER_SINGLE':
        expected = {'assetId':TARGET[0], 'ip':TARGET[1], 'port':27017, 'answer':'确认独立部署'}
        if not confirmation or confirmation.get('sourceVersion') != version or confirmation.get('standalone') != expected:
            raise ValueError('独立部署的用户确认无效')
    deployments = row.get('hostDeployments') or []
    if deployments:
        confirmed = [x for x in (confirmation or {}).get('results', []) if (x.get('assetId'),x.get('ip'),x.get('pid')) == TARGET and x.get('hostDeploymentsDigest') == db.digest(deployments) and x.get('hostDeploymentsCount') == len(deployments)]
        if (not confirmation or confirmation.get('sourceVersion') != version or confirmation.get('answer') != '不负责' or len(confirmed) != 1):
            raise ValueError('同机应用管理关系未确认')
    if (row.get('processStatus') != 'complete' or row.get('name') != 'mongod' or row.get('snapshotPorts') != [port]
        or row.get('containerMatches') or set(row.get('applicationCandidates', {})) != {'TEST','SIMULATION','PRODUCT'}
        or any(row['applicationCandidates'].values())):
        raise ValueError('进程、容器或应用归属未确认')
    if process.get('status') != 'OBSERVED' or review.get('status') != 'OBSERVED' or review.get('exitCode') != 0:
        raise ValueError('实时核查失败')
    lines = review.get('output', '').splitlines()
    def unique(pattern):
        matches = [m for line in lines if (m := re.fullmatch(pattern, line))]
        if len(matches) != 1:
            raise ValueError('配置核查字段缺失或重复')
        return matches[0]
    before = unique(r'BEFORE'); after = unique(r'AFTER')
    bi, ai = lines.index(before.group()), lines.index(after.group())
    identity = re.compile(re.escape(str(TARGET[2])) + r' \(mongod\) (\d+)')
    b = identity.fullmatch(lines[bi + 1]); a = identity.fullmatch(lines[ai + 1])
    if not b or not a or b[1] != a[1]:
        raise ValueError('进程已变化')
    started = datetime.fromisoformat(row['startedAt'].replace('Z', '+00:00')).timestamp()
    if bind == 'DEVOPS_CONFIG':
        ps_start = unique(r'PS_START\s+(.+)')[1]
        live_started = datetime.strptime(ps_start, '%a %b %d %H:%M:%S %Y').replace(tzinfo=timezone.utc).timestamp()
        if started != live_started: raise ValueError('进程与快照启动时间不一致')
    else:
        btime = int(unique(r'BTIME (\d+)')[1]); hz = int(unique(r'HZ (\d+)')[1])
        if hz <= 0: raise ValueError('时钟频率无效')
        live_started = btime + int(b[1]) / hz
    if bind in ('EXPLICIT_ARGV','USER_SINGLE'):
        ps_start = unique(r'PS_START\s+(.+)')[1]
        live_ps = datetime.strptime(ps_start, '%a %b %d %H:%M:%S %Y').replace(tzinfo=timezone.utc).timestamp()
        # ps derives wall-clock start from boot time, which can shift after clock correction.
        if abs(live_ps - live_started) > 2 or abs(started - live_ps) > (120 if bind == 'USER_SINGLE' else 30):
            raise ValueError('进程与快照启动时间不一致')
    elif bind != 'DEVOPS_CONFIG' and abs(started - live_started) > 2:
        raise ValueError('进程与快照启动时间不一致')
    executable = '/usr/local/server/mongod/bin/mongod' if bind == 'EXPLICIT_ARGV' else ('/opt/devops/mongodb/bin/mongod' if bind == 'DEVOPS_CONFIG' else '/usr/bin/mongod')
    if 'EXE '+executable not in lines or 'ARGV_MATCH' not in lines:
        raise ValueError('可执行文件或配置启动链未确认')
    if bind == 'EXPLICIT_ARGV':
        expected_args = [executable, '--dbpath=/usr/local/server/mongod/data', '--logpath', '/usr/local/server/mongod/mongolog/mongo.log', '--port', '9998', '--fork', '-auth']
        if review.get('expectedArgv') != expected_args or 'NO_EXPLICIT_CONFIG' not in lines:
            raise ValueError('启动参数或配置覆盖待核实')
        config_path = None
    elif bind == 'DEVOPS_CONFIG':
        config_path = '/opt/devops/mongodb/etc/mongodb.conf'
        option = '--config' if TARGET[1]=='10.179.2.152' else '-f'
        if review.get('expectedArgv') != [executable,option,config_path] or 'CONFIG_READABLE' not in lines: raise ValueError('启动参数或配置不符')
        stat=unique(r'CONFIG_STAT (\d+) (\d+) (\d+)');endstat=unique(r'CONFIG_STAT_AFTER (\d+) (\d+) (\d+)')
        if stat.groups()!=endstat.groups() or int(stat[2])>live_started: raise ValueError('配置变更时间或身份不可靠')
        keys=[line[4:] for line in lines if line.startswith('KEY ')]
        expected={'port','dbpath','logpath','pidfilepath','directoryperdb','logappend','bind_ip','oplogSize','fork','noprealloc'}
        if len(keys)!=len(set(keys)) or set(keys)!=expected or 'KEY_UNPARSED' in lines or [x for x in lines if x.startswith(('VALUE ','VALUE_UNPARSED '))]!=['VALUE port 27017','VALUE bind_ip 0.0.0.0']:
            raise ValueError('旧格式配置存在未知或复制选项')
    else:
        if 'CONFIG_READABLE' not in lines: raise ValueError('配置不可读取')
        stat = unique(r'CONFIG_STAT (\d+) (\d+) (\d+)')
        endstat = unique(r'CONFIG_STAT_AFTER (\d+) (\d+) (\d+)')
        if stat.groups() != endstat.groups() or (bind != 'USER_SINGLE' and int(stat[2]) > live_started):
            raise ValueError('配置变更时间或文件身份不可靠')
        roots = [line[5:] for line in lines if line.startswith('ROOT ')]
        expected_roots = {'systemLog','storage','processManagement','net'}
        if TARGET[1] == '10.58.1.42': expected_roots.add('setParameter')
        if bind == 'USER_SINGLE': expected_roots.update(('security','setParameter'))
        if TARGET[1] == '10.58.1.112': expected_roots.add('replication')
        if len(roots) != len(set(roots)) or set(roots) != expected_roots:
            raise ValueError('配置模式或可能的复制、分片配置待核实')
        fields = [line.strip() for line in lines if line.startswith(('FIELD ', 'FIELD_UNPARSED '))]
        expected_fields = 3 if TARGET[1] == '10.58.1.112' else 2
        expected_bind = TARGET[1] if bind == 'USER_SINGLE' else bind
        if len(fields) != expected_fields or not any(re.fullmatch(r'FIELD net\s+port:\s*27017', x) for x in fields) or not any(re.fullmatch(r'FIELD net\s+bindIp:\s*'+re.escape(expected_bind), x) for x in fields):
            raise ValueError('监听配置或额外参数未确认')
        if TARGET[1] == '10.58.1.112' and not any(re.fullmatch(r'FIELD replication\s+replSetName:\s*test', x) for x in fields):
            raise ValueError('副本集名称未确认')
        config_path = '/etc/mongod.conf'
    hello_lines = [m for line in lines if (m := re.search(r'HELLO_JSON (\{.*\})$', line))]
    if len(hello_lines) != 1: raise ValueError('服务响应缺失或重复')
    hello = json.loads(hello_lines[0][1])
    if TARGET[1] == '10.58.1.112':
        address = TARGET[1]+':27017'
        if hello != {'ok':1,'setName':'test','hosts':[address],'primary':address,'me':address}:
            raise ValueError('副本集服务视图不匹配')
        if not replica or replica.get('sourceVersion') != version or replica.get('assetId') != TARGET[0] or replica.get('pid') != TARGET[2] or replica.get('status') != 'OBSERVED' or replica.get('exitCode') != 0:
            raise ValueError('副本集配置未确认')
        rt = datetime.fromisoformat(replica['checkedAt'].replace('Z','+00:00'))
        if not timedelta(minutes=-5) <= now-rt <= timedelta(hours=2): raise ValueError('副本集配置核查已过期')
        rlines = replica.get('output','').splitlines()
        if rlines.count(str(TARGET[2])+' (mongod) '+b[1]) != 2: raise ValueError('副本集进程身份变化')
        members = [m for line in rlines if (m := re.fullmatch(r'CONFIG_JSON (\{.*\})', line))]
        if len(members) != 1 or json.loads(members[0][1]) != {'_id':'test','members':[{'_id':0,'host':address,'hidden':False,'arbiterOnly':False,'votes':1}]}:
            raise ValueError('副本集完整配置不是单成员')
        mode = 'REPLICA_SET'
    else:
        if hello != {'ok': 1}: raise ValueError('服务角色或复制状态不符合独立部署')
        mode = 'STANDALONE'
    observed = process.get('output', '')
    pid = str(TARGET[2])
    expected_ips = [TARGET[1]] if bind == 'USER_SINGLE' else (['0.0.0.0'] if TARGET[1] == '10.58.1.112' else ([TARGET[1], '127.0.0.1'] if bind not in ('0.0.0.0','EXPLICIT_ARGV','DEVOPS_CONFIG') else ['*']))
    if (observed.count(pid+' (mongod) '+b[1]) != 2 or any(
        not re.search(re.escape(ip)+':'+str(port)+r'\s+.*pid='+pid+',', observed) for ip in expected_ips)):
        raise ValueError('进程监听证据不足')
    return {'row': row, 'review': review, 'startTicks': b[1], 'checkedAt': review['checkedAt'],
            'endpoints': [{'ip':ip,'port':port,'protocol':'tcp'} for ip in expected_ips], 'port':port, 'configPath':config_path, 'mode':mode}


def import_report(store, snapshot, live, config, version, data, can_apply=lambda: True, replica=None, confirmation=None):
    item = plan(snapshot, live, config, version, replica=replica, confirmation=confirmation)
    report = {'ruleVersion': RULE, 'sourceVersion': version, 'target': {'assetId': TARGET[0], 'ip': TARGET[1], 'pid': TARGET[2]}, 'snapshot': snapshot, 'live': live, 'config': config, 'replica':replica, 'appConfirmation':confirmation}
    payload, digest = db.encode(report), db.digest(report)
    root = Path(data) / 'checks/mongodb-review/evidence'; root.mkdir(parents=True, exist_ok=True)
    path = root / (digest + '.json')
    if path.exists() and path.read_text() != payload:
        raise ValueError('证据哈希冲突')
    if not path.exists():
        tmp = path.with_suffix('.tmp'); tmp.write_text(payload); tmp.chmod(0o600); os.replace(tmp, path)
    store.migrate(); source = store.source_key + ':mongodb'; key = {'assetId': TARGET[0], 'configPath': item['configPath'], 'port':item['port']}
    if not item['configPath']: key['dbPath'] = '/usr/local/server/mongod/data'
    object_kind = 'MONGODB_REPLICA_SET' if item['mode']=='REPLICA_SET' else 'MONGODB_STANDALONE'
    if item['mode']=='REPLICA_SET': key['replicaSetName'] = 'test'
    stamp = db.timestamp(item['checkedAt']); evidence = {'sourceVersion':version,'reportPath':'checks/mongodb-review/evidence/'+digest+'.json',
        'groupingBasis':'USER_CONFIRMED_STANDALONE' if TARGET[1]=='10.58.6.4' else ('REPLICA_CONFIG_AND_MEMBER_VIEW' if item['mode']=='REPLICA_SET' else ('STARTUP_CONFIG_AND_ISMASTER_STANDALONE' if item['configPath'] else 'EXPLICIT_ARGV_AND_ISMASTER_STANDALONE')),'groupingConfidence':'INFERRED',
        'coverage':'用户确认当前独立部署；配置文件在进程启动后修改，运行配置未获授权读取' if TARGET[1]=='10.58.6.4' else '启动配置或参数、当前服务响应及监听；不排除历史关系，应用清单以快照范围为限'}
    with store.connection() as conn, store.database_lock(conn, 'component-mongodb'):
        conn.begin()
        try:
            found = store.execute(conn, "SELECT s.unit_id,u.name FROM deployment_unit_source s JOIN deployment_unit u ON u.id=s.unit_id WHERE s.source_instance_key=%s AND s.object_kind=%s AND s.native_key_hash=%s", (source, object_kind, db.digest(key)))
            if found:
                conn.rollback(); return {'name':found[0]['name'],'existing':True}
            existing = store.execute(conn, "SELECT id FROM deployment_instance_source WHERE source_instance_key=%s AND JSON_UNQUOTE(JSON_EXTRACT(native_key_json,'$.assetId'))=%s", (source, TARGET[0]))
            if existing: raise db.CatalogError('该资产已有 MongoDB 绑定，需复核')
            if not can_apply(): raise db.CatalogError('当前快照或核查证据已变化')
            now, run, unit, env, instance = db.utcnow(), db.identifier(), db.identifier(), db.identifier(), db.identifier()
            store.execute(conn, "INSERT INTO catalog_component_counter (technology,next_number) VALUES ('MongoDB',1) ON DUPLICATE KEY UPDATE technology=technology")
            number = store.execute(conn, "SELECT next_number FROM catalog_component_counter WHERE technology='MongoDB' FOR UPDATE")[0]['next_number']
            store.execute(conn, "UPDATE catalog_component_counter SET next_number=next_number+1 WHERE technology='MongoDB'")
            name = 'MongoDB_cluster_'+str(number)
            if store.execute(conn, 'SELECT id FROM deployment_unit WHERE name=%s', (name,)): raise db.CatalogError('单元名称冲突')
            store.insert(conn,'catalog_import_run',{'id':run,'source_instance_key':source,'source_version':version,'source_relative_path':evidence['reportPath'],'snapshot_sha256':digest,'mapper_version':RULE,'scope_json':db.encode({'type':'MongoDB','assets':[TARGET[0]]}),'completeness_json':db.encode({'complete':False}),'observed_at':stamp,'imported_at':now,'status':'WRITING'})
            store.insert(conn,'deployment_unit',{'id':unit,'code':'DU-'+unit,'name':name,'unit_type':'DATABASE','technology_code':'MongoDB','creation_origin':'PROCESS_REVIEW','name_policy':'MANUAL','created_at':now,'updated_at':now})
            store.insert(conn,'deployment_unit_environment',{'id':env,'unit_id':unit,'environment':'UNIDENTIFIED','configuration_state':'HAS_DEPLOYMENTS','coverage_state':'PARTIAL','observed_at':stamp,'updated_at':now})
            def obs(kind, owner, suffix, attrs):
                oid=db.identifier(); loc={**key,'record':suffix}
                store.insert(conn,'catalog_observation',{'id':oid,'import_run_id':run,'record_kind':kind,**owner,'source_locator_json':db.encode(loc),'source_locator_hash':db.digest(loc),'observed_at':stamp,'time_basis':'LIVE_CHECK','source_presence':'PRESENT','confidence':'INFERRED','matching_basis':'MONGODB_CONFIG_AND_RUNTIME','source_attributes_json':db.encode(attrs),'evidence_json':db.encode(evidence),'semantic_hash':db.digest(attrs)})
                return oid
            unit_attrs = {'technology':'MongoDB','mode':item['mode']}
            if item['mode']=='REPLICA_SET': unit_attrs.update(replicaSetName='test', configuredMembers=[TARGET[1]+':27017'])
            uobs=obs('COMPONENT',{'unit_id':unit},'unit',unit_attrs)
            eobs=obs('ENVIRONMENT',{'unit_environment_id':env},'environment',{'coverage':'PARTIAL'})
            store.update(conn,'deployment_unit_environment',env,{'current_observation_id':eobs})
            store.insert(conn,'deployment_unit_source',{'id':db.identifier(),'unit_id':unit,'source_instance_key':source,'object_kind':object_kind,'native_key_json':db.encode(key),'native_key_hash':db.digest(key),'presence_status':'PRESENT','first_seen_at':stamp,'last_seen_at':stamp,'latest_observation_id':uobs,'confidence':'INFERRED','evidence_json':db.encode(evidence)})
            store.insert(conn,'deployment_instance',{'id':instance,'unit_environment_id':env,'instance_kind':'SERVICE_PROCESS','primary_ip':TARGET[1],'declared_port_raw':str(item['port']),'transport_protocol':'tcp','host_reference_json':db.encode({'assetId':TARGET[0]}),'identity_quality':'INFERRED','presence_status':'PRESENT','first_seen_at':stamp,'last_seen_at':stamp,'created_at':now,'updated_at':now})
            iobs=obs('DEPLOYMENT',{'instance_id':instance},'instance',{'mode':item['mode'],'observedEndpoints':item['endpoints'],'configPath':item['configPath'],'pid':TARGET[2],'startTicks':item['startTicks']})
            store.update(conn,'deployment_instance',instance,{'current_observation_id':iobs})
            store.insert(conn,'deployment_instance_source',{'id':db.identifier(),'instance_id':instance,'unit_environment_id':env,'source_instance_key':source,'object_kind':'MONGODB_PROCESS','native_key_json':db.encode(key),'native_key_hash':db.digest(key),'identity_strength':'INFERRED','confidence':'INFERRED','matching_basis':'ASSET_CONFIG_ENDPOINT','first_seen_at':stamp,'last_seen_at':stamp,'evidence_json':db.encode(evidence)})
            for typ, oid in (('INSTANCE',instance),('UNIT',unit)):
                store.insert(conn,'catalog_change',{'id':db.identifier(),'change_batch_id':run,'object_type':typ,'object_id':oid,'action':'CREATE','after_json':db.encode({'name':name,'ip':TARGET[1]}),'actor_type':'IMPORT','import_run_id':run,'changed_at':now,'reason':'MongoDB 启动配置、服务响应和监听只读核查'})
            if not can_apply(): raise db.CatalogError('当前快照或核查证据已变化')
            store.execute(conn, 'UPDATE catalog_import_run SET is_current=FALSE WHERE source_instance_key=%s AND is_current=TRUE', (source,))
            store.update(conn,'catalog_import_run',run,{'status':'READY','is_current':True,'counts_json':db.encode({'createdUnits':1})})
            conn.commit(); return {'name':name,'ip':TARGET[1],'existing':False,'evidence':evidence['reportPath']}
        except Exception:
            conn.rollback(); raise


def main():
    import argparse
    global TARGET
    parser=argparse.ArgumentParser(); parser.add_argument('--apply',action='store_true'); parser.add_argument('--target',choices=PROFILES,default='10.58.1.42'); args=parser.parse_args()
    TARGET, _, config_prefix = PROFILES[args.target]
    data=Path(os.environ.get('ENVSCOPE_DATA','/data')); read=lambda p:json.loads(p.read_text()); version=read(data/'current.json')['id']; root=data/'checks/mongodb-review'
    paths=[root/(prefix+version+'.json') for prefix in ('snapshot-','live-process-',config_prefix)]
    if args.target == '10.58.1.112': paths.append(root/('replica-config-'+version+'.json'))
    if args.target in ('10.58.9.81','10.179.2.152','10.179.2.156'): paths.append(root/('user-app-confirmation-'+version+'.json'))
    if args.target == '10.58.6.4': paths.append(root/('user-final-confirmation-'+version+'.json'))
    raw=[p.read_bytes() for p in paths]; reports=[json.loads(x) for x in raw]; replica=reports[3] if args.target == '10.58.1.112' else None
    confirmation=reports[3] if args.target in ('10.58.9.81','10.179.2.152','10.179.2.156','10.58.6.4') else None
    item=plan(*reports[:3],version,replica=replica,confirmation=confirmation)
    if not args.apply:
        print(json.dumps({'ip':TARGET[1],'port':item['port'],'mode':item['mode'],'confidence':'INFERRED','checkedAt':item['checkedAt']},ensure_ascii=False)); return
    def unchanged():
        return read(data/'current.json').get('id')==version and all(p.read_bytes()==v for p,v in zip(paths,raw))
    print(json.dumps(import_report(db.Catalog.from_environment(),*reports[:3],version,data,unchanged,replica=replica,confirmation=confirmation),ensure_ascii=False))

if __name__=='__main__': main()

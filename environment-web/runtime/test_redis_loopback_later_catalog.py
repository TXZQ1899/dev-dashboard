import copy,unittest
import redis_loopback_later_catalog as mod

def fixture():
    k={'assetId':'a','pid':7,'ip':'10.0.0.1','startTicks':'123','status':'OBSERVED','observedAt':'2026-01-01T00:00:00Z'}; app={'TEST':[],'SIMULATION':[],'PRODUCT':[]}
    audit={'sourceVersion':'v','completedAt':'2026-01-01T00:01:00Z','results':[{**k,'frames':5,'warnings':[],'container':None,'containerMatches':0,'dockerWarnings':[],'applicationCandidates':copy.deepcopy(app),'lines':['META\texe\tredis-server','META\tnetns\tnet:[1]','META\thostnetns\tnet:[1]','META\tservice\tredisd.service']}]}
    runtime={'status':'OBSERVED','sections':{'server':{'redis_mode':'standalone','run_id':'a'},'replication':{'role':'master','connected_slaves':'0'},'cluster':{'cluster_enabled':'0'}},'config':{'bind':'127.0.0.1','port':'7201','dir':'/','dbfilename':'dump.rdb','replicaof':'','slaveof':''},'clients':{'replicaCount':0}}
    deep={'sourceVersion':'v','results':[{**k,'frames':5,'warnings':[],'applicationCandidates':copy.deepcopy(app),'runtime':runtime}]}
    conf={'path':'/etc/redis/7201.conf','pidfileMatches':True,'directives':[['bind','127.0.0.1'],['port','7201'],['dir','./'],['dbfilename','dump.rdb']]}
    discovery={'sourceVersion':'v','results':[{**k,'detail':{'status':'OBSERVED','cwd':'/','service':{'unit':'redisd.service'},'processes':[{'pid':7,'name':'redis-server'}],'files':[conf]}}]}
    legacy={'sourceVersion':'v','results':[]};legacycluster={'sourceVersion':'v','results':[]}
    loop={'sourceVersion':'v','results':[{**k,'frames':5,'warnings':[],'connectionCount':0,'connections':[],'listeners':[{'ip':'127.0.0.1','port':7201,'protocol':'tcp'}]}]}
    sent={'sourceVersion':'v','results':[{'status':'OBSERVED','info':{'status':'OBSERVED','masters':[]}} for _ in range(9)]}
    return audit,deep,discovery,legacy,legacycluster,loop,sent

class LaterLoopbackTests(unittest.TestCase):
    def test_pidfile_and_runtime_config_proof(self):
        p=mod.plan(*fixture());self.assertEqual(7201,p['accepted'][0]['port'])
    def test_legacy_redis_requires_original_service_and_no_cluster_rename(self):
        a,b,c,d,e,f,g=fixture(); key={k:a['results'][0][k] for k in ('assetId','pid','ip','startTicks','status','observedAt')}
        a['results'][0]['lines'][-1]='META\tservice\tredis.service'; b['results'][0].pop('runtime')
        b['results'][0]['runtimeLines']=['INFO\tredis_version\t2.8.19','INFO\tredis_mode\tstandalone','INFO\trun_id\tx','INFO\trole\tmaster','INFO\tconnected_slaves\t0','CONFIG\tbind\t127.0.0.1','CONFIG\tport\t6379','CONFIG\tslaveof\t','CLIENTS\t1\t0']
        c['results']=[]; d['results']=[{**key,'lines':['PROCESS\t7\tredis-server','REFERENCE\t/usr/lib/systemd/system/redis.service\t/etc/redis.conf\tDIRECT','FILE\t/etc/redis.conf\tREADABLE','DIRECTIVE\t/etc/redis.conf\tbind\t127.0.0.1','DIRECTIVE\t/etc/redis.conf\tport\t6379']}]
        e['results']=[{'ip':key['ip'],'status':'OBSERVED','startTicks':'123','clusterStatus':'UNKNOWN_COMMAND'}]; f['results'][0]['listeners'][0]['port']=6379
        self.assertEqual('REDIS_2_8_UNKNOWN_CLUSTER_COMMAND',mod.plan(a,b,c,d,e,f,g)['accepted'][0]['clusterEvidence'])
        d['results'][0]['lines'].append('RENAME_CLUSTER\tYES')
        self.assertEqual([],mod.plan(a,b,c,d,e,f,g)['accepted'])
    def test_rejects_incomplete_or_conflicting_observations(self):
        for issue in ('pidfile','replica','sentinel','uncollected','bind','config','process','tcp','app','source'):
            a,b,c,d,e,f,g=fixture()
            if issue=='pidfile':c['results'][0]['detail']['files'][0]['pidfileMatches']=False
            if issue=='replica':b['results'][0]['runtime']['clients']['replicaCount']=1
            if issue=='sentinel':g['results'][0]['info']['masters']=[{'ip':'10.0.0.1','replicas':[]}]
            if issue=='uncollected':g['results'].pop()
            if issue=='bind':f['results'][0]['listeners'][0]['ip']='0.0.0.0'
            if issue=='config':c['results'][0]['detail']['files'][0]['directives'].append(['replicaof','10.0.0.2 6379'])
            if issue=='process':c['results'][0]['detail']['processes'].append({'pid':8,'name':'redis-sentinel'})
            if issue=='tcp':f['results'][0]['connectionCount']=1
            if issue=='app':b['results'][0]['applicationCandidates']['PRODUCT']=['app']
            if issue=='source':
                g['sourceVersion']='other'
                with self.assertRaises(ValueError):mod.plan(a,b,c,d,e,f,g)
                continue
            self.assertEqual([],mod.plan(a,b,c,d,e,f,g)['accepted'],issue)

if __name__=='__main__':unittest.main()

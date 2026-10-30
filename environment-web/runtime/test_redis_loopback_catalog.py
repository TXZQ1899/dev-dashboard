import copy,unittest
import redis_loopback_catalog as mod

def fixture():
    key={'assetId':'a','pid':1,'ip':'10.0.0.1','startTicks':'10','status':'OBSERVED','observedAt':'2026-01-01T00:00:00Z'}
    app={'TEST':[],'SIMULATION':[],'PRODUCT':[]}; ep={'ip':'127.0.0.1','port':6379,'protocol':'tcp'}
    audit={'sourceVersion':'v','completedAt':'2026-01-01T00:01:00Z','results':[{**key,'frames':5,'warnings':[],'container':None,'containerMatches':0,'dockerWarnings':[],'connectionCount':0,'connections':[],'applicationCandidates':app,'lines':['META\texe\tredis-server','META\tnetns\tnet:[1]','META\thostnetns\tnet:[1]','META\tservice\tredis.service','FILE\t/etc/redis.conf\tsystemd_unit_file\tREADABLE','DIRECTIVE\t/etc/redis.conf\tbind\t127.0.0.1','DIRECTIVE\t/etc/redis.conf\tport\t6379','INFO\trole\tmaster','INFO\tconnected_slaves\t0','INFO\trun_id\tabc','CONFIG_GET\tbind\t127.0.0.1','CONFIG_GET\tport\t6379']}]}
    fresh={'sourceVersion':'v','results':[{**key,'frames':5,'warnings':[],'listeners':[ep],'redisInfo':{'status':'OBSERVED','sections':{'server':{'redis_mode':'standalone','run_id':'abc'},'replication':{'role':'master','connected_slaves':'0'},'cluster':{'cluster_enabled':'0'}}}}]}
    startup={'sourceVersion':'v','results':[{**key,'detail':{'status':'OBSERVED','startup':{'unit':'redis.service','unitConfigReferences':['/etc/redis.conf']},'processes':[{'pid':1,'name':'redis-server'}]}}]}
    return audit,fresh,startup,{'sourceVersion':'v','results':[]}

class LoopbackTests(unittest.TestCase):
    def test_accepts_runtime_and_bound_configuration(self):
        a,b,c,d=fixture();self.assertEqual(1,len(mod.plan(a,b,c,d)['accepted']))
    def test_rejects_conflicts_or_incomplete_evidence(self):
        for issue in ('nonloopback','replica','include','unbound','sentinel','app','truncated','cluster','changed','unparsed'):
            a,b,c,d=fixture();row=a['results'][0];f=b['results'][0];s=c['results'][0]
            if issue=='nonloopback':f['listeners'][0]['ip']='0.0.0.0'
            if issue=='replica':row['lines'].append('DIRECTIVE\t/etc/redis.conf\treplicaof\t10.0.0.2 6379')
            if issue=='include':row['lines'].append('DIRECTIVE\t/etc/redis.conf\tinclude\t/etc/redis/x.conf')
            if issue=='unbound':row['lines']=[x.replace('systemd_unit_file','conventional_path') for x in row['lines']];s['detail']['startup']['unitConfigReferences']=[]
            if issue=='sentinel':s['detail']['processes'].append({'pid':2,'name':'redis-sentinel'})
            if issue=='app':row['applicationCandidates']['PRODUCT']=['app']
            if issue=='truncated':row['connectionCount']=1
            if issue=='cluster':f['redisInfo']['sections']['cluster']['cluster_enabled']='1'
            if issue=='changed':s['startTicks']='11'
            if issue=='unparsed':row['lines'].append('DIRECTIVE_UNPARSED\t/etc/redis.conf\treplicaof')
            self.assertEqual([],mod.plan(a,b,c,d)['accepted'],issue)
    def test_nonloopback_connections_not_misrepresented(self):
        a,b,c,d=fixture();r=a['results'][0];r['connectionCount']=1;r['connections']=[{'local':{'ip':'127.0.0.1','port':6379},'peer':{'ip':'10.0.0.2','port':1010}}]
        self.assertEqual([],mod.plan(a,b,c,d)['accepted'])
    def test_mismatched_source(self):
        a,b,c,d=fixture();d['sourceVersion']='other'
        with self.assertRaises(ValueError):mod.plan(a,b,c,d)

if __name__=='__main__':unittest.main()

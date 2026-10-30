import copy,unittest
import redis_log_single_catalog as mod

def fixture():
 k={'assetId':'a','pid':7,'ip':'10.0.0.1','startTicks':'123','status':'OBSERVED','observedAt':'2026-01-01T10:00:00Z'};app={'TEST':[],'SIMULATION':[],'PRODUCT':[]}
 audit={'sourceVersion':'v','completedAt':'2026-01-01T10:00:00Z','results':[{**k,'frames':5,'warnings':[],'container':None,'containerMatches':0,'dockerWarnings':[],'applicationCandidates':copy.deepcopy(app),'lines':['META\tnetns\tnet:[1]','META\thostnetns\tnet:[1]','META\tservice\tredis_6379.service']}]}
 lines=['INFO\tredis_version\t3.2.6','INFO\tredis_mode\tstandalone','INFO\trun_id\tx','INFO\trole\tmaster','INFO\tconnected_slaves\t0','INFO\tcluster_enabled\t0','CONFIG\tbind\t10.0.0.1','CONFIG\tport\t6379','CONFIG\tdir\t/var/lib/redis/6379','CONFIG\tdbfilename\tdump.rdb','CONFIG\tlogfile\t/var/log/redis_6379.log','CONFIG\tslaveof\t','CLIENTS\t1\t0']
 deep={'sourceVersion':'v','results':[{**k,'method':'redis-cli','frames':5,'warnings':[],'applicationCandidates':copy.deepcopy(app),'runtimeLines':lines,'listeners':[{'ip':'10.0.0.1','port':6379,'protocol':'tcp'}],'connections':[],'connectionCount':0}]}
 startup={'sourceVersion':'v','results':[{**k,'lines':['PROCESS\t7\tredis-server','REFERENCE\t/etc/init.d/redis_6379\t/etc/redis/6379.conf\tDIRECT','FILE\t/etc/redis/6379.conf\tREADABLE','DIRECTIVE\t/etc/redis/6379.conf\tbind\t10.0.0.1','DIRECTIVE\t/etc/redis/6379.conf\tport\t6379','DIRECTIVE\t/etc/redis/6379.conf\tpidfile\t/var/run/redis.pid','DIRECTIVE\t/etc/redis/6379.conf\tdir\t/var/lib/redis/6379','DIRECTIVE\t/etc/redis/6379.conf\tdbfilename\tdump.rdb','DIRECTIVE\t/etc/redis/6379.conf\tlogfile\t/var/log/redis_6379.log','PIDFILE_MATCH\t/var/run/redis.pid']}]}
 logs={'sourceVersion':'v','results':[{**k,'lines':['FILE\tredis_6379.log\t10000','LINES\t200','EVENTS\t0','FILE_COUNT\t1']}]}
 stats={'sourceVersion':'v','completedAt':'2026-01-01T12:00:00Z','results':[{**k,'lines':['STAT size=10000 mtime=2026-01-01 18:00:00.000000 +0800 birth=-']}]}
 sent={'sourceVersion':'v','results':[{'status':'OBSERVED','info':{'status':'OBSERVED','masters':[]}} for _ in range(9)]}
 return audit,deep,startup,logs,stats,sent

class LogSingleTests(unittest.TestCase):
 def test_correlated_service_runtime_and_retained_log(self):
  p=mod.plan(*fixture());self.assertEqual(6379,p['accepted'][0]['hostPort'])
 def test_does_not_accept_stale_or_incomplete_evidence(self):
  for issue in ('stale','events','truncated','replica','sentinel','pidfile','application','port','source'):
   a,b,c,d,e,f=fixture()
   if issue=='stale':e['results'][0]['lines']=['STAT size=10000 mtime=2025-01-01 18:00:00.000 +0800 birth=-']
   if issue=='events':d['results'][0]['lines'][2]='EVENTS\t1'
   if issue=='truncated':d['results'][0]['lines'].append('LIMIT\tsize')
   if issue=='replica':b['results'][0]['runtimeLines'][-1]='CLIENTS\t2\t1'
   if issue=='sentinel':f['results'][0]['info']['masters']=[{'ip':'other','replicas':[{'ip':'10.0.0.1'}]}]
   if issue=='pidfile':c['results'][0]['lines'].pop()
   if issue=='application':b['results'][0]['applicationCandidates']['TEST']=['app']
   if issue=='port':b['results'][0]['listeners'][0]['ip']='0.0.0.0'
   if issue=='source':
    f['sourceVersion']='other'
    with self.assertRaises(ValueError):mod.plan(a,b,c,d,e,f)
    continue
   self.assertEqual([],mod.plan(a,b,c,d,e,f)['accepted'],issue)

if __name__=='__main__':unittest.main()

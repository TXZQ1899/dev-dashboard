import copy,unittest
import redis_sentinel_single_catalog as mod

def fixture():
 k={'assetId':'a','pid':7,'ip':'10.0.0.1','startTicks':'123','status':'OBSERVED','observedAt':'2026-01-01T00:00:00Z'};app={'TEST':[],'SIMULATION':[],'PRODUCT':[]}
 container={'id':'a'*64,'name':'redis','initPid':7,'startTicks':'123','networkNamespace':'net:[1]'}
 a={'sourceVersion':'v','completedAt':'2026-01-01T00:00:00Z','results':[{**k,'frames':5,'warnings':[],'containerMatches':1,'dockerWarnings':[],'container':container,'applicationCandidates':copy.deepcopy(app),'lines':['META\tnetns\tnet:[1]','META\thostnetns\tnet:[1]']}]}
 r={'status':'OBSERVED','sections':{'server':{'redis_mode':'standalone','run_id':'r'},'replication':{'role':'master','connected_slaves':'0'},'cluster':{'cluster_enabled':'0'}},'config':{'bind':'0.0.0.0 ::','port':'6379','dir':'/data','dbfilename':'dump.rdb'},'clients':{'replicaCount':0}}
 b={'sourceVersion':'v','results':[{**k,'frames':5,'warnings':[],'runtime':r,'listeners':[{'ip':'*','port':6379,'protocol':'tcp'}],'connections':[],'connectionCount':0,'applicationCandidates':copy.deepcopy(app)}]}
 script={'path':'/opt/bitnami/scripts/redis/run.sh','safeLines':[{'hasArgsArray':True,'hasConfPath':True,'redisVariables':['REDIS_BASE_DIR']},{'hasExec':True,'hasRedisServer':True,'hasArgsArray':True}]}
 conf={'path':'/opt/bitnami/redis/etc/redis.conf','directives':[['bind','0.0.0.0 ::'],['port','6379'],['dir','/data'],['dbfilename','dump.rdb']]}
 c={'sourceVersion':'v','results':[{**k,'detail':{'status':'OBSERVED','scripts':[script],'configs':[conf]}}]}
 d={'sourceVersion':'v','results':[{**k,'detail':{'exe':'/opt/bitnami/redis/bin/redis-server','assignments':[{'key':'BITNAMI_ROOT_DIR','value':'/opt/bitnami'},{'key':'REDIS_BASE_DIR','value':'${BITNAMI_ROOT_DIR}/redis'}]}}]}
 sentinel={'ip':'10.0.0.1','assetId':'a','status':'OBSERVED','info':{'status':'OBSERVED','masters':[{'ip':'10.0.0.1','port':'6379','flags':'master','num-slaves':'0','replicas':[]}]}}
 e={'sourceVersion':'v','results':[sentinel]+[{'status':'OBSERVED','info':{'status':'OBSERVED','masters':[]}} for _ in range(8)]}
 return a,b,c,d,e

class SentinelSingleTests(unittest.TestCase):
 def test_current_sentinel_and_runtime_agree(self):
  a,b,c,d,e=fixture();r=mod.plan(a,b,c,d,e);self.assertEqual(1,len(r['accepted']));self.assertEqual(6379,r['accepted'][0]['hostPort'])
 def test_unconfirmed_replica_or_configuration_blocks_singleton(self):
  for issue in ('replica','other_sentinel','coverage','script','mapping','runtime','apps','remote','config'):
   a,b,c,d,e=fixture()
   if issue=='replica':e['results'][0]['info']['masters'][0]['num-slaves']='1'
   if issue=='other_sentinel':e['results'][1]['info']['masters']=[{'ip':'other','replicas':[{'ip':'10.0.0.1','port':'0'}]}]
   if issue=='coverage':e['results'].pop()
   if issue=='script':c['results'][0]['detail']['scripts'][0]['safeLines']=[]
   if issue=='mapping':a['results'][0]['container']['networkNamespace']='net:[2]'
   if issue=='runtime':b['results'][0]['runtime']['clients']['replicaCount']=1
   if issue=='apps':b['results'][0]['applicationCandidates']['PRODUCT']=['app']
   if issue=='remote':b['results'][0].update(connections=[{'local':{'port':6379},'peer':{'ip':'10.0.0.2'}}],connectionCount=1)
   if issue=='config':c['results'][0]['detail']['configs'][0]['directives'].append(['include','/x.conf'])
   self.assertEqual([],mod.plan(a,b,c,d,e)['accepted'],issue)

if __name__=='__main__':unittest.main()

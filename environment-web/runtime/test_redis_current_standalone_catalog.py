import copy,unittest
import redis_current_standalone_catalog as mod

def fixture(docker=False):
 k={'assetId':'a','pid':7,'ip':'10.0.0.1','startTicks':'123','status':'OBSERVED','observedAt':'2026-01-01T10:00:00Z'};app={'TEST':[],'SIMULATION':[],'PRODUCT':[]}
 cont={'id':'a'*64,'name':'redis','initPid':7,'startTicks':'123','networkNamespace':'net:[2]','ips':['172.17.0.2'],'ports':{'6379/tcp':[{'HostIp':'0.0.0.0','HostPort':'6379'}]}}
 row={**k,'frames':5,'warnings':[],'container':cont if docker else None,'containerMatches':1 if docker else 0,'dockerWarnings':[],'applicationCandidates':copy.deepcopy(app),'lines':['META\tnetns\t'+('net:[2]' if docker else 'net:[1]'),'META\thostnetns\tnet:[1]','META\tservice\tredis.service']}
 config={'sourceVersion':'v','completedAt':'2026-01-01T10:01:00Z','results':[row]}
 info={'status':'OBSERVED','sections':{'server':{'redis_mode':'standalone','run_id':'r'},'replication':{'role':'master','connected_slaves':'0'},'cluster':{'cluster_enabled':'0'}},'config':{'bind':'' if docker else '0.0.0.0','port':'6379','slaveof':''},'clients':{'replicaCount':0}}
 deep={'sourceVersion':'v','results':[{**k,'frames':5,'warnings':[],'runtime':info,'listeners':[{'ip':'*','port':6379,'protocol':'tcp'}],'connections':[],'connectionCount':0,'applicationCandidates':copy.deepcopy(app)}]}
 start={'sourceVersion':'v','results':[] if docker else [{**k,'lines':['PROCESS\t7\tredis-server','REFERENCE\t/etc/systemd/redis.service\t/etc/redis.conf\tDIRECT','FILE\t/etc/redis.conf\tREADABLE','DIRECTIVE\t/etc/redis.conf\tbind\t0.0.0.0','DIRECTIVE\t/etc/redis.conf\tport\t6379']}]}
 detail={'status':'OBSERVED','scanComplete':True,'configFiles':[],'argsRedisServerOnly':True,'imageCmdRedisServerOnly':True,'entrypointExecArgv':True,'entrypointMentionsConf':False,'configArgs':[],'modeFlags':[],'hasExtraArguments':False}
 docks={'sourceVersion':'v','results':[{**k,'container':cont,'publishedPort':6379,'publishedApplicationCandidates':copy.deepcopy(app),'detail':detail}] if docker else []}
 sent={'sourceVersion':'v','results':[{'status':'OBSERVED','info':{'status':'OBSERVED','masters':[]}} for _ in range(9)]}
 chk={'sourceVersion':'v','status':'partial','target':{'assetId':'a','serverIp':'10.0.0.1','pid':7,'startTicks':'123'},'coverage':{'lastObservedAt':'2026-01-01T10:00:30Z','samples':255,'actualSeconds':60},'warnings':[],'upstreamCount':0,'downstreamCount':0,'reportPath':'checks/x/report.json','reportSha256':'hash'}
 checks={'sourceVersion':'v','completedAt':'2026-01-01T10:01:00Z','results':[chk]}
 return config,deep,start,docks,sent,checks

class CurrentStandaloneTests(unittest.TestCase):
 def test_host_and_default_container(self):
  for docker in (False,True):
   r=mod.plan(*fixture(docker));self.assertEqual(1,len(r['accepted']))
   self.assertEqual('DOCKER_PUBLISHED' if docker else 'LIVE_LISTENER',r['accepted'][0].get('portBasis','LIVE_LISTENER'))
 def test_rejects_conflicting_or_incomplete_evidence(self):
  for issue in ('replica','app','sentinel','downstream','stale','include','changed','config','mapping','truncated','coverage','source'):
   a,b,c,d,e,f=fixture(issue in ('config','mapping','truncated'))
   if issue=='replica':b['results'][0]['runtime']['clients']['replicaCount']=1
   if issue=='app':b['results'][0]['applicationCandidates']['PRODUCT']=['app']
   if issue=='sentinel':e['results'][0]['info']['masters']=[{'ip':'other','replicas':[{'ip':'10.0.0.1'}]}]
   if issue=='downstream':f['results'][0]['downstreamCount']=1
   if issue=='stale':f['results'][0]['coverage']['lastObservedAt']='2025-01-01T10:00:00Z'
   if issue=='include':c['results'][0]['lines'].append('DIRECTIVE\t/etc/redis.conf\tinclude\t/x.conf')
   if issue=='changed':b['results'][0]['startTicks']='999'
   if issue=='config':d['results'][0]['detail']['configFiles']=[{'path':'/data/redis.conf'}]
   if issue=='mapping':d['results'][0]['container']['ports']={'6379/tcp':[{'HostIp':'0.0.0.0','HostPort':'6380'}]}
   if issue=='truncated':f['results'][0]['warnings']=['连接数量超过 100，已限制本次检查范围']
   if issue=='coverage':e['results'].pop()
   if issue=='source':
    f['sourceVersion']='x'
    with self.assertRaises(ValueError):mod.plan(a,b,c,d,e,f)
    continue
   self.assertEqual([],mod.plan(a,b,c,d,e,f)['accepted'],issue)
 def test_host_process_runtime_config_is_explicitly_unresolved(self):
  a,b,c,d,e,f=fixture();row=a['results'][0];row['lines']=['META\tnetns\tnet:[1]','META\thostnetns\tnet:[1]','META\tservice\t'];b['results'][0]['runtime']['config'].update(bind='',dir='/opt');c['results']=[]
  key={k:row[k] for k in ('assetId','pid','ip','startTicks','status','observedAt')};g={'sourceVersion':'v','results':[{**key,'detail':{'status':'OBSERVED','exePath':'/usr/bin/redis-server','cwd':'/opt','service':{'unit':None},'processes':[{'pid':7,'name':'redis-server'}],'files':[{'path':'/etc/redis.conf','pidfileMatches':False}]}}]}
  result=mod.plan(a,b,c,d,e,f,g);self.assertEqual('UNIDENTIFIED',result['accepted'][0]['configPath']);self.assertEqual('REDIS_HOST_PROCESS',result['accepted'][0]['sourceKind'])
  g['results'][0]['detail']['files'][0]['pidfileMatches']=True
  self.assertEqual([],mod.plan(a,b,c,d,e,f,g)['accepted'])
 def test_proxy_truncation_only_with_complete_runtime_client_evidence(self):
  a,b,c,d,e,f=fixture(True);target=f['results'][0]['target'];target.update(pid=20,startTicks='555',dockerTarget={'pid':7,'startTicks':'123','mappings':[{'containerId':'a'*64}]});f['results'][0]['warnings']=['连接数量超过 100，已限制本次检查范围']
  self.assertEqual(1,len(mod.plan(a,b,c,d,e,f)['accepted']))
  b['results'][0]['connectionCount']=5
  self.assertEqual([],mod.plan(a,b,c,d,e,f)['accepted'])

if __name__=='__main__':unittest.main()

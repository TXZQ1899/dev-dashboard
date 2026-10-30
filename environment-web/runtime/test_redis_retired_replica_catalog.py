import unittest
import redis_retired_replica_catalog as mod
from test_redis_current_standalone_catalog import fixture

def retired_fixture():
 a,b,c,d,e,f=fixture();old='10.0.0.1';new='10.58.9.219'
 for r in (a['results'][0],b['results'][0],c['results'][0]):r['ip']=new
 f['results'][0]['target']['serverIp']=new
 h={'sourceVersion':'v','results':[{'status':'OBSERVED','ip':new,'pid':7,'startTicks':'123','peerEndpoint':{'ip':'10.58.1.229','port':6379,'protocol':'tcp'},'lines':['EVENTS\t157','UPSTREAM_PATTERN\t0','CONFIRMED_IP_EVENTS\t143','OTHER_EVENTS\t14','EVENT_IP\t10.58.1.229:6379\t143']}]}
 conf={'sourceVersion':'v','confirmedBy':'USER','evidenceBasis':'USER_CONFIRMATION','retiredIp':'10.58.1.229','retiredPort':6379,'retirementTime':'UNKNOWN','statement':'用户确认 IP 已下线'}
 return a,b,c,d,e,f,h,conf

class RetiredReplicaTests(unittest.TestCase):
 def test_user_confirmation_and_current_evidence(self):
  r=mod.plan(*retired_fixture());self.assertEqual(1,len(r['accepted']));self.assertEqual('USER_CONFIRMATION',r['accepted'][0]['evidenceExtra']['retirementBasis'])
 def test_rejects_absent_confirmation_or_other_members(self):
  for issue in ('user','ip','other_peer','upstream_history','identity','replica','source'):
   a,b,c,d,e,f,h,conf=retired_fixture()
   if issue=='user':conf['confirmedBy']='INFERRED'
   if issue=='ip':conf['retiredIp']='10.58.1.228'
   if issue=='other_peer':h['results'][0]['lines'].append('EVENT_IP\t10.1.1.2:6379\t1')
   if issue=='upstream_history':h['results'][0]['lines'][1]='UPSTREAM_PATTERN\t1'
   if issue=='identity':h['results'][0]['startTicks']='changed'
   if issue=='replica':b['results'][0]['runtime']['clients']['replicaCount']=1
   if issue=='source':
    conf['sourceVersion']='other'
    with self.assertRaises(ValueError):mod.plan(a,b,c,d,e,f,h,conf)
    continue
   self.assertEqual([],mod.plan(a,b,c,d,e,f,h,conf)['accepted'],issue)

if __name__=='__main__':unittest.main()

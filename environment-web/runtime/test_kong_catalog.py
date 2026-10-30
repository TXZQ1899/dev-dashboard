import copy
import unittest
import kong_catalog as k


def fixture():
    row={'assetId':'a1','ip':'192.0.2.10','pid':11,'status':'OBSERVED','frames':5,'warnings':[],
         'observedAt':'2026-10-30T00:00:00Z','startTicks':'100','connectionCount':0,'connections':[],
         'applicationCandidates':{e:[] for e in ('TEST','SIMULATION','PRODUCT')},
         'configEvidence':['CONFIG_READABLE','role = traditional','database = postgres'],
         'admin':{'node_id':'6e77e6ad-85db-464d-9f79-2bec26e3c5c1','version':'2.4.1','database':'postgres','role':'traditional'},
         'listeners':[{'ip':'*','port':8000,'protocol':'tcp'},{'ip':'*','port':8001,'protocol':'tcp'}]}
    worker={'ip':row['ip'],'master':11,'status':'OBSERVED','workers':[{'pid':12,'status':'OBSERVED','frames':5,'warnings':[],'connections':[],'connectionCount':0}]}
    listener={'assetId':'a1','pid':11,'status':'OBSERVED','warnings':[],'dockerWarnings':[],'containerAncestors':[],
              'hostNetwork':'net:[42]','processNetwork':'net:[42]','hostMountNamespace':'mnt:[42]','processMountNamespace':'mnt:[42]','startTicks':'100','observedAt':'2026-10-30T00:00:03Z',
              'listeners':[{'endpoint':{'ip':'*','port':8001,'protocol':'tcp'},'pids':[11,12]}]}
    return ({'sourceVersion':'v1','completedAt':'2026-10-30T00:00:01Z','results':[row]},
            {'sourceVersion':'v1','completedAt':'2026-10-30T00:00:02Z','results':[worker]},
            {'sourceVersion':'v1','completedAt':'2026-10-30T00:00:03Z','results':[listener]})

class KongPlanTests(unittest.TestCase):
    def test_valid_candidate_preserves_all_listeners(self):
        a,b,c=fixture();x=k.plan(a,b,c)['accepted'][0];self.assertEqual(len(x['endpoints']),2)
    def test_uncertainty_excludes_candidate(self):
        for issue in ('app','config','role','worker','truncated','owner','container','mount','identity','node'):
            a,b,c=fixture();r=a['results'][0];w=b['results'][0];l=c['results'][0]
            if issue=='app':r['applicationCandidates']['TEST']=[{'appId':'1'}]
            if issue=='config':r['configEvidence']=['CONFIG_UNAVAILABLE']
            if issue=='role':r['admin']['role']='data_plane'
            if issue=='worker':w['status']='PARTIAL'
            if issue=='truncated':w['workers'][0]['connectionCount']=1
            if issue=='owner':l['listeners'][0]['pids']=[11,12,13]
            if issue=='container':l['containerAncestors']=[{'id':'x'}]
            if issue=='mount':l['processMountNamespace']='mnt:[43]'
            if issue=='identity':l['startTicks']='101'
            if issue=='node':a['results'].append(copy.deepcopy(r))
            self.assertFalse(k.plan(a,b,c)['accepted'],issue)
    def test_systemd_mount_isolation_is_accepted_when_identity_matches(self):
        a,b,c=fixture();l=c['results'][0]
        l.update(processMountNamespace='mnt:[43]',hostRoot='8:2',processRoot='8:2',hostPidNamespace='pid:[1]',processPidNamespace='pid:[1]',serviceId='kong.service',serviceMainPid='11',serviceControlGroup='/system.slice/kong.service',ancestors=[11])
        self.assertEqual(len(k.plan(a,b,c)['accepted']),1)
        l['serviceMainPid']='12'
        self.assertEqual(k.plan(a,b,c)['accepted'],[])
    def test_version_mismatch_fails(self):
        a,b,c=fixture();b['sourceVersion']='v2'
        with self.assertRaises(ValueError):k.plan(a,b,c)

if __name__=='__main__':unittest.main()

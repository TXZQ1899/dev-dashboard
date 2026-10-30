import copy
import unittest
import nginx_catalog as n

def fixture():
    row={'assetId':'a1','ip':'192.0.2.1','pid':11,'status':'OBSERVED','frames':5,'warnings':[],
         'observedAt':'2026-10-29T00:00:00Z','startTicks':'100','openresty':False,'kongEvidence':'UNKNOWN',
         'applicationCandidates':{e:[] for e in ('TEST','SIMULATION','PRODUCT')},
         'hostNetwork':'net:[42]','networkNamespace':'net:[42]','tcpConnectionsObserved':3,
         'listeners':[{'ip':'*','port':80,'protocol':'tcp'},{'ip':'*','port':443,'protocol':'tcp'}]}
    tree={'assetId':'a1','ip':row['ip'],'pid':11,'status':'OBSERVED','warnings':[],
          'observedAt':'2026-10-29T00:00:03Z','startTicks':'100','configPath':'/etc/nginx/nginx.conf','files':3,'kongHints':0}
    return {'sourceVersion':'v1','completedAt':'2026-10-29T00:00:03Z','results':[row]}, {'sourceVersion':'v1','results':[tree]}

class NginxPlanTest(unittest.TestCase):
    def test_multiple_ports_are_not_reduced_to_one(self):
        a,b=fixture();x=n.plan(a,b)['accepted'][0]
        self.assertIsNone(x['port']);self.assertEqual(len(x['endpoints']),2)
    def test_unresolved_remains_uncreated(self):
        for issue in ('kong','openresty','identity','application','container','warnings','includes','duplicate'):
            a,b=fixture();row=a['results'][0];tree=b['results'][0]
            if issue=='kong':tree['kongHints']=1
            if issue=='openresty':row['openresty']=True
            if issue=='identity':tree['startTicks']='101'
            if issue=='application':row['applicationCandidates']['TEST']=[{'appId':'x'}]
            if issue=='container':row['networkNamespace']='net:[43]'
            if issue=='warnings':row['warnings']=['truncated']
            if issue=='includes':tree['status']='UNKNOWN'
            if issue=='duplicate':a['results'].append(copy.deepcopy(row))
            self.assertEqual(n.plan(a,b)['accepted'],[],issue)
    def test_versions_must_match(self):
        a,b=fixture();b['sourceVersion']='v2'
        with self.assertRaises(ValueError):n.plan(a,b)
if __name__=='__main__': unittest.main()

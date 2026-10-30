import copy
import unittest
import redis_single_cluster_catalog as mod

def fixture():
    app={'TEST':[],'SIMULATION':[],'PRODUCT':[]};key={'assetId':'a','pid':12};node={'id':'a'*40,'address':':6379@16379','flags':['myself','master'],'masterId':'-','link':'connected'}
    row={**key,'ip':'10.0.0.1','status':'OBSERVED','startTicks':'123','clusterHint':True,'applicationCandidates':app}
    live={'snapshotMatches':True,'startTicks':'123','netns':'net:[1]','tcpFrames':5,'warnings':[],'connectionCount':0,'connections':[],'listeners':[{'port':6379,'protocol':'tcp'},{'port':16379,'protocol':'tcp'}]}
    host={**key,'ip':row['ip'],'status':'OBSERVED','observedAt':'2026-01-01T00:00:00Z','live':live,'remote':{'status':'OBSERVED','exe':'redis-server','hostNet':True,'networkNamespace':'net:[1]'},'dockerWarnings':[],'containerMatches':[]}
    file={**key,'ip':row['ip'],'status':'OBSERVED','observedAt':'2026-01-01T00:00:00Z','files':{'files':[{'path':'/data/nodes.conf','nodes':[node],'openFileDescriptors':['8']}]}}
    return ({'sourceVersion':'v','results':[row]}, {'sourceVersion':'v','results':[host]}, {'sourceVersion':'v','completedAt':'2026-01-01T00:01:00Z','results':[file]})

class SingleClusterTests(unittest.TestCase):
    def test_accepts_held_single_node(self):
        a,b,c=fixture();self.assertEqual(1,len(mod.plan(a,b,c)['accepted']))
    def test_rejects_ambiguous_members_and_stale_file(self):
        for issue in ('members','fd','bus','apps','process','truncated'):
            a,b,c=fixture();f=c['results'][0]['files']['files'][0];live=b['results'][0]['live']
            if issue=='members':f['nodes'].append(copy.deepcopy(f['nodes'][0]))
            if issue=='fd':f['openFileDescriptors']=[]
            if issue=='bus':live['connections']=[{'local':{'port':16379},'peer':{'port':123}}];live['connectionCount']=1
            if issue=='apps':a['results'][0]['applicationCandidates']['PRODUCT']=['app']
            if issue=='process':live['startTicks']='changed'
            if issue=='truncated':live['connectionCount']=2
            self.assertEqual([],mod.plan(a,b,c)['accepted'],issue)
    def test_mismatched_snapshot_fails(self):
        a,b,c=fixture();b['sourceVersion']='x'
        with self.assertRaises(ValueError):mod.plan(a,b,c)

if __name__=='__main__':unittest.main()

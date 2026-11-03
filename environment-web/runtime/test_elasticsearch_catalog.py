import copy
from datetime import datetime, timedelta, timezone
import unittest
import elasticsearch_catalog as catalog


def fixture():
    uuid='RXpPCXzURNaDjQ6r9ixU-w'
    nodes={f'node{i}':{'name':f'n{i}','transport_address':f'10.0.0.{i}:9300','version':'7.4','roles':['data'],'http':{'publish_address':f'10.0.0.{i}:9200'}} for i in (1,2,3)}
    rows=[]
    for i in (1,2,3):
        rows.append({'assetId':f'a{i}','ip':f'10.0.0.{i}','pid':i,'status':'OBSERVED','startTicks':'12','observedAt':'2026-10-31T00:00:00Z','hostNetwork':True,'httpPort':9200,'warnings':[],'applicationCandidates':{'TEST':[],'SIMULATION':[],'PRODUCT':[]},'listeners':[{'ip':f'10.0.0.{i}','port':p,'protocol':'tcp'} for p in (9200,9300)],'root':{'cluster_uuid':uuid,'cluster_name':'es','name':f'n{i}'},'local':{'cluster_name':'es','nodes':{f'node{i}':nodes[f'node{i}']}},'view':{'cluster_name':'es','_nodes':{'failed':0,'successful':3,'total':3},'nodes':copy.deepcopy(nodes)}})
    return {'sourceVersion':'v1','results':rows}


class ElasticsearchPlanTest(unittest.TestCase):
    def test_complete_views_create_group(self):
        self.assertEqual([len(g['members']) for g in catalog.plan(fixture())['groups']], [3])

    def test_conflict_missing_or_unavailable_never_groups(self):
        for mutate in (lambda r:r['results'].pop(), lambda r:r['results'][0]['view']['_nodes'].update(failed=1), lambda r:r['results'][0]['applicationCandidates']['TEST'].append({'appId':'x'}), lambda r:r['results'][0]['warnings'].append('partial'), lambda r:r['results'][0]['root'].update(cluster_uuid='F97iVn08RIK-bKKO2CI1cA'), lambda r:r['results'][0]['listeners'].clear()):
            r=fixture();mutate(r);self.assertEqual(catalog.plan(r)['groups'],[])

    def test_one_node_view_does_not_prove_standalone(self):
        r=fixture();r['results']=r['results'][:1];row=r['results'][0];row['view']['nodes']=row['local']['nodes'];row['view']['_nodes']={'total':1,'successful':1,'failed':0}
        self.assertFalse(catalog.plan(r)['groups'])

    def test_explicit_single_node_mode_with_complete_evidence(self):
        r=fixture();r['results']=r['results'][:1];row=r['results'][0];row['view']['nodes']=row['local']['nodes'];row['view']['_nodes']={'total':1,'successful':1,'failed':0}
        row['settings']={'nodes':{'node1':{'settings':{'discovery':{'type':'single-node'}}}}}
        self.assertEqual(len(catalog.plan(r)['groups']),1)
        row['settings']['nodes']['node1']['settings']['discovery']['type']='multi-node'
        self.assertFalse(catalog.plan(r)['groups'])

    def test_same_name_different_uuid_does_not_merge(self):
        r=fixture();r['results'][1]['root']['cluster_uuid']='F97iVn08RIK-bKKO2CI1cA'
        self.assertFalse(catalog.plan(r)['groups'])

    def test_user_tcp_policy_requires_complete_sampling_and_no_transport_connection(self):
        r=fixture();r['results']=r['results'][:1];row=r['results'][0];row['view']['nodes']=row['local']['nodes'];row['view']['_nodes']={'total':1,'successful':1,'failed':0}
        r['singleNodePolicy']='USER_CURRENT_TCP_NO_OTHER_ES_V1'
        r['knownNodeIps']=['10.0.0.1','10.0.0.2']
        stamp=datetime.now(timezone.utc).isoformat();row['observedAt']=stamp
        listeners=[row['listeners'] for _ in range(6)]
        socket={'sourceVersion':'v1','startedAt':stamp,'completedAt':stamp,'status':'OBSERVED','pid':1,'assetId':'a1','startTicks':'12','intervalSeconds':5,'frames':[[] for _ in range(6)],'listeners':listeners}
        traffic={'sourceVersion':'v1','startedAt':stamp,'finishedAt':stamp,'target':{'pid':1,'assetId':'a1','startTicks':'12'},'status':'complete','coverage':{'actualSeconds':29,'samples':10},'upstream':[],'downstream':[]}
        row['trafficEvidence']={'socketCheck':socket,'applicationCheck':traffic}
        self.assertEqual(len(catalog.plan(r)['groups']),1)
        row['trafficEvidence']['socketCheck']['frames'][0].append({'local':{'ip':'10.0.0.1','port':9300,'protocol':'tcp'},'peer':{'ip':'10.0.0.2','port':40000,'protocol':'tcp'}})
        self.assertFalse(catalog.plan(r)['groups'])
        row['trafficEvidence']['socketCheck']['frames'][0]=[{'local':{'ip':'10.0.0.1','port':9200,'protocol':'tcp'},'peer':{'ip':'10.0.0.2','port':40000,'protocol':'tcp'}}]
        self.assertFalse(catalog.plan(r)['groups'])
        row['trafficEvidence']['socketCheck']['frames'][0]=[]; row['trafficEvidence']['socketCheck']['frames'].pop()
        self.assertFalse(catalog.plan(r)['groups'])

    def test_non_http_peer_requires_overlapping_attribution(self):
        r=fixture();r['results']=r['results'][:1];row=r['results'][0];row['view']['nodes']=row['local']['nodes'];row['view']['_nodes']={'total':1,'successful':1,'failed':0}
        r.update(singleNodePolicy='USER_CURRENT_TCP_NO_OTHER_ES_V1', knownNodeIps=['10.0.0.1'])
        now=datetime.now(timezone.utc); stamp=lambda delta:(now+timedelta(seconds=delta)).isoformat();row['observedAt']=stamp(-40)
        local={'ip':'10.0.0.1','port':40000,'protocol':'tcp'};peer={'ip':'10.0.0.8','port':80,'protocol':'tcp'}
        socket={'sourceVersion':'v1','startedAt':stamp(-30),'completedAt':stamp(-1),'status':'OBSERVED','pid':1,'assetId':'a1','startTicks':'12','intervalSeconds':5,'frames':[[{'local':local,'peer':peer}] for _ in range(6)],'listeners':[row['listeners'] for _ in range(6)]}
        flow={'local':local,'peer':peer,'socketId':'stable','resolution':'EXACT','firstSeenAt':stamp(-35),'lastSeenAt':stamp(0),'remoteProcesses':[{'confidence':'EXACT','service':{'name':'Nginx'},'observationStart':stamp(-34),'observationEnd':stamp(-33)}]}
        traffic={'sourceVersion':'v1','startedAt':stamp(-40),'finishedAt':stamp(0),'target':{'pid':1,'assetId':'a1','startTicks':'12'},'status':'partial','coverage':{'actualSeconds':39,'samples':10},'upstream':[],'downstream':[flow]}
        row['trafficEvidence']={'socketCheck':socket,'applicationCheck':traffic}
        self.assertEqual(len(catalog.plan(r)['groups']),1)
        flow['lastSeenAt']=stamp(-31)
        self.assertFalse(catalog.plan(r)['groups'])

if __name__=='__main__':unittest.main()

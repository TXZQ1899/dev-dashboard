import copy
import unittest
from datetime import datetime,timezone
try: import kibana_catalog as k
except ModuleNotFoundError: from runtime import kibana_catalog as k


class KibanaPlanTest(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,11,3,9,tzinfo=timezone.utc);stamp=self.now.isoformat();self.version=k.VERSION
        self.snapshot={'assets':[{'id':'asset','ip':'10.0.0.1','inspection':{'processes':[{'pid':123,'name':'node','command':'/usr/share/kibana/bin/node'}]}}]}
        self.devops={'complete':True,'scope':'all','apps':[]}
        self.binding={'unitId':'unit','unitName':'Elasticsearch_cluster_1','environmentId':'env','ip':'10.0.0.1','assetId':'asset','pid':456,'nodeId':'node','startTicks':'20','clusterUuid':'uuid'}
        self.es={'sourceVersion':self.version,'results':[{'ip':'10.0.0.1','assetId':'asset','pid':456,'unitName':'Elasticsearch_cluster_1','catalogNodeId':'node','catalogStartTicks':'20','observedAt':stamp,'status':'OBSERVED','exitCode':0,'output':'BEFORE\n456 (java) 20\nNETNS net:[1]\nHOST_NETNS net:[1]\nLISTEN\n*:9200\nAFTER\n456 (java) 20'}]}
        self.live={'sourceVersion':self.version,'results':[{'ip':'10.0.0.1','assetId':'asset','pid':123,'snapshotStartedAt':'2026-10-01T00:00:00+00:00','snapshotProcessStatus':'complete','observedAt':stamp,'status':'OBSERVED','details':{'before':{'comm':'node','startTicks':'10'},'after':{'comm':'node','startTicks':'10'},'argvReadable':True,'envReadable':True,'socketStatus':'OBSERVED','netns':'net:[1]','hostNetns':'net:[1]','listeners':[{'local':{'host':'*','port':5601}}],'connections':[{'peer':{'host':'10.0.0.1','port':9200}}],'configuredEndpoints':[{'host':'10.0.0.1','port':9200,'scheme':'http'}],'overrideKeysPresent':False,'configStatus':'READ','configMtime':'2026-09-01T00:00:00+00:00','configBasis':'EXPLICIT_ARG','configPath':'/etc/kibana/kibana.yml'}}]}
        self.workers={'sourceVersion':self.version,'results':[]}
    def plan(self):return k.plan(self.snapshot,self.devops,self.live,self.workers,self.es,[self.binding],self.version,self.now)
    def test_attaches_only_to_verified_node(self):
        result=self.plan();self.assertEqual(len(result['items']),1);self.assertEqual(result['items'][0]['unitId'],'unit');self.assertEqual(result['unresolved'],[])
    def test_missing_or_changed_es_and_connections_remain_unresolved(self):
        self.es['results'][0]['output']=self.es['results'][0]['output'].replace('AFTER\n456 (java) 20','AFTER\n456 (java) 21');self.assertEqual(len(self.plan()['items']),0)
        self.es['results'][0]['output']=self.es['results'][0]['output'].replace('AFTER\n456 (java) 21','AFTER\n456 (java) 20');self.live['results'][0]['details']['connections']=[];self.assertEqual(len(self.plan()['items']),0)
    def test_devops_candidate_and_multiple_es_units_block(self):
        self.devops['apps']=[{'id':'1','envs':{'TEST':[{'ip':'10.0.0.1','port':'5601'}]}}];self.assertEqual(len(self.plan()['items']),0)
        self.devops['apps']=[];other=copy.deepcopy(self.binding);other.update(unitId='another',nodeId='other',pid=457);er=copy.deepcopy(self.es['results'][0]);er.update(pid=457,unitName='Elasticsearch_cluster_1',catalogNodeId='other',output=er['output'].replace('456','457'));self.es['results'].append(er)
        r=k.plan(self.snapshot,self.devops,self.live,self.workers,self.es,[self.binding,other],self.version,self.now);self.assertEqual(len(r['items']),0)
    def test_child_identity_and_config_after_start_block(self):
        details=self.live['results'][0]['details'];details['children']=[{'pid':124,'startTicks':'30'}];details['childrenAfter']=[{'pid':124,'startTicks':'31','ppid':123}];self.assertEqual(len(self.plan()['items']),0)
        details['children']=[];details['configMtime']='2026-11-01T00:00:00+00:00';self.assertEqual(len(self.plan()['items']),0)
    def test_loopback_candidates_must_agree_and_remain_on_same_asset(self):
        details=self.live['results'][0]['details'];details.update(configBasis='AMBIGUOUS_DEFAULTS',configStatus='UNKNOWN',configPath=None,configuredEndpoints=None,configCandidates=[{'status':'READ','endpoints':[{'host':'127.0.0.1','port':9200,'scheme':'http'}]},{'status':'READ','endpoints':[{'host':'127.0.0.1','port':9200,'scheme':'http'}]}]);details['connections']=[{'peer':{'host':'127.0.0.1','port':9200}}]
        self.assertEqual(len(self.plan()['items']),1)
        details['configCandidates'][1]['endpoints'][0]['host']='10.0.0.2';self.assertEqual(len(self.plan()['items']),0)
        details['configCandidates'][1]['endpoints'][0]['host']='127.0.0.1';self.binding['assetId']='other-asset';self.assertEqual(len(self.plan()['items']),0)

if __name__=='__main__':unittest.main()

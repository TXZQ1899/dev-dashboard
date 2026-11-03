import copy
from datetime import datetime, timezone
import os
import unittest
import deployment_catalog as db
import elasticsearch_catalog as es
from test_elasticsearch_catalog import fixture

@unittest.skipUnless(os.environ.get('ENVSCOPE_CATALOG_MYSQL_TEST')=='1','requires isolated MySQL')
class ElasticsearchMysqlTests(unittest.TestCase):
    def test_sampled_single_node_import(self):
        store=db.Catalog.from_environment();store.source_key='es-tcp-test-'+db.identifier()
        report=fixture();report['completedAt']='2026-10-31T00:01:00Z';report['results']=report['results'][:1];report['singleNodePolicy']='USER_CURRENT_TCP_NO_OTHER_ES_V1';report['knownNodeIps']=['10.0.0.1']
        row=report['results'][0];stamp=datetime.now(timezone.utc).isoformat();row['observedAt']=stamp
        row['view']['nodes']=row['local']['nodes'];row['view']['_nodes']={'total':1,'successful':1,'failed':0}
        socket={'sourceVersion':'v1','startedAt':stamp,'completedAt':stamp,'status':'OBSERVED','pid':1,'assetId':'a1','startTicks':'12','intervalSeconds':5,'frames':[[] for _ in range(6)],'listeners':[row['listeners'] for _ in range(6)]}
        traffic={'sourceVersion':'v1','startedAt':stamp,'finishedAt':stamp,'target':{'pid':1,'assetId':'a1','startTicks':'12'},'status':'complete','coverage':{'actualSeconds':29,'samples':10},'upstream':[],'downstream':[]}
        row['trafficEvidence']={'socketCheck':socket,'applicationCheck':traffic}
        result=es.import_report(store,report,'v1',report_name='current-tcp.json')
        unit=store.read(query=result['units'][0]['name'])['units'][0]
        self.assertEqual(len(store.read(unit_id=unit['id'])['instances']),1)

    def test_single_node_import(self):
        store=db.Catalog.from_environment();store.source_key='es-single-test-'+db.identifier()
        report=fixture();report['completedAt']='2026-10-31T00:01:00Z';report['results']=report['results'][:1]
        row=report['results'][0];row['view']['nodes']=row['local']['nodes'];row['view']['_nodes']={'total':1,'successful':1,'failed':0}
        row['settings']={'nodes':{'node1':{'settings':{'discovery':{'type':'single-node'}}}}}
        result=es.import_report(store,report,'v1',report_name='auth-extra.json')
        unit=store.read(query=result['units'][0]['name'])['units'][0]
        self.assertEqual(len(store.read(unit_id=unit['id'])['instances']),1)

    def test_atomic_import_idempotence_and_membership_conflict(self):
        store=db.Catalog.from_environment();store.source_key='es-test-'+db.identifier()
        report=fixture();report['completedAt']='2026-10-31T00:01:00Z'
        with self.assertRaises(db.CatalogError): es.import_report(store,report,'v1',can_apply=lambda:False)
        self.assertEqual(store.read()['summary']['units'],0)
        result=es.import_report(store,report,'v1');unit=store.read(query=result['units'][0]['name'])['units'][0]
        self.assertEqual(unit['environments'][0]['environment'],'UNIDENTIFIED')
        self.assertEqual(len(store.read(unit_id=unit['id'])['instances']),3)
        self.assertTrue(es.import_report(store,report,'v1')['unchanged'])
        newer=copy.deepcopy(report);newer['completedAt']='2026-10-31T00:02:00Z'
        self.assertTrue(es.import_report(store,newer,'v1')['units'][0]['existing'])
        changed=copy.deepcopy(newer);changed['completedAt']='2026-10-31T00:03:00Z'
        for row in changed['results']:
            row['view']['nodes']['newnode']=row['view']['nodes'].pop('node3')
        changed['results'][2]['local']['nodes']['newnode']=changed['results'][2]['local']['nodes'].pop('node3')
        with self.assertRaises(db.CatalogError): es.import_report(store,changed,'v1')
        self.assertEqual(store.read()['summary']['units'],1)

import copy
import os
import unittest
import deployment_catalog as db
import redis_catalog
from test_redis_catalog import fixture

@unittest.skipUnless(os.environ.get('ENVSCOPE_CATALOG_MYSQL_TEST')=='1','requires isolated MySQL')
class RedisMysqlTests(unittest.TestCase):
    def test_atomic_import_idempotence_and_member_conflict(self):
        store=db.Catalog.from_environment();store.source_key='redis-test-'+db.identifier()
        report,checks=fixture();report['completedAt']='2026-01-01T00:01:00Z'
        with self.assertRaises(db.CatalogError):redis_catalog.import_report(store,report,checks,'v',can_apply=lambda:False)
        self.assertEqual(0,store.read()['summary']['units'])
        result=redis_catalog.import_report(store,report,checks,'v')
        unit=store.read(query=result['units'][0]['name'])['units'][0]
        self.assertEqual('UNIDENTIFIED',unit['environments'][0]['environment'])
        self.assertEqual(2,len(store.read(unit_id=unit['id'],environment='UNIDENTIFIED')['instances']))
        self.assertTrue(redis_catalog.import_report(store,report,checks,'v')['unchanged'])
        newer=copy.deepcopy(report);newer['completedAt']='2026-01-01T00:02:00Z'
        self.assertTrue(redis_catalog.import_report(store,newer,checks,'v')['units'][0]['existing'])
        # Reuse one bound node in a different group: reject the whole transaction.
        newer['completedAt']='2026-01-01T00:03:00Z'
        newer['results'][1]['pid']=3;newer['results'][1]['startTicks']='3';newer['results'][1]['networkNamespace']='3'
        node=checks['results'][0]['nodes'][1];node['pid']=3;node['startTicks']='3';node['container'].update(id='3',name='other',initPid=3,startTicks='3',networkNamespace='3')
        with self.assertRaises(db.CatalogError):redis_catalog.import_report(store,newer,checks,'v')
        self.assertEqual(1,store.read()['summary']['units'])

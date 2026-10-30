import os,unittest
import deployment_catalog as db
import redis_loopback_catalog as base
import redis_loopback_later_catalog as mod
from test_redis_loopback_later_catalog import fixture

@unittest.skipUnless(os.environ.get('ENVSCOPE_CATALOG_MYSQL_TEST')=='1','requires isolated MySQL')
class LaterLoopbackMysqlTests(unittest.TestCase):
    def test_dynamic_port_atomic_import_and_idempotence(self):
        store=db.Catalog.from_environment();store.source_key='redis-later-'+db.identifier();reports=fixture();plan=mod.plan(*reports)
        with self.assertRaises(db.CatalogError):base.import_report(store,*reports[:4],'v',prepared=plan,extra_reports=reports[4:],mapper_version='later-test',can_apply=lambda:False)
        self.assertEqual(0,store.read()['summary']['units'])
        result=base.import_report(store,*reports[:4],'v',prepared=plan,extra_reports=reports[4:],mapper_version='later-test')
        u=store.read(query=result['units'][0]['name'])['units'][0];instance=store.read(unit_id=u['id'],environment='UNIDENTIFIED')['instances'][0]
        self.assertIsNone(instance['port']);self.assertEqual(7201,instance['attributes']['observedEndpoints'][0]['port'])
        self.assertTrue(base.import_report(store,*reports[:4],'v',prepared=plan,extra_reports=reports[4:],mapper_version='later-test')['unchanged'])

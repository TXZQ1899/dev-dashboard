import os,unittest
import deployment_catalog as db
import redis_loopback_catalog as mod
from test_redis_loopback_catalog import fixture

@unittest.skipUnless(os.environ.get('ENVSCOPE_CATALOG_MYSQL_TEST')=='1','requires isolated MySQL')
class LoopbackMysqlTests(unittest.TestCase):
    def test_atomic_idempotent_and_endpoint_is_loopback(self):
        s=db.Catalog.from_environment();s.source_key='redis-loopback-'+db.identifier();a,b,c,d=fixture()
        with self.assertRaises(db.CatalogError):mod.import_report(s,a,b,c,d,'v',can_apply=lambda:False)
        self.assertEqual(0,s.read()['summary']['units'])
        r=mod.import_report(s,a,b,c,d,'v');unit=s.read(query=r['units'][0]['name'])['units'][0]
        detail=s.read(unit_id=unit['id'],environment='UNIDENTIFIED')['instances'][0]
        self.assertEqual('10.0.0.1',detail['ip']);self.assertEqual('127.0.0.1',detail['attributes']['observedEndpoints'][0]['ip'])
        self.assertTrue(mod.import_report(s,a,b,c,d,'v')['unchanged'])

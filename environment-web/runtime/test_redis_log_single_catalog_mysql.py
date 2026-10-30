import os,unittest
import deployment_catalog as db
import redis_loopback_catalog as base
import redis_log_single_catalog as mod
from test_redis_log_single_catalog import fixture

@unittest.skipUnless(os.environ.get('ENVSCOPE_CATALOG_MYSQL_TEST')=='1','requires isolated MySQL')
class LogSingleMysqlTests(unittest.TestCase):
 def test_direct_host_endpoint_and_atomicity(self):
  store=db.Catalog.from_environment();store.source_key='redis-log-'+db.identifier();reports=fixture();p=mod.plan(*reports)
  with self.assertRaises(db.CatalogError):base.import_report(store,*reports[:4],'v',prepared=p,extra_reports=reports[4:],mapper_version='log-test',can_apply=lambda:False)
  self.assertEqual(0,store.read()['summary']['units'])
  r=base.import_report(store,*reports[:4],'v',prepared=p,extra_reports=reports[4:],mapper_version='log-test');u=store.read(query=r['units'][0]['name'])['units'][0]
  node=store.read(unit_id=u['id'],environment='UNIDENTIFIED')['instances'][0];self.assertEqual('6379',node['port']);self.assertEqual('10.0.0.1',node['attributes']['observedEndpoints'][0]['ip'])
  self.assertTrue(base.import_report(store,*reports[:4],'v',prepared=p,extra_reports=reports[4:],mapper_version='log-test')['unchanged'])

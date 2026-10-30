import os,unittest
import deployment_catalog as db
import redis_loopback_catalog as base
import redis_current_standalone_catalog as mod
from test_redis_current_standalone_catalog import fixture

@unittest.skipUnless(os.environ.get('ENVSCOPE_CATALOG_MYSQL_TEST')=='1','requires isolated MySQL')
class CurrentStandaloneMysqlTests(unittest.TestCase):
 def test_container_endpoint_atomicity_and_idempotence(self):
  store=db.Catalog.from_environment();store.source_key='redis-current-'+db.identifier();reports=fixture(True);p=mod.plan(*reports)
  with self.assertRaises(db.CatalogError):base.import_report(store,*reports[:4],'v',prepared=p,extra_reports=reports[4:],mapper_version='current-test',can_apply=lambda:False)
  self.assertEqual(0,store.read()['summary']['units'])
  r=base.import_report(store,*reports[:4],'v',prepared=p,extra_reports=reports[4:],mapper_version='current-test');u=store.read(query=r['units'][0]['name'])['units'][0]
  node=store.read(unit_id=u['id'],environment='UNIDENTIFIED')['instances'][0];self.assertEqual('6379',node['port']);self.assertEqual('DOCKER_PUBLISHED',node['attributes']['portBasis']);self.assertEqual('172.17.0.2',node['attributes']['observedEndpoints'][0]['ip'])
  self.assertTrue(base.import_report(store,*reports[:4],'v',prepared=p,extra_reports=reports[4:],mapper_version='current-test')['unchanged'])

"""Run only against a disposable, isolated MySQL database."""
import copy
import os
import tempfile
import unittest
from unittest.mock import patch
import deployment_catalog as db
import canal_catalog as canal
from test_canal_catalog import fixture

@unittest.skipUnless(os.environ.get('ENVSCOPE_CATALOG_MYSQL_TEST')=='1','requires isolated MySQL')
class CanalMysqlTests(unittest.TestCase):
    def test_transaction_idempotence_and_identity_conflict(self):
        store=db.Catalog.from_environment();store.source_key='canal-test-'+db.identifier();report=fixture()
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,{'ENVSCOPE_DATA':folder}):
            with self.assertRaises(db.CatalogError):canal.import_report(store,report,'v',can_apply=lambda:False)
            self.assertEqual(store.read()['summary']['units'],0)
            out=canal.import_report(store,report,'v');unit=store.read(query=out['units'][0]['name'])['units'][0]
            self.assertEqual(unit['environments'][0]['environment'],'UNIDENTIFIED')
            self.assertEqual(len(store.read(unit_id=unit['id'])['instances']),1)
            self.assertTrue(canal.import_report(store,report,'v')['unchanged'])
            with store.connection() as conn:
                store.execute(conn,"UPDATE deployment_unit_source SET native_key_hash=%s WHERE source_instance_key=%s",('0'*64,store.source_key+':canal'))
            changed=copy.deepcopy(report);changed['extraObservation']='new'
            with self.assertRaises(db.CatalogError):canal.import_report(store,changed,'v')
            self.assertEqual(store.read()['summary']['units'],1)

if __name__=='__main__':unittest.main()

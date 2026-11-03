"""Run only against a disposable, isolated MySQL database."""
import copy
import os
import tempfile
import unittest
from unittest.mock import patch
import deployment_catalog as db
import rabbitmq_catalog as rabbit
from test_rabbitmq_catalog import fixture

@unittest.skipUnless(os.environ.get('ENVSCOPE_CATALOG_MYSQL_TEST') == '1', 'requires isolated MySQL')
class RabbitMQMysqlTests(unittest.TestCase):
    def test_transaction_idempotence_and_identity_conflict(self):
        store=db.Catalog.from_environment();store.source_key='rabbit-test-'+db.identifier();report=fixture()
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'ENVSCOPE_DATA':directory}):
            with self.assertRaises(db.CatalogError): rabbit.import_report(store,report,'v',can_apply=lambda:False)
            self.assertEqual(store.read()['summary']['units'],0)
            result=rabbit.import_report(store,report,'v');name=result['units'][0]['name']
            unit=store.read(query=name)['units'][0]
            self.assertEqual(unit['environments'][0]['environment'],'UNIDENTIFIED')
            self.assertEqual(len(store.read(unit_id=unit['id'])['instances']),1)
            self.assertTrue(rabbit.import_report(store,report,'v')['unchanged'])
            changed=copy.deepcopy(report);m=changed['results'][0]['member']
            m.update(node='rabbit@other',all='[rabbit@other]',running='[rabbit@other]')
            with self.assertRaises(db.CatalogError): rabbit.import_report(store,changed,'v')
            self.assertEqual(store.read()['summary']['units'],1)

if __name__=='__main__':unittest.main()

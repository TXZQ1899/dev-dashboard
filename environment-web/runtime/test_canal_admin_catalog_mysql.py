"""Run only against a disposable, isolated MySQL database."""
import os
import tempfile
import unittest
from unittest.mock import patch
import deployment_catalog as db
import canal_admin_catalog as admin
import canal_admin_zk_catalog as zk
import canal_pending_catalog as pending
from test_canal_admin_catalog import fixture
from test_canal_admin_zk_catalog import fixture as zk_fixture
from test_canal_pending_catalog import fixture as pending_fixture

@unittest.skipUnless(os.environ.get('ENVSCOPE_CATALOG_MYSQL_TEST')=='1','requires isolated MySQL')
class CanalAdminMysqlTests(unittest.TestCase):
    def test_transaction_and_idempotence(self):
        store=db.Catalog.from_environment();store.source_key='canal-admin-test-'+db.identifier();report=fixture()
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'ENVSCOPE_DATA':folder}):
            with self.assertRaises(db.CatalogError):admin.import_report(store,report,'v',can_apply=lambda:False)
            self.assertEqual(store.read()['summary']['units'],0)
            out=admin.import_report(store,report,'v');unit=store.read(query=out['units'][0]['name'])['units'][0]
            self.assertEqual(unit['environments'][0]['environment'],'UNIDENTIFIED')
            self.assertEqual(len(store.read(unit_id=unit['id'])['instances']),1)
            self.assertTrue(admin.import_report(store,report,'v')['unchanged'])

    def test_zk_group_transaction_and_conflicting_prior_identity(self):
        store=db.Catalog.from_environment();store.source_key='canal-zk-test-'+db.identifier();report=zk_fixture()
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'ENVSCOPE_DATA':folder}):
            with self.assertRaises(db.CatalogError):admin.import_report(store,report,'v',can_apply=lambda:False,planner=zk.plan)
            self.assertEqual(store.read()['summary']['units'],0)
            result=admin.import_report(store,report,'v',planner=zk.plan)
            self.assertEqual(len(result['units']),1)
            self.assertTrue(admin.import_report(store,report,'v',planner=zk.plan)['unchanged'])
            with store.connection() as conn:
                store.execute(conn,"UPDATE deployment_unit_source SET evidence_json=JSON_SET(evidence_json,'$.adminClusterId',99) WHERE source_instance_key=%s",(store.source_key+':canal',))
            changed=dict(report,extraObservation='new')
            with self.assertRaises(db.CatalogError):admin.import_report(store,changed,'v',planner=zk.plan)
            self.assertEqual(store.read()['summary']['units'],1)

    def test_user_pending_state_is_visible_and_grouping_unknown(self):
        store=db.Catalog.from_environment();store.source_key='canal-pending-test-'+db.identifier();report=pending_fixture()
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'ENVSCOPE_DATA':folder}):
            result=admin.import_report(store,report,'v',planner=pending.plan)
            unit=store.read(query=result['units'][0]['name'])['units'][0]
            self.assertEqual(unit['groupingState'],'PENDING')
            instance=store.read(unit_id=unit['id'])['instances'][0]
            self.assertEqual(instance['attributes']['groupingConfidence'],'UNKNOWN')
            self.assertIsNone(instance['attributes']['destinations'])
            self.assertEqual(instance['attributes']['destinationsState'],'UNKNOWN')
            self.assertFalse(pending.correct_legacy_unknown(store,report))
            self.assertTrue(admin.import_report(store,report,'v',planner=pending.plan)['unchanged'])

    def test_legacy_empty_destination_is_corrected_with_new_observation(self):
        store=db.Catalog.from_environment();store.source_key='canal-pending-correction-'+db.identifier();report=pending_fixture()
        def legacy(value):
            result=pending.plan(value);result['groups'][0]['destinations']=[];return result
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'ENVSCOPE_DATA':folder}):
            result=admin.import_report(store,report,'v',planner=legacy)
            unit=store.read(query=result['units'][0]['name'])['units'][0]
            instance=store.read(unit_id=unit['id'])['instances'][0]
            self.assertEqual(instance['attributes']['destinations'],[])
            self.assertTrue(pending.correct_legacy_unknown(store,report))
            instance=store.read(unit_id=unit['id'])['instances'][0]
            self.assertIsNone(instance['attributes']['destinations'])
            self.assertEqual(instance['attributes']['destinationsState'],'UNKNOWN')
            self.assertFalse(pending.correct_legacy_unknown(store,report))
            with store.connection() as conn:
                rows=store.execute(conn,"SELECT COUNT(*) AS n FROM catalog_observation WHERE instance_id=%s",(instance['id'],))
                self.assertEqual(rows[0]['n'],2)

if __name__=='__main__':unittest.main()

import copy
from datetime import datetime,timezone
import unittest
import canal_pending_catalog as pending
from test_canal_admin_catalog import fixture as admin_fixture


def fixture():
    r=admin_fixture();r['ruleVersion']=pending.RULE;r['adminMembers']['results'][0].update(status='UNKNOWN',error='URLError')
    r['pendingConfirmation']={'sourceVersion':'v','kind':'USER_PER_NODE_PENDING','assetId':'a','ip':'10.0.0.1','confirmedAt':datetime.now(timezone.utc).isoformat()}
    return r

class CanalPendingTests(unittest.TestCase):
    def test_explicit_asset_is_pending_not_standalone(self):
        p=pending.plan(fixture());self.assertEqual(len(p['groups']),1);self.assertEqual(p['groups'][0]['groupingBasis'],'USER_PER_NODE_PENDING');self.assertIsNone(p['groups'][0]['destinations'])
    def test_scope_required_and_conflicts_stay_unresolved(self):
        r=fixture();r['pendingConfirmation']['assetId']='b'
        with self.assertRaises(ValueError):pending.plan(r)
        r=fixture();r['pendingConfirmation']['kind']='other'
        with self.assertRaises(ValueError):pending.plan(r)
        r=fixture();r['results'].append(copy.deepcopy(r['results'][0]))
        with self.assertRaises(ValueError):pending.plan(r)
        for mutate in (lambda x:x['adminMembers']['results'][0].update(status='COMPLETE'),lambda x:x['results'][0]['hostDeployments'].append({'appId':'x'}),lambda x:x['results'][0]['initial'].update(after='123 (java) 999'),lambda x:x['pendingConfirmation'].update(confirmedAt='2020-01-01T00:00:00Z')):
            r=fixture();mutate(r);self.assertFalse(pending.plan(r)['groups'])

if __name__=='__main__':unittest.main()

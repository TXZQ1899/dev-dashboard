import base64
import json
import unittest
from datetime import datetime, timezone
import kubernetes_inventory as k

POD = 'c76a0f4e-0b3a-4b81-8b4b-032ebe38700d'
CID = 'a' * 64
CGROUP = '1:pids:/kubepods.slice/kubepods-burstable.slice/kubepods-burstable-pod' + POD.replace('-', '_') + '.slice/cri-containerd-' + CID + '.scope'
EPOCH = 1700000000
PROC = {'pid': 7170, 'startedAt': datetime.fromtimestamp(EPOCH, timezone.utc).isoformat()}

def b64(value): return base64.b64encode(value.encode()).decode()
def record(epoch=EPOCH, start=42, end=42): return f'K8S_PROC\t7170\t{start}\t{end}\t{b64(CGROUP)}\tnet:[123]\t{epoch}\n'

class KubernetesInventoryTests(unittest.TestCase):
    def test_cgroups_and_non_kubernetes(self):
        self.assertEqual(k.parse_cgroup(CGROUP), {'podUid': POD, 'containerId': CID})
        self.assertEqual(k.parse_cgroup(f'0::/kubepods/burstable/pod{POD}/{CID}')['containerId'], CID)
        self.assertIsNone(k.parse_cgroup('0::/system.slice/docker-' + CID + '.scope'))

    def test_pid_identity_and_namespace_listeners(self):
        meta = [dict(id=CID, podUid=POD, podName='scrm-1', namespace='uat', containerName='scrm', image='image')]
        body = record() + 'K8S_LISTENER\t7170\t*\t8080\nK8S_LISTENER_STATUS\t7170\tobserved\nK8S_META\t' + b64(json.dumps(meta)) + '\nK8S_END\n'
        result = k.parse_output(body, [PROC])
        self.assertEqual(result['status'], 'complete')
        row = result['processes'][0]
        self.assertEqual((row['podName'], row['namespace'], row['listeners'][0]['port']), ('scrm-1', 'uat', 8080))
        self.assertNotIn('ports', row)
        self.assertEqual(k.parse_output(record(epoch=EPOCH + 100) + 'K8S_END\n', [PROC])['processes'], [])
        self.assertEqual(k.parse_output(record(end=43) + 'K8S_END\n', [PROC])['processes'], [])

    def test_missing_metadata_and_incomplete_result_remain_unresolved(self):
        result = k.parse_output(record(), [PROC])
        self.assertEqual(result['status'], 'partial')
        self.assertNotIn('podName', result['processes'][0])
        self.assertEqual(result['processes'][0]['listenerStatus'], 'not_collected')
        meta = [dict(id=CID, podUid='00000000-0000-0000-0000-000000000000', podName='wrong')]
        result = k.parse_output(record() + 'K8S_META\t' + b64(json.dumps(meta)) + '\nK8S_END\n', [PROC])
        self.assertNotIn('podName', result['processes'][0])

if __name__ == '__main__': unittest.main()

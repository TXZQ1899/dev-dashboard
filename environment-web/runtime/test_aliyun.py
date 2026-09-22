import importlib.util
import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from aliyun import normalize_clb

class CloudTests(unittest.TestCase):
    def test_partial_export_cannot_become_snapshot(self):
        with self.assertRaises(ValueError): normalize_clb({'complete':False})

    def test_redirect_and_backend_port_are_preserved(self):
        data={'complete':True,'collectedAt':'today','region':'cn-shanghai','serverCertificates':{},'instances':[{
            'attributes':{'LoadBalancerId':'lb','LoadBalancerName':'name','Address':'1.2.3.4','AddressType':'intranet','LoadBalancerStatus':'active',
                          'BackendServers':{'BackendServer':[{'ServerId':'ecs','ResolvedServerIp':'10.0.0.1','Weight':100}]}},
            'listeners':[{'protocol':'HTTP','attributes':{'ListenerPort':80,'ListenerForward':'on','ForwardPort':443}},
                         {'protocol':'HTTPS','attributes':{'ListenerPort':443,'BackendServerPort':8080}}], 'serverGroups':[]}]}
        row=normalize_clb(data)['instances'][0]
        self.assertEqual(row['listeners'][0]['forwardPort'],443)
        self.assertEqual(row['listeners'][1]['backendPort'],8080)
        self.assertEqual(row['groups'][0]['servers'][0]['ip'],'10.0.0.1')

if __name__=='__main__':unittest.main()

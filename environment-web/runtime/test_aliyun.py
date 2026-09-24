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

    def _instance(self, backend_ports, server_port=None):
        listeners=[{'protocol':'HTTP','attributes':{'ListenerPort':80,'BackendServerPort':backend_ports[0]}}]
        if len(backend_ports) > 1:
            listeners.append({'protocol':'HTTPS','attributes':{'ListenerPort':443,'BackendServerPort':backend_ports[1]}})
        server={'ServerId':'ecs','ResolvedServerIp':'10.0.0.1','Weight':100}
        if server_port is not None: server['Port']=server_port
        return {'attributes':{'LoadBalancerId':'lb','LoadBalancerName':'name','Address':'1.2.3.4','AddressType':'intranet','LoadBalancerStatus':'active',
                              'BackendServers':{'BackendServer':[server]}},
                'listeners':listeners,'serverGroups':[]}

    def test_default_group_port_filled_from_unanimous_listener_backend_port(self):
        data={'complete':True,'collectedAt':'today','region':'cn-shanghai','serverCertificates':{},
              'instances':[self._instance([80,80])]}
        groups=normalize_clb(data)['instances'][0]['groups']
        self.assertEqual(groups[0]['id'],'default')
        self.assertEqual(groups[0]['servers'][0]['port'],80)

    def test_default_group_port_left_none_when_listeners_disagree(self):
        data={'complete':True,'collectedAt':'today','region':'cn-shanghai','serverCertificates':{},
              'instances':[self._instance([80,8443])]}
        server=normalize_clb(data)['instances'][0]['groups'][0]['servers'][0]
        self.assertIsNone(server['port'])

    def test_explicit_default_server_port_is_preserved(self):
        data={'complete':True,'collectedAt':'today','region':'cn-shanghai','serverCertificates':{},
              'instances':[self._instance([80,80],server_port=8080)]}
        server=normalize_clb(data)['instances'][0]['groups'][0]['servers'][0]
        self.assertEqual(server['port'],8080)

if __name__=='__main__':unittest.main()

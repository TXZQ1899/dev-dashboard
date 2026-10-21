import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import aliyun_openapi as openapi


class FakeRpc:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def call(self, action, params=None):
        self.calls.append((action, params or {}))
        value = self.responses[action]
        if isinstance(value, Exception):
            raise value
        return value


class FakeRoa(FakeRpc):
    def call(self, action, path, params=None):
        self.calls.append((action, path, params or {}))
        value = self.responses[action]
        if callable(value):
            return value(params or {})
        if isinstance(value, Exception):
            raise value
        return value


class OpenApiClientTests(unittest.TestCase):
    def test_rpc_client_signs_and_parses(self):
        seen = {}

        def send(method, url, headers, body, timeout):
            seen.update({'method': method, 'url': url, 'headers': headers})
            return 200, b'{"TotalCount": 0}'

        client = openapi.RpcClient('LTAI-test', 'secret', 'ecs.cn-shanghai.aliyuncs.com',
                                   '2014-05-26', send=send)
        self.assertEqual(client.call('DescribeInstances', {'RegionId': 'cn-shanghai'}), {'TotalCount': 0})
        query = parse_qs(urlparse(seen['url']).query)
        self.assertEqual(query['Action'], ['DescribeInstances'])
        self.assertEqual(query['Version'], ['2014-05-26'])
        self.assertEqual(query['AccessKeyId'], ['LTAI-test'])
        # parse_qs 会把未编码的 '+' 还原成空格，签名本身是 20 字节 SHA1 的 Base64。
        signature = query['Signature'][0].replace(' ', '+')
        self.assertRegex(signature, r'^[A-Za-z0-9+/]{27}=$')
        self.assertEqual(seen['method'], 'GET')

    def test_rpc_auth_error_is_typed(self):
        def send(method, url, headers, body, timeout):
            return 400, b'{"Code": "InvalidAccessKeyId.NotFound", "Message": "bad"}'

        client = openapi.RpcClient('LTAI-test', 'secret', 'ecs.cn-shanghai.aliyuncs.com',
                                   '2014-05-26', send=send)
        with self.assertRaises(openapi.AuthenticationError):
            client.call('DescribeInstances')

    def test_resource_forbidden_is_not_treated_as_access_key_failure(self):
        def send(method, url, headers, body, timeout):
            return 403, b'{"errorCode": "SYSTEM_FORBIDDEN_ERROR", "errorMessage": "no"}'

        client = openapi.RoaClient('LTAI-test', 'secret', openapi.CODEUP_HOST,
                                   openapi.CODEUP_VERSION, send=send)
        with self.assertRaises(openapi.OpenApiError):
            client.call('GetGroupDetail', '/repository/groups/get_detail')


class EcsTests(unittest.TestCase):
    def test_collect_ecs_uses_openapi_and_merges_tags(self):
        instance = {
            'InstanceId': 'i-1', 'InstanceName': 'web-1', 'RegionId': 'cn-shanghai',
            'Status': 'Running', 'Cpu': 4, 'Memory': 8192, 'OSType': 'linux',
            'InstanceType': 'ecs.g6', 'EcsInstanceType': 'ecs.g6', 'InstanceTypeFamily': 'ecs.g6',
            'ResourceGroupId': 'rg-1', 'ZoneId': 'cn-shanghai-a', 'CreationTime': '2025-01-01T00:00:00Z',
            'ExpiredTime': '2027-01-01T00:00:00Z', 'InstanceChargeType': 'PrePaid',
            'OSName': 'CentOS 7', 'HostName': 'web-1',
            'VpcAttributes': {'VpcId': 'vpc-1', 'PrivateIpAddress': {'IpAddress': ['10.0.0.5']}},
            'InnerIpAddress': {'IpAddress': []}, 'PublicIpAddress': {'IpAddress': []},
            'EipAddress': {'IpAddress': ''}, 'NetworkInterfaces': {'NetworkInterface': []},
        }
        fake = FakeRpc({
            'DescribeInstances': {'TotalCount': 1, 'Instances': {'Instance': [instance]}},
            'ListTagResources': {'TagResources': {'TagResource': [
                {'ResourceId': 'i-1', 'TagKey': 'project', 'TagValue': 'demo'}]}},
        })
        with patch.object(openapi, 'RpcClient', lambda *args, **kwargs: fake):
            raw = openapi.collect_ecs_ak('LTAI-test', 'secret', out=tempfile.mkdtemp())
        from aliyun import ecs_snapshot
        snapshot = ecs_snapshot(raw, [])
        self.assertEqual(len(snapshot['instances']), 1)
        self.assertEqual(snapshot['instances'][0]['privateIps'], ['10.0.0.5'])
        self.assertEqual(snapshot['instances'][0]['tags'], {'project': 'demo'})


class NetworkTests(unittest.TestCase):
    def test_clb_raw_shape_normalizes(self):
        lb = {'LoadBalancerId': 'lb-1', 'LoadBalancerName': 'lb', 'RegionId': 'cn-shanghai'}
        responses = {
            'DescribeLoadBalancers': {'TotalCount': 1, 'LoadBalancers': {'LoadBalancer': [lb]}},
            'DescribeServerCertificates': {'ServerCertificates': {'ServerCertificate': []}},
            'DescribeCACertificates': {'CACertificates': {'CACertificate': []}},
            'DescribeLoadBalancerAttribute': {
                'LoadBalancerId': 'lb-1', 'LoadBalancerName': 'lb', 'Address': '1.2.3.4',
                'AddressType': 'internet', 'LoadBalancerStatus': 'active',
                'ListenerPortsAndProtocol': {'ListenerPortAndProtocol': [
                    {'ListenerPort': 80, 'ListenerProtocol': 'http'}]},
            },
            'DescribeLoadBalancerHTTPListenerAttribute': {
                'ListenerPort': 80, 'BackendServerPort': 80, 'VServerGroupId': 'vsg-1'},
            'DescribeRules': {'Rules': {'Rule': []}},
            'DescribeHealthStatus': {'BackendServers': {'BackendServer': [
                {'ServerId': 'i-1', 'ServerIp': '10.0.0.5', 'Port': 8080}]}},
            'DescribeVServerGroups': {'VServerGroups': {'VServerGroup': [{'VServerGroupId': 'vsg-1'}]}},
            'DescribeVServerGroupAttribute': {'VServerGroupId': 'vsg-1', 'BackendServers': {
                'BackendServer': [{'ServerId': 'i-1', 'ServerIp': '10.0.0.5', 'Port': 8080}]}},
            'DescribeMasterSlaveServerGroups': {'MasterSlaveServerGroups': {'MasterSlaveServerGroup': []}},
        }

        def factory(*args, **kwargs):
            endpoint = args[2] if len(args) > 2 else kwargs.get('endpoint', '')
            return FakeRpc({} if 'ecs.' in endpoint else responses)

        with patch.object(openapi, 'RpcClient', factory):
            raw = openapi.collect_clb_ak('LTAI-test', 'secret', out=tempfile.mkdtemp())
        from aliyun import normalize_clb
        snapshot = normalize_clb(raw)
        self.assertEqual(len(snapshot['instances']), 1)
        self.assertEqual(snapshot['instances'][0]['ip'], '1.2.3.4')
        self.assertEqual(snapshot['instances'][0]['listeners'][0]['protocol'], 'HTTP')
        group = next(g for g in snapshot['instances'][0]['groups'] if g['id'] == 'vsg-1')
        self.assertEqual(group['servers'][0]['ip'], '10.0.0.5')
        self.assertEqual(group['servers'][0]['port'], 8080)

    def test_nat_raw_shape_normalizes(self):
        gateway = {'NatGatewayId': 'ngw-1', 'Name': 'nat', 'ForwardTableIds': {'ForwardTableId': ['ftb-1']}}
        entry = {'ForwardEntryId': 'fwd-1', 'ExternalIp': '1.2.3.4', 'ExternalPort': '80',
                 'InternalIp': '10.0.0.5', 'InternalPort': '8080', 'IpProtocol': 'tcp',
                 'Status': 'Available'}
        responses = {
            'DescribeNatGateways': {'TotalCount': 1, 'NatGateways': {'NatGateway': [gateway]}},
            'DescribeForwardTableEntries': {'TotalCount': 1,
                                            'ForwardTableEntries': {'ForwardTableEntry': [entry]}},
        }
        with patch.object(openapi, 'RpcClient', lambda *args, **kwargs: FakeRpc(responses)):
            raw = openapi.collect_nat_ak('LTAI-test', 'secret', out=tempfile.mkdtemp())
        from export_nat import normalize_nat
        snapshot = normalize_nat(raw)
        self.assertEqual(snapshot['gateways'][0]['id'], 'ngw-1')
        entry = snapshot['gateways'][0]['entries'][0]
        self.assertEqual((entry['externalIp'], entry['externalPort']), ('1.2.3.4', '80'))
        self.assertEqual((entry['internalIp'], entry['internalPort']), ('10.0.0.5', '8080'))


class DmsTests(unittest.TestCase):
    def test_list_instances_maps_console_shape(self):
        row = {
            'InstanceId': 123, 'InstanceAlias': '订单库', 'Host': 'db.example.com', 'Port': 3306,
            'InstanceType': 'mysql', 'EcsRegion': 'cn-shanghai', 'State': 'NORMAL',
            'SellTrust': False, 'InstanceSource': 'ECS_OWN', 'EnvType': 'product',
            'StandardGroup': {'GroupMode': 'COMMON'},
        }
        fake = FakeRpc({'ListInstances': {'TotalCount': 1, 'InstanceList': {'Instance': [row]}}})
        with patch.object(openapi, 'RpcClient', lambda *args, **kwargs: fake), \
                patch.object(openapi, 'resolve_ip', lambda host, cache: '10.0.0.9'):
            result = openapi.collect_dms_ak('LTAI-test', 'secret', out=tempfile.mkdtemp())
        item = result['instances'][0]
        self.assertEqual(result['rawRecordCount'], 1)
        self.assertEqual(item['实例名称'], '订单库')
        self.assertEqual(item['实例地址'], 'db.example.com:3306')
        self.assertEqual(item['实例IP'], '10.0.0.9')
        self.assertEqual(item['登录状态'], '免登录')
        self.assertEqual(item['环境'], '生产')


class CodeupTests(unittest.TestCase):
    def test_repository_branches_and_history(self):
        repo_row = {'Id': 9, 'name': 'demo', 'path': 'demo', 'pathWithNamespace': 'org/team/demo',
                    'namespaceId': 7, 'webUrl': 'https://codeup.aliyun.com/org/team/demo',
                    'description': 'Demo'}

        def commits(params):
            if params['refName'] == 'c2':
                return {'total': 2, 'result': [
                    {'id': 'c2', 'parentIds': ['c1'], 'committedDate': '2026-01-02 10:00:00'},
                    {'id': 'c1', 'parentIds': [], 'committedDate': '2026-01-01 09:00:00'}]}
            return {'total': 2, 'result': [
                {'id': 'c1', 'parentIds': [], 'committedDate': '2026-01-01 09:00:00'}]}

        fake = FakeRoa({
            'ListRepositoryBranches': {'total': 1, 'result': [
                {'name': 'main', 'commit': {'id': 'c2', 'committedDate': '2026-01-02 10:00:00'}}]},
            'ListRepositoryCommits': commits,
        })
        repo = openapi.codeup_repo_ak(fake, 'org', repo_row)
        self.assertEqual(repo['url'], 'https://codeup.aliyun.com/org/team/demo.git')
        self.assertEqual(repo['groupName'], 'team')
        self.assertEqual(repo['branches'], 1)
        self.assertEqual(repo['commits'], 2)
        self.assertEqual(repo['reportedCommits'], 2)
        self.assertEqual(repo['lastCommitId'], 'c2')
        self.assertEqual(repo['commitTimeScope'], 'all_current_branches_unique_sha')
        self.assertEqual(repo['commitDailyCounts'], {'2026-01-01': 1, '2026-01-02': 1})

    def test_reference_status_maps_result_and_skips_listed_repos(self):
        def repository(params):
            if params['identity'] == 'org/ok':
                return {'success': True, 'repository': {'id': 1}}
            if params['identity'] == 'org/missing':
                raise openapi.OpenApiError('GetRepository', 404, 'SYSTEM_NOT_FOUND_ERROR', 'not found')
            raise openapi.OpenApiError('GetRepository', 403, 'SYSTEM_FORBIDDEN_ERROR', 'forbidden')

        fake = FakeRoa({'GetRepository': repository})
        devops = {'apps': [
            {'repository': 'https://codeup.aliyun.com/org/ok', 'envs': {'TEST': [
                {'repository': 'https://codeup.aliyun.com/org/missing.git'},
                {'repository': 'https://codeup.aliyun.com/org/denied'}]}},
            {'repository': 'https://codeup.aliyun.com/org/listed', 'envs': {'PRODUCT': []}},
        ]}
        codeup = {'namespaceId': '61a9', 'repos': [{'url': 'https://codeup.aliyun.com/org/listed.git'}]}
        with patch.object(openapi, '_codeup_client', lambda *args: fake):
            statuses = openapi.codeup_reference_status(devops, codeup, 'ak', 'sk')
        self.assertEqual(set(statuses), {'https://codeup.aliyun.com/org/ok.git',
                                         'https://codeup.aliyun.com/org/missing.git',
                                         'https://codeup.aliyun.com/org/denied.git'})
        self.assertEqual(statuses['https://codeup.aliyun.com/org/ok.git']['access'], '是')
        self.assertEqual(statuses['https://codeup.aliyun.com/org/missing.git']['access'], '地址不存在')
        self.assertEqual(statuses['https://codeup.aliyun.com/org/denied.git']['access'], '暂无权限访问')
        self.assertTrue(all(call[2]['organizationId'] == '61a9' for call in fake.calls))

    def test_groups_and_organization_resolution(self):
        fake = FakeRoa({
            'GetGroupDetail': {'result': {'id': 7, 'name': 'team', 'path': 'team',
                                          'pathWithNamespace': 'org/team', 'parentId': None,
                                          'webUrl': 'https://codeup.aliyun.com/org/team'}},
        })
        groups = openapi._codeup_groups(fake, 'org', [{'namespaceId': 7, 'path': 'org/team/demo'}])
        self.assertEqual(groups[0]['id'], '7')
        self.assertEqual(groups[0]['repository_count'], 1)
        with tempfile.TemporaryDirectory() as root:
            current = Path(root) / 'versions' / 'v1'
            current.mkdir(parents=True)
            (current / 'repositories.json').write_text(json.dumps(
                {'groups': [{'path': '61a9/team'}]}), encoding='utf-8')
            with patch.object(openapi, '_codeup_client', lambda *args: self.fail('不应回退到 API')):
                self.assertEqual(openapi.resolve_codeup_organization(current, 'ak', 'sk'), '61a9')


if __name__ == '__main__':
    unittest.main()

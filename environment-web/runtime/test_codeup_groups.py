import unittest
from unittest.mock import patch
from daily_collection import collect_codeup_groups, reconcile

class CodeupGroupTests(unittest.TestCase):
    def test_recursive_collection_keeps_descriptions(self):
        def group(identifier, name, path, description):
            return {'id': identifier, 'name': name, 'path': name, 'path_with_namespace': path,
                    'web_url': 'https://codeup.aliyun.com/' + path, 'project_count': 1, 'description': description}
        rows = {'org': [group(1, 'fosun-fyh', 'org/fosun-fyh', '根组')],
                '1': [group(2, 'backend-order', 'org/fosun-fyh/backend-order', '订单')],
                '2': [group(3, 'hotels', 'org/fosun-fyh/backend-order/hotels', '酒店')], '3': []}
        with patch('daily_collection.paginate', side_effect=lambda client, path, params: rows[params['parent_id']]):
            result = collect_codeup_groups(object(), 'org')
        self.assertEqual([g['chinese_name'] for g in result], ['根组', '订单', '酒店'])

    def test_reconciliation_uses_deepest_group(self):
        groups = [{'id': str(i), 'group_name': name, 'chinese_name': '', 'full_path': path,
                   'repository_count': 1} for i, name, path in [(1, 'root', 'org/root'), (2, 'hotels', 'org/root/hotels')]]
        repo = {'namespaceId': '2', 'groupName': 'hotels', 'name': 'app',
                'path': 'org/root/hotels/app', 'url': 'https://codeup.aliyun.com/org/root/hotels/app.git'}
        result = reconcile({'complete': True, 'scope': 'all', 'source': 'test', 'collectedAt': 'now', 'apps': []},
                           {'complete': True, 'collectedAt': 'now', 'groups': groups, 'repos': [repo]})
        self.assertEqual(result['repos'][0]['groupId'], '2')

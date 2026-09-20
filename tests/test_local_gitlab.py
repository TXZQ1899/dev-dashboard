import unittest
from unittest.mock import patch, MagicMock
from urllib.error import URLError
from daily_collection import LocalGitlabClient, CollectionError

class GitlabPagingTests(unittest.TestCase):
    def test_detail_request_does_not_send_project_only_sort(self):
        client=LocalGitlabClient('unused'); client.opener=MagicMock()
        response=client.opener.open.return_value.__enter__.return_value
        response.read.return_value=b'[]'; response.headers={}
        client.get_project_rows(38,'merge_requests')
        url=client.opener.open.call_args.args[0].full_url
        self.assertNotIn('order_by',url)
        self.assertIn('per_page=100',url)

    def test_legacy_branches_are_one_complete_response(self):
        client=LocalGitlabClient('unused')
        rows=[{'name':str(i)} for i in range(157)]
        with patch.object(client,'request_rows',return_value=(rows,{})) as request:
            self.assertEqual(client.get_project_rows(179,'branches'),rows)
            self.assertEqual(request.call_count,1)

    def test_paginated_branch_headers_are_respected(self):
        client=LocalGitlabClient('unused')
        with patch.object(client,'request_rows',side_effect=[([{'name':'a'}],{'x-next-page':'2'}),([{'name':'b'}],{'x-next-page':''})]):
            self.assertEqual(len(client.get_project_rows(1,'branches')),2)

    def test_repeated_commit_page_fails_instead_of_looping(self):
        client=LocalGitlabClient('unused'); rows=[{'id':str(i)} for i in range(100)]
        with patch.object(client,'request_rows',return_value=(rows,{})) as request:
            with self.assertRaisesRegex(CollectionError,'第 2 页重复'):
                client.get_project_rows(1,'commits')
            self.assertEqual(request.call_count,2)

    def test_network_failure_retries_and_has_safe_context(self):
        client=LocalGitlabClient('secret')
        client.opener=MagicMock(); client.opener.open.side_effect=URLError('secret-host-error')
        with patch('daily_collection.time.sleep'):
            with self.assertRaises(CollectionError) as error:
                client.get_project_rows(179,'branches')
        self.assertEqual(client.opener.open.call_count,3)
        self.assertIn('项目 179 / branches',error.exception.safe_message)
        self.assertIn('重试 3 次',error.exception.safe_message)
        self.assertNotIn('secret',error.exception.safe_message)

if __name__=='__main__':unittest.main()

import unittest

try:
    import business_catalog as business
except ModuleNotFoundError:
    from runtime import business_catalog as business


class BusinessCatalogTests(unittest.TestCase):
    def setUp(self):
        self.repo = {'path':'org/fosun-fyh/backend-order/hotels/fcrs', 'url':'https://codeup.aliyun.com/org/fosun-fyh/backend-order/hotels/fcrs.git', 'inCodeup':True, 'apps':[{'id':'1540'}]}
        self.repos = {'complete':True, 'codeupConnection':{'organizationId':'org'}, 'groups':[{'path':'org/fosun-fyh/backend-order'}], 'repos':[self.repo]}
        self.snapshot = {'complete':True, 'apps':[{'id':'1540', 'repository':self.repo['url']}]}

    def test_group_depth_and_exact_application_repository(self):
        result = business.plan(self.repos, self.snapshot)
        self.assertEqual(result['domains'], ['fosun-fyh'])
        self.assertEqual(result['systems'], [('fosun-fyh', 'backend-order')])
        self.assertEqual(result['assignments']['1540']['system'], 'backend-order')
        self.snapshot['apps'][0]['repository'] = self.repo['url'].replace('fcrs.git', 'other.git')
        self.assertEqual(business.plan(self.repos, self.snapshot)['assignments'], {})

    def test_unlisted_repos_and_direct_domain_repos_do_not_create_systems(self):
        self.repos['groups'] = [{'path':'org/fosun-cq'}]
        self.repos['repos'] = [{**self.repo, 'inCodeup':False}, {**self.repo, 'path':'org/fosun-cq/repo', 'url':'https://codeup.aliyun.com/org/fosun-cq/repo.git'}]
        result = business.plan(self.repos, self.snapshot)
        self.assertEqual(result['domains'], ['fosun-cq'])
        self.assertEqual(result['systems'], [])
        self.assertEqual(result['assignments'], {})

    def test_incomplete_snapshot_is_rejected(self):
        self.repos['complete'] = False
        with self.assertRaises(ValueError): business.plan(self.repos, self.snapshot)
        self.repos['complete'] = True
        self.snapshot['complete'] = False
        with self.assertRaises(ValueError): business.plan(self.repos, self.snapshot)

    def test_scoped_codes_are_stable_and_distinct(self):
        self.assertEqual(business.code('system', 'org', 'd', 's'), business.code('system', 'org', 'd', 's'))
        self.assertNotEqual(business.code('system', 'org', 'd', 's'), business.code('system', 'org', 'other', 's'))


if __name__ == '__main__': unittest.main()

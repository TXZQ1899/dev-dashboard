import unittest
from check_codeup_access import normalize_url, classify, Checker


class CodeupTests(unittest.TestCase):
    def test_normalization(self):
        urls = ['https://codeup.aliyun.com/org/group/repo.git',
                'git@codeup.aliyun.com:org/group/repo.git',
                'ssh://git@codeup.aliyun.com/org/group/repo',
                'http://CODEUP.aliyun.com/org/group/repo/']
        self.assertEqual(len({normalize_url(u) for u in urls}), 1)
        self.assertNotEqual(normalize_url(urls[0]), normalize_url(urls[0].replace('repo.git', 'Repo.git')))

    def test_invalid_urls(self):
        for url in ['', 'cat', '1', 'https://codeup.aliyun.com/org/../repo',
                    'https://codeup.aliyun.com:8443/org/repo', 'https://codeup.aliyun.com/org/repo?token=secret']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                normalize_url(url)

    def test_classify(self):
        self.assertEqual(classify({'success': False, 'errorCode': 'FORBIDDEN',
                                  'result': {'pathResource': {'id': '1', 'type': 'PROJECT'}}})[0], '否')
        self.assertEqual(classify({'success': True, 'result': {'pathResource': {'id': '1', 'type': 'PROJECT'}}})[0], '是')
        self.assertEqual(classify({'success': True, 'result': {}})[0], '无法确认')
        self.assertTrue(classify({'errorCode': 'UNAUTHORIZED'})[3])

    def test_cookie_never_sent_to_other_host(self):
        checker = Checker('secret=value', delay=0)
        class NeverOpen:
            def open(self, *args, **kwargs):
                raise AssertionError('must not send request')
        checker.opener = NeverOpen()
        self.assertEqual(checker.check({'host': 'code.aliyun.com', 'path': 'org/repo'})[0], '未检查')


if __name__ == '__main__':
    unittest.main()

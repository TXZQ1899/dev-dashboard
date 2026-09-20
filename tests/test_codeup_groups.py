import json
import unittest
from export_codeup_groups import fetch_groups, namespace_from_page, validate_group
from export_devops import ExportError

GROUP={'id':1,'name':'Example','path':'example','path_with_namespace':'org/example',
       'web_url':'https://codeup.aliyun.com/org/example','description':'示例代码组',
       'updated_at':'2026-09-03T09:30:00+08:00','project_count':0}


class GroupTests(unittest.TestCase):
    def test_mapping_keeps_empty_and_zero(self):
        row=validate_group(dict(GROUP,description=None))
        self.assertEqual(row['chinese_name'],'')
        self.assertEqual(row['repository_count'],0)
        self.assertEqual(row['english_name'],'example')
        with self.assertRaises(ExportError):
            validate_group(dict(GROUP,project_count=None))

    def test_namespace_is_organization_scoped(self):
        self.assertEqual(namespace_from_page("user: {namespace_id: '12'}, organization: {namespace_id: '99'}"),'99')
        with self.assertRaises(ExportError):
            namespace_from_page('<html>Login</html>')

    def test_short_page_does_not_stop_early(self):
        class Fake:
            def read(self,path,params):
                if path=='/groups':return "organization: {namespace_id: '99'}"
                page=params['page']
                return json.dumps([dict(GROUP,id=page)] if page<3 else [])
        _,rows=fetch_groups(Fake(),100)
        self.assertEqual(len(rows),2)

    def test_duplicate_page_fails(self):
        class Fake:
            def read(self,path,params):
                return "organization: {namespace_id: '99'}" if path=='/groups' else json.dumps([GROUP])
        with self.assertRaises(ExportError):fetch_groups(Fake())


if __name__=='__main__':unittest.main()

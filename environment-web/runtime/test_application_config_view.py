import json
import tempfile
import unittest
from pathlib import Path

import application_config_view as view


class ConfigViewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.folder = self.root / 'application-config' / 'orders-42'
        (self.folder / 'other').mkdir(parents=True)

    def manifest(self, files, folder=None):
        folder = folder or self.folder
        (folder / 'other').mkdir(parents=True, exist_ok=True)
        (folder / 'other' / 'manifest.json').write_text(json.dumps({'appId':'42', 'name':'orders', 'files':files}))

    def test_only_real_listed_files_are_visible_and_preview_is_bounded(self):
        path = self.folder / 'test' / 'resources' / 'a.yml'
        path.parent.mkdir(parents=True)
        path.write_text('password: visible\ntoken: secret\n' + 'x' * view.LIMIT)
        self.manifest([{'file':'test/resources/a.yml', 'source':'repository', 'path':'src/a.yml'},
                       {'file':'test/resources/missing.yml'}, {'file':'../other/manifest.json'}])
        self.assertEqual(view.list_ids(self.root)['appIds'], ['42'])
        result = view.detail(self.root, '42')
        self.assertEqual(len(result['files']), 1)
        preview = view.detail(self.root, '42', result['files'][0]['id'])
        self.assertIn('password: visible', preview['content'])
        self.assertNotIn('secret', preview['content'])
        self.assertTrue(preview['truncated'])
        with self.assertRaises(ValueError): view.detail(self.root, '42', '../other/manifest.json')

    def test_empty_and_ambiguous_snapshots_are_not_exposed(self):
        self.manifest([])
        self.assertEqual(view.list_ids(self.root)['appIds'], [])
        path = self.folder / 'test/resources/a.yml'
        path.parent.mkdir(parents=True)
        path.write_text('ok')
        self.manifest([{'file':'test/resources/a.yml'}])
        second = self.root / 'application-config' / 'renamed-42'
        (second / 'test/resources').mkdir(parents=True)
        (second / 'test/resources/a.yml').write_text('ok')
        self.manifest([{'file':'test/resources/a.yml'}], second)
        self.assertEqual(view.list_ids(self.root)['appIds'], [])
        with self.assertRaises(ValueError): view.detail(self.root, '42')

    def test_symlink_outside_snapshot_is_not_visible(self):
        outside = self.root / 'secret'
        outside.write_text('secret')
        target = self.folder / 'test/resources/a.yml'
        target.parent.mkdir(parents=True)
        target.symlink_to(outside)
        self.manifest([{'file':'test/resources/a.yml'}])
        self.assertEqual(view.list_ids(self.root)['appIds'], [])

    def test_old_mapper_files_are_hidden_without_removing_them(self):
        for name, content in [('mapper/User.xml', '<mapper namespace="user"/>'),
                              ('queries.xml', '<mapper namespace="orders"/>'),
                              ('mybatis-config.xml', '<configuration/>')]:
            path = self.folder / 'other/resources' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        self.manifest([{'file':'other/resources/'+name, 'path':'src/main/resources/'+name, 'source':'repository'}
                       for name in ('mapper/User.xml', 'queries.xml', 'mybatis-config.xml')])
        result = view.detail(self.root, '42')
        self.assertEqual([item['path'] for item in result['files']], ['src/main/resources/mybatis-config.xml'])
        self.assertTrue((self.folder/'other/resources/mapper/User.xml').exists())


if __name__ == '__main__': unittest.main()

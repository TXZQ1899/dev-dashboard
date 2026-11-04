import base64
import json
import unittest
from unittest.mock import patch

import container_info as ci


class ContainerInfoTests(unittest.TestCase):
    def setUp(self):
        self.cid = 'a'*64
        self.proc = {'pid':123, 'startTicks':'456'}
        self.mount = {'Type':'bind', 'Source':'/srv/data', 'Destination':'/app', 'RW':True}
        values = [self.cid, '/demo', 'demo:latest', 'sha256:123', 123, [self.mount]]
        self.response = 'INFO\t'+base64.b64encode('\t'.join(json.dumps(v) for v in values).encode()).decode()+'\nFILES_DONE\n'

    def test_inspect_exposes_selected_fields_and_checks_identity(self):
        with patch.object(ci, 'run_probe', return_value=self.response):
            info = ci.inspect(None, self.proc, self.cid)
        self.assertEqual(info['image'], 'demo:latest')
        self.assertEqual(info['mounts'][0]['source'], '/srv/data')
        with patch.object(ci, 'run_probe', return_value=self.response):
            with self.assertRaisesRegex(ValueError, '无效'):
                ci.inspect(None, self.proc, 'b'*64)

    def test_browse_decodes_metadata_and_rejects_escape(self):
        with patch.object(ci, 'run_probe', return_value=self.response):
            info = ci.inspect(None, self.proc, self.cid)
        raw = b'd\t4096\t100.0\tconfigs\0f\t13\t99.0\tfile\tname.yml\0'
        listing = 'FILES_BEGIN\n'+base64.b64encode(raw).decode()+'\nFILES_END\nFILES_DONE\n'
        with patch.object(ci, 'run_probe', side_effect=[listing, self.response]):
            result = ci.browse(None, self.proc, self.cid, info, 0, '')
        self.assertEqual(result['entries'][0]['type'], 'directory')
        self.assertEqual(result['entries'][1]['name'], 'file\tname.yml')
        for path in ('../other', '/etc', 'a/../b', 'a//b'):
            with self.assertRaisesRegex(ValueError, '路径'):
                ci.browse(None, self.proc, self.cid, info, 0, path)

    def test_browse_rejects_changed_mounts_and_nonpersistent_mounts(self):
        with patch.object(ci, 'run_probe', return_value=self.response):
            info = ci.inspect(None, self.proc, self.cid)
        changed = {**info, 'mounts':[{**info['mounts'][0], 'source':'/other'}]}
        listing = 'FILES_BEGIN\n\nFILES_END\nFILES_DONE\n'
        with patch.object(ci, 'run_probe', return_value=listing), patch.object(ci, 'inspect', return_value=changed):
            with self.assertRaisesRegex(ValueError, '已变化'):
                ci.browse(None, self.proc, self.cid, info, 0, '')
        info['mounts'][0]['type'] = 'tmpfs'
        with self.assertRaisesRegex(ValueError, '不是可浏览'):
            ci.browse(None, self.proc, self.cid, info, 0, '')

    def test_mount_order_does_not_change_identity(self):
        other = {'Type':'volume', 'Source':'/srv/volume', 'Destination':'/z', 'RW':False}
        def response(mounts):
            row = [self.cid, '/demo', 'demo:latest', 'sha256:123', 123, mounts]
            return 'INFO\t'+base64.b64encode('\t'.join(map(json.dumps, row)).encode()).decode()+'\nFILES_DONE\n'
        with patch.object(ci, 'run_probe', side_effect=[response([self.mount, other]), response([other, self.mount])]):
            self.assertEqual(ci.inspect(None, self.proc, self.cid), ci.inspect(None, self.proc, self.cid))

    def test_browse_limits_page_and_validates_offset(self):
        with patch.object(ci, 'run_probe', return_value=self.response):
            info = ci.inspect(None, self.proc, self.cid)
        raw = b''.join(('f\t1\t99\tfile%03d\0' % i).encode() for i in range(201))
        listing = 'FILES_BEGIN\n'+base64.b64encode(raw).decode()+'\nFILES_END\nFILES_DONE\n'
        with patch.object(ci, 'run_probe', side_effect=[listing, self.response]) as run:
            result = ci.browse(None, self.proc, self.cid, info, 0, '', 200)
        self.assertEqual((len(result['entries']), result['truncated'], result['offset']), (200, True, 200))
        self.assertEqual(run.call_args_list[0].args[-1], '200')
        for offset in (-1, 1, True, 1000200):
            with self.assertRaisesRegex(ValueError, '页码'):
                ci.browse(None, self.proc, self.cid, info, 0, '', offset)

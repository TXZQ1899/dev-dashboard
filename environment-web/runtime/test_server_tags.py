import unittest
import io
import json
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from runtime import service
from runtime.server_tags import identity, update

class ServerTagsTest(unittest.TestCase):
    def setUp(self):
        self.row = {'id': 'i-one', 'tenantId': 'group', 'region': 'cn-shanghai', 'tags': {'env': 'prod'}}
        self.snapshot = {'instances': [self.row]}
        self.resource = identity(self.row)

    def test_add_delete_and_tenant_isolation(self):
        data = update({}, self.snapshot, {'resource': self.resource, 'key': 'owner', 'value': 'team'})
        self.assertEqual(data[self.resource], {'owner': 'team'})
        self.assertNotEqual(self.resource, identity({**self.row, 'tenantId': 'other'}))
        self.assertEqual(update(data, self.snapshot, {'resource': self.resource, 'key': 'owner', 'action': 'delete'}), {})

    def test_reject_duplicate_and_unknown_instance(self):
        with self.assertRaises(ValueError):
            update({}, self.snapshot, {'resource': self.resource, 'key': 'env', 'value': 'test'})
        with self.assertRaises(ValueError):
            update({}, self.snapshot, {'resource': 'unknown', 'key': 'owner', 'value': 'team'})
        data = update({}, self.snapshot, {'resource': self.resource, 'key': 'owner', 'value': ''})
        with self.assertRaises(ValueError):
            update(data, self.snapshot, {'resource': self.resource, 'key': 'owner', 'value': 'other'})

    def test_http_persists_outside_snapshots_and_requires_same_origin(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            version = root / 'versions' / 'v1'
            version.mkdir(parents=True)
            (root / 'current.json').write_text(json.dumps({'id': 'v1'}))
            (version / 'ecs-snapshot.json').write_text(json.dumps(self.snapshot))
            payload = json.dumps({'resource': self.resource, 'key': 'owner', 'value': 'team'}).encode()
            handler = object.__new__(service.Handler)
            handler.path = '/api/catalog/server-tags'
            handler.headers = {'Host': 'localhost:3000', 'Origin': 'http://localhost:3000', 'Content-Type': 'application/json', 'Content-Length': str(len(payload))}
            handler.rfile = io.BytesIO(payload)
            handler.json = Mock()
            handler.server = SimpleNamespace(service=SimpleNamespace(lock=threading.RLock(), version=lambda _: version))
            with patch.object(service, 'DATA', root):
                handler.do_POST()
                handler.json.assert_called_once_with(200, {self.resource: {'owner': 'team'}})
                self.assertEqual(json.loads((root / 'server-tags.json').read_text())[self.resource]['owner'], 'team')
                self.assertEqual(json.loads((version / 'ecs-snapshot.json').read_text()), self.snapshot)
                handler.json.reset_mock()
                handler.headers['Origin'] = 'http://example.com'
                handler.do_POST()
                self.assertEqual(handler.json.call_args.args[0], 403)

if __name__ == '__main__': unittest.main()

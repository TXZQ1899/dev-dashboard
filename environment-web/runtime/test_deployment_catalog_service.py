import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('catalog_service_test', Path(__file__).with_name('service.py'))
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)


class CatalogServiceTests(unittest.TestCase):
    def handler(self, method, path, payload=None, origin='http://localhost:3001'):
        handler = object.__new__(s.Handler)
        body = json.dumps(payload if payload is not None else {}).encode()
        handler.headers = {'Host': 'localhost:3001', 'Origin': origin, 'Content-Type': 'application/json', 'Content-Length': str(len(body))}
        handler.rfile = io.BytesIO(body)
        handler.path = path
        service = Mock()
        handler.server = SimpleNamespace(service=service)
        handler.json = Mock()
        return handler, service

    def test_get_catalog_routes_to_runtime_and_masks_unexpected_errors(self):
        handler, service = self.handler('GET', '/api/catalog/deployment-units?page=2')
        service.catalog_query.return_value = {'units': []}
        handler.do_GET()
        service.catalog_query.assert_called_once_with({'page': ['2']})
        handler.json.assert_called_once_with(200, {'units': []})
        handler.json.reset_mock()
        service.catalog_query.side_effect = RuntimeError('credential-secret')
        handler.do_GET()
        self.assertEqual(handler.json.call_args.args[0], 503)
        self.assertNotIn('secret', str(handler.json.call_args))

    def test_import_requires_same_origin(self):
        handler, service = self.handler('POST', '/api/catalog/deployment-units/import', origin='http://other.invalid')
        handler.do_POST()
        handler.json.assert_called_once_with(403, {'error': '请求来源无效'})
        service.start_catalog_sync.assert_not_called()
        handler, service = self.handler('POST', '/api/catalog/deployment-units/import')
        service.start_catalog_sync.return_value = {'job': {'status': 'queued'}}
        handler.do_POST()
        handler.json.assert_called_once_with(202, {'job': {'status': 'queued'}})

    def test_sync_pins_snapshot_and_database_failure_does_not_change_pointer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root/'versions'/'ready-1'
            s.write(root/'current.json', {'id': 'ready-1'})
            s.write(folder/'version.json', {'status': 'ready'})
            s.write(folder/'snapshot.json', {'apps': [], 'complete': True})
            service = object.__new__(s.Service)
            service.lock = s.threading.RLock()
            service.catalog_lock = s.threading.Lock()
            service.catalog_job = {'status': 'idle'}
            service.catalog_store = Mock()
            service.catalog_store.import_snapshot.side_effect = s.CatalogError('database unavailable')
            with patch.object(s, 'DATA', root):
                before = (root/'current.json').read_bytes()
                state = service.catalog_sync_once()
                self.assertEqual(state['status'], 'failed')
                self.assertEqual(s.read(root/'catalog-job.json')['status'], 'failed')
                self.assertEqual((root/'current.json').read_bytes(), before)
                service.catalog_store.import_snapshot.assert_called_once()
                s.write(folder/'version.json', {'status': 'collecting'})
                with self.assertRaises(s.CatalogError): service.catalog_context()

    def test_catalog_query_rejects_invalid_filters_before_database_access(self):
        service = object.__new__(s.Service)
        service.catalog_store = Mock()
        for params in ({'page': ['0']}, {'presence': ['deleted']}, {'environment': ['UAT']}, {'unitId': ['../../secret']}, {'q': ['a' * 201]}):
            with self.assertRaises((ValueError, s.CatalogInputError)): service.catalog_query(params)
        service.catalog_store.read.assert_not_called()


if __name__ == '__main__':
    unittest.main()

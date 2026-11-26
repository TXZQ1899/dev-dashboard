import base64
import json
import unittest
import zlib
from unittest.mock import Mock

import kong_admin_via_jumpserver as kong


class KongAdminTerminalTests(unittest.TestCase):
    def test_transport_only_allows_read_paths_used_by_the_resolver(self):
        terminal = Mock()
        for path in ('http://example.test/routes', '//example.test/routes', '/config',
                     '/routes#fragment', '/services/../routes', '/routes\r\nHost: other'):
            self.assertIn('error', kong.request(terminal, path))
        terminal.run.assert_not_called()

    def test_local_admin_response_keeps_success_and_http_failure_separate(self):
        terminal = Mock()
        payload = {'data': [], 'next': None}
        encoded = base64.b64encode(zlib.compress(json.dumps(payload).encode())).decode()
        terminal.run.return_value = (0, f'200:{encoded}')
        self.assertEqual(kong.request(terminal, '/routes?size=1000'), {'data': payload})
        command = terminal.run.call_args.args[0]
        self.assertIn('127.0.0.1', command)
        self.assertIn("GET", command)
        self.assertNotIn('\n', command)
        terminal.run.return_value = (0, f'404:{encoded}')
        self.assertEqual(kong.request(terminal, '/services/unknown'), {'error': 'Admin API HTTP 404'})


if __name__ == '__main__':
    unittest.main()

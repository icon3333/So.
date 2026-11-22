import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from n8n_connection import Connection, ISSUER
from triage_state import StateError


class CallbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.connection = Connection(Path(self.temp.name), 'http://127.0.0.1:8769')
        self.connection.pending = {'state': 'test-state', 'verifier': 'synthetic-verifier', 'expires': time.time() + 60}
        self.connection.metadata = {'token_endpoint': ISSUER + '/mcp-oauth/token'}
        self.query = {'state': ['test-state'], 'iss': [ISSUER], 'code': ['synthetic-code']}

    @patch('n8n_connection.request_json')
    def test_wrong_state_cannot_exchange(self, exchange):
        self.query['state'] = ['wrong']
        with self.assertRaises(StateError):
            self.connection.finish(self.query)
        exchange.assert_not_called()

    @patch('n8n_connection.request_json')
    def test_wrong_issuer_cannot_exchange(self, exchange):
        self.query['iss'] = ['https://unrelated.example']
        with self.assertRaises(StateError):
            self.connection.finish(self.query)
        exchange.assert_not_called()

    @patch('n8n_connection.request_json')
    def test_expired_callback_cannot_exchange(self, exchange):
        self.connection.pending['expires'] = time.time() - 1
        with self.assertRaises(StateError):
            self.connection.finish(self.query)
        exchange.assert_not_called()

    @patch.object(Connection, 'set_tokens')
    @patch('n8n_connection.request_json', return_value={})
    def test_valid_callback_can_only_exchange_once(self, exchange, accept):
        self.connection.finish(self.query)
        with self.assertRaises(StateError):
            self.connection.finish(self.query)
        exchange.assert_called_once()
        accept.assert_called_once()
        body = exchange.call_args.args[1]
        self.assertEqual(body['code_verifier'], 'synthetic-verifier')
        self.assertEqual(body['redirect_uri'], 'http://127.0.0.1:8769/oauth/callback')


if __name__ == '__main__':
    unittest.main()

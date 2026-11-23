import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import account_settings
from karakeep_connection import KarakeepConnection
from n8n_connection import Connection
from triage_state import StateError
from x_connection import XConnection


class AccountSettingsTests(unittest.TestCase):
    def test_get_status_is_strictly_local_and_never_claims_tested(self):
        app = SimpleNamespace(connection=Mock(), x_connection=Mock(), karakeep=Mock())
        app.x_connection.client_id = None
        for provider in ('inoreader', 'x', 'karakeep', 'tasksorg'):
            with self.subTest(provider=provider), patch('cockpit.posteo_source', side_effect=AssertionError('probe')):
                result = account_settings.account_status(app, provider)
                self.assertNotEqual(result['state'], 'connected')
        app.connection.status.assert_not_called()
        app.x_connection.status.assert_not_called()
        app.karakeep.status.assert_not_called()
        self.assertEqual(account_settings.account_status(app, 'x')['fields'][-1]['name'], 'confirmCost')

    def test_inoreader_connect_calls_existing_oauth_begin_and_test_calls_inventory(self):
        app = SimpleNamespace(connection=Mock())
        app.connection.begin.return_value = {'url': 'https://n8n.invalid/oauth'}
        self.assertEqual(account_settings.account_action(app, 'inoreader', 'connect', {})['authorizationUrl'], 'https://n8n.invalid/oauth')
        app.connection.begin.assert_called_once_with()
        app.connection.read_later_inventory.return_value = {'count': 0, 'coverage': 'complete', 'checkedAt': 'synthetic'}
        self.assertEqual(account_settings.account_action(app, 'inoreader', 'test', {})['state'], 'connected')
        app.connection.read_later_inventory.assert_called_once_with()

    def test_karakeep_test_uses_candidate_only_and_does_not_store(self):
        app = SimpleNamespace(karakeep=KarakeepConnection())
        app.karakeep._api_key = 'previous-key'
        with patch('karakeep_connection.request_json', return_value={'id': 'synthetic-user'}) as request:
            result = account_settings.account_action(app, 'karakeep', 'test', {'apiKey': 'candidate-key'})
        self.assertEqual(result['state'], 'connected')
        self.assertIs(result['candidate'], True)
        request.assert_called_once_with('/users/me', 'candidate-key')
        self.assertEqual(app.karakeep._api_key, 'previous-key')

    def test_karakeep_test_requires_pasted_candidate_and_validates_identity(self):
        app = SimpleNamespace(karakeep=KarakeepConnection())
        with patch('karakeep_connection.request_json', return_value={'id': ''}):
            result = account_settings.account_action(app, 'karakeep', 'test', {'apiKey': 'candidate'})
        self.assertEqual(result['state'], 'error')
        self.assertEqual(account_settings.account_action(app, 'karakeep', 'test', {})['state'], 'unconfigured')

    def test_x_begin_and_test_require_explicit_cost_confirmation(self):
        app = SimpleNamespace(x_connection=Mock())
        for action in ('connect', 'test'):
            denied = account_settings.account_action(app, 'x', action, {'clientId': 'synthetic'})
            self.assertEqual(denied['state'], 'error')
        app.x_connection.begin.return_value = {'url': 'https://x.invalid/auth'}
        accepted = account_settings.account_action(app, 'x', 'connect', {'clientId': 'synthetic', 'confirmCost': True})
        self.assertEqual(accepted['authorizationUrl'], 'https://x.invalid/auth')
        app.x_connection.begin.assert_called_once_with('synthetic', {'users': 1, 'usd': 0.01})

    def test_x_local_status_does_not_count_as_identity_test(self):
        app = SimpleNamespace(x_connection=XConnection('http://127.0.0.1:1', SimpleNamespace(data={'accountId': None})))
        app.x_connection.tokens = {'access_token': 'synthetic', 'expires_at': 9999999999, 'scopes': list(__import__('x_connection').SCOPES)}
        app.x_connection.account = {'id': '123', 'username': 'synthetic'}
        with patch('x_connection._safe_json_request', return_value={'data': {'id':'123'}}) as probe:
            result = account_settings.account_action(app, 'x', 'test', {'confirmCost': 'yes'})
        probe.assert_called_once()
        self.assertEqual(result['state'], 'connected')

    def test_posteo_is_trusted_adapter_only(self):
        app = SimpleNamespace()
        with patch('cockpit.posteo_source', return_value={'state': 'ready', 'items': []}) as probe:
            result = account_settings.account_action(app, 'tasksorg', 'test', {})
        self.assertEqual(result['state'], 'connected')
        probe.assert_called_once_with()
        self.assertEqual(account_settings.account_status(app, 'tasksorg')['fields'], [])


if __name__ == '__main__':
    unittest.main()

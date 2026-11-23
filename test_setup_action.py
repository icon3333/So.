import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

from server import App
from triage_state import StateError


class SetupWorkflowTests(unittest.TestCase):
    def app(self) -> Any:
        app = App.__new__(App)
        app.operation_lock = __import__('threading').RLock()
        app.accounts = {'inoreader': 'Inoreader', 'x': 'X', 'karakeep': 'Karakeep', 'tasksorg': 'Tasks'}
        app.imports = {'keep': 'Keep', 'to': 'To', 'projects': 'Projects'}
        app.account_checks = {}
        app.connection = Mock()
        app.x_connection = Mock()
        app.karakeep = Mock()
        app.connection.status.return_value = {'connected': False}
        app.x_connection.status.return_value = {'connected': False}
        return app

    def test_oauth_setup_returns_existing_authorization_and_resume_never_restarts(self):
        app = self.app()
        app.connection.begin.return_value = {'url': 'https://auth.example.test'}
        result = app.integration_action({'id': 'inoreader', 'action': 'setup', 'fields': {}})
        self.assertEqual(result['authorizationUrl'], 'https://auth.example.test')
        self.assertEqual(result['resumeAction'], 'setup')
        result = app.integration_action({'id': 'inoreader', 'action': 'resume', 'fields': {}})
        self.assertEqual(result['state'], 'authorization_required')
        app.connection.begin.assert_called_once()

    def test_x_oauth_setup_respects_explicit_cost_confirmation(self):
        app = self.app()
        denied = app.integration_action({'id': 'x', 'action': 'setup', 'fields': {'clientId': 'id'}})
        self.assertEqual(denied['state'], 'error')
        app.x_connection.begin.return_value = {'url': 'https://x.example.test'}
        allowed = app.integration_action({'id': 'x', 'action': 'setup', 'fields': {'clientId': 'id', 'confirmCost': True}})
        self.assertEqual(allowed['authorizationUrl'], 'https://x.example.test')
        self.assertEqual(app.x_connection.begin.call_args.args[1], {'users': 1, 'usd': 0.01})

    def test_local_import_setup_validates_saves_then_uses_cockpit_importer(self):
        app = self.app()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app.data_root = root / 'private-data'
            app.data_root.mkdir()
            app.import_root = root / 'allowed'
            app.import_root.mkdir()
            (app.import_root / 'Keep').mkdir()
            (app.import_root / 'Keep' / 'one.json').write_text('{"title":"One note"}')
            result = app.integration_action({'id': 'keep', 'action': 'setup', 'fields': {'path': 'Keep'}})
            self.assertEqual(result['imported'], 1)
            self.assertEqual(result['state'], 'readable')

    def test_tasks_setup_lists_posteo_entries(self):
        app = self.app()
        app.cockpit_posteo = Mock(return_value={'state': 'ready', 'items': [{'id': 'task-1'}]})
        with patch('cockpit.posteo_source', return_value={'state': 'ready', 'items': []}):
            result = app.integration_action({'id': 'tasksorg', 'action': 'setup', 'fields': {}})
        app.cockpit_posteo.assert_called_once()
        self.assertEqual(result['imported'], 1)
        self.assertEqual(app._setup_posteo_listing['items'][0]['id'], 'task-1')

    def test_karakeep_setup_uses_verified_connection_without_sending_content(self):
        app = self.app()
        with patch('karakeep_connection.request_json', return_value={'id': 'user'}):
            result = app.integration_action({'id': 'karakeep', 'action': 'setup', 'fields': {'apiKey': 'candidate'}})
        self.assertEqual(result['state'], 'connected')
        app.karakeep.connect.assert_called_once_with('candidate')

    def test_unknown_setup_provider_fails(self):
        app = self.app()
        with self.assertRaises(StateError):
            app.integration_action({'id': 'unknown', 'action': 'setup', 'fields': {}})


if __name__ == '__main__':
    unittest.main()

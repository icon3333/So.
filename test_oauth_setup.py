import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from triage_state import StateError
from oauth_setup import oauth_setup
from account_settings import account_status


class OAuthSetupTests(unittest.TestCase):
    def make_app(self, connected=True):
        adapter = Mock()
        adapter.status.return_value = {'connected': connected}
        app = SimpleNamespace(connection=adapter, x_connection=adapter,
                              integration_action=Mock(), import_x_bookmarks=Mock())
        return app, adapter

    def test_x_starts_oauth_without_claiming_import(self):
        app, _ = self.make_app(False)
        app.integration_action.return_value = {'authorizationUrl': 'https://x.invalid/auth'}
        with patch('account_settings.account_action', return_value={'authorizationUrl': 'https://x.invalid/auth'}):
            result = oauth_setup(app, 'x', {'confirmCost': True})
        self.assertEqual(result['state'], 'authorization_required')
        self.assertEqual(result['resumeAction'], 'setup')
        app.import_x_bookmarks.assert_not_called()

    def test_x_resume_uses_explicit_bounded_approval_and_reports_partial(self):
        app, _ = self.make_app()
        app.import_x_bookmarks.return_value = {'xImport': {'complete': False, 'count': 3,
            'checkpoint': 'cursor', 'budget': {'spent': 0.02}}}
        fields = {'confirmCost': True, 'maxPages': 2, 'maxResults': 4, 'maxUsd': .06,
                  'includeAuthors': False}
        result = oauth_setup(app, 'x', fields, resume=True)
        app.import_x_bookmarks.assert_called_once_with({'posts': 8, 'users': 0, 'pages': 2, 'usd': .06},
            max_pages=2, max_results=4, restart=False)
        self.assertEqual(result['state'], 'partial')
        self.assertEqual(result['imported'], 3)

    def test_x_invalid_or_missing_consent_does_not_import(self):
        app, _ = self.make_app()
        for fields in ({}, {'confirmCost': True, 'maxPages': 1001}):
            with self.subTest(fields=fields), self.assertRaises(StateError):
                oauth_setup(app, 'x', fields, resume=True)
        app.import_x_bookmarks.assert_not_called()

    def test_inoreader_import_uses_bounded_local_adapter(self):
        app, _ = self.make_app()
        app.import_inoreader_entries = Mock(return_value={'imported': 2, 'total': 2, 'complete': True, 'pages': 1})
        result = oauth_setup(app, 'inoreader', {}, resume=True)
        app.import_inoreader_entries.assert_called_once_with(max_pages=5, page_size=100)
        self.assertEqual(result['imported'], 2)
        self.assertTrue(result['importSupported'])

    def test_resume_without_oauth_does_not_start_it(self):
        app, adapter = self.make_app(False)
        result = oauth_setup(app, 'inoreader', {}, resume=True)
        self.assertEqual(result['state'], 'authorization_required')
        app.integration_action.assert_not_called()
        adapter.read_later_inventory.assert_not_called()

    def test_x_setup_fields_expose_consent_and_cap(self):
        app = SimpleNamespace(x_connection=SimpleNamespace(client_id=None))
        fields = {f['name']: f for f in account_status(app, 'x')['fields']}
        self.assertEqual(set(fields) & {'confirmCost', 'maxPages', 'maxResults', 'maxUsd', 'includeAuthors', 'restart'},
                         {'confirmCost', 'maxPages', 'maxResults', 'maxUsd', 'includeAuthors', 'restart'})
        self.assertEqual((fields['maxPages']['value'], fields['maxResults']['value']), (10, 100))


if __name__ == '__main__':
    unittest.main()

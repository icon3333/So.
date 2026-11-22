import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from triage_state import StateError
from x_bookmarks import XBookmarkCatalog
from x_connection import API, CALLBACK_PATH, SCOPES, XConnection, BudgetLedger


class OAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.catalog = XBookmarkCatalog(Path(self.temp.name) / 'x-bookmarks.json')
        self.connection = XConnection('http://127.0.0.1:8769', self.catalog)
        self.approval = {'posts': 0, 'users': 1, 'mutations': 0, 'verification_posts': 0, 'usd': .01}

    def authorize(self):
        result = self.connection.begin('synthetic-public-client-id', self.approval)
        from urllib.parse import parse_qs, urlsplit
        query = parse_qs(urlsplit(result['url']).query)
        callback = {'state': query['state'], 'code': ['synthetic-code']}
        return query, callback

    @patch('x_connection._safe_json_request')
    def test_pkce_callback_scopes_identity_and_ephemeral_token(self, request):
        query, callback = self.authorize()
        self.assertEqual(query['redirect_uri'], ['http://127.0.0.1:8769' + CALLBACK_PATH])
        self.assertEqual(query['code_challenge_method'], ['S256'])
        self.assertEqual(set(query['scope'][0].split()), set(SCOPES))
        self.assertNotIn('offline.access', query['scope'][0])
        request.side_effect = [
            {'access_token': 'synthetic-secret-token', 'token_type': 'Bearer', 'expires_in': 3600, 'scope': list(SCOPES)},
            {'data': {'id': '12345', 'username': 'reader'}},
        ]
        status = self.connection.finish(callback)
        self.assertTrue(status['connected'])
        self.assertEqual(status['account'], {'id': '12345', 'username': 'reader'})
        self.assertNotIn('synthetic-secret-token', repr(status))
        token_call = request.call_args_list[0]
        self.assertEqual(token_call.args[0], API + '/2/oauth2/token')
        self.assertIn('code_verifier', token_call.args[1])
        self.assertEqual(self.connection.tokens['access_token'], 'synthetic-secret-token')

    @patch('x_connection._safe_json_request')
    def test_wrong_state_consumes_nothing_and_valid_state_consumes_before_exchange(self, request):
        _, callback = self.authorize()
        bad = {**callback, 'state': ['wrong']}
        with self.assertRaises(StateError):
            self.connection.finish(bad)
        request.assert_not_called()
        with patch('x_connection._safe_json_request', side_effect=StateError('safe')) as exchange:
            with self.assertRaises(StateError):
                self.connection.finish(callback)
            self.assertIsNone(self.connection.pending)
            with self.assertRaises(StateError):
                self.connection.finish(callback)
            exchange.assert_called_once()

    @patch('x_connection._safe_json_request')
    def test_missing_scope_or_wrong_catalog_account_rejected(self, request):
        self.catalog.merge_page('12345', [{'postId': '1'}], None, complete=True)
        _, callback = self.authorize()
        request.side_effect = [
            {'access_token': 'synthetic', 'token_type': 'Bearer', 'expires_in': 600, 'scope': list(SCOPES)},
            {'data': {'id': '67890', 'username': 'other'}},
        ]
        with self.assertRaises(StateError) as error:
            self.connection.finish(callback)
        self.assertEqual(error.exception.status, 409)
        self.assertFalse(self.connection.status()['connected'])

    def test_zero_default_cost_blocks_auth_begin_and_disconnect_clears_memory(self):
        with self.assertRaises(StateError):
            self.connection.begin('client-id', {})
        self.assertIsNone(self.connection.pending)
        self.connection.pending = {'state': 'synthetic'}
        self.connection.tokens = {'access_token': 'synthetic'}
        self.connection.account = {'id': '123'}
        self.connection.disconnect()
        self.assertIsNone(self.connection.pending)
        self.assertIsNone(self.connection.tokens)
        self.assertIsNone(self.connection.account)

    @patch('x_connection._safe_json_request')
    def test_missing_scopes_fails_before_billed_identity_and_preserves_old_session(self, request):
        old_tokens = {'access_token': 'synthetic-old', 'expires_at': time.time() + 60, 'scopes': list(SCOPES)}
        self.connection.tokens = old_tokens.copy()
        self.connection.account = {'id': '12345', 'username': 'original'}
        _, callback = self.authorize()
        request.return_value = {'access_token': 'synthetic-new', 'token_type': 'Bearer',
                                'expires_in': 600, 'scope': 'users.read'}
        with self.assertRaises(StateError) as error:
            self.connection.finish(callback)
        self.assertEqual(error.exception.status, 403)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(self.connection.tokens, old_tokens)
        self.assertEqual(self.connection.status()['account']['id'], '12345')

    @patch('x_connection._safe_json_request')
    def test_expired_callback_and_replayed_success_never_exchange_again(self, request):
        _, callback = self.authorize()
        self.connection.pending['expires'] = time.time() - 1
        with self.assertRaises(StateError):
            self.connection.finish(callback)
        request.assert_not_called()
        _, callback = self.authorize()  # A new, independent explicit approval.
        request.side_effect = [
            {'access_token': 'synthetic-new', 'token_type': 'Bearer', 'expires_in': 600, 'scope': list(SCOPES)},
            {'data': {'id': '12345', 'username': 'reader'}}]
        self.connection.finish(callback)
        with self.assertRaises(StateError):
            self.connection.finish(callback)
        self.assertEqual(request.call_count, 2)


class BudgetTests(unittest.TestCase):
    def test_exact_decimal_boundary_and_immutable_operation_approval(self):
        budget = BudgetLedger({'posts': 3, 'pages': 3, 'usd': .015})
        for _ in range(3):
            budget.reserve({'posts': 1, 'pages': 1})
        self.assertEqual(budget.status()['usdCeilingReserved'], .015)
        with self.assertRaises(StateError):
            budget.reserve({'posts': 1})
        with self.assertRaises(StateError):
            budget.reserve({}, {'posts': 100, 'usd': 100})

    def test_invalid_counts_and_nonfinite_money_fail_closed(self):
        for value in [True, -1, 1.5, '1']:
            with self.subTest(value=value), self.assertRaises(StateError):
                BudgetLedger({'users': value, 'usd': 1})
        for value in [True, -1, float('nan'), float('inf'), '1']:
            with self.subTest(value=value), self.assertRaises(StateError):
                BudgetLedger({'users': 1, 'usd': value})

    def test_concurrent_reservations_cannot_exceed_single_allowance(self):
        from concurrent.futures import ThreadPoolExecutor
        budget = BudgetLedger({'users': 1, 'usd': .01})
        def reserve_once(_):
            try:
                budget.reserve({'users': 1})
                return True
            except StateError:
                return False
        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(reserve_once, range(8)))
        self.assertEqual(sum(outcomes), 1)
        self.assertEqual(budget.status()['reserved']['users'], 1)


if __name__ == '__main__':
    unittest.main()

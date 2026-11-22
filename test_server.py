import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from server import App
from triage_state import StateError, Store


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = App.__new__(App)
        self.app.articles = [{'id': 'a', 'url': 'https://x.com/u/status/1'}, {'id': 'b', 'url': 'https://x.com/u/status/2'}, {'id': 'c', 'url': 'https://x.com/u/status/3'}]
        self.app.snapshot = {'fetchedAt': 'snapshot'}
        self.app.store = Store(Path(self.temp.name) / 'state.json', self.app.articles)
        self.app.operation_lock = threading.Lock()
        self.app.connection = Mock()
        self.app.connection.status.return_value = {'connected': True}
        self.app.x_connection = Mock()
        self.app.x_connection.status.return_value = {'ready': True, 'connected': False, 'account': None}
        self.app.karakeep = Mock()
        self.app.karakeep.status.return_value = {'connected': False}
        self.app.store.decide(0, ['a', 'b'], 'remove')

    def remote(self, ids):
        return {'starredIds': ids, 'checkedAt': 'now'}

    def test_only_selected_still_saved_ids_are_written(self):
        self.app.connection.call.side_effect = [self.remote(['a', 'c']), self.remote(['c'])]
        result = self.app.sync(1, ['a', 'b'])
        self.assertEqual(self.app.connection.call.call_args_list[1].args, ('remove', ['a']))
        self.assertEqual(result['result']['confirmed'], ['a', 'b'])
        self.assertEqual(result['result']['unconfirmed'], [])

    def test_unselected_and_stale_requests_never_reach_connection(self):
        for version, ids in [(0, ['a']), (1, ['c'])]:
            with self.assertRaises(StateError):
                self.app.sync(version, ids)
        self.app.connection.call.assert_not_called()

    def test_write_timeout_reconciles_without_retrying_write(self):
        self.app.connection.call.side_effect = [self.remote(['a', 'b', 'c']), StateError('timeout', 502), self.remote(['b', 'c'])]
        result = self.app.sync(1, ['a', 'b'])
        self.assertEqual([c.args[0] for c in self.app.connection.call.call_args_list], ['read', 'remove', 'read'])
        self.assertEqual(result['result']['confirmed'], ['a'])
        self.assertEqual(result['result']['unconfirmed'], ['b'])
        self.assertEqual([a['id'] for a in self.app.store.preview()['items']], ['b'])

    def test_unverifiable_result_preserves_pending_choices(self):
        self.app.connection.call.side_effect = [self.remote(['a', 'b']), StateError('timeout'), StateError('offline')]
        with self.assertRaises(StateError):
            self.app.sync(1, ['a'])
        self.assertEqual(self.app.store.view()['version'], 1)
        self.assertEqual(self.app.store.preview()['count'], 2)

    def test_read_refresh_keeps_local_undo(self):
        self.app.connection.call.return_value = self.remote(['a', 'b', 'c'])
        self.app.refresh()
        self.app.store.undo(2)
        self.assertEqual(self.app.store.view()['decisions'], {})

    def test_partial_sync_undo_only_affects_unremoved_articles(self):
        self.app.connection.call.side_effect = [self.remote(['a', 'b', 'c']), self.remote(['b', 'c'])]
        self.app.sync(1, ['a', 'b'])
        self.app.store.undo(2)
        decisions = self.app.store.view()['decisions']
        self.assertEqual(decisions['a']['choice'], 'remove')
        self.assertNotIn('b', decisions)

    def test_fetch_x_rejects_non_eligible_and_persists_cache(self):
        self.app.eligible_ids = {'a'}
        self.app.x_content_path = Path(self.temp.name) / 'x-content.json'
        self.app.x_content = {'schemaVersion': 1, 'entries': {}}
        with patch('server.fetch_x_content', return_value={'status': 'partial', 'text': 'raw'}):
            self.app.fetch_x('a')
            with self.assertRaises(StateError):
                self.app.fetch_x('b')
        saved = json.loads(self.app.x_content_path.read_text())
        self.assertEqual(saved['entries']['a']['status'], 'partial')
        self.assertEqual(saved['entries']['a']['text'], 'raw')

    def test_explicit_inventory_checks_keep_last_success_on_failure(self):
        connection = Mock()
        connection.status.return_value = {'connected': True}
        self.app.connection = connection
        connection.read_later_inventory.return_value = {
            'count': 41, 'coverage': 'complete', 'checkedAt': 'source-check'}
        result = self.app.check_source_inventory('inoreader')
        self.assertEqual(result['sourceInventory']['inoreader']['count'], 41)
        self.assertIsNone(result['sourceInventory']['x']['count'])
        before = self.app.store.view()
        self.app.payload()
        self.assertEqual(self.app.store.view(), before)
        connection.read_later_inventory.side_effect = StateError('offline', 502)
        with self.assertRaises(StateError):
            self.app.check_source_inventory('inoreader')
        self.assertEqual(self.app.payload()['sourceInventory']['inoreader'], {
            'count': 41, 'checkedAt': 'source-check', 'coverage': 'complete', 'state': 'stale'})
        self.assertEqual(set(self.app.store.view()['decisions']), {'a', 'b'})

    def test_inventory_first_failure_is_unknown_and_invalid_source_does_not_call_connection(self):
        connection = Mock()
        connection.status.return_value = {'connected': True}
        self.app.connection = connection
        connection.read_later_inventory.side_effect = StateError('offline', 502)
        with self.assertRaises(StateError):
            self.app.check_source_inventory('inoreader')
        self.assertEqual(self.app.payload()['sourceInventory']['inoreader'], {
            'count': None, 'checkedAt': None, 'coverage': 'unknown', 'state': 'unknown'})
        connection.read_later_inventory.reset_mock()
        with self.assertRaises(StateError):
            self.app.check_source_inventory('other')
        connection.read_later_inventory.assert_not_called()

    def test_adopting_an_old_x_scan_cannot_clear_known_membership_changes(self):
        self.app.store.x_account_id = 'acct'
        self.app.store.apply_x_remote('acct', ['2'], 'newer-check')
        self.app.x_bookmarks = Mock()
        self.app.x_bookmarks.source_inventory.return_value = {
            'count': 2, 'coverage': 'complete', 'checkedAt': 'old-check'}
        self.app.x_bookmarks.status.return_value = {}
        snapshot = self.app.check_source_inventory('x')['sourceInventory']['x']
        self.assertEqual(snapshot['count'], 2)
        self.assertEqual(snapshot['state'], 'stale')

    def test_payload_source_inventory_is_independent_from_local_catalog_and_triage(self):
        self.app.store.record_source_inventory('x', 2, 'partial', 'x-check')
        self.app.x_bookmarks = Mock()
        self.app.x_bookmarks.status.return_value = {'count': 3, 'inventorySeenCount': 2}
        self.app.x_bookmarks.source_inventory.return_value = {'count': 2, 'coverage': 'partial', 'checkedAt': None}
        self.app.x_content_path = None
        self.app.x_content = {'entries': {}}
        self.app.snapshot = {'fetchedAt': 'snapshot'}
        payload = self.app.payload()
        self.assertIsNone(payload['sourceInventory']['inoreader']['count'])
        self.assertEqual(payload['sourceInventory']['x']['state'], 'partial')
        self.assertEqual(payload['xImport']['count'], 3)
        self.assertEqual(payload['sourceInventory']['inoreader']['state'], 'unknown')
        self.assertEqual(set(self.app.store.view()['decisions']), {'a', 'b'})

    def test_fetch_x_keeps_successful_enrichment_without_refetch(self):
        self.app.eligible_ids = {'a'}
        self.app.x_content_path = Path(self.temp.name) / 'x-content.json'
        entry = {'status': 'read', 'summary': 'Verified summary', 'text': 'source'}
        self.app.x_content = {'schemaVersion': 1, 'entries': {'a': entry}}
        with patch('server.fetch_x_content', side_effect=RuntimeError('offline')) as fetch:
            self.app.fetch_x('a')
        fetch.assert_not_called()
        self.assertEqual(self.app.x_content['entries']['a'], entry)

    def test_failed_cache_write_keeps_memory_unchanged(self):
        self.app.eligible_ids = {'a'}
        self.app.x_content_path = Path(self.temp.name) / 'x-content.json'
        self.app.x_content = {'schemaVersion': 1, 'entries': {}}
        with patch('server.fetch_x_content', return_value={'status': 'partial', 'text': 'source'}), \
                patch('server.save_x_content', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.app.fetch_x('a')
        self.assertEqual(self.app.x_content['entries'], {})

    def test_x_operation_requires_explicit_budget_and_server_binds_catalog(self):
        from x_connection import XConnection
        catalog = Mock()
        catalog.data = {'accountId': 'acct-1'}
        connection = XConnection('http://127.0.0.1:8769', catalog)
        connection.tokens = {'access_token': 'synthetic', 'expires_at': 10**20, 'scopes': []}
        connection.account = {'id': 'acct-2'}
        with self.assertRaises(StateError):
            connection.new_operation({'posts': 1, 'usd': 1})
        connection.account = {'id': 'acct-1'}
        from x_connection import SCOPES
        connection.tokens['scopes'] = list(SCOPES)
        with self.assertRaises(StateError):
            connection.new_operation({}).reserve({'posts': 1})


if __name__ == '__main__':
    unittest.main()

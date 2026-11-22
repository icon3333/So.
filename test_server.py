import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from server import App
from triage_state import StateError, Store
from x_bookmarks import XBookmarkCatalog


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
        self.app.karakeep = Mock()
        self.app.karakeep.status.return_value = {'connected': False}
        self.app.store.decide(0, ['a', 'b'], 'remove')

    def remote(self, ids):
        return {'starredIds': ids, 'checkedAt': 'now'}

    def test_inoreader_inventory_counts_all_unique_ids_and_keeps_last_success_on_failure(self):
        before = self.app.store.view()
        self.app.connection.call.side_effect = [self.remote(['a', 'a', 'outside-catalog']), StateError('offline', 502)]
        self.assertEqual(self.app.payload()['sourceInventory'], {})
        self.app.connection.call.assert_not_called()
        checked = self.app.check_source_inventory('inoreader')
        self.assertEqual(checked['sourceInventory']['inoreader'],
                         {'count': 2, 'coverage': 'complete', 'checkedAt': 'now'})
        self.assertEqual(checked['state'], before)
        with self.assertRaises(StateError):
            self.app.check_source_inventory('inoreader')
        self.assertTrue(self.app.payload()['sourceInventory']['inoreader']['stale'])
        self.assertEqual(self.app.payload()['sourceInventory']['inoreader']['count'], 2)
        self.assertEqual([call.args for call in self.app.connection.call.call_args_list], [('read',), ('read',)])

    def test_x_inventory_adopts_legacy_partial_scan_without_connections(self):
        path = Path(self.temp.name) / 'x-bookmarks.json'
        catalog = XBookmarkCatalog(path)
        catalog.merge_page('account', [{'postId': '1'}, {'postId': '2'}], 'old-cursor',
                           seen_ids=['1', '2'], begin=True)
        catalog.merge_page('account', [{'postId': '3'}], 'cursor', seen_ids=['3'], begin=True)
        legacy = json.loads(path.read_text())
        legacy.pop('inventoryCheckedAt', None)
        path.write_text(json.dumps(legacy))
        self.app.x_bookmarks = XBookmarkCatalog(path)
        self.app.x_connection = Mock()
        self.app.x_connection.status.return_value = {'connected': False}
        before = self.app.store.path.read_bytes()
        result = self.app.check_source_inventory('x')
        item = result['sourceInventory']['x']
        self.assertEqual((item['count'], item['coverage'], item['checkedAt']), (1, 'partial', None))
        self.assertEqual(self.app.store.path.read_bytes(), before)
        self.assertEqual(json.loads(path.read_text()), legacy)
        self.app.connection.call.assert_not_called()
        self.app.x_connection.bookmarks_page.assert_not_called()

    def test_legacy_empty_scan_is_unknown_but_timestamped_empty_scan_is_zero(self):
        path = Path(self.temp.name) / 'x-bookmarks.json'
        catalog = XBookmarkCatalog(path)
        catalog.merge_page('account', [], None, complete=True, begin=True)
        self.app.x_bookmarks = catalog
        self.assertEqual(self.app.check_source_inventory('x')['sourceInventory']['x']['count'], 0)
        legacy = json.loads(path.read_text())
        legacy.pop('inventoryCheckedAt')
        legacy.pop('inventorySeen')
        path.write_text(json.dumps(legacy))
        self.app.x_bookmarks = XBookmarkCatalog(path)
        with self.assertRaisesRegex(StateError, 'kein auswertbarer X-API-Scan'):
            self.app.check_source_inventory('x')

    def test_unreadable_source_inventory_does_not_block_the_app(self):
        self.app.store.inventory_path.mkdir()
        self.assertEqual(self.app.payload()['sourceInventory'], {})
        self.assertEqual(self.app.payload()['state']['version'], 1)

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


if __name__ == '__main__':
    unittest.main()

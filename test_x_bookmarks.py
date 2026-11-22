import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from server import App
from x_bookmarks import XBookmarkCatalog


class XBookmarkCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.legacy = {
            'fetchedAt': 'legacy-fetch',
            'articles': [
                {'id': 'ir:tweet-7', 'ref': 'ref-7', 'url': 'https://x.com/u/status/7',
                 'publisher': {'title': 'X via Inoreader'}, 'title': 'Original', 'excerpt': 'Legacy'},
            ],
            'sameUrlGroups': [],
        }
        (self.root / 'articles.json').write_bytes(json.dumps(self.legacy, ensure_ascii=False).encode())
        self.state = {
            'version': 4, 'decisions': {'ir:tweet-7': {'choice': 'keep', 'updatedAt': 'then'}},
            'history': [{'ir:tweet-7': None}], 'karakeepSaved': {
                'ir:tweet-7': {'bookmarkId': 'receipt-1', 'url': 'https://x.com/u/status/7', 'savedAt': 'then'}},
            'remote': {'checkedAt': 'legacy-check', 'starredIds': ['ir:tweet-7']},
        }
        (self.root / 'state.json').write_text(json.dumps(self.state))

    def test_characterize_legacy_identity_receipts_state_and_article_bytes(self):
        before = (self.root / 'articles.json').read_bytes()
        app = App(self.root, 'http://127.0.0.1:8769')
        self.assertEqual([a['id'] for a in app.articles], ['ir:tweet-7'])
        self.assertEqual(app.articles[0]['ref'], 'ref-7')
        self.assertEqual(app.articles[0]['publisher'], self.legacy['articles'][0]['publisher'])
        self.assertEqual(app.store.view(), self.state)
        self.assertEqual((self.root / 'articles.json').read_bytes(), before)
        self.assertFalse((self.root / 'x-bookmarks.json').exists())

    def test_characterize_reopen_preserves_legacy_decision_and_receipt(self):
        App(self.root, 'http://127.0.0.1:8769')
        again = App(self.root, 'http://127.0.0.1:8769')
        self.assertEqual(again.store.view()['decisions']['ir:tweet-7'], self.state['decisions']['ir:tweet-7'])
        self.assertEqual(again.store.view()['karakeepSaved'], self.state['karakeepSaved'])

    def test_same_url_keeps_distinct_origin_ids_and_decisions(self):
        catalog = XBookmarkCatalog(self.root / 'x-bookmarks.json')
        catalog.merge_page('acct-1', [{'postId': '7', 'url': self.legacy['articles'][0]['url'],
                                      'title': 'Native', 'text': 'raw'}], 'cursor-next')
        app = App(self.root, 'http://127.0.0.1:8769')
        self.assertEqual({a['id'] for a in app.articles}, {'ir:tweet-7', 'x:acct-1:7'})
        app.store.decide(4, ['x:acct-1:7'], 'remove')
        self.assertEqual(app.store.view()['decisions']['ir:tweet-7']['choice'], 'keep')
        self.assertEqual(app.store.view()['decisions']['x:acct-1:7']['choice'], 'remove')

    def test_catalog_import_restart_repeat_page_and_tombstone_history(self):
        catalog = XBookmarkCatalog(self.root / 'x-bookmarks.json')
        catalog.merge_page('acct-1', [{'postId': '7', 'url': 'https://x.com/u/status/7', 'text': 'raw'}], 'c1')
        app = App(self.root, 'http://127.0.0.1:8769')
        app.store.decide(4, ['x:acct-1:7'], 'keep')
        catalog.merge_page('acct-1', [{'postId': '7', 'url': 'https://x.com/u/status/7', 'text': 'updated'}], None, complete=True)
        reopened = App(self.root, 'http://127.0.0.1:8769')
        self.assertEqual([a['id'] for a in reopened.articles].count('x:acct-1:7'), 1)
        self.assertEqual(reopened.store.view()['decisions']['x:acct-1:7']['choice'], 'keep')
        self.assertEqual(reopened.x_bookmarks.data['checkpoint'], None)
        self.assertTrue(reopened.x_bookmarks.data['complete'])

    def test_failed_catalog_replace_keeps_old_page_and_cursor(self):
        catalog = XBookmarkCatalog(self.root / 'x-bookmarks.json')
        catalog.merge_page('acct-1', [{'postId': '1', 'url': 'u1'}], 'old-cursor')
        old_bytes = catalog.path.read_bytes()
        with patch('x_bookmarks.atomic_json', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                catalog.merge_page('acct-1', [{'postId': '2', 'url': 'u2'}], 'new-cursor')
        self.assertEqual(catalog.path.read_bytes(), old_bytes)
        self.assertEqual(catalog.data['checkpoint'], 'old-cursor')

    def test_partial_inventory_has_its_own_check_time_after_restart(self):
        catalog = XBookmarkCatalog(self.root / 'x-bookmarks.json')
        with patch('x_bookmarks.now', return_value='first-check'):
            catalog.merge_page('acct-1', [], None, complete=True, seen_ids=['1', '2'])
        with patch('x_bookmarks.now', return_value='second-check'):
            catalog.merge_page('acct-1', [], 'next', seen_ids=['3'], begin=True)
        self.assertEqual(catalog.source_inventory(), {
            'count': 1, 'coverage': 'partial', 'checkedAt': 'second-check'})

    def test_source_inventory_uses_seen_ids_not_catalog_size_and_only_complete_scan_is_complete(self):
        catalog = XBookmarkCatalog(self.root / 'x-bookmarks.json')
        catalog.merge_page('acct-1', [{'postId': '99'}], 'cursor', seen_ids=['1'])
        partial = catalog.source_inventory()
        self.assertEqual(partial['count'], 1)
        self.assertEqual(partial['coverage'], 'partial')
        catalog.merge_page('acct-1', [{'postId': '2'}], None, complete=True, begin=True, seen_ids=['2'])
        complete = catalog.source_inventory()
        self.assertEqual(complete['count'], 1)
        self.assertEqual(complete['coverage'], 'complete')
        self.assertTrue(complete['checkedAt'])

    def test_different_account_cannot_replace_existing_catalog(self):
        catalog = XBookmarkCatalog(self.root / 'x-bookmarks.json')
        catalog.merge_page('acct-1', [{'postId': '7', 'summary': 'Verified summary'}], 'c1')
        before = catalog.path.read_bytes()
        with self.assertRaises(ValueError):
            catalog.merge_page('acct-2', [{'postId': '8'}], None)
        self.assertEqual(catalog.path.read_bytes(), before)
        self.assertEqual(catalog.data['accountId'], 'acct-1')

    def test_additive_page_preserves_enrichment_and_updates_active_store(self):
        app = App(self.root, 'http://127.0.0.1:8769')
        before = (self.root / 'state.json').read_bytes()
        app.x_bookmarks.merge_page('acct-1', [{'postId': '7', 'summary': 'Verified summary'}], 'c1')
        app.x_bookmarks.merge_page('acct-1', [{'postId': '7', 'text': 'raw'}], None, complete=True)
        app.refresh_catalog()
        self.assertEqual(app.store.articles['x:acct-1:7']['summary'], 'Verified summary')
        self.assertEqual((self.root / 'state.json').read_bytes(), before)
        app.store.decide(4, ['x:acct-1:7'], 'remove')
        self.assertIn('x:acct-1:7', {a['id'] for a in app.store.preview()['items']})
        self.assertIn('x:acct-1:7', app.payload()['savedIds'])

    def test_native_record_never_reaches_inoreader_sync_or_public_mirror(self):
        catalog = XBookmarkCatalog(self.root / 'x-bookmarks.json')
        catalog.merge_page('acct-1', [{'postId': '7', 'url': 'https://x.com/u/status/7'}], None, complete=True)
        app = App(self.root, 'http://127.0.0.1:8769')
        app.connection = Mock()
        app.connection.status.return_value = {'connected': True}
        app.store.decide(4, ['x:acct-1:7'], 'remove')
        from triage_state import StateError
        with self.assertRaises(StateError):
            app.sync(5, ['x:acct-1:7'])
        app.connection.call.assert_not_called()
        with patch('server.fetch_x_content') as fetch:
            with self.assertRaises(StateError):
                app.fetch_x('x:acct-1:7')
        fetch.assert_not_called()

    def test_inoreader_reconcile_does_not_prune_native_undo_history(self):
        catalog = XBookmarkCatalog(self.root / 'x-bookmarks.json')
        catalog.merge_page('acct-1', [{'postId': '7', 'url': 'https://x.com/u/status/7'}], None, complete=True)
        app = App(self.root, 'http://127.0.0.1:8769')
        app.store.decide(4, ['ir:tweet-7'], 'remove')
        app.store.decide(5, ['x:acct-1:7'], 'keep')
        app.store.apply_remote(['ir:tweet-7'], 'checked')
        self.assertIn('x:acct-1:7', app.store.view()['decisions'])
        app.store.undo(7)
        self.assertNotIn('x:acct-1:7', app.store.view()['decisions'])
        self.assertEqual(app.store.view()['remote']['starredIds'], ['ir:tweet-7'])


if __name__ == '__main__':
    unittest.main()

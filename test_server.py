import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from server import App
from triage_state import StateError, Store
from http.server import ThreadingHTTPServer
from threading import Thread
from server import Handler


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

    def test_cockpit_area_assignments_persist_by_stable_item_id_and_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            articles = [
                {'id': 'article-1', 'origin': 'inoreader', 'title': 'Synthetic one', 'url': 'https://example.test/1', 'excerpt': 'Synthetic excerpt'},
                {'id': 'article-2', 'origin': 'inoreader', 'title': 'Synthetic two', 'url': 'https://example.test/2'},
            ]
            app = App.__new__(App)
            app.data_root = root
            app.area_path = root / 'cockpit-areas.json'
            app.operation_lock = threading.RLock()
            app.articles = articles
            saved_ids = {'article-1', 'article-2'}
            app.x_content = {'entries': {}}
            app.store = Mock()
            app.store.is_saved.side_effect = lambda item_id: item_id in saved_ids
            app.store.view.return_value = {'decisions': {}}
            app.snapshot = {'fetchedAt': None}
            app.x_bookmarks = Mock()
            app.x_bookmarks.articles.return_value = []
            app.x_bookmarks.data = {'accountId': None}
            app.x_content_path = root / 'x-content.json'
            app.payload = lambda: {'articles': articles, 'savedIds': list(saved_ids),
                                   'state': {'decisions': {}}, 'xContent': {'entries': {}}}
            with patch('server.local_sources', return_value={'to': {'state': 'ready', 'items': []}}):
                item_id = __import__('cockpit').item('reading', 'article-1', 'Synthetic one', 'reading')['id']
                app.assign_cockpit_area([item_id], 'Home')
                first = app.cockpit_items()['reading']['items'][0]
                self.assertEqual(first['lifeArea'], 'Home')
                self.assertEqual(first['excerpt'], 'Synthetic excerpt')
                self.assertEqual(first['excerptStatus'], 'available')
                app2 = App.__new__(App)
                app2.data_root = root
                app2.area_path = root / 'cockpit-areas.json'
                app2.operation_lock = threading.RLock()
                app2.articles = articles

                app2.x_content = {'entries': {}}
                app2.store = app.store
                app2.snapshot = app.snapshot
                app2.x_bookmarks = app.x_bookmarks
                app2.x_content_path = root / 'x-content.json'
                app2.payload = app.payload
                with patch('server.local_sources', return_value={'to': {'state': 'ready', 'items': []}}):
                    self.assertEqual(app2.cockpit_items()['reading']['items'][0]['lifeArea'], 'Home')
                    self.assertEqual([i['id'] for i in app2.cockpit_items()['reading']['items']
                                      if i['lifeArea'] == 'Home'], [item_id])

    def test_concurrent_area_assignments_are_atomic_and_do_not_lose_updates(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = App.__new__(App)
            app.data_root = Path(tmp)
            app.area_path = app.data_root / 'cockpit-areas.json'
            app.operation_lock = threading.RLock()
            ids = ['stable:a', 'stable:b']
            app.cockpit_items = lambda: {'reading': {'items': [{'id': value} for value in ids]}}
            barrier = threading.Barrier(3)
            def assign(value):
                barrier.wait()
                app.assign_cockpit_area([value], 'Home')
            threads = [threading.Thread(target=assign, args=(value,)) for value in ids]
            for thread in threads: thread.start()
            barrier.wait()
            for thread in threads: thread.join()
            self.assertFalse(any(thread.is_alive() for thread in threads))
            self.assertEqual(app.cockpit_areas(), {value: 'Home' for value in ids})
            self.assertEqual(list(Path(tmp).glob('*.tmp')) + list(Path(tmp).glob('.*.tmp')), [])

    def test_cockpit_posteo_is_only_loaded_on_explicit_endpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = App.__new__(App)
            app.data_root = Path(tmp)
            app.area_path = app.data_root / 'cockpit-areas.json'
            app.operation_lock = threading.RLock()
            app.payload = lambda: {'articles': [], 'savedIds': [], 'state': {'decisions': {}},
                                   'xContent': {'entries': {}}}
            with patch('server.local_sources', return_value={'to': {'state': 'ready', 'items': []}}), \
                    patch('server.posteo_source', side_effect=AssertionError('unexpected Posteo call')):
                app.cockpit_items = lambda: {'to': {'items': [{'id': 'desktop-id'}]}}
                self.assertIn('to', app.cockpit_items())
                # Normal cockpit GET and assignment save do not request the Posteo adapter.
                app.assign_cockpit_area(['desktop-id'], 'Home')
                self.assertEqual(app.cockpit_areas()['desktop-id'], 'Home')

    def test_production_data_root_separates_all_runtime_files_and_archive_is_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, data, archive = (Path(tmp) / x for x in ('code', 'prod_data', 'archive_data'))
            for folder in (code, data, archive): folder.mkdir()
            (code / 'index.html').write_text('synthetic-code')
            (data / 'articles.json').write_text(json.dumps({'fetchedAt':'synthetic', 'articles':[
                {'id':'prod','title':'PROD_SENTINEL','ref':'p','url':'https://example.test'}], 'sameUrlGroups':[]}))
            (archive / 'articles.json').write_text(json.dumps({'fetchedAt':'archive', 'articles':[
                {'id':'archive','title':'ARCHIVE_SENTINEL','ref':'a','url':'https://example.test'}], 'sameUrlGroups':[]}))
            app = App(code, 'http://127.0.0.1:8769', data_root=data)
            self.assertEqual([a['id'] for a in app.articles], ['prod'])
            self.assertEqual(app.store.path, data / 'state.json')
            self.assertEqual(app.x_bookmarks.path, data / 'x-bookmarks.json')
            self.assertEqual(app.x_content_path, data / 'x-content.json')
            self.assertEqual(app.connection.root, data)
            httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            setattr(httpd, 'app', app)
            thread = Thread(target=httpd.serve_forever, daemon=True); thread.start()
            self.addCleanup(httpd.server_close); self.addCleanup(httpd.shutdown)
            import urllib.request
            base = 'http://127.0.0.1:%d' % httpd.server_port
            origin = 'http://127.0.0.1:8769'
            app.origin = origin
            def request(path):
                req = urllib.request.Request(base + path, headers={'Host': '127.0.0.1:8769'})
                return urllib.request.urlopen(req)
            state = request('/api/state').read().decode()
            self.assertIn('PROD_SENTINEL', state)
            self.assertNotIn('ARCHIVE_SENTINEL', state)
            for url in ('/articles.json', '/state.json', '/archive_data/articles.json'):
                with self.assertRaises(Exception): request(url)
            self.assertEqual(request('/').read(), b'synthetic-code')
            self.assertEqual(request('/reader').read(), b'synthetic-code')
            self.assertEqual(request('/cockpit').read(), b'synthetic-code')
            self.assertEqual(request('/cockpit.html').read(), b'synthetic-code')
            app.store.decide(0, ['prod'], 'keep')
            self.assertTrue((data / 'state.json').is_file())
            self.assertEqual(set(code.iterdir()), {code / 'index.html'})
            self.assertEqual(list(archive.iterdir()), [archive / 'articles.json'])

    def test_home_reader_and_cockpit_aliases_share_index_while_settings_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'cockpit.html').write_text('Legacy cockpit')
            (root / 'index.html').write_text('Reader')
            app = App.__new__(App)
            app.root = root
            app.origin = 'http://127.0.0.1:8769'
            httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            setattr(httpd, 'app', app)
            thread = Thread(target=httpd.serve_forever, daemon=True); thread.start()
            self.addCleanup(httpd.server_close); self.addCleanup(httpd.shutdown)
            import urllib.request
            base = 'http://127.0.0.1:%d' % httpd.server_port
            def request(path):
                req = urllib.request.Request(base + path, headers={'Host': '127.0.0.1:8769'})
                return urllib.request.urlopen(req).read().decode()
            (root / 'settings.html').write_text('Settings')
            self.assertEqual(request('/'), 'Reader')
            self.assertEqual(request('/index.html'), 'Reader')
            self.assertEqual(request('/reader'), 'Reader')
            for alias in ('/cockpit', '/cockpit.html'):
                self.assertEqual(request(alias), 'Reader')
            self.assertEqual(request('/settings'), 'Settings')

    def test_production_launcher_rejects_missing_bad_or_checkout_data_directory(self):
        script = Path(__file__).with_name('server.py')
        env = dict(os.environ); env.pop('SO_DATA_DIR', None)
        missing = subprocess.run([sys.executable, str(script)], env=env, capture_output=True, text=True)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn('SO_DATA_DIR', missing.stderr)
        env['SO_DATA_DIR'] = '.'
        checkout = subprocess.run([sys.executable, str(script)], env=env, capture_output=True, text=True)
        self.assertNotEqual(checkout.returncode, 0)
        self.assertIn('SO_DATA_DIR', checkout.stderr)
        env['SO_DATA_DIR'] = 'relative-data'
        relative = subprocess.run([sys.executable, str(script)], env=env, capture_output=True, text=True)
        self.assertNotEqual(relative.returncode, 0)

    def test_desktop_launcher_refuses_missing_or_relative_data_dir_before_existing_instance(self):
        from unittest.mock import patch
        import start
        with patch.dict(os.environ, {'SO_DATA_DIR': 'relative-data'}), patch('start.running') as running:
            with self.assertRaises(RuntimeError): start.main()
            running.assert_not_called()

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

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server import App
from triage_state import StateError
from x_connection import SCOPES


class XImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'articles.json').write_text(json.dumps({'fetchedAt': 'fixture', 'articles': [], 'sameUrlGroups': []}))
        self.app = App(self.root, 'http://127.0.0.1:8769')
        self.app.x_connection.tokens = {'access_token': 'fixture-token', 'expires_at': 10**20, 'scopes': list(SCOPES)}
        self.app.x_connection.account = {'id': '12345', 'username': 'reader'}
        self.approval = {'posts': 5, 'users': 5, 'pages': 5, 'usd': .1}

    def page(self, posts, cursor=None, **extra):
        return {'data': posts, 'meta': ({'next_token': cursor} if cursor else {}), **extra}

    def test_manual_paged_import_restart_keeps_decisions_enrichment_and_content(self):
        responses = [self.page([{'id': '101', 'text': 'first raw body', 'author_id': 'u1'}], 'next'),
                     self.page([{'id': '102', 'text': 'second raw body'}], 'last'),
                     self.page([{'id': '101', 'text': 'first refreshed'}], 'tail'),
                     self.page([{'id': '102', 'text': 'second refreshed'}], None)]
        with patch('x_connection._safe_json_request', side_effect=responses) as request:
            first = self.app.import_x_bookmarks(self.approval, max_pages=2, max_results=1)
            self.assertEqual(first['xImport']['checkpoint'], 'last')
            self.assertFalse(first['xImport']['complete'])
            self.app.store.decide(self.app.store.view()['version'], ['x:12345:101'], 'keep')
            self.app.x_bookmarks.merge_page('12345', [{'postId': '101', 'summary': 'Local Hermès summary'}], 'last')
            second = self.app.import_x_bookmarks(self.approval, max_pages=2, max_results=1)
        self.assertTrue(second['xImport']['complete'])
        self.assertEqual(set(self.app.store.articles), {'x:12345:101', 'x:12345:102'})
        self.assertEqual(self.app.store.view()['decisions']['x:12345:101']['choice'], 'keep')
        self.assertEqual(self.app.store.articles['x:12345:101']['summary'], 'Local Hermès summary')
        self.assertEqual(self.app.store.articles['x:12345:101']['text'], 'first refreshed')
        self.assertEqual(request.call_count, 4)
        self.assertEqual(request.call_args_list[1].args[0].split('pagination_token=')[1], 'next')

    def test_budget_failure_does_not_call_transport_or_advance_catalog(self):
        with patch('x_connection._safe_json_request') as request:
            with self.assertRaises(StateError):
                self.app.import_x_bookmarks({'posts': 0, 'pages': 0, 'usd': 0}, max_pages=1, max_results=1)
        request.assert_not_called()
        self.assertIsNone(self.app.x_bookmarks.data['accountId'])

    def test_partial_api_error_never_proves_absence_or_complete(self):
        self.app.x_bookmarks.merge_page('12345', [{'postId': '99', 'text': 'existing'}], None, complete=True)
        response = self.page([{'id': '101', 'text': 'returned'}], 'next', errors=[{'title': 'one post unavailable'}])
        with patch('x_connection._safe_json_request', return_value=response):
            result = self.app.import_x_bookmarks(self.approval, max_pages=1, max_results=1)
        self.assertFalse(result['xImport']['complete'])
        self.assertIn('x:12345:99', self.app.store.articles)
        self.assertIn('x:12345:101', self.app.store.articles)
        self.assertNotIn('x:12345:99', self.app.store.view().get('xRemote', {}).get('starredIds', []))

    def test_invalid_cursor_requires_explicit_restart_and_new_approval(self):
        self.app.x_bookmarks.merge_page('12345', [{'postId': '101', 'text': 'existing'}], 'invalid')
        with patch('x_connection._safe_json_request', return_value={'errors': [{'title': 'Invalid pagination token'}]}) as request:
            result = self.app.import_x_bookmarks(self.approval, max_pages=1, max_results=1)
            self.assertFalse(result['xImport']['complete'])
            self.assertTrue(result['xImport']['restartRequired'])
            self.assertEqual(self.app.x_bookmarks.data['checkpoint'], 'invalid')
            self.assertEqual(request.call_count, 1)
            with self.assertRaises(StateError):
                self.app.import_x_bookmarks(self.approval, max_pages=1, max_results=1)
            restarted = self.app.import_x_bookmarks(self.approval, max_pages=1, max_results=1, restart=True)
        self.assertFalse(restarted['xImport']['complete'])
        self.assertEqual(request.call_count, 2)
        self.assertIn('x:12345:101', self.app.store.articles)

    def test_account_change_stops_before_request_and_does_not_apply_full_absence(self):
        self.app.x_bookmarks.merge_page('12345', [{'postId': '99'}], None, complete=True)
        self.app.store.x_account_id = '12345'
        self.app.refresh_catalog()
        self.app.x_connection.account = {'id': '54321', 'username': 'other'}
        with patch('x_connection._safe_json_request') as request:
            with self.assertRaises(StateError):
                self.app.import_x_bookmarks(self.approval, max_pages=1, max_results=1)
        request.assert_not_called()
        self.assertIn('x:12345:99', self.app.store.articles)

    def test_disk_failure_keeps_page_cursor_seen_pair_unchanged(self):
        self.app.x_bookmarks.merge_page('12345', [{'postId': '100'}], 'old')
        old_bytes = self.app.x_bookmarks.path.read_bytes()
        response = self.page([{'id': '101', 'text': 'new'}], 'new')
        with patch('x_connection._safe_json_request', return_value=response), \
                patch('x_bookmarks.atomic_json', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.app.import_x_bookmarks(self.approval, max_pages=1, max_results=1)
        self.assertEqual(self.app.x_bookmarks.path.read_bytes(), old_bytes)
        self.assertEqual(self.app.x_bookmarks.data['checkpoint'], 'old')
        self.assertNotIn('x:12345:101', self.app.store.articles)

    def test_native_import_does_not_fetch_public_mirror_or_fake_author(self):
        with patch('x_connection._safe_json_request', return_value=self.page([{'id': '101', 'text': 'full raw text'}], None)), \
                patch('server.fetch_x_content') as public_fetch:
            self.app.import_x_bookmarks(self.approval, max_pages=1, max_results=1)
        item = self.app.store.articles['x:12345:101']
        self.assertEqual(item['author'], '')
        self.assertEqual(item['text'], 'full raw text')
        self.assertEqual(item['url'], 'https://x.com/i/status/101')
        public_fetch.assert_not_called()

    def test_post_import_local_enrichment_reload_appears_without_network(self):
        with patch('x_connection._safe_json_request', return_value=self.page([{'id': '101', 'text': 'raw'}], None)):
            self.app.import_x_bookmarks(self.approval, max_pages=1, max_results=1)
        cache = {'schemaVersion': 1, 'entries': {'x:12345:101': {'summary': 'Hermes local', 'status': 'read'}}}
        self.app.x_content_path.write_text(json.dumps(cache))
        self.assertEqual(self.app.payload()['xContent']['entries']['x:12345:101']['summary'], 'Hermes local')

    def test_budget_shrinks_page_and_returns_partial_before_exhaustion(self):
        with patch('x_connection._safe_json_request', return_value=self.page([{'id': '101', 'text': 'raw'}], 'next')) as request:
            result = self.app.import_x_bookmarks({'posts': 1, 'users': 1, 'pages': 5, 'usd': .015}, max_pages=5)
        self.assertEqual(request.call_count, 1)
        self.assertIn('max_results=1', request.call_args.args[0])
        self.assertEqual(result['xImport']['partialReason'], 'budget_exhausted')
        self.assertEqual(result['xImport']['checkpoint'], 'next')
        self.assertFalse(result['xImport']['complete'])

    def test_session_change_during_response_does_not_publish_unbound_page(self):
        def changed_session(*args, **kwargs):
            self.app.x_connection.tokens = {**self.app.x_connection.tokens, 'access_token': 'other-fixture-token'}
            return self.page([{'id': '101', 'text': 'raw'}])
        with patch('x_connection._safe_json_request', side_effect=changed_session) as request:
            result = self.app.import_x_bookmarks(self.approval, max_pages=2, max_results=1)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(result['xImport']['partialReason'], 'session_changed')
        self.assertNotIn('x:12345:101', self.app.store.articles)
        self.assertIsNone(self.app.store.view().get('xRemote', {}).get('starredIds'))

    def test_invalid_post_never_confirms_complete_inventory(self):
        self.app.x_bookmarks.merge_page('12345', [{'postId': '99'}], None, complete=True)
        self.app.refresh_catalog()
        with patch('x_connection._safe_json_request', return_value=self.page([{'id': 'not-a-post'}])):
            result = self.app.import_x_bookmarks(self.approval, max_pages=1, max_results=1)
        self.assertFalse(result['xImport']['complete'])
        self.assertTrue(self.app.store.is_saved('x:12345:99'))

    def test_repeated_cursor_is_detected_across_explicit_resumptions(self):
        with patch('x_connection._safe_json_request', side_effect=[
            self.page([{'id': '101'}], 'c1'), self.page([{'id': '102'}], 'c2'),
            self.page([{'id': '103'}], 'c1')]) as request:
            for _ in range(2):
                self.app.import_x_bookmarks(self.approval, max_pages=1, max_results=1)
            result = self.app.import_x_bookmarks(self.approval, max_pages=1, max_results=1)
        self.assertEqual(request.call_count, 3)
        self.assertTrue(result['xImport']['restartRequired'])
        self.assertFalse(result['xImport']['complete'])
        self.assertEqual(result['xImport']['checkpoint'], 'c2')

    def test_missing_protected_flag_is_not_assumed_public(self):
        post = {'id': '101', 'text': 'raw', 'author_id': '22'}
        item = self.app._normalize_x_post(post, '12345', 'reader', {'22': {'id': '22', 'username': 'known'}})
        self.assertEqual(item['visibility'], 'unknown')

    def test_http_import_route_has_same_origin_and_custom_header_guards(self):
        from http.client import HTTPConnection
        from http.server import ThreadingHTTPServer
        from server import Handler
        import threading
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.addCleanup(httpd.server_close)
        origin = 'http://127.0.0.1:%s' % httpd.server_port
        self.app.origin = origin
        httpd.app = self.app
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(httpd.shutdown)
        def post(headers):
            connection = HTTPConnection('127.0.0.1', httpd.server_port, timeout=2)
            connection.request('POST', '/api/x/import', json.dumps({**self.approval, 'maxPages': 1, 'maxResults': 1}), headers)
            response = connection.getresponse()
            payload = json.loads(response.read())
            connection.close()
            return response.status, payload
        good = {'Content-Type': 'application/json', 'Origin': origin, 'X-Triage-Request': '1'}
        with patch('x_connection._safe_json_request', return_value=self.page([{'id': '101', 'text': 'fixture'}])) as request:
            self.assertEqual(post({**good, 'Origin': 'https://foreign.example'})[0], 403)
            request.assert_not_called()
            status, result = post(good)
        self.assertEqual(status, 200)
        self.assertTrue(result['xImport']['complete'])
        self.assertEqual(result['articles'][0]['id'], 'x:12345:101')


if __name__ == '__main__':
    unittest.main()

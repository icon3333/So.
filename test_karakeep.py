import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from karakeep_connection import KarakeepConnection
from server import App
from triage_state import StateError, Store


class KarakeepTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'state.json'
        self.app = App.__new__(App)
        self.app.articles = [{'id': 'a', 'url': 'https://example.com/article'}]
        self.app.snapshot = {'fetchedAt': 'snapshot'}
        self.app.store = Store(self.path, self.app.articles)
        self.app.operation_lock = threading.Lock()
        self.app.connection = Mock()
        self.app.connection.status.return_value = {'connected': False}
        self.app.karakeep = KarakeepConnection()
        self.bookmark = {'id': 'bookmark1', 'content': {'type': 'link', 'url': 'https://example.com/article'}}

    def connect(self, request):
        request.return_value = {'id': 'synthetic-user'}
        self.app.karakeep.connect('synthetic-key')
        request.reset_mock()

    @patch('karakeep_connection.request_json')
    def test_save_readback_then_local_removal_and_no_inoreader_call(self, request):
        self.connect(request)
        request.side_effect = [self.bookmark, self.bookmark]
        result = self.app.save_to_karakeep(0, 'a')
        self.assertEqual([c.args[0] for c in request.call_args_list], ['/bookmarks', '/bookmarks/bookmark1'])
        self.assertEqual(result['state']['decisions']['a']['choice'], 'remove')
        self.assertEqual(result['state']['karakeepSaved']['a']['bookmarkId'], 'bookmark1')
        self.app.connection.call.assert_not_called()
        self.assertNotIn('synthetic-key', json.dumps(result))
        self.assertNotIn('synthetic-key', self.path.read_text())

    @patch('karakeep_connection.request_json')
    def test_failed_or_mismatched_readback_does_not_mark(self, request):
        self.connect(request)
        for failure in [StateError('offline', 502), {'id': 'bookmark1', 'content': {'type': 'link', 'url': 'https://unrelated.example/'}}]:
            request.side_effect = [self.bookmark, failure]
            with self.assertRaises(StateError):
                self.app.save_to_karakeep(0, 'a')
            self.assertEqual(self.app.store.view()['decisions'], {})
            self.assertEqual(self.app.store.view()['karakeepSaved'], {})
        self.app.connection.call.assert_not_called()

    @patch('karakeep_connection.request_json')
    def test_timeout_and_unauthorized_do_not_mark_or_retry(self, request):
        for status in [502, 401]:
            self.connect(request)
            request.side_effect = StateError('failed', status)
            with self.assertRaises(StateError):
                self.app.save_to_karakeep(0, 'a')
            self.assertEqual(request.call_count, 1)
            self.assertEqual(self.app.store.view()['version'], 0)
            request.side_effect = None

    @patch('karakeep_connection.request_json')
    def test_stale_or_unknown_article_never_reaches_network(self, request):
        self.connect(request)
        for version, article_id, status in [(1, 'a', 409), (0, 'unknown', 400)]:
            with self.assertRaises(StateError) as error:
                self.app.save_to_karakeep(version, article_id)
            self.assertEqual(error.exception.status, status)
            request.assert_not_called()

    @patch('karakeep_connection.request_json')
    def test_undo_reverts_vormerkung_but_keeps_saved_receipt(self, request):
        self.connect(request)
        request.side_effect = [self.bookmark, self.bookmark]
        self.app.save_to_karakeep(0, 'a')
        self.app.store.undo(1)
        again = Store(self.path, self.app.articles)
        self.assertEqual(again.view()['decisions'], {})
        self.assertEqual(again.view()['karakeepSaved']['a']['bookmarkId'], 'bookmark1')

    @patch('karakeep_connection.request_json')
    def test_local_write_failure_does_not_partially_mark_saved(self, request):
        self.connect(request)
        request.side_effect = [self.bookmark, self.bookmark]
        with patch('triage_state.atomic_json', side_effect=OSError('disk full')):
            with self.assertRaises(StateError) as error:
                self.app.save_to_karakeep(0, 'a')
        self.assertIn('In Karakeep gespeichert', str(error.exception))
        self.assertEqual(self.app.store.view()['version'], 0)
        self.assertEqual(self.app.store.view()['decisions'], {})
        self.assertEqual(self.app.store.view()['karakeepSaved'], {})

    def test_old_state_loads_without_rewriting_it(self):
        old = self.app.store.view()
        old.pop('karakeepSaved')
        self.path.write_text(json.dumps(old))
        again = Store(self.path, self.app.articles)
        self.assertEqual(again.view()['karakeepSaved'], {})
        self.assertNotIn('karakeepSaved', json.loads(self.path.read_text()))

    @patch('karakeep_connection.request_json')
    def test_invalid_connection_is_not_reported_connected(self, request):
        request.side_effect = StateError('unauthorized', 401)
        with self.assertRaises(StateError):
            self.app.karakeep.connect('synthetic-key')
        self.assertFalse(self.app.karakeep.status()['connected'])


if __name__ == '__main__':
    unittest.main()

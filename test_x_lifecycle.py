"""Offline X lifecycle checks: all transport is replaced; only temporary files are used."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from server import App
from triage_state import StateError
from x_connection import SCOPES


def allowance(posts=1000, pages=1000, usd=5, mutations=0, verification_posts=0):
    return dict(posts=posts, users=0, pages=pages, usd=usd,
                mutations=mutations, verification_posts=verification_posts)


def page(posts, next_token=None):
    value = {'data': [{'id': str(i), 'text': text} for i, text in posts],
             'meta': {'result_count': len(posts)}}
    if next_token:
        value['meta']['next_token'] = next_token
    return value


class XLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        (root / 'articles.json').write_text(json.dumps({
            'fetchedAt': 'fixture', 'articles': [
                {'id': 'ir1', 'ref': '001', 'title': 'Inoreader fixture',
                 'url': 'https://example.org', 'topic': 'Sonstiges'}]}))
        self.app = App(root, 'http://127.0.0.1:9876')
        self.app.x_connection.account = {'id': '42', 'username': 'fixture'}
        self.app.x_connection.tokens = {'access_token': 'synthetic-test-value',
            'expires_at': time.time() + 600, 'scopes': list(SCOPES)}
        self.transport = patch('x_connection._safe_json_request')
        self.http = self.transport.start()
        self.addCleanup(self.transport.stop)

    def import_pages(self, responses, **limits):
        self.http.side_effect = responses
        return self.app.import_x_bookmarks(allowance(**limits), max_pages=1000)

    def test_all_pages_receive_topics_and_do_not_modify_inoreader(self):
        result = self.import_pages([page([(1, 'Python coding with AI agents')], 'next'),
                                    page([(2, 'Strength training and neck mobility')])])
        native = {a['postId']: a for a in result['articles'] if a['origin'] == 'x'}
        self.assertEqual(native['1']['topic'], 'KI & Agenten')
        self.assertEqual(native['2']['topic'], 'Gesundheit & Training')
        self.assertTrue(result['xImport']['complete'])
        self.assertEqual(self.http.call_count, 2)
        self.assertIn('pagination_token=next', self.http.call_args_list[1].args[0])
        self.assertEqual(result['articles'][0]['id'], 'ir1')

    def test_import_missing_x_uses_only_unimported_verified_ids_and_keeps_history(self):
        self.import_pages([page([(1, 'Already imported')])])
        self.app.store.apply_x_remote('42', ['1', '2', '3'], 'checked')
        before = self.app.x_bookmarks.articles()[0]
        with self.assertRaises(StateError):
            self.app.import_missing('x', 'old check', {'posts': 2, 'usd': .01})
        self.http.reset_mock(side_effect=True)
        self.http.return_value = page([(2, 'Python code'), (88, 'Unexpected response')])
        result = self.app.import_missing('x', 'checked', {'posts': 2, 'usd': .01})
        self.assertIn('/2/tweets?ids=2%2C3', self.http.call_args.args[0])
        self.assertEqual(result['importResult'], {'count': 1, 'requested': 2})
        self.assertEqual(self.app.x_bookmarks.articles()[0], before)
        self.assertEqual(result['state']['xRemote']['starredIds'], ['1', '2', '3'])
        self.assertIn('x:42:2', result['savedIds'])
        self.assertNotIn('x:42:88', result['savedIds'])

    def test_inoreader_import_is_additive_and_survives_restart(self):
        self.app.store.decide(0, ['ir1'], 'keep')
        self.app.store.apply_remote(['ir1', 'ir2'], 'checked')
        with patch.object(self.app.connection, 'call', return_value={
            'complete': True, 'checkedAt': 'updated', 'starredIds': ['ir1', 'ir2'],
            'articles': [{'id': 'ir2', 'title': 'New saved article', 'url': 'https://example.org/new'},
                         {'id': 'ir1', 'title': 'Must keep original title', 'url': 'https://example.org'}],
        }) as connection:
            result = self.app.import_missing('inoreader', 'checked')
            connection.assert_called_once_with('import', ['ir2'])
        self.assertEqual(result['importResult'], {'count': 1, 'requested': 1})
        self.assertEqual(result['state']['decisions']['ir1']['choice'], 'keep')
        reloaded = App(self.app.root, self.app.origin)
        self.assertEqual([a['id'] for a in reloaded.articles], ['ir1', 'ir2'])
        self.assertEqual(reloaded.articles[0]['title'], 'Inoreader fixture')
        self.assertIn('ir2', reloaded.payload()['savedIds'])

    def test_money_limit_stops_then_resumes_without_repeating_saved_page(self):
        result = self.import_pages([page([(1, 'AI agents')], 'resume')], usd=.50)
        self.assertFalse(result['xImport']['complete'])
        self.assertEqual(result['xImport']['partialReason'], 'budget_exhausted')
        self.assertEqual(self.http.call_count, 1)
        self.assertLessEqual(result['xImport']['budget']['usdCeilingReserved'], .50)
        self.http.reset_mock()
        result = self.import_pages([page([(2, 'A piano tutorial and music')])], usd=.50)
        self.assertIn('pagination_token=resume', self.http.call_args.args[0])
        self.assertEqual(result['xImport']['count'], 2)
        self.assertTrue(result['xImport']['complete'])

    def test_unknown_content_and_manual_enrichment_survive_later_imports(self):
        self.import_pages([page([(1, 'https://t.co/onlylink')])])
        self.assertEqual(self.app.x_bookmarks.articles()[0]['topic'],
                         'Inhalt nicht ausreichend erkennbar')
        # Simulate a reviewed local enrichment, not another untrusted API page.
        self.app.x_bookmarks.data['entries']['x:42:1'].update(
            topic='Familie & Alltag', summary='Reviewed locally', topicSource='local-review')
        self.import_pages([page([(1, 'Python code and AI agents')])])
        article = self.app.x_bookmarks.articles()[0]
        self.assertEqual(article['topic'], 'Familie & Alltag')
        self.assertEqual(article['summary'], 'Reviewed locally')

    def test_automatic_unknown_topic_does_not_freeze_missing_title(self):
        self.import_pages([page([(1, '')])])
        self.import_pages([page([(1, 'A piano tutorial with music')])])
        article = self.app.x_bookmarks.articles()[0]
        self.assertEqual(article['title'], 'A piano tutorial with music')
        self.assertEqual(article['topic'], 'Musik, DJ und Audio')

    def prepare_removal(self):
        self.import_pages([page([(1, 'AI agents'), (2, 'Music and piano tutorial')])])
        self.http.reset_mock()
        self.app.store.decide(self.app.store.view()['version'], ['x:42:1'], 'remove')
        return self.app.preview_removals()

    def remove(self, preview, cap=None):
        return self.app.confirm_removals(preview['version'], ['x:42:1'],
            preview['confirmationToken'], preview['catalogRevision'], cap or
            allowance(posts=0, pages=1, usd=.51, mutations=1, verification_posts=100))

    def test_delete_targets_exact_bookmark_and_confirms_with_readback(self):
        preview = self.prepare_removal()
        self.http.side_effect = [{'data': {'bookmarked': False}}, page([(2, 'Music')])]
        result = self.remove(preview)
        first = self.http.call_args_list[0]
        self.assertEqual(first.args[0], 'https://api.x.com/2/users/42/bookmarks/1')
        self.assertEqual(first.kwargs['method'], 'DELETE')
        self.assertEqual(self.http.call_count, 2)
        self.assertEqual(result['result']['confirmed'], ['x:42:1'])
        self.assertIn('x:42:2', result['savedIds'])
        self.assertIn('ir1', result['savedIds'])
        self.assertIn('x:42:1', self.app.x_bookmarks.data['entries'])
        with self.assertRaises(StateError):
            self.remove(preview)
        self.assertEqual(self.http.call_count, 2)

    def test_insufficient_combined_budget_never_deletes(self):
        preview = self.prepare_removal()
        with self.assertRaises(StateError):
            self.remove(preview, allowance(posts=0, pages=1, usd=.01,
                        mutations=1, verification_posts=100))
        self.http.assert_not_called()

    def test_timeout_never_repeats_delete_and_partial_read_cannot_confirm(self):
        preview = self.prepare_removal()
        self.http.side_effect = [StateError('timeout', 502), page([(2, 'Music')], 'more')]
        result = self.remove(preview)
        self.assertEqual(self.http.call_count, 2)
        self.assertEqual(result['result']['confirmed'], [])
        self.assertEqual(result['result']['unconfirmed'], ['x:42:1'])
        self.assertEqual(self.app.store.view()['decisions']['x:42:1']['choice'], 'remove')

    def test_changed_session_cannot_use_old_removal_preview(self):
        preview = self.prepare_removal()
        self.app.x_connection.tokens = dict(self.app.x_connection.tokens)
        with self.assertRaises(StateError):
            self.remove(preview)
        self.http.assert_not_called()

    def test_delete_success_alone_does_not_prove_bookmark_was_removed(self):
        preview = self.prepare_removal()
        self.http.side_effect = [{'data': {'bookmarked': False}},
                                 page([(1, 'Still bookmarked'), (2, 'Music')])]
        result = self.remove(preview)
        self.assertEqual(result['result']['confirmed'], [])
        self.assertIn('x:42:1', result['savedIds'])

    def test_timeout_is_confirmed_by_full_readback_without_second_delete(self):
        preview = self.prepare_removal()
        self.http.side_effect = [StateError('timeout', 502), page([(2, 'Music')])]
        result = self.remove(preview)
        self.assertEqual(result['result']['confirmed'], ['x:42:1'])
        self.assertEqual(self.http.call_count, 2)

    def test_partial_response_and_rate_limit_keep_import_resumable(self):
        result = self.import_pages([page([(1, 'AI agents')], 'more'), StateError('rate limit', 429)])
        self.assertFalse(result['xImport']['complete'])
        self.assertEqual(result['xImport']['partialReason'], 'api_stopped_429')
        self.assertEqual(result['xImport']['checkpoint'], 'more')
        self.assertEqual(self.http.call_count, 2)

    def test_invalid_readback_metadata_cannot_confirm_absence(self):
        preview = self.prepare_removal()
        response = page([(2, 'Music')])
        del response['meta']['result_count']
        self.http.side_effect = [{'data': {'bookmarked': False}}, response]
        result = self.remove(preview)
        self.assertEqual(result['result']['confirmed'], [])

    def test_multiple_readback_pages_are_covered_before_absence_is_confirmed(self):
        preview = self.prepare_removal()
        self.http.side_effect = [{'data': {'bookmarked': False}}, page([(2, 'Music')], 'more'), page([])]
        cap = allowance(posts=0, pages=2, usd=1.01, mutations=1, verification_posts=200)
        result = self.remove(preview, cap)
        self.assertEqual(result['result']['confirmed'], ['x:42:1'])
        self.assertEqual(self.http.call_count, 3)


if __name__ == '__main__':
    unittest.main()

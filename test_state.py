import json, tempfile, unittest
from pathlib import Path
from triage_state import Store, StateError

class StateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'state.json'
        self.articles = [{'id': 'a', 'title': 'A'}, {'id': 'b', 'title': 'B'}]
        self.s = Store(self.path, self.articles)
    def tearDown(self): self.temp.cleanup()
    def test_decisions_persist_and_undo_batch(self):
        self.s.decide(0, ['a','b'], 'remove')
        again = Store(self.path, self.articles)
        self.assertEqual(again.view()['decisions']['a']['choice'], 'remove')
        again.undo(1)
        self.assertEqual(again.view()['decisions'], {})
    def test_stale_revision_cannot_overwrite(self):
        self.s.decide(0, ['a'], 'keep')
        with self.assertRaises(StateError): self.s.decide(0, ['a'], 'remove')
        self.assertEqual(self.s.view()['decisions']['a']['choice'], 'keep')
    def test_invalid_id_has_no_partial_effect(self):
        with self.assertRaises(StateError): self.s.decide(0, ['a','unknown'], 'remove')
        self.assertEqual(self.s.view()['version'], 0)
    def test_skip_is_not_pending_remove(self):
        self.s.decide(0, ['a'], 'skip')
        self.assertEqual(self.s.preview()['count'], 0)
    def test_partial_sync_truth_and_pending(self):
        self.s.decide(0, ['a','b'], 'remove')
        self.s.apply_remote(['b'], '2026-11-11T12:00:00Z')
        self.assertEqual([x['id'] for x in self.s.preview()['items']], ['b'])
        self.assertEqual(self.s.view()['decisions']['a']['choice'], 'remove')
    def test_preview_exact_ids_and_no_newly_unreviewed(self):
        self.s.decide(0, ['a'], 'remove')
        self.s.check_sync(1, ['a'])
        with self.assertRaises(StateError): self.s.check_sync(1, ['b'])
    def test_corrupt_file_never_silently_resets(self):
        self.path.write_text('{broken')
        with self.assertRaises(StateError): Store(self.path, self.articles)

    def test_eligibility_scopes_preview_and_ids_but_keeps_state(self):
        articles = [{'id': 'x', 'url': 'https://x.com/a/status/1'}, {'id': 'n', 'url': 'https://example.com/n'}]
        scoped = Store(self.path, articles, eligible_ids={'x'})
        scoped.decide(0, ['x'], 'remove')
        with self.assertRaises(StateError): scoped.decide(1, ['n'], 'remove')
        self.assertEqual([i['id'] for i in scoped.preview()['items']], ['x'])
        # A pre-existing non-X decision stays in state yet never enters the removal preview.
        raw = json.loads(self.path.read_text())
        raw['decisions']['n'] = {'choice': 'remove', 'updatedAt': 'now'}
        self.path.write_text(json.dumps(raw))
        reopened = Store(self.path, articles, eligible_ids={'x'})
        self.assertIn('n', reopened.view()['decisions'])
        self.assertEqual([i['id'] for i in reopened.preview()['items']], ['x'])

    def test_undo_only_reverts_eligible_and_preserves_non_x_history(self):
        articles = [{'id': 'x', 'url': 'https://x.com/a/status/1'}, {'id': 'n', 'url': 'https://example.com/n'}]
        scoped = Store(self.path, articles, eligible_ids={'x'})
        raw = scoped.view()
        raw['decisions']['n'] = {'choice': 'keep', 'updatedAt': 'old'}
        raw['history'] = [{'n': None}, {'x': None, 'n': {'choice': 'keep', 'updatedAt': 'old'}}]
        scoped.save(raw)
        scoped.undo(1)
        state = scoped.view()
        self.assertNotIn('x', state['decisions'])
        self.assertEqual(state['decisions']['n']['choice'], 'keep')
        self.assertEqual(state['history'], [{'n': None}, {'n': {'choice': 'keep', 'updatedAt': 'old'}}])

    def test_remote_reconcile_preserves_non_x_history(self):
        articles = [{'id': 'x', 'url': 'https://x.com/a/status/1'}, {'id': 'n', 'url': 'https://example.com/n'}]
        scoped = Store(self.path, articles, eligible_ids={'x'})
        raw = scoped.view()
        raw['history'] = [{'x': None, 'n': None}]
        scoped.save(raw)
        scoped.apply_remote([], 'now')
        self.assertEqual(scoped.view()['history'], [{'n': None}])

    def test_x_remote_is_account_scoped_and_inoreader_reconcile_preserves_native_undo(self):
        articles = [{'id': 'ir', 'url': 'same'}, {'id': 'x:acct:1', 'url': 'same',
                    'origin': 'x', 'originAccountId': 'acct', 'postId': '1'}]
        scoped = Store(self.path, articles, x_account_id='acct')
        scoped.decide(0, ['ir'], 'remove')
        scoped.decide(1, ['x:acct:1'], 'keep')
        scoped.apply_x_remote('acct', ['1'], 'x-check')
        scoped.apply_remote(['ir'], 'ir-check')
        self.assertEqual(scoped.view()['remote']['starredIds'], ['ir'])
        self.assertEqual(scoped.view()['xRemote'], {'accountId': 'acct', 'checkedAt': 'x-check', 'starredIds': ['1']})
        scoped.undo(4)
        self.assertNotIn('x:acct:1', scoped.view()['decisions'])

    def test_legacy_state_is_not_rewritten_until_a_real_update(self):
        legacy = {'version': 0, 'decisions': {}, 'history': [], 'karakeepSaved': {},
                  'remote': {'checkedAt': None, 'starredIds': None}}
        self.path.write_text(json.dumps(legacy))
        before = self.path.read_bytes()
        Store(self.path, self.articles)
        self.assertEqual(self.path.read_bytes(), before)

if __name__ == '__main__': unittest.main()

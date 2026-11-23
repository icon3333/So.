import json
import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path
from types import SimpleNamespace

from import_settings import import_action, import_status
import cockpit


class ImportSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'trusted'
        self.root.mkdir()
        self.data = Path(self.temp.name) / 'data'
        self.app = SimpleNamespace(data_root=self.data, import_root=self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_test_is_non_persistent_and_readable_or_empty(self):
        (self.root / 'Keep').mkdir()
        (self.root / 'To').mkdir()
        (self.root / 'To' / 'synthetic.txt').write_text('synthetic only')
        before = import_action(self.app, 'keep', 'test', {'path': 'Keep'})
        self.assertEqual(before['state'], 'empty')
        self.assertTrue(before['checkedAt'])
        self.assertFalse((self.data / 'import-settings.json').exists())
        self.assertEqual(import_action(self.app, 'to', 'test', {'path': 'To'})['state'], 'readable')

    def test_connect_persists_relative_path_used_by_cockpit(self):
        (self.root / 'Keep').mkdir()
        (self.root / 'Keep' / 'note.json').write_text(json.dumps({'title': 'Synthetic note'}))
        result = import_action(self.app, 'keep', 'connect', {'path': 'Keep'})
        saved = json.loads((self.data / 'import-settings.json').read_text())
        self.assertEqual(saved['keep']['path'], 'Keep')
        self.assertEqual(import_status(self.app, 'keep')['checkedAt'], result['checkedAt'])
        self.assertEqual([x['title'] for x in cockpit.local_sources(self.app)['keep']['items']], ['Synthetic note'])

    def test_rejects_escape_absolute_symlink_and_unknown_fields(self):
        outside = Path(self.temp.name) / 'outside'
        outside.mkdir()
        (self.root / 'link').symlink_to(outside, target_is_directory=True)
        for bad in ('../outside', str(outside), 'link', 'missing'):
            self.assertEqual(import_action(self.app, 'keep', 'connect', {'path': bad})['state'], 'unavailable')
        self.assertFalse((self.data / 'import-settings.json').exists())
        with self.assertRaises(ValueError):
            import_action(self.app, 'adapter', 'connect', {'path': 'x'})
        self.assertNotIn('adapter', import_status(self.app, 'keep'))

    def test_unconfigured_when_no_root_or_source(self):
        app = SimpleNamespace(data_root=self.data)
        self.assertEqual(import_status(app, 'projects')['state'], 'unconfigured')
        self.assertEqual(import_action(app, 'projects', 'test', {'path': 'Projects'})['state'], 'unavailable')

    def test_scanner_keeps_provider_semantics_and_scans_only_selected_folder(self):
        (self.root / 'To').mkdir()
        (self.root / 'Projects').mkdir()
        (self.root / 'To' / 'synthetic.txt').write_text('task')
        (self.root / 'Projects' / 'Synthetic project').mkdir()
        (self.root / 'To' / 'unrelated.json').write_text(json.dumps({'title': 'not a Keep note'}))
        import_action(self.app, 'to', 'connect', {'path': 'To'})
        import_action(self.app, 'projects', 'connect', {'path': 'Projects'})
        result = cockpit.local_sources(self.app)
        self.assertEqual([x['title'] for x in result['to']['items']], ['synthetic.txt', 'unrelated.json'])
        self.assertEqual([x['title'] for x in result['projects']['items']], ['Synthetic project'])
        self.assertEqual(result['keep']['items'], [])
        self.assertEqual(result['keep']['state'], 'unavailable')

    def test_rejects_dot_segments_and_symlink_children(self):
        (self.root / 'Keep').mkdir()
        outside = Path(self.temp.name) / 'outside.txt'
        outside.write_text(json.dumps({'title': 'outside'}))
        (self.root / 'Keep' / 'escape.json').symlink_to(outside)
        for bad in ('Keep/../Keep', './Keep', 'Keep/.'):
            self.assertEqual(import_action(self.app, 'keep', 'test', {'path': bad})['state'], 'unavailable')
        import_action(self.app, 'keep', 'connect', {'path': 'Keep'})
        self.assertEqual(cockpit.local_sources(self.app)['keep']['items'], [])

    def test_legacy_defaults_resolve_independently(self):
        desktop = self.root / 'Desktop'
        (desktop / 'To').mkdir(parents=True)
        (desktop / 'Projects').mkdir()
        keep = self.root / 'legacy-keep'
        keep.mkdir()
        (keep / 'note.json').write_text(json.dumps({'title': 'Legacy note'}))
        app = SimpleNamespace(data_root=self.data)
        with mock.patch.dict(os.environ, {
                'LESELISTE_IMPORT_ROOT': '', 'LESELISTE_DESKTOP': str(desktop),
                'LESELISTE_KEEP_EXPORT': str(keep)}):
            self.assertEqual(import_status(app, 'keep')['state'], 'readable')
            self.assertEqual(import_status(app, 'to')['state'], 'empty')
            self.assertEqual(import_status(app, 'projects')['state'], 'empty')


if __name__ == '__main__':
    unittest.main()

import json
import tempfile
import unittest
from pathlib import Path

from inoreader_import import import_entries, normalize_entries
from triage_state import StateError


ENTRY = {'id': 'tag:google.com,2005:reader/item/abc', 'url': 'https://example.org/story',
         'title': 'Fixture story', 'content': 'Story body', 'excerpt': 'Excerpt',
         'author': 'A. Author', 'publisher': {'title': 'Example'}, 'published': '2026-01-01T00:00:00Z'}


class FixtureAdapter:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def read_entries_page(self, cursor=None, limit=100):
        self.calls.append((cursor, limit))
        return self.pages[cursor]


class InoreaderImportTests(unittest.TestCase):
    def test_normalize_bounded_safe_entry(self):
        item = normalize_entries([ENTRY])[0]
        self.assertEqual(item['id'], 'inoreader:' + ENTRY['id'])
        self.assertEqual(item['text'], 'Story body')
        self.assertFalse(item['missingContent'])

    def test_normalize_rejects_bad_ids_urls_and_duplicates(self):
        for entries in ([{**ENTRY, 'id': '../bad'}], [{**ENTRY, 'url': 'javascript:alert(1)'}], [ENTRY, ENTRY]):
            with self.subTest(entries=entries), self.assertRaises(StateError):
                normalize_entries(entries)

    def test_paginated_import_is_idempotent_and_preserves_existing_catalog(self):
        adapter = FixtureAdapter({None: {'complete': True, 'entries': [ENTRY], 'nextCursor': 'page-2'},
                                  'page-2': {'complete': True, 'entries': [{**ENTRY, 'title': 'Updated'}], 'nextCursor': None}})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'entries.json'
            path.write_text(json.dumps({'schemaVersion': 1, 'entries': {'legacy': {'id': 'legacy'}}}))
            one = import_entries(adapter, path, max_pages=2, page_size=1)
            two = import_entries(adapter, path, max_pages=2, page_size=1)
            data = json.loads(path.read_text())
            self.assertEqual(one['imported'], 2)
            self.assertTrue(one['complete'])
            self.assertEqual(two['total'], 2)
            self.assertIn('legacy', data['entries'])
            self.assertEqual(data['entries']['inoreader:' + ENTRY['id']]['title'], 'Updated')
            self.assertEqual(len(adapter.calls), 4)

    def test_bounds_and_old_inventory_adapter_reject(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(StateError):
                import_entries(object(), Path(directory) / 'entries.json')
            with self.assertRaises(StateError):
                import_entries(FixtureAdapter({}), Path(directory) / 'entries.json', max_pages=6)


if __name__ == '__main__':
    unittest.main()

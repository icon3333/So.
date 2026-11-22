import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from x_content import fetch, is_x_article, load, save, x_post_id


class XContentTests(unittest.TestCase):
    def test_only_exact_x_hosts_and_status_ids_are_eligible(self):
        self.assertEqual(x_post_id('https://x.com/user/status/123?x=1'), '123')
        self.assertEqual(x_post_id('https://www.x.com/user/status/123'), '123')
        self.assertIsNone(x_post_id('https://twitter.com/user/status/123'))
        self.assertFalse(is_x_article({'url': 'https://example.com/status/123'}))
        self.assertTrue(is_x_article({'url': 'https://x.com/user'}))
        self.assertFalse(is_x_article({'url': 'https://x.com.evil.test/user'}))

    @patch('x_content.urllib.request.urlopen')
    def test_fetch_marks_raw_result_partial(self, opener):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, limit): return b'{"tweet":{"text":"hello","author":{"name":"A"}}}'
        opener.return_value = Response()
        result = fetch('https://x.com/a/status/123')
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['text'], 'hello')
        self.assertEqual(result['author'], 'A')

    def test_missing_cache_is_empty_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(load(Path(directory) / 'x-content.json'), {'schemaVersion': 1, 'entries': {}})

    @patch('x_content.urllib.request.urlopen')
    def test_fetch_extracts_article_blocks_and_dict_media(self, opener):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, limit): return b'{"tweet":{"text":"intro","article":{"content":{"blocks":[{"text":"one"},{"text":"two"}]}},"media":{"photos":[{"url":"p"}]}}}'
        opener.return_value = Response()
        result = fetch('https://x.com/a/status/123')
        self.assertEqual(result['text'], 'intro\n\none\n\ntwo')
        self.assertEqual(result['media'], [{'url': 'p'}])

    def test_save_is_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'x-content.json'
            save(path, {'schemaVersion': 1, 'entries': {'a': {'status': 'complete'}}})
            self.assertEqual(load(path)['entries']['a']['status'], 'complete')


if __name__ == '__main__':
    unittest.main()

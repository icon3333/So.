import io
import json
import unittest
from unittest.mock import patch
import start


class StartupTests(unittest.TestCase):
    def test_running_accepts_growing_catalog_and_rejects_unrelated_payload(self):
        for total in [371, 391, 1000]:
            payload = {'total': total, 'articles': [{}] * total, 'state': {}}
            with self.subTest(total=total), patch('start.urllib.request.urlopen',
                    return_value=io.BytesIO(json.dumps(payload).encode())):
                self.assertTrue(start.running())
        for payload in [{'total': 391, 'articles': []},
                        {'total': 0, 'articles': [], 'state': 'unrelated'}]:
            with self.subTest(payload=payload), patch('start.urllib.request.urlopen',
                    return_value=io.BytesIO(json.dumps(payload).encode())):
                self.assertFalse(start.running())

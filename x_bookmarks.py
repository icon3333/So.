"""Persistent catalog for native X bookmarks, separate from articles.json."""
import copy
import json
import re
import threading
from pathlib import Path

from triage_state import atomic_json


# API refreshes may update source fields, but must not erase local Hermes enrichment.
ENRICHMENT_KEYS = {'title', 'summary', 'topic', 'takeaway', 'assessment', 'limitations'}


class XBookmarkCatalog:
    """Account-scoped native entries plus import checkpoint in one atomic file."""

    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.data = {
            'schemaVersion': 1, 'accountId': None, 'revision': 0,
            'checkpoint': None, 'complete': False, 'entries': {},
            'inventorySeen': [], 'scanStarted': False, 'partial': False,
            'restartRequired': False,
            'cursorHistory': [],
        }
        if self.path.exists():
            try:
                saved = json.loads(self.path.read_text())
                assert isinstance(saved, dict) and saved.get('schemaVersion') == 1
                assert saved.get('accountId') is None or isinstance(saved.get('accountId'), str)
                assert type(saved.get('revision')) is int and saved['revision'] >= 0
                assert saved.get('checkpoint') is None or isinstance(saved.get('checkpoint'), str)
                assert type(saved.get('complete')) is bool
                assert isinstance(saved.get('entries'), dict)
                assert all(isinstance(k, str) and isinstance(v, dict) for k, v in saved['entries'].items())
                assert not saved['entries'] or saved['accountId']
                assert all(k == self.native_id(saved['accountId'], v.get('postId'))
                           for k, v in saved['entries'].items())
                saved.setdefault('inventorySeen', [])
                saved.setdefault('scanStarted', False)
                saved.setdefault('partial', False)
                saved.setdefault('restartRequired', False)
                saved.setdefault('cursorHistory', [])
                assert isinstance(saved['cursorHistory'], list) and all(isinstance(v, str) for v in saved['cursorHistory'])
                assert isinstance(saved['inventorySeen'], list) and all(isinstance(v, str) for v in saved['inventorySeen'])
                assert all(type(saved[k]) is bool for k in ('scanStarted', 'partial', 'restartRequired'))
                self.data = saved
            except (ValueError, KeyError, AssertionError, TypeError):
                raise ValueError('The X bookmark catalog is damaged; it was not overwritten.') from None

    @staticmethod
    def native_id(account_id, post_id):
        if (not isinstance(account_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', account_id)
                or not isinstance(post_id, str) or not re.fullmatch(r'[0-9]+', post_id)):
            raise ValueError('Account and post identifiers are required.')
        return 'x:%s:%s' % (account_id, post_id)

    def articles(self):
        with self.lock:
            out = []
            for native_id, entry in self.data['entries'].items():
                item = copy.deepcopy(entry)
                item.update(id=native_id, origin='x', originAccountId=self.data['accountId'])
                out.append(item)
            return out

    def status(self):
        with self.lock:
            return {**{k: copy.deepcopy(v) for k, v in self.data.items() if k not in ('entries', 'inventorySeen', 'cursorHistory')},
                    'count': len(self.data['entries']), 'inventorySeenCount': len(self.data['inventorySeen'])}

    def begin_inventory(self, account_id, restart=False):
        with self.lock:
            if self.data['accountId'] not in (None, account_id):
                raise ValueError('Das X-Konto stimmt nicht mit dem gespeicherten Bestand überein.')
            if self.data['restartRequired'] and not restart:
                raise ValueError('Die X-Inventur benötigt einen ausdrücklich freigegebenen Neustart.')
            if restart or self.data['complete'] or not self.data['scanStarted']:
                return {'checkpoint': None, 'seen': [], 'started': False}
            return {'checkpoint': self.data['checkpoint'], 'seen': list(self.data['inventorySeen']), 'started': True}

    def merge_page(self, account_id, entries, next_cursor, complete=False, seen_ids=None,
                   partial=False, restart_required=False, begin=False, cursor_history=None):
        """Atomically persist a page and its next cursor before another page starts."""
        if not isinstance(account_id, str) or not account_id:
            raise ValueError('Account identity is required.')
        if not isinstance(entries, list) or any(not isinstance(e, dict) for e in entries):
            raise ValueError('Entries must be a list of records.')
        if next_cursor is not None and not isinstance(next_cursor, str):
            raise ValueError('Cursor must be text or None.')
        if type(complete) is not bool:
            raise ValueError('Completeness must be boolean.')
        if complete and (next_cursor is not None or partial or restart_required):
            raise ValueError('Eine unvollständige Antwort darf keine vollständige Inventur bestätigen.')
        if cursor_history is not None and (not isinstance(cursor_history, list) or
                                          any(not isinstance(v, str) for v in cursor_history)):
            raise ValueError('Invalid cursor history.')
        if any(type(v) is not bool for v in (partial, restart_required, begin)):
            raise ValueError('Import state must be boolean.')
        seen_ids = [] if seen_ids is None else seen_ids
        if not isinstance(seen_ids, list) or any(not isinstance(v, str) for v in seen_ids):
            raise ValueError('Seen identifiers must be text.')
        with self.lock:
            candidate = copy.deepcopy(self.data)
            if candidate['accountId'] not in (None, account_id):
                raise ValueError('Das X-Konto stimmt nicht mit dem gespeicherten Bestand überein.')
            candidate['accountId'] = account_id
            if begin:
                candidate.update(inventorySeen=[], scanStarted=True, partial=False, restartRequired=False, cursorHistory=[])
            for entry in entries:
                post_id = entry.get('postId')
                if not isinstance(post_id, str) or not post_id:
                    raise ValueError('Each record requires a postId.')
                native_id = self.native_id(account_id, post_id)
                saved = {**candidate['entries'].get(native_id, {}), **copy.deepcopy(entry)}
                old = candidate['entries'].get(native_id, {})
                enriched = any(old.get(key) for key in ('summary', 'topic', 'takeaway', 'assessment'))
                for key in ENRICHMENT_KEYS:
                    if old.get(key) and (key not in ('title', 'limitations') or enriched):
                        saved[key] = copy.deepcopy(old[key])
                saved.pop('id', None)
                saved.pop('origin', None)
                saved.pop('originAccountId', None)
                saved['origin'] = 'x'
                saved['originAccountId'] = account_id
                candidate['entries'][native_id] = saved
            candidate['checkpoint'] = next_cursor
            candidate['inventorySeen'] = list(dict.fromkeys(candidate['inventorySeen'] + seen_ids))
            candidate['complete'] = complete
            candidate['scanStarted'] = not complete
            candidate['partial'] = not complete
            candidate['restartRequired'] = False if complete else restart_required
            if cursor_history is not None:
                candidate['cursorHistory'] = list(dict.fromkeys(cursor_history))
            candidate['revision'] += 1
            atomic_json(self.path, candidate)
            self.data = candidate
            return self.articles()

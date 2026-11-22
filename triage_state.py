import copy
import datetime
import json
import os
import tempfile
import threading
from pathlib import Path


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class StateError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def atomic_json(path, value):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as out:
            json.dump(value, out, ensure_ascii=False, indent=2)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class Store:
    def __init__(self, path, articles, eligible_ids=None, x_account_id=None):
        self.path = Path(path)
        self.inventory_path = self.path.with_name(self.path.name + '.source-inventory')
        self.articles = {a['id']: a for a in articles}
        self.eligible_ids = set(self.articles) if eligible_ids is None else set(eligible_ids)
        self.lock = threading.RLock()
        self.x_account_id = x_account_id
        self.data = {'version': 0, 'decisions': {}, 'history': [], 'karakeepSaved': {},
                     'remote': {'checkedAt': None, 'starredIds': None}}
        if self.path.exists():
            try:
                saved = json.loads(self.path.read_text())
                assert isinstance(saved['version'], int)
                assert isinstance(saved['decisions'], dict)
                assert isinstance(saved['history'], list)
                assert isinstance(saved['remote'], dict)
                x_remote = saved.get('xRemote', {})
                assert isinstance(x_remote, dict)
                assert x_remote.get('accountId') is None or isinstance(x_remote.get('accountId'), str)
                assert x_remote.get('accountId') in (None, self.x_account_id)
                assert isinstance(x_remote.get('checkedAt'), (str, type(None)))
                assert isinstance(x_remote.get('starredIds'), (list, type(None)))
                assert all(isinstance(i, str) for i in (x_remote.get('starredIds') or []))
                assert set(saved['decisions']).issubset(self.articles)
                assert all(v['choice'] in ('keep', 'remove', 'skip') for v in saved['decisions'].values())
                receipts = saved.setdefault('karakeepSaved', {})
                assert isinstance(receipts, dict) and set(receipts).issubset(self.articles)
                assert all(isinstance(v, dict) and all(isinstance(v.get(k), str) for k in ('bookmarkId', 'url', 'savedAt')) for v in receipts.values())
                self.data = saved
            except (ValueError, KeyError, AssertionError, TypeError):
                raise StateError('Der gespeicherte Fortschritt ist beschädigt. Die Datei wurde nicht überschrieben.', 500)

    def view(self):
        with self.lock:
            return copy.deepcopy(self.data)

    def check_version(self, version):
        if type(version) is not int or version != self.data['version']:
            raise StateError('Der Stand hat sich geändert. Bitte die aktuelle Ansicht erneut prüfen.', 409)

    def ids(self, ids):
        if not isinstance(ids, list) or not ids or len(ids) > len(self.articles):
            raise StateError('Bitte mindestens einen gültigen Artikel auswählen.')
        if any(not isinstance(i, str) or i not in self.articles or i not in self.eligible_ids for i in ids):
            raise StateError('Die Auswahl enthält einen unbekannten oder nicht unterstützten Artikel.')
        if len(set(ids)) != len(ids):
            raise StateError('Die Auswahl enthält doppelte Artikel.')
        return ids

    def save(self, candidate):
        candidate['version'] += 1
        atomic_json(self.path, candidate)
        self.data = candidate

    def decide(self, version, ids, choice):
        with self.lock:
            self.check_version(version)
            self.ids(ids)
            if choice not in ('keep', 'remove', 'skip', 'clear'):
                raise StateError('Unbekannte Entscheidung.')
            candidate = self.view()
            previous = {i: candidate['decisions'].get(i) for i in ids}
            candidate['history'] = (candidate['history'] + [previous])[-30:]
            for i in ids:
                if choice == 'clear':
                    candidate['decisions'].pop(i, None)
                else:
                    candidate['decisions'][i] = {'choice': choice, 'updatedAt': now()}
            self.save(candidate)

    def undo(self, version):
        with self.lock:
            self.check_version(version)
            candidate = self.view()
            if not candidate['history']:
                raise StateError('Keine vorgemerkte Entscheidung zum Rückgängigmachen.')
            undone = False
            for entry_index in range(len(candidate['history']) - 1, -1, -1):
                entry = candidate['history'][entry_index]
                eligible = {i: previous for i, previous in entry.items() if i in self.eligible_ids}
                if not eligible:
                    continue
                for i, previous in eligible.items():
                    if previous is None:
                        candidate['decisions'].pop(i, None)
                    else:
                        candidate['decisions'][i] = previous
                noneligible = {i: previous for i, previous in entry.items() if i not in self.eligible_ids}
                if noneligible:
                    candidate['history'][entry_index] = noneligible
                else:
                    candidate['history'].pop(entry_index)
                undone = True
                break
            if not undone:
                raise StateError('Keine vorgemerkte Entscheidung zum Rückgängigmachen.')
            self.save(candidate)

    def record_karakeep_saved(self, version, article_id, receipt):
        with self.lock:
            self.check_version(version)
            self.ids([article_id])
            candidate = self.view()
            candidate['history'] = (candidate['history'] + [{article_id: candidate['decisions'].get(article_id)}])[-30:]
            candidate['karakeepSaved'][article_id] = {**receipt, 'savedAt': now()}
            candidate['decisions'][article_id] = {'choice': 'remove', 'updatedAt': now()}
            self.save(candidate)

    def preview(self):
        with self.lock:
            items = [self.articles[i] for i, v in self.data['decisions'].items()
                     if i in self.eligible_ids and v['choice'] == 'remove' and self.is_saved(i)]
            return {'version': self.data['version'], 'items': items, 'count': len(items)}

    def is_native(self, article_id):
        return self.articles[article_id].get('origin') == 'x'

    def is_saved(self, article_id):
        if self.is_native(article_id):
            remote = self.data.get('xRemote', {})
            if remote.get('accountId') != self.articles[article_id].get('originAccountId'):
                return True  # Unknown membership is never evidence of removal.
            stars = remote.get('starredIds')
            return stars is None or self.articles[article_id].get('postId') in stars
        stars = self.data['remote']['starredIds']
        return stars is None or article_id in stars

    def check_sync(self, version, ids):
        self.check_version(version)
        self.ids(ids)
        pending = {a['id'] for a in self.preview()['items']}
        if not set(ids).issubset(pending):
            raise StateError('Die Auswahl stimmt nicht mehr mit den vorgemerkten Änderungen überein.', 409)

    def source_inventory(self):
        try:
            inventory = json.loads(self.inventory_path.read_text())
        except (OSError, ValueError):
            return {}
        if not isinstance(inventory, dict):
            return {}
        return {source: snapshot for source, snapshot in inventory.items()
                if source in ('x', 'inoreader') and isinstance(snapshot, dict)
                and type(snapshot.get('count')) is int and snapshot['count'] >= 0
                and snapshot.get('coverage') in ('partial', 'complete')
                and 'checkedAt' in snapshot and isinstance(snapshot['checkedAt'], (str, type(None)))}

    def record_source_inventory(self, source, snapshot):
        with self.lock:
            inventory = self.source_inventory()
            previous = inventory.get(source)
            if snapshot is None and previous is not None:
                snapshot = {**previous, 'stale': True}
            if snapshot != previous:
                inventory[source] = snapshot
                atomic_json(self.inventory_path, inventory)

    def apply_remote(self, starred_ids, checked_at):
        with self.lock:
            if not isinstance(starred_ids, list) or any(not isinstance(i, str) for i in starred_ids):
                raise StateError('Der Inoreader-Stand konnte nicht vollständig geprüft werden.', 502)
            candidate = self.view()
            candidate['remote'] = {'checkedAt': checked_at, 'starredIds': sorted(set(starred_ids))}
            # Keep local undo for unaffected articles, never for confirmed remote removals.
            remaining = set(starred_ids)
            candidate['history'] = [kept for entry in candidate['history']
                                    if (kept := {i: previous for i, previous in entry.items()
                                                 if i not in self.eligible_ids or self.is_native(i) or i in remaining})]
            self.save(candidate)

    def mark_x_membership_unknown(self, account_id):
        """Clear stale X membership knowledge while a fresh inventory is in flight."""
        with self.lock:
            if not isinstance(account_id, str) or not account_id or self.x_account_id != account_id:
                raise StateError('Das X-Konto stimmt nicht mit dem gespeicherten Bestand überein.', 409)
            candidate = self.view()
            candidate['xRemote'] = {'accountId': account_id, 'checkedAt': now(), 'starredIds': None}
            self.save(candidate)

    def apply_x_remote(self, account_id, starred_ids, checked_at):
        """Store account-scoped X membership without changing Inoreader state."""
        with self.lock:
            if not isinstance(account_id, str) or not account_id:
                raise StateError('X account identity is required.')
            if self.x_account_id != account_id:
                raise StateError('Das X-Konto stimmt nicht mit dem gespeicherten Bestand überein.', 409)
            if not isinstance(starred_ids, list) or any(not isinstance(i, str) for i in starred_ids):
                raise StateError('The X bookmark state could not be fully checked.', 502)
            candidate = self.view()
            candidate['xRemote'] = {'accountId': account_id, 'checkedAt': checked_at,
                                    'starredIds': sorted(set(starred_ids))}
            remaining = set(starred_ids)
            candidate['history'] = [kept for entry in candidate['history']
                                    if (kept := {i: previous for i, previous in entry.items()
                                                 if i not in self.eligible_ids or not self.is_native(i)
                                                 or self.articles[i].get('postId') in remaining})]
            self.save(candidate)

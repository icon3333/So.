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
    def __init__(self, path, articles, eligible_ids=None):
        self.path = Path(path)
        self.articles = {a['id']: a for a in articles}
        self.eligible_ids = set(self.articles) if eligible_ids is None else set(eligible_ids)
        self.lock = threading.RLock()
        self.data = {'version': 0, 'decisions': {}, 'history': [], 'karakeepSaved': {},
                     'remote': {'checkedAt': None, 'starredIds': None}}
        if self.path.exists():
            try:
                saved = json.loads(self.path.read_text())
                assert isinstance(saved['version'], int)
                assert isinstance(saved['decisions'], dict)
                assert isinstance(saved['history'], list)
                assert isinstance(saved['remote'], dict)
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
            stars = self.data['remote']['starredIds']
            items = [self.articles[i] for i, v in self.data['decisions'].items()
                     if i in self.eligible_ids and v['choice'] == 'remove' and (stars is None or i in stars)]
            return {'version': self.data['version'], 'items': items, 'count': len(items)}

    def check_sync(self, version, ids):
        self.check_version(version)
        self.ids(ids)
        pending = {a['id'] for a in self.preview()['items']}
        if not set(ids).issubset(pending):
            raise StateError('Die Auswahl stimmt nicht mehr mit den vorgemerkten Änderungen überein.', 409)

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
                                                 if i not in self.eligible_ids or i in remaining})]
            self.save(candidate)

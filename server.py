#!/usr/bin/env python3
import argparse
import html
import json
import re
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from n8n_connection import Connection
from karakeep_connection import KarakeepConnection
from triage_state import Store, StateError
from x_content import fetch as fetch_x_content, is_x_article, load as load_x_content, save as save_x_content
from x_bookmarks import XBookmarkCatalog
from x_connection import XConnection


def load_articles(root):
    snapshot = json.loads((root / 'articles.json').read_text())
    duplicate_refs = {}
    for group in snapshot.get('sameUrlGroups', []):
        for ref in group:
            duplicate_refs[ref] = [other for other in group if other != ref]
    articles = []
    for item in snapshot['articles']:
        a = dict(item)
        title = html.unescape(str(a.get('title') or '')).strip()
        excerpt = html.unescape(str(a.get('excerpt') or ''))
        excerpt = re.sub(r'[\u00ad\u034f\u200b-\u200f\ufeff]', '', excerpt)
        excerpt = re.sub(r'\s+', ' ', excerpt).strip()
        missing = title.lower() in ('', 'null', 'none')
        if missing:
            match = re.search(r'(?:x|twitter)\.com/([^/]+)/status/', a.get('url', ''))
            title = '@' + match[1] + ' · Inhalt fehlt' if match else 'Inhalt nicht verfügbar'
            excerpt = ''
        a.update(title=title[:5000], excerpt=excerpt[:1200], missingContent=missing,
                 duplicateRefs=duplicate_refs.get(a['ref'], []), origin='inoreader', originAccountId=None)
        articles.append(a)
    return snapshot, articles


class App:
    def __init__(self, root, origin, state_path=None):
        self.root, self.origin = root, origin
        self.snapshot, self.articles = load_articles(root)
        self.x_bookmarks = XBookmarkCatalog(root / 'x-bookmarks.json')
        self.x_connection = XConnection(origin, self.x_bookmarks)
        self.articles.extend(self.x_bookmarks.articles())
        for article in self.articles:
            article.setdefault('origin', 'inoreader')
            article.setdefault('originAccountId', None)
        self.eligible_ids = {a['id'] for a in self.articles}
        self.x_ids = {a['id'] for a in self.articles if a['origin'] == 'inoreader' and is_x_article(a)}
        self.store = Store(state_path or root / 'state.json', self.articles, eligible_ids=self.eligible_ids,
                           x_account_id=self.x_bookmarks.data['accountId'])
        self.x_content_path = root / 'x-content.json'
        self.x_content = load_x_content(self.x_content_path)
        self.connection = Connection(root, origin)
        self.karakeep = KarakeepConnection()
        self.operation_lock = threading.Lock()

    def payload(self):
        x_connection = getattr(self, 'x_connection', None)
        x_status = x_connection.status() if x_connection else {'ready': False, 'connected': False, 'account': None}
        return {'articles': self.articles, 'eligibleIds': sorted(getattr(self, 'eligible_ids', {a['id'] for a in self.articles})), 'xContent': getattr(self, 'x_content', {'schemaVersion': 1, 'entries': {}}),
                'savedIds': [a['id'] for a in self.articles if self.store.is_saved(a['id'])],
                'xImport': self.x_bookmarks.status() if getattr(self, 'x_bookmarks', None) else None,
                'total': len(self.articles), 'fetchedAt': self.snapshot['fetchedAt'], 'state': self.store.view(),
                'connection': self.connection.status(), 'xConnection': x_status, 'karakeep': self.karakeep.status()}

    def refresh_catalog(self):
        """Publish an atomically persisted additive page to the active review store."""
        with self.store.lock:
            legacy = [a for a in self.articles if a.get('origin') != 'x']
            self.articles = legacy + self.x_bookmarks.articles()
            self.eligible_ids = {a['id'] for a in self.articles}
            self.store.articles = {a['id']: a for a in self.articles}
            self.store.eligible_ids = self.eligible_ids.copy()
            self.store.x_account_id = self.x_bookmarks.data['accountId']

    def fetch_x(self, article_id):
        with self.operation_lock:
            if not isinstance(article_id, str) or article_id not in getattr(self, 'x_ids', self.eligible_ids):
                raise StateError('Nur X-Artikel können abgerufen werden.')
            old = self.x_content['entries'].get(article_id)
            if old and old.get('status') in ('read', 'partial'):
                return self.payload()
            article = self.store.articles[article_id]
            try:
                entry = fetch_x_content(article['url'])
            except (RuntimeError, ValueError) as exc:
                entry = {'status': 'unavailable', 'author': '', 'title': 'Inhalt nicht abrufbar',
                         'summary': '', 'topic': '', 'takeaway': '', 'assessment': '', 'text': '',
                         'sourceUrl': article['url'], 'retrievedAt': None,
                         'limitations': [str(exc)], 'media': []}
            candidate = {**self.x_content, 'entries': {**self.x_content['entries'], article_id: entry}}
            save_x_content(self.x_content_path, candidate)
            self.x_content = candidate
            return self.payload()

    def connect_karakeep(self, api_key):
        self.karakeep.connect(api_key)
        return self.payload()

    def save_to_karakeep(self, version, article_id):
        with self.operation_lock, self.store.lock:
            self.store.check_version(version)
            self.store.ids([article_id])
            receipt = self.karakeep.save(self.store.articles[article_id]['url'])
            try:
                self.store.record_karakeep_saved(version, article_id, receipt)
            except OSError:
                raise StateError('In Karakeep gespeichert, aber die lokale Vormerkung konnte nicht gespeichert werden. Bitte freien Speicherplatz prüfen; Inoreader wurde nicht verändert.', 500) from None
            result = self.payload()
            result['result'] = {'message': 'In Karakeep gespeichert und zum Entfernen aus „Später lesen“ vorgemerkt. Inoreader wird erst nach deiner Abgleich-Bestätigung geändert.'}
            return result

    def refresh(self):
        with self.operation_lock, self.store.lock:
            result = self.connection.call('read')
            self.store.apply_remote(result['starredIds'], result['checkedAt'])
            return self.payload()

    def sync(self, version, ids):
        with self.operation_lock, self.store.lock:
            self.store.check_sync(version, ids)
            if any(self.store.is_native(i) for i in ids):
                raise StateError('Native X-Lesezeichen benötigen eine getrennte X-Entfernung.', 409)
            before = self.connection.call('read')
            existing = set(before['starredIds'])
            write_ids = [i for i in ids if i in existing]
            if write_ids:
                try:
                    after = self.connection.call('remove', write_ids)
                except StateError:
                    # A timeout can happen after the write. Reconcile without repeating it.
                    try:
                        after = self.connection.call('read')
                    except StateError:
                        raise StateError('Der Abgleich konnte nicht bestätigt werden. Änderungen könnten bereits übernommen sein. Bitte „Stand prüfen“ nutzen; es wurde nichts automatisch erneut gesendet.', 502)
            else:
                after = before
            self.store.apply_remote(after['starredIds'], after['checkedAt'])
            remaining = set(after['starredIds'])
            confirmed = [i for i in ids if i not in remaining]
            unconfirmed = [i for i in ids if i in remaining]
            out = self.payload()
            out['result'] = {'confirmed': confirmed, 'unconfirmed': unconfirmed,
                             'message': '%s entfernt bzw. bereits nicht mehr gespeichert; %s noch offen.' % (len(confirmed), len(unconfirmed))}
            return out


class Handler(BaseHTTPRequestHandler):
    server_version = 'Lesestapel'

    def log_message(self, *args):
        # OAuth callback query strings must never appear in logs.
        pass

    @property
    def app(self):
        return self.server.app

    def safe_host(self):
        if self.headers.get('Host') != urllib.parse.urlsplit(self.app.origin).netloc:
            raise StateError('Bitte die lokale Adresse 127.0.0.1 verwenden.', 403)

    def send(self, content, status=200, content_type='application/json; charset=utf-8'):
        if isinstance(content, (dict, list)):
            content = json.dumps(content, ensure_ascii=False).encode()
        elif isinstance(content, str):
            content = content.encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(content)

    def error(self, exc):
        self.send({'error': str(exc)}, exc.status)

    def do_GET(self):
        try:
            self.safe_host()
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            if path == '/oauth/callback':
                try:
                    self.app.connection.finish(urllib.parse.parse_qs(parsed.query))
                    self.send_response(303)
                    self.send_header('Location', '/')
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('Referrer-Policy', 'no-referrer')
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                except StateError as exc:
                    self.send('<h1>Anmeldung nicht abgeschlossen</h1><p>' + html.escape(str(exc)) + '</p><a href="/">Zurück zur Leseliste</a>', exc.status, 'text/html; charset=utf-8')
            elif path == '/oauth/x/callback':
                try:
                    self.app.x_connection.finish(urllib.parse.parse_qs(parsed.query))
                    self.send_response(303)
                    self.send_header('Location', '/')
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('Referrer-Policy', 'no-referrer')
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                except StateError as exc:
                    self.send('<h1>X-Anmeldung nicht abgeschlossen</h1><p>' + html.escape(str(exc)) + '</p><a href="/">Zurück zur Leseliste</a>', exc.status, 'text/html; charset=utf-8')
            elif path == '/api/state':
                self.send(self.app.payload())
            elif path == '/api/sync/preview':
                self.send(self.app.store.preview())
            elif path in ('/', '/index.html', '/app.js', '/styles.css'):
                filename = 'index.html' if path == '/' else path[1:]
                types = {'index.html': 'text/html; charset=utf-8', 'app.js': 'text/javascript; charset=utf-8', 'styles.css': 'text/css; charset=utf-8'}
                self.send((self.app.root / filename).read_bytes(), content_type=types[filename])
            elif path == '/favicon.ico':
                self.send(b'', 204, 'image/x-icon')
            else:
                self.send({'error': 'Nicht gefunden.'}, 404)
        except StateError as exc:
            self.error(exc)
        except (OSError, ValueError, KeyError):
            self.send({'error': 'Die lokalen Daten konnten nicht geladen werden.'}, 500)

    def do_POST(self):
        try:
            self.safe_host()
            if self.headers.get('Origin') != self.app.origin or self.headers.get('X-Triage-Request') != '1':
                raise StateError('Diese Aktion muss aus der lokalen Oberfläche kommen.', 403)
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise StateError('JSON-Anfrage erforderlich.', 415)
            length = int(self.headers.get('Content-Length', '0'))
            if length <= 0 or length > 65536:
                raise StateError('Ungültige Anfragegröße.', 413)
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise StateError('Ungültige Anfrage.')
            path = urllib.parse.urlsplit(self.path).path
            if path == '/api/fetch-x':
                self.send(self.app.fetch_x(body.get('id')))
            elif path == '/api/decisions':
                self.app.store.decide(body.get('version'), body.get('ids'), body.get('choice'))
                self.send(self.app.payload())
            elif path == '/api/undo':
                self.app.store.undo(body.get('version'))
                self.send(self.app.payload())
            elif path == '/api/connect':
                self.send(self.app.connection.begin())
            elif path == '/api/x/connect':
                approval = {'users': body.get('users', 0), 'posts': body.get('posts', 0),
                            'mutations': body.get('mutations', 0), 'verification_posts': body.get('verification_posts', 0),
                            'usd': body.get('usd', 0)}
                result = self.app.x_connection.begin(body.get('clientId'), approval)
                self.send(result)
            elif path == '/api/x/disconnect':
                self.app.x_connection.disconnect()
                self.send(self.app.payload())
            elif path == '/api/refresh':
                self.send(self.app.refresh())
            elif path == '/api/sync':
                self.send(self.app.sync(body.get('version'), body.get('ids')))
            elif path == '/api/karakeep/connect':
                self.send(self.app.connect_karakeep(body.get('apiKey')))
            elif path == '/api/karakeep/save':
                self.send(self.app.save_to_karakeep(body.get('version'), body.get('id')))
            else:
                self.send({'error': 'Nicht gefunden.'}, 404)
        except StateError as exc:
            self.error(exc)
        except (ValueError, KeyError, TypeError):
            self.send({'error': 'Die Anfrage konnte nicht verarbeitet werden.'}, 400)
        except OSError:
            self.send({'error': 'Die Entscheidung konnte nicht gespeichert werden. Bitte freien Speicherplatz prüfen.'}, 500)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8769)
    parser.add_argument('--state', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    origin = 'http://127.0.0.1:%s' % args.port
    app = App(root, origin, args.state)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.app = app
    print('Lesestapel öffnen: ' + origin, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()

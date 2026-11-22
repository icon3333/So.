#!/usr/bin/env python3
import argparse
import html
import json
import re
import secrets
import threading
import urllib.error
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from n8n_connection import Connection
from karakeep_connection import KarakeepConnection
from triage_state import Store, StateError, now
from x_content import fetch as fetch_x_content, is_x_article, x_post_id, load as load_x_content, save as save_x_content
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
        self.operation_lock = threading.RLock()
        self.removal_confirmations = {}

    def payload(self):
        if getattr(self, 'x_content_path', None) and self.x_content_path.exists():
            self.x_content = load_x_content(self.x_content_path)
        content = dict(getattr(self, 'x_content', {'entries': {}})['entries'])
        public_posts = {x_post_id(a.get('url')): content[a['id']] for a in self.articles
                        if a.get('origin') != 'x' and a['id'] in content and x_post_id(a.get('url'))}
        for article in self.articles:
            if (article.get('origin') == 'x' and article.get('visibility') == 'public'
                    and article['id'] not in content and article.get('postId') in public_posts):
                content[article['id']] = public_posts[article['postId']]
        x_connection = getattr(self, 'x_connection', None)
        x_status = x_connection.status() if x_connection else {'ready': False, 'connected': False, 'account': None}
        return {'articles': self.articles, 'eligibleIds': sorted(getattr(self, 'eligible_ids', {a['id'] for a in self.articles})), 'xContent': {'schemaVersion': 1, 'entries': content},
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

    @staticmethod
    def _normalize_x_post(post, account_id, username, user_map):
        post_id = post.get('id')
        if not isinstance(post_id, str) or not re.fullmatch(r'[0-9]+', post_id):
            return None
        author_id = post.get('author_id')
        user = user_map.get(author_id) if isinstance(author_id, str) else None
        handle = user.get('username') if isinstance(user, dict) else None
        verified_handle = handle if isinstance(handle, str) and re.fullmatch(r'[A-Za-z0-9_]{1,15}', handle) else None
        link = 'https://x.com/%s/status/%s' % (verified_handle, post_id) if verified_handle else 'https://x.com/i/status/%s' % post_id
        raw = post.get('text')
        article = post.get('article')
        note = post.get('note_post') or post.get('note_tweet')
        if isinstance(note, dict):
            raw = note.get('text') or raw
        if isinstance(article, dict):
            raw = article.get('text') or article.get('body') or raw
        text = raw if isinstance(raw, str) else ''
        protection = user.get('protected') if isinstance(user, dict) else None
        visibility = 'public' if protection is False else ('protected' if protection is True else 'unknown')
        return {'postId': post_id, 'ref': 'x-%s-%s' % (account_id, post_id), 'url': link,
                'title': re.sub(r'\s+', ' ', text).strip()[:140] if text else 'X-Beitrag · Inhalt fehlt',
                'excerpt': text[:1200], 'text': text, 'author': ('@' + verified_handle) if verified_handle else '',
                'publisher': {'title': 'X', 'url': 'https://x.com'}, 'createdAt': post.get('created_at'),
                'missingContent': not bool(text), 'contentStatus': 'partial' if text else 'unavailable',
                'visibility': visibility, 'limitations': ['Thread- und Medieninhalte sind nicht vollständig verifiziert.',
                'Lokale Hermes-Zusammenfassung fehlt.'],
                'media': post.get('attachments', {}).get('media_keys', []) if isinstance(post.get('attachments'), dict) else []}

    def import_x_bookmarks(self, approval, max_pages=10, max_results=100, restart=False):
        if type(restart) is not bool:
            raise StateError('Ein Neustart muss ausdrücklich bestätigt werden.', 400)
        if type(max_pages) is not int or max_pages < 1 or max_pages > 1000:
            raise StateError('Seitenobergrenze muss zwischen 1 und 1000 liegen.', 400)
        if type(max_results) is not int or max_results < 1 or max_results > 100:
            raise StateError('Seitengröße muss zwischen 1 und 100 Posts liegen.', 400)
        with self.operation_lock:
            ledger = self.x_connection.new_operation(approval)
            with self.x_connection.lock:
                account_id = self.x_connection.account['id']
                identity = (account_id, id(self.x_connection.tokens), self.x_connection.tokens['access_token'])
                username = self.x_connection.account.get('username', '')
            try:
                if self.store.x_account_id not in (None, account_id):
                    raise StateError('Das X-Konto stimmt nicht mit dem gespeicherten Bestand überein.', 409)
                inventory = self.x_bookmarks.begin_inventory(account_id, restart=restart)
            except ValueError as exc:
                raise StateError(str(exc), 409) from None
            cursor, seen = inventory['checkpoint'], list(inventory['seen'])
            cursors = set(self.x_bookmarks.data.get('cursorHistory', []) if inventory['started'] else [])
            if cursor:
                cursors.add(cursor)
            pages = 0
            begin = not inventory['started']
            previously_complete = bool(self.x_bookmarks.data.get('complete'))
            partial_reason = None
            for _ in range(max_pages):
                with self.x_connection.lock:
                    if (not self.x_connection.tokens or (self.x_connection.account or {}).get('id') != identity[0]
                            or id(self.x_connection.tokens) != identity[1]
                            or self.x_connection.tokens.get('access_token') != identity[2]):
                        raise StateError('X-Konto oder Sitzung wurde während des Imports geändert.', 409)
                with ledger.lock:
                    remaining = {k: ledger.approval[k] - ledger.reserved[k] for k in ledger.COUNTS}
                    include_author = ledger.approval['users'] > 0
                    rate = ledger.RATES['posts'] + (ledger.RATES['users'] if include_author else 0)
                    affordable = int((ledger.approval['usd'] - ledger.spent_ceiling) / rate)
                    page_size = min(max_results, remaining['posts'], affordable,
                                    remaining['users'] if include_author else max_results)
                if page_size < 1 or remaining['pages'] < 1:
                    if pages == 0:
                        raise StateError('Die Kostenfreigabe reicht nicht für eine weitere Importseite.', 403)
                    partial_reason = 'budget_exhausted'
                    break
                if begin and previously_complete:
                    # Invalidate old absence proof before a fresh, approved scan can fail.
                    self.refresh_catalog()
                    self.store.mark_x_membership_unknown(account_id)
                    self.x_bookmarks.merge_page(account_id, [], None, begin=True, partial=True)
                    begin = False
                try:
                    response = self.x_connection.bookmarks_page(ledger, account_id, cursor, page_size,
                                                                include_author=include_author)
                except StateError as exc:
                    if pages == 0:
                        raise
                    partial_reason = 'api_stopped_%s' % exc.status
                    break
                pages += 1
                with self.x_connection.lock:
                    if (not self.x_connection.tokens or id(self.x_connection.tokens) != identity[1]
                            or (self.x_connection.account or {}).get('id') != account_id
                            or self.x_connection.tokens.get('access_token') != identity[2]):
                        partial_reason = 'session_changed'
                        break
                if not isinstance(response, dict):
                    response = {}
                errors = response.get('errors')
                meta = response.get('meta') if isinstance(response.get('meta'), dict) else {}
                data = response.get('data')
                if data is None and meta.get('result_count') == 0:
                    data = []
                if not isinstance(data, list):
                    data = []
                    errors = errors or [{'title': 'Invalid response'}]
                includes = response.get('includes') if isinstance(response.get('includes'), dict) else {}
                users = includes.get('users') if isinstance(includes.get('users'), list) else []
                if len(data) > page_size or len(users) > (page_size if include_author else 0):
                    raise StateError('X lieferte mehr Ressourcen als angefordert. Keine weitere Anfrage wurde gesendet.', 502)
                user_map = {u.get('id'): u for u in users if isinstance(u, dict) and isinstance(u.get('id'), str)}
                normalized, ids = [], []
                for post in data:
                    item = self._normalize_x_post(post, account_id, username, user_map) if isinstance(post, dict) else None
                    if item:
                        normalized.append(item)
                        ids.append(item['postId'])
                result_count = meta.get('result_count') if isinstance(response.get('meta'), dict) else None
                malformed = (not isinstance(response.get('meta'), dict) or len(normalized) != len(data)
                             or len(set(ids)) != len(ids)
                             or type(result_count) is not int
                             or result_count != len(data))
                if errors or malformed:
                    details = errors if isinstance(errors, list) else []
                    title = ' '.join(str(e.get('title', '')) + ' ' + str(e.get('detail', ''))
                                     for e in details if isinstance(e, dict)).lower()
                    invalid = 'pagination' in title and ('invalid' in title or 'token' in title)
                    self.x_bookmarks.merge_page(account_id, normalized, cursor, seen_ids=ids, partial=True,
                                                restart_required=invalid, begin=begin,
                                                cursor_history=sorted(cursors))
                    begin = False
                    partial_reason = 'invalid_cursor' if invalid else ('api_errors' if errors else 'invalid_response')
                    break
                token = meta.get('next_token')
                if token is not None and (not isinstance(token, str) or not token or len(token) > 4096 or token in cursors):
                    self.x_bookmarks.merge_page(account_id, normalized, cursor, seen_ids=ids, partial=True,
                                                restart_required=True, begin=begin, cursor_history=sorted(cursors))
                    begin = False
                    partial_reason = 'repeated_or_invalid_cursor'
                    break
                complete = token is None
                merged_seen = list(dict.fromkeys(seen + ids))
                self.x_bookmarks.merge_page(account_id, normalized, token, complete=complete,
                                            seen_ids=ids, begin=begin,
                                            cursor_history=sorted(cursors | ({cursor} if cursor else set())))
                begin = False
                seen = merged_seen
                cursor = token
                self.refresh_catalog()
                if complete:
                    self.store.apply_x_remote(account_id, seen, now())
                    break
                cursors.add(token)
            self.refresh_catalog()
            out = self.payload()
            out['xImport'] = {**out['xImport'], 'pagesThisRun': pages, 'budget': ledger.status(),
                              'partialReason': partial_reason}
            return out

    def fetch_x(self, article_id):
        with self.operation_lock:
            if (not isinstance(article_id, str) or article_id not in getattr(self, 'x_ids', self.eligible_ids)
                    or article_id.startswith('x:')):
                raise StateError('Nur X-Artikel können abgerufen werden.')
            if getattr(self, 'x_content_path', None) and self.x_content_path.exists():
                self.x_content = load_x_content(self.x_content_path)
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
            origin_label = 'X-Lesezeichen' if self.store.is_native(article_id) else 'Inoreader „Später lesen“'
            receipt = self.karakeep.save(self.store.articles[article_id]['url'])
            try:
                self.store.record_karakeep_saved(version, article_id, receipt)
            except OSError:
                raise StateError('In Karakeep gespeichert, aber die lokale Vormerkung konnte nicht gespeichert werden. Bitte freien Speicherplatz prüfen; %s wurde nicht verändert.' % origin_label, 500) from None
            result = self.payload()
            result['result'] = {'message': 'In Karakeep gespeichert. Entfernung aus %s ist vorgemerkt; geändert wird die Herkunft erst nach separater Vorschau und deiner Bestätigung.' % origin_label}
            return result

    def preview_removals(self):
        with self.operation_lock, self.store.lock:
            preview = self.store.preview()
            groups = {'inoreader': {'count': 0, 'available': bool(self.connection.status().get('connected'))},
                      'x': {'count': 0, 'available': bool(self.x_connection.status().get('connected'))}}
            for item in preview['items']:
                groups['x' if self.store.is_native(item['id']) else 'inoreader']['count'] += 1
            account = self.x_bookmarks.data.get('accountId')
            session = id(self.x_connection.tokens) if groups['x']['available'] else None
            revision = self.x_bookmarks.data.get('revision') if getattr(self, 'x_bookmarks', None) else None
            token = secrets.token_urlsafe(32)
            self.removal_confirmations = {token: {'version': preview['version'], 'ids': tuple(i['id'] for i in preview['items']),
                'revision': revision, 'account': account, 'session': session, 'xAvailable': groups['x']['available']}}
            preview.update(catalogRevision=revision, accountId=account, confirmationToken=token,
                groups=groups, xCost={'deleteUsdEach': 0.010, 'verificationPostUsdEach': 0.005,
                                      'verificationPosts': max(100, len(self.x_bookmarks.data['entries'])),
                                      'pages': max(1, (len(self.x_bookmarks.data['entries']) + 99) // 100)})
            return preview

    def confirm_removals(self, version, ids, token, revision, approval):
        with self.operation_lock, self.store.lock:
            if (not isinstance(token, str) or type(revision) is not int or
                    not isinstance(ids, list) or any(not isinstance(i, str) for i in ids)):
                raise StateError('Eine gültige Entfernungsvorschau ist erforderlich.', 409)
            binding = self.removal_confirmations.get(token)
            if (not binding or binding['version'] != version or binding['ids'] != tuple(ids)
                    or binding['revision'] != revision):
                raise StateError('Vorschau ist veraltet oder wurde bereits verwendet. Bitte neu prüfen.', 409)
            self.store.check_sync(version, ids)
            if self.x_bookmarks.data.get('revision') != revision:
                raise StateError('X-Katalog hat sich geändert. Bitte neu prüfen.', 409)
            native = [i for i in ids if self.store.is_native(i)]
            ir = [i for i in ids if not self.store.is_native(i)]
            ledger = None
            if native and binding['xAvailable']:
                if (not self.x_connection.status().get('connected') or
                    (self.x_connection.account or {}).get('id') != binding['account'] or id(self.x_connection.tokens) != binding['session']):
                    raise StateError('X-Sitzung oder Konto hat sich geändert. Bitte neu prüfen.', 409)
                ledger = self.x_connection.new_operation(approval)
                cap = ledger.approval
                if cap['verification_posts'] < 1 or cap['pages'] < 1:
                    raise StateError('Vor der Entfernung muss eine kostenbegrenzte Rückprüfung freigegeben sein.', 403)
                ledger.require_capacity({'mutations': len(native), 'verification_posts': cap['verification_posts'], 'pages': cap['pages']})
            # A nonce is spent before any read/write side effect, including ambiguous failures.
            self.removal_confirmations.pop(token)
            confirmed, skipped = [], []
            if ir and self.connection.status().get('connected'):
                try:
                    result=self.sync(version, ir)
                    confirmed += result.get('result',{}).get('confirmed',[])
                except (StateError, OSError):
                    pass  # Source remains unconfirmed; never roll back a different provider.
            elif ir:
                skipped.extend(ir)
            proof_complete = False
            if native and ledger is not None:
                for article_id in native:
                    try:
                        self.x_connection.delete_bookmark(ledger, binding['account'], self.store.articles[article_id]['postId'])
                    except StateError as exc:
                        if exc.status in (401, 403, 409, 429):
                            break
                        # A timeout may mean success. Do not repeat this DELETE.
                try:
                    remote=self._check_x_ledger(ledger)
                    proof_complete = remote['complete']
                    if proof_complete:
                        stars=set(remote['starredIds'])
                        for article_id in native:
                            if self.store.articles[article_id]['postId'] not in stars:
                                confirmed.append(article_id)
                except (StateError, OSError):
                    pass  # Readback or local persistence failed: keep every native item unclear.
            elif native:
                skipped.extend(native)
            confirmed = [i for i in ids if i in set(confirmed)]
            unconfirmed = [i for i in ids if i not in confirmed]
            out=self.payload()
            out['result']={'confirmed':confirmed,'unconfirmed':unconfirmed,
                'skipped':skipped, 'verificationComplete':not unconfirmed,
                'xVerificationComplete':proof_complete, 'budget':ledger.status() if ledger else None,
                'message':('%s Entfernungen bestätigt; %s noch offen oder unklar. Nicht verbundene Dienste bleiben unverändert. Es wurde kein DELETE automatisch wiederholt.' % (len(confirmed),len(unconfirmed)))}
            return out

    def check_x_membership(self, approval):
        with self.operation_lock, self.store.lock:
            ledger = self.x_connection.new_operation(approval)
            if ledger.approval['verification_posts'] < 1 or ledger.approval['pages'] < 1:
                raise StateError('Die reine X-Standprüfung benötigt eine ausdrückliche Lese- und Kostenfreigabe.', 403)
            if ledger.approval['mutations']:
                raise StateError('Die Standprüfung darf keine Mutationen freigeben.', 400)
            membership = self._check_x_ledger(ledger)
            out = self.payload()
            out['xMembership'] = membership
            return out

    def _check_x_ledger(self, ledger):
        membership = self.x_connection.membership(ledger)
        with self.x_connection.lock:
            if (id(self.x_connection.tokens) != ledger.session_id or
                    (self.x_connection.account or {}).get('id') != ledger.account_id):
                membership = {**membership, 'complete': False, 'reason': 'session_changed'}
                membership.pop('starredIds', None)
            if membership['complete']:
                self.store.apply_x_remote(ledger.account_id, membership['starredIds'], now())
        return membership

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
                self.send(self.app.preview_removals())

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
            elif path == '/api/x/import':
                approval = {key: body.get(key, 0) for key in ('posts', 'users', 'mutations', 'verification_posts', 'pages', 'usd')}
                self.send(self.app.import_x_bookmarks(approval, body.get('maxPages', 10),
                                                      body.get('maxResults', 100), body.get('restart', False)))
            elif path == '/api/refresh':
                self.send(self.app.refresh())
            elif path == '/api/x/check':
                approval = {key: body.get(key, 0) for key in ('posts','users','mutations','verification_posts','pages','usd')}
                self.send(self.app.check_x_membership(approval))
            elif path == '/api/sync':
                approval = {key: body.get(key, 0) for key in ('posts','users','mutations','verification_posts','pages','usd')}
                self.send(self.app.confirm_removals(body.get('version'), body.get('ids'), body.get('confirmationToken'),
                                                     body.get('catalogRevision'), approval))
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

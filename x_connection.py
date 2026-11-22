"""In-memory public-client OAuth and bounded X API budget primitives."""
import base64
from decimal import Decimal
import hashlib
import json
import math
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from triage_state import StateError

API = 'https://api.x.com'
CALLBACK_PATH = '/oauth/x/callback'
SCOPES = ('bookmark.read', 'bookmark.write', 'tweet.read', 'users.read')
POST_RATE = 0.005
USER_RATE = 0.010
DELETE_CEILING = 0.010


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _safe_json_request(url, data=None, headers=None, form=False, method=None):
    if method not in (None, 'DELETE'):
        raise StateError('X-Anfragemethode nicht zugelassen.', 400)
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != 'https' or parts.hostname != 'api.x.com' or parts.port not in (None, 443) or parts.username or parts.password:
        raise StateError('X-Verbindung: ungültiger Endpunkt.', 502)
    request_headers = dict(headers or {})
    raw = None
    if data is not None:
        raw = urllib.parse.urlencode(data).encode() if form else json.dumps(data).encode()
        request_headers['Content-Type'] = 'application/x-www-form-urlencoded' if form else 'application/json'
    request = urllib.request.Request(url, data=raw, headers=request_headers, method=method)
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
            body = response.read(1_000_001)
            if len(body) > 1_000_000:
                raise StateError('X-Verbindung: Antwort zu groß.', 502)
            value = json.loads(body)
            if not isinstance(value, dict):
                raise StateError('X-Verbindung: ungültige Antwort.', 502)
            return value
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise StateError('X-Anmeldung abgelaufen oder nicht autorisiert; bitte erneut verbinden.', 401) from None
        if exc.code == 403:
            raise StateError('X-Zugriff verweigert; Konto, Freigaben und Berechtigungen prüfen.', 403) from None
        if exc.code == 429:
            raise StateError('X hat das Anfragelimit erreicht. Es wurde nichts automatisch wiederholt.', 429) from None
        raise StateError('X-Anfrage fehlgeschlagen (HTTP %s); Antwortdetails wurden verborgen.' % exc.code, 502) from None
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        raise StateError('X ist nicht erreichbar oder lieferte keine gültige Antwort.', 502) from None


class BudgetLedger:
    """Thread-safe in-memory maximum resource allowance; reservations never refund."""
    COUNTS = ('posts', 'users', 'mutations', 'verification_posts', 'pages')
    RATES = {'posts': Decimal('0.005'), 'users': Decimal('0.010'),
             'mutations': Decimal('0.010'), 'verification_posts': Decimal('0.005'),
             'pages': Decimal('0')}

    def __init__(self, approval=None):
        self.lock = threading.RLock()
        self.reserved = {key: 0 for key in self.COUNTS}
        self.spent_ceiling = Decimal('0')
        self.approval = None
        if approval is not None:
            self.approval = self._approval(approval)

    @staticmethod
    def _count(value):
        if type(value) is not int or value < 0:
            raise StateError('Kostenfreigabe ungültig; Mengen müssen nichtnegative ganze Zahlen sein.', 400)
        return value

    @classmethod
    def _approval(cls, approval):
        if not isinstance(approval, dict):
            raise StateError('Für jeden X-Vorgang ist eine ausdrückliche Kostenfreigabe erforderlich.', 403)
        cap = {key: cls._count(approval.get(key, 0)) for key in cls.COUNTS}
        raw_usd = approval.get('usd', 0)
        if isinstance(raw_usd, bool) or not isinstance(raw_usd, (int, float)) or not math.isfinite(raw_usd) or raw_usd < 0:
            raise StateError('Kostenfreigabe ungültig; USD-Grenze muss endlich und nichtnegativ sein.', 400)
        return {**cap, 'usd': Decimal(str(raw_usd))}

    def reserve(self, requested, approval=None):
        if not isinstance(requested, dict):
            raise StateError('Ungültige X-Ressourcenmenge.', 400)
        with self.lock:
            req = {key: self._count(requested.get(key, 0)) for key in self.COUNTS}
            if approval is not None:
                cap = self._approval(approval)
                if self.approval is not None and self.approval != cap:
                    raise StateError('Eine laufende X-Kostenfreigabe darf nicht erweitert werden.', 409)
                self.approval = cap
            cap = self.approval
            if cap is None:
                raise StateError('Eine ausdrückliche X-Kostenfreigabe fehlt.', 403)
            if any(self.reserved[key] + req[key] > cap[key] for key in self.COUNTS):
                raise StateError('Die freigegebene X-Ressourcenmenge reicht für diesen Vorgang nicht aus.', 403)
            ceiling = sum(req[key] * self.RATES[key] for key in self.COUNTS)
            if self.spent_ceiling + ceiling > cap['usd']:
                raise StateError('Die ausdrückliche USD-Obergrenze reicht für diesen X-Vorgang nicht aus.', 403)
            for key in self.COUNTS:
                self.reserved[key] += req[key]
            self.spent_ceiling += ceiling
            return {'resources': dict(req), 'usdCeiling': float(ceiling)}

    def status(self):
        with self.lock:
            return {'reserved': dict(self.reserved), 'usdCeilingReserved': float(self.spent_ceiling)}

    def require_capacity(self, requested):
        """Validate a complete write-plus-readback allowance without spending it twice."""
        with self.lock:
            req = {key: self._count(requested.get(key, 0)) for key in self.COUNTS}
            if self.approval is None or any(self.reserved[k] + req[k] > self.approval[k] for k in self.COUNTS):
                raise StateError('Die Freigabe deckt Entfernung und Rückprüfung nicht vollständig ab.', 403)
            cost = sum(req[k] * self.RATES[k] for k in self.COUNTS)
            if self.spent_ceiling + cost > self.approval['usd']:
                raise StateError('Die USD-Freigabe muss Entfernung und Rückprüfung gemeinsam abdecken.', 403)


class XConnection:
    def __init__(self, origin, catalog):
        self.origin, self.catalog = origin, catalog
        self.redirect_uri = origin + CALLBACK_PATH
        self.lock = threading.RLock()
        self.client_id = None
        self.pending = None
        self.tokens = None
        self.account = None


    def status(self):
        with self.lock:
            expected = self.catalog.data.get('accountId')
            connected = bool(self.tokens and self.tokens['expires_at'] > time.time()
                            and self.account and (expected is None or expected == self.account['id']))
            return {'ready': True, 'connected': connected,
                    'account': ({'id': self.account['id'], 'username': self.account.get('username', '')}
                                if connected else None),
                    'message': 'Mit X-Konto @%s verbunden' % self.account.get('username', self.account['id'])
                    if connected else 'X-Client-ID eingeben und den bezahlten Identitätsabruf ausdrücklich freigeben.'}

    def disconnect(self):
        with self.lock:
            self.pending = self.tokens = self.account = None
            return self.status()

    def begin(self, client_id, approval):
        if not isinstance(client_id, str) or not client_id.strip() or len(client_id) > 256 or any(c.isspace() for c in client_id):
            raise StateError('Bitte eine gültige öffentliche X-Client-ID eingeben.', 400)
        budget = BudgetLedger(approval)
        budget.reserve({'users': 1})
        with self.lock:
            verifier = secrets.token_urlsafe(64)
            state = secrets.token_urlsafe(32)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
            self.pending = {'state': state, 'verifier': verifier, 'expires': time.time() + 600,
                            'client_id': client_id.strip(), 'budget': budget}
            params = {'response_type': 'code', 'client_id': client_id.strip(),
                      'redirect_uri': self.redirect_uri, 'scope': ' '.join(SCOPES),
                      'state': state, 'code_challenge': challenge, 'code_challenge_method': 'S256'}
            return {'url': 'https://x.com/i/oauth2/authorize?' + urllib.parse.urlencode(params)}

    def finish(self, query):
        with self.lock:
            pending = self.pending
            if (not pending or pending['expires'] <= time.time()
                    or not secrets.compare_digest(str((query.get('state') or [''])[0]), pending['state'])):
                raise StateError('X-Anmeldung abgelaufen oder ungültig; bitte neu beginnen.', 400)
            self.pending = None
            code = (query.get('code') or [''])[0]
            if (query.get('error') or not code or len(code) > 4096
                    or any(c in code for c in '\r\n')):
                raise StateError('X-Anmeldung wurde nicht abgeschlossen.', 400)
            token = _safe_json_request(API + '/2/oauth2/token', {
                'grant_type': 'authorization_code', 'client_id': pending['client_id'], 'redirect_uri': self.redirect_uri,
                'code': code, 'code_verifier': pending['verifier']}, form=True)
            # Do not replace a working session until scopes and identity both pass.
            tokens = self._parse_token(token)
            access = tokens['access_token']
            me = _safe_json_request(API + '/2/users/me?user.fields=id,username', headers={'Authorization': 'Bearer ' + access})
            data = me.get('data')
            if (me.get('errors') or not isinstance(data, dict) or not isinstance(data.get('id'), str)
                    or not data['id'].isascii() or not data['id'].isdigit() or len(data['id']) > 19):
                raise StateError('X-Kontoidentität konnte nicht bestätigt werden.', 502)
            expected = self.catalog.data.get('accountId')
            if expected and expected != data['id']:
                raise StateError('Das angemeldete X-Konto stimmt nicht mit dem vorhandenen Lesezeichenbestand überein.', 409)
            self.tokens, self.client_id = tokens, pending['client_id']
            self.account = {'id': data['id'], 'username': data.get('username', '')}
            return self.status()

    @staticmethod
    def _parse_token(result):
        if not isinstance(result, dict) or not isinstance(result.get('access_token'), str) or not result['access_token']:
            raise StateError('X-Anmeldung konnte nicht bestätigt werden.', 502)
        if str(result.get('token_type', '')).lower() != 'bearer':
            raise StateError('X-Anmeldung lieferte keinen Bearer-Token.', 502)
        expires = result.get('expires_in')
        if type(expires) is not int or expires <= 0:
            raise StateError('X-Anmeldung lieferte keine gültige Ablaufzeit.', 502)
        scopes = result.get('scope', [])
        if isinstance(scopes, str):
            scopes = scopes.split()
        if not isinstance(scopes, list) or any(not isinstance(s, str) for s in scopes):
            raise StateError('X-Anmeldung lieferte ungültige Berechtigungen.', 502)
        if not set(SCOPES).issubset(scopes):
            raise StateError('X-App fehlen erforderliche Berechtigungen. Bitte Scopes prüfen und erneut verbinden.', 403)
        return {'access_token': result['access_token'], 'expires_at': time.time() + expires,
                'scopes': scopes}

    def new_operation(self, approval):
        with self.lock:
            if not self.tokens or self.tokens['expires_at'] <= time.time():
                self.tokens = self.account = None
                raise StateError('X-Anmeldung abgelaufen; bitte erneut verbinden.', 401)
            expected = self.catalog.data.get('accountId')
            if not self.account or (expected and expected != self.account['id']):
                raise StateError('Das X-Konto stimmt nicht mit dem gespeicherten Bestand überein.', 409)
            if not set(SCOPES).issubset(self.tokens.get('scopes', [])):
                raise StateError('Der X-Anmeldung fehlen erforderliche Berechtigungen.', 403)
            ledger = BudgetLedger(approval)
            ledger.account_id, ledger.session_id = self.account['id'], id(self.tokens)
            return ledger

    def bookmarks_page(self, ledger, account_id, cursor=None, max_results=100, include_author=True, verification=False):
        if type(max_results) is not int or not 1 <= max_results <= 100:
            raise StateError('X-Lesezeichenseiten müssen 1 bis 100 Posts anfordern.', 400)
        if cursor is not None and (not isinstance(cursor, str) or not cursor or len(cursor) > 4096):
            raise StateError('Ungültiger X-Seitencursor.', 400)
        with self.lock:
            if (not isinstance(ledger, BudgetLedger) or getattr(ledger, 'account_id', None) != account_id
                    or getattr(ledger, 'session_id', None) != id(self.tokens)):
                raise StateError('Die X-Kostenfreigabe gehört nicht zu dieser Sitzung.', 409)
            if (not self.tokens or self.tokens['expires_at'] <= time.time()
                    or not self.account or self.account['id'] != account_id):
                raise StateError('X-Anmeldung oder Konto hat sich geändert; bitte Import neu starten.', 409)
            if not set(SCOPES).issubset(self.tokens.get('scopes', [])):
                raise StateError('Der X-Anmeldung fehlen erforderliche Berechtigungen.', 403)
            if self.catalog.data.get('accountId') not in (None, account_id):
                raise StateError('Das X-Konto stimmt nicht mit dem gespeicherten Bestand überein.', 409)
            # Reserve worst-case posts and expanded user records before transport.
            ledger.reserve({'verification_posts': max_results, 'posts': 0, 'users': 0, 'pages': 1} if verification else
                           {'posts': max_results, 'users': max_results if include_author else 0, 'pages': 1})
            params = {'max_results': max_results,
                      'post.fields': 'id' if verification else 'id,text,created_at,article,note_post,attachments,entities',
                      'expansions': 'author_id' if include_author and not verification else '',
                      'user.fields': 'id,username,protected' if include_author and not verification else ''}
            if cursor is not None:
                params['pagination_token'] = cursor
            query = urllib.parse.urlencode({k: v for k, v in params.items() if v})
            url = API + '/2/users/' + account_id + '/bookmarks?' + query
            return _safe_json_request(url, headers={'Authorization': 'Bearer ' + self.tokens['access_token']})

    def lookup_posts(self, ledger, ids):
        if not 1 <= len(ids) <= 100 or any(not i.isascii() or not i.isdigit() or len(i) > 19 for i in ids):
            raise StateError('Ungültige X-Beitragsauswahl.', 400)
        with self.lock:
            if (not self.tokens or self.tokens['expires_at'] <= time.time()
                    or id(self.tokens) != ledger.session_id
                    or (self.account or {}).get('id') != ledger.account_id):
                raise StateError('Die X-Sitzung hat sich geändert.', 409)
            ledger.reserve({'posts': len(ids)})
            query = urllib.parse.urlencode({'ids': ','.join(ids),
                'post.fields': 'id,text,created_at,article,note_post,attachments,entities'})
            return _safe_json_request(API + '/2/tweets?' + query,
                                      headers={'Authorization': 'Bearer ' + self.tokens['access_token']})

    def delete_bookmark(self, ledger, account_id, post_id):
        with self.lock:
            if (not isinstance(ledger, BudgetLedger) or ledger.account_id != account_id or ledger.session_id != id(self.tokens)
                    or not self.tokens or not self.account or self.account['id'] != account_id
                    or not isinstance(post_id, str) or not post_id.isascii() or not post_id.isdigit()):
                raise StateError('X-Sitzung oder Konto hat sich geändert.', 409)
            if (self.tokens['expires_at'] <= time.time() or self.catalog.data.get('accountId') != account_id
                    or not account_id.isascii() or not account_id.isdigit() or len(post_id) > 19):
                raise StateError('X-Sitzung abgelaufen oder gespeichertes Konto geändert.', 409)
            if not set(SCOPES).issubset(self.tokens.get('scopes', [])):
                raise StateError('Der X-Anmeldung fehlen erforderliche Berechtigungen.', 403)
            ledger.reserve({'mutations': 1})
            return _safe_json_request(API + '/2/users/' + account_id + '/bookmarks/' + post_id,
                headers={'Authorization': 'Bearer ' + self.tokens['access_token']}, method='DELETE')

    def membership(self, ledger):
        """Bounded read-only coverage proof, using the SAME ledger as any preceding writes."""
        account_id = ledger.account_id
        seen, cursor, cursors = set(), None, set()
        complete, reason = False, 'budget_exhausted'
        while True:
            with ledger.lock:
                posts_left = ledger.approval['verification_posts'] - ledger.reserved['verification_posts']
                pages_left = ledger.approval['pages'] - ledger.reserved['pages']
                affordable = int((ledger.approval['usd'] - ledger.spent_ceiling) / ledger.RATES['verification_posts'])
            size = min(100, posts_left, affordable)
            if size < 1 or pages_left < 1:
                break
            try:
                response = self.bookmarks_page(ledger, account_id, cursor, size, include_author=False, verification=True)
            except StateError as exc:
                reason = 'api_%s' % exc.status
                break
            if not isinstance(response, dict) or response.get('errors') or not isinstance(response.get('meta'), dict):
                reason = 'partial_response'
                break
            meta = response['meta']
            data = response.get('data', [] if meta.get('result_count') == 0 else None)
            if (not isinstance(data, list) or len(data) > size
                    or any(not isinstance(p, dict) or not isinstance(p.get('id'), str)
                           or not p['id'].isascii() or not p['id'].isdigit() or len(p['id']) > 19 for p in data)
                    or type(meta.get('result_count')) is not int or meta['result_count'] != len(data)):
                reason = 'invalid_response'
                break
            ids = [p['id'] for p in data]
            if len(set(ids)) != len(ids) or seen.intersection(ids):
                reason = 'overlapping_pages'
                break
            seen.update(ids)
            token = meta.get('next_token')
            if token is None:
                complete, reason = True, None
                break
            if not isinstance(token, str) or not token or token in cursors:
                reason = 'invalid_cursor'
                break
            cursors.add(token)
            cursor = token
        with self.lock:
            if (not self.tokens or self.tokens['expires_at'] <= time.time() or id(self.tokens) != ledger.session_id
                    or (self.account or {}).get('id') != account_id or self.catalog.data.get('accountId') != account_id):
                complete, reason = False, 'session_changed'
        result = {'complete': complete, 'reason': reason, 'budget': ledger.status()}
        if complete:
            result['starredIds'] = sorted(seen)
        return result

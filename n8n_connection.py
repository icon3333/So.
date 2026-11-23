"""Public-client OAuth. Authentication material lives only in process memory.

The browser receives a sign-in URL and status, never access/refresh tokens.
Only the existing n8n credential can access Inoreader.
"""
import base64
import hashlib
import json
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from triage_state import StateError, atomic_json


ISSUER = 'https://n8n.example.invalid'
WEBHOOK_PATH = '/webhook/inoreader-triage-20261111'
ALLOWED_HOSTS = {'n8n.example.invalid', 'n8n-webhook.example.invalid'}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_json(url, data=None, headers=None, form=False):
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != 'https' or parts.hostname not in ALLOWED_HOSTS or parts.port not in (None, 443):
        raise StateError('Unerwartete Adresse bei der Anmeldung.', 502)
    headers = dict(headers or {})
    raw = None
    if data is not None:
        raw = (urllib.parse.urlencode(data) if form else json.dumps(data)).encode()
        headers['Content-Type'] = 'application/x-www-form-urlencoded' if form else 'application/json'
    req = urllib.request.Request(url, data=raw, headers=headers)
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=110) as response:
            content = response.read(2_000_001)
            if len(content) > 2_000_000:
                raise StateError('Die Antwort ist zu groß.', 502)
            value = json.loads(content)
            if not isinstance(value, dict):
                raise StateError('Unerwartetes Antwortformat.', 502)
            return value
    except urllib.error.HTTPError as exc:
        # Never expose response bodies or request URLs containing OAuth parameters.
        if exc.code in (401, 403):
            raise StateError('Die Verbindung wurde nicht autorisiert. Bitte erneut bei n8n anmelden.', 401)
        raise StateError('n8n antwortet mit HTTP %s. Es wurde kein Erfolg bestätigt.' % exc.code, 502)
    except (urllib.error.URLError, TimeoutError, ValueError):
        raise StateError('n8n ist gerade nicht erreichbar oder liefert keine gültige Antwort. Bitte LAN/Tailscale prüfen.', 502)


class Connection:
    def __init__(self, root, origin):
        self.root, self.origin = root, origin
        self.redirect = origin + '/oauth/callback'
        self.lock = threading.RLock()
        self.pending = None
        self.tokens = None
        self.metadata = None
        self.resource = None
        self.webhook = None
        self.client_id = None

    def status(self):
        with self.lock:
            connected = bool(self.tokens and (self.tokens['expires_at'] > time.time() or self.tokens.get('refresh_token')))
            return {'ready': True, 'connected': connected,
                    'message': 'Mit n8n verbunden' if connected else 'Zum Abgleich einmal bei n8n anmelden. Deine Entscheidungen sind lokal gespeichert.'}

    def begin(self):
        with self.lock:
            meta = request_json(ISSUER + '/.well-known/oauth-authorization-server')
            if meta.get('issuer') != ISSUER or 'S256' not in meta.get('code_challenge_methods_supported', []):
                raise StateError('n8n bietet nicht die erwartete sichere Anmeldung an.', 502)
            resource_meta = request_json(ISSUER + '/.well-known/oauth-protected-resource' + WEBHOOK_PATH + '?method=POST')
            resource = resource_meta.get('resource', '')
            url = urllib.parse.urlsplit(resource)
            if url.hostname not in ALLOWED_HOSTS or url.scheme != 'https' or url.path != WEBHOOK_PATH or url.query != 'method=POST':
                raise StateError('Der Abgleich-Endpunkt wurde nicht erkannt.', 502)
            if ISSUER not in resource_meta.get('authorization_servers', []):
                raise StateError('Unbekannter Anmeldedienst.', 502)
            client_path = self.root / 'oauth-client.json'
            if client_path.exists():
                client = json.loads(client_path.read_text())
                if client.get('redirect') == self.redirect and client.get('issuer') == ISSUER:
                    self.client_id = client.get('client_id')
            if not self.client_id:
                registered = request_json(meta['registration_endpoint'], {
                    'client_name': 'Inoreader Triage lokal', 'redirect_uris': [self.redirect],
                    'grant_types': ['authorization_code', 'refresh_token'],
                    'response_types': ['code'], 'token_endpoint_auth_method': 'none'})
                if not isinstance(registered.get('client_id'), str) or registered.get('token_endpoint_auth_method') != 'none':
                    raise StateError('n8n hat keinen öffentlichen Anmeldeclient bereitgestellt.', 502)
                self.client_id = registered['client_id']
                atomic_json(client_path, {'client_id': self.client_id, 'redirect': self.redirect, 'issuer': ISSUER})
            verifier = secrets.token_urlsafe(48)
            state = secrets.token_urlsafe(32)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
            self.pending = {'state': state, 'verifier': verifier, 'expires': time.time() + 600}
            self.metadata, self.resource = meta, resource
            # Use the private admin hostname; the resource audience remains the canonical webhook URL.
            self.webhook = ISSUER + WEBHOOK_PATH
            return {'url': meta['authorization_endpoint'] + '?' + urllib.parse.urlencode({
                'client_id': self.client_id, 'redirect_uri': self.redirect, 'response_type': 'code',
                'resource': resource, 'state': state, 'code_challenge': challenge, 'code_challenge_method': 'S256'})}

    def finish(self, query):
        with self.lock:
            pending = self.pending
            if not pending or pending['expires'] < time.time() or not secrets.compare_digest(query.get('state', [''])[0], pending['state']):
                raise StateError('Die Anmeldung ist abgelaufen oder gehört nicht zu diesem Fenster.')
            self.pending = None
            if query.get('iss', [''])[0] != ISSUER:
                raise StateError('Der Absender der Anmeldung stimmt nicht überein.')
            if query.get('error') or not query.get('code'):
                raise StateError('Die Anmeldung wurde nicht abgeschlossen.')
            result = request_json(self.metadata['token_endpoint'], {
                'grant_type': 'authorization_code', 'client_id': self.client_id,
                'code': query['code'][0], 'redirect_uri': self.redirect,
                'code_verifier': pending['verifier'], 'resource': self.resource}, form=True)
            self.set_tokens(result)

    def set_tokens(self, result):
        if not isinstance(result.get('access_token'), str) or result.get('token_type', '').lower() != 'bearer':
            raise StateError('Die Anmeldung konnte nicht bestätigt werden.', 502)
        self.tokens = {'access_token': result['access_token'], 'refresh_token': result.get('refresh_token'),
                       'expires_at': time.time() + max(0, int(result.get('expires_in', 300)) - 30)}

    def call(self, operation, ids=None, entries_request=None):
        with self.lock:
            if not self.tokens:
                raise StateError('Bitte zuerst mit n8n verbinden.', 401)
            if self.tokens['expires_at'] <= time.time():
                if not self.tokens.get('refresh_token'):
                    self.tokens = None
                    raise StateError('Bitte erneut bei n8n anmelden.', 401)
                try:
                    self.set_tokens(request_json(self.metadata['token_endpoint'], {
                        'grant_type': 'refresh_token', 'client_id': self.client_id,
                        'refresh_token': self.tokens['refresh_token'], 'resource': self.resource}, form=True))
                except StateError:
                    self.tokens = None
                    raise
            body = {'operation': operation}
            if operation == 'entries':
                if not isinstance(entries_request, dict):
                    raise StateError('Ungültige Eintragsabfrage.', 400)
                body.update(entries_request)
            elif ids is not None:
                body['ids'] = ids
            try:
                result = request_json(self.webhook, body, {'Authorization': 'Bearer ' + self.tokens['access_token']})
            except StateError as exc:
                if exc.status == 401:
                    self.tokens = None
                raise
            if operation == 'entries':
                return result
            if result.get('complete') is not True or not isinstance(result.get('starredIds'), list) or not isinstance(result.get('checkedAt'), str):
                raise StateError('Inoreader hat keinen vollständigen überprüfbaren Stand geliefert.', 502)
            return result

    def read_later_inventory(self):
        """Count the complete current starred-ID set returned by the verified workflow."""
        result = self.call('read')
        if result.get('complete') is not True or not isinstance(result.get('starredIds'), list) or not isinstance(result.get('checkedAt'), str):
            raise StateError('Inoreader hat keinen vollständigen überprüfbaren Stand geliefert.', 502)
        ids = result['starredIds']
        if any(not isinstance(item, str) or not item for item in ids):
            raise StateError('Inoreader lieferte ungültige Lesezeichen-IDs.', 502)
        return {'count': len(set(ids)), 'coverage': 'complete', 'checkedAt': result['checkedAt']}

    def read_entries_page(self, cursor=None, limit=100):
        """Read one bounded page using the upgraded private workflow contract."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise StateError('Inoreader-Seitengröße muss zwischen 1 und 100 liegen.', 400)
        result = self.call('entries', entries_request={'cursor': cursor, 'limit': limit})
        if 'entries' not in result:
            raise StateError('Der n8n-Inoreader-Workflow ist veraltet; bitte den Eintrags-Endpunkt einrichten.', 409)
        if not isinstance(result.get('entries'), list):
            raise StateError('Der n8n-Inoreader-Workflow lieferte ungültige Einträge.', 502)
        if len(result['entries']) > limit:
            raise StateError('Inoreader lieferte mehr Einträge als angefordert.', 502)
        return result

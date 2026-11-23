import http.client
import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request

from n8n_connection import NoRedirect
from triage_state import StateError

API_ORIGIN = 'https://karakeep.example.invalid/api/v1'


def request_json(path, api_key, data=None):
    raw = None if data is None else json.dumps(data).encode()
    request = urllib.request.Request(API_ORIGIN + path, data=raw,
                                     headers={'Authorization': 'Bearer ' + api_key,
                                              'Content-Type': 'application/json'})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
            content = response.read(2_000_001)
            if len(content) > 2_000_000:
                raise StateError('Die Karakeep-Antwort ist zu groß.', 502)
            result = json.loads(content)
            if not isinstance(result, dict):
                raise StateError('Karakeep liefert kein gültiges Antwortformat.', 502)
            return result
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise StateError('Bitte Karakeep erneut verbinden. Der Schlüssel wurde nicht akzeptiert.', 401) from None
        raise StateError('Karakeep antwortet mit HTTP %s.' % exc.code, 502) from None
    except (OSError, ValueError, http.client.HTTPException):
        raise StateError('Karakeep ist nicht erreichbar oder liefert keine gültige Antwort.', 502) from None


def comparable_url(value):
    if not isinstance(value, str):
        raise StateError('Der Artikel hat keine gültige Originaladresse.')
    try:
        url = urllib.parse.urlsplit(value)
        if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password:
            raise ValueError()
        port = url.port or (443 if url.scheme == 'https' else 80)
        return url.scheme, url.hostname.lower(), port, url.path or '/', url.query, url.fragment
    except ValueError:
        raise StateError('Der Artikel hat keine gültige Originaladresse.') from None


class KarakeepConnection:
    def __init__(self):
        self.lock = threading.RLock()
        self._api_key = None

    def status(self):
        with self.lock:
            connected = bool(self._api_key)
            return {'connected': connected,
                    'message': 'Mit Karakeep verbunden' if connected else 'Zum Speichern Karakeep verbinden. Der Schlüssel bleibt nur für diese Sitzung im Speicher.'}

    def connect(self, api_key):
        if not isinstance(api_key, str) or not api_key.strip() or len(api_key) > 4096 or any(ord(c) < 32 for c in api_key):
            raise StateError('Bitte einen gültigen Karakeep-Schlüssel eingeben.')
        with self.lock:
            result = request_json('/users/me', api_key.strip())
            if not isinstance(result.get('id'), str) or not result['id']:
                raise StateError('Karakeep hat die Verbindung nicht bestätigt.', 502)
            self._api_key = api_key.strip()

    def save(self, url):
        expected_url = comparable_url(url)
        with self.lock:
            if not self._api_key:
                raise StateError('Bitte zuerst Karakeep verbinden.', 401)
            try:
                created = request_json('/bookmarks', self._api_key, {'type': 'link', 'url': url})
                bookmark_id = created.get('id')
                if not isinstance(bookmark_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', bookmark_id):
                    raise StateError('Karakeep hat keine gültige Lesezeichen-ID bestätigt.', 502)
                verified = request_json('/bookmarks/' + bookmark_id, self._api_key)
                content = verified.get('content')
                if (verified.get('id') != bookmark_id or not isinstance(content, dict)
                        or content.get('type') != 'link' or comparable_url(content.get('url')) != expected_url):
                    raise StateError('Das gespeicherte Lesezeichen stimmt nicht mit dem Artikel überein.', 502)
                return {'bookmarkId': bookmark_id, 'url': url}
            except StateError as exc:
                if exc.status == 401:
                    self._api_key = None
                raise StateError('Speichern in Karakeep ist nicht bestätigt; der Link könnte dort bereits vorhanden sein. Inoreader wurde nicht verändert und keine Entfernung vorgemerkt. ' + str(exc), exc.status) from None

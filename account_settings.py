"""Local account integration settings. All probes use trusted existing adapters."""
from triage_state import StateError

PROVIDERS = {
    'inoreader': ('Inoreader via n8n', ('configured',)),
    'x': ('X', ('clientId',)),
    'karakeep': ('Karakeep', ('apiKey',)),
    'tasksorg': ('Tasks.org / Posteo', ()),
}


def _public_error(exc):
    # Never return transport exception text, which may contain URLs or credentials.
    return {'state': 'error', 'message': str(exc) if isinstance(exc, StateError) else 'Verbindungstest fehlgeschlagen; vorhandene Zugangsdaten wurden beibehalten.'}


def account_status(app, provider):
    """Describe local configuration only; GET never probes a remote/provider adapter."""
    if provider not in PROVIDERS:
        return {'state': 'error', 'message': 'Unbekannter Anbieter.', 'fields': []}
    if provider == 'inoreader':
        return {'state': 'untested', 'message': 'Inoreader-Status wird nur durch einen ausdrücklichen Test bestätigt.', 'fields': []}
    if provider == 'x':
        return {'state': 'untested', 'message': 'X-Status wird nur durch OAuth-Identitätsprüfung bestätigt.',
                'fields': [{'name': 'clientId', 'type': 'text', 'label': 'Öffentliche X OAuth Client-ID', 'value': getattr(app.x_connection, 'client_id', None) or ''},
                           {'name': 'confirmCost', 'type': 'checkbox', 'label': 'Kostenpflichtigen Identitätsabruf (bis zu 0,01 USD) ausdrücklich bestätigen', 'value': False}]}
    if provider == 'karakeep':
        return {'state': 'untested', 'message': 'Karakeep-Status wird nur durch einen ausdrücklichen Test bestätigt.',
                'fields': [{'name': 'apiKey', 'type': 'password', 'label': 'Karakeep API-Schlüssel', 'value': ''}]}
    return {'state': 'untested', 'message': 'Posteo wird nur durch den vertrauenswürdigen Adapter ausdrücklich geprüft.', 'fields': []}


def account_action(app, provider, action, fields):
    if provider not in PROVIDERS:
        return {'state': 'error', 'message': 'Unbekannter Anbieter.'}
    if action not in ('connect', 'test'):
        return {'state': 'error', 'message': 'Unbekannte Aktion.'}
    if not isinstance(fields, dict):
        return {'state': 'error', 'message': 'Ungültige Einstellungen.'}
    try:
        if provider == 'inoreader':
            # The adapter owns its fixed OAuth issuer, webhook, and callback.
            if action == 'connect':
                result = app.connection.begin()
                return {'state': 'untested', 'message': 'Inoreader OAuth abschließen, danach ausdrücklich testen.', 'authorizationUrl': result['url']}
            app.connection.read_later_inventory()
            return {'state': 'connected', 'message': 'Der schreibgeschützte Inoreader-Test war erfolgreich.'}
        if provider == 'x':
            if fields.get('confirmCost') not in (True, 'yes'):
                raise StateError('Vor X-OAuth muss der kostenpflichtige Identitätsabruf ausdrücklich bestätigt werden.', 403)
            client_id = fields.get('clientId', getattr(app.x_connection, 'client_id', None))
            if action == 'connect':
                result = app.x_connection.begin(client_id, {'users': 1, 'usd': 0.01})
                return {'state': 'untested', 'message': 'OAuth bei X abschließen; die Identitätsprüfung ist kostenpflichtig (bis zu 0,01 USD).', 'authorizationUrl': result['url']}
            if app.x_connection.status().get('connected'):
                app.x_connection.verify_identity({'users': 1, 'usd': 0.01})
                return {'state': 'connected', 'message': 'X hat die Kontoidentität bestätigt.'}
            return {'state': 'unconfigured', 'message': 'X ist nicht verbunden; zuerst OAuth starten.'}
        if provider == 'karakeep':
            candidate = fields.get('apiKey')
            if action == 'connect':
                app.karakeep.connect(candidate)
                return {'state': 'connected', 'message': 'Karakeep-Verbindung bestätigt.'}
            # Test the pasted candidate only; never replace/clear the active key.
            if not isinstance(candidate, str) or not candidate.strip():
                return {'state': 'unconfigured', 'message': 'Zum Test einen Karakeep-Schlüssel eingeben.'}
            from karakeep_connection import request_json
            result = request_json('/users/me', candidate.strip())
            if not isinstance(result.get('id'), str) or not result['id']:
                raise StateError('Karakeep hat die Verbindung nicht bestätigt.', 502)
            return {'state': 'connected', 'candidate': True, 'message': 'Schreibgeschützter Karakeep-Test des Kandidaten erfolgreich; nicht gespeichert.'}
        if provider == 'tasksorg':
            from cockpit import posteo_source
            result = posteo_source()
            if result.get('state') in ('ready', 'partial'):
                return {'state': 'connected', 'message': 'Tasks.org/Posteo-Adapter antwortet schreibgeschützt.'}
            if result.get('state') == 'unconfigured':
                return {'state': 'unconfigured', 'message': 'Installiere/configuriere den vertrauenswürdigen Tasks.org/Posteo-Adapter serverseitig.'}
            return {'state': 'error', 'message': 'Tasks.org/Posteo-Adaptertest fehlgeschlagen.'}
        return {'state': 'error', 'message': 'Unbekannter Anbieter.'}
    except Exception as exc:
        return _public_error(exc)

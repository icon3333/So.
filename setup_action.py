"""Setup, safely test, and import configured integrations."""


def setup_action(app, provider, fields):
    """Perform one approved setup workflow, without bypassing provider limits."""
    from triage_state import StateError

    if not isinstance(fields, dict):
        raise StateError('Ungültige Einstellungen.')

    if provider in ('inoreader', 'x'):
        from oauth_setup import oauth_setup
        return oauth_setup(app, provider, fields)

    if provider == 'karakeep':
        # connect() verifies the identity before retaining the candidate key.
        return app.integration_action({'id': provider, 'action': 'connect', 'fields': fields})

    if provider == 'tasksorg':
        listing = app.cockpit_posteo()
        if listing['state'] not in ('ready', 'partial'):
            return {'state': listing['state'], 'message': 'Posteo nicht gelesen. Serverseitigen Tasks.org/Posteo-Adapter prüfen.'}
        app._setup_posteo_listing = listing
        count = len(listing['items'])
        result = {'state': 'connected' if listing['state'] == 'ready' else 'partial', 'imported': count,
                  'message': f'{count} Posteo-Einträge im Cockpit geladen.'}
        app.account_checks[provider] = {k: result[k] for k in ('state', 'message')}
        return result

    if provider in app.imports:
        status = app.integration_action({'id': provider, 'action': 'test', 'fields': fields})
        if status.get('state') not in ('readable', 'empty'):
            return status
        saved = app.integration_action({'id': provider, 'action': 'connect', 'fields': fields})
        if saved.get('state') not in ('readable', 'empty'):
            return saved
        # Reuse the established import pipeline only after destination validation
        # and persistence have succeeded.
        from cockpit import scan_keep, scan_desktop
        from import_settings import _root, _target
        root = _root(app)
        target = _target(root, saved['path']) if root else None
        if target is None:
            return {'state': 'unavailable', 'message': 'Importordner nicht mehr verfügbar.'}
        entry = scan_keep(target) if provider == 'keep' else scan_desktop(target.parent, target.name)
        if entry['state'] not in ('ready', 'partial'):
            return {'state': 'error', 'message': 'Importordner konnte nicht gelesen werden.'}
        count = len(entry['items'])
        return {**saved, 'state': 'partial' if entry['skipped'] else saved['state'],
                'message': f"{count} Einträge geladen; {entry['skipped']} übersprungen.", 'imported': count}

    raise StateError('Unbekannte Integration oder ungültige Felder.')

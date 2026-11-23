"""OAuth setup/resume with explicit, bounded X bookmark imports."""
import math
from triage_state import StateError


def _int_field(fields, key, default, low, high):
    value = fields.get(key, default)
    if type(value) is not int or not low <= value <= high:
        raise StateError('%s muss zwischen %s und %s liegen.' % (key, low, high), 400)
    return value


def _x_approval(fields):
    if fields.get('confirmCost') is not True:
        raise StateError('Vor dem X-Import müssen Kosten ausdrücklich bestätigt werden.', 403)
    pages = _int_field(fields, 'maxPages', 10, 1, 1000)
    results = _int_field(fields, 'maxResults', 100, 1, 100)
    authors = fields.get('includeAuthors', True)
    restart = fields.get('restart', False)
    if type(authors) is not bool or type(restart) is not bool:
        raise StateError('includeAuthors und restart müssen ausdrücklich gesetzt sein.', 400)
    posts = pages * results
    users = posts if authors else 0
    usd = fields.get('maxUsd', 5.0)
    if isinstance(usd, bool) or not isinstance(usd, (int, float)) or not math.isfinite(usd) or usd < 0:
        raise StateError('maxUsd muss eine endliche, nichtnegative Zahl sein.', 400)
    return {'approval': {'posts': posts, 'users': users, 'pages': pages, 'usd': usd},
            'max_pages': pages, 'max_results': results,
            'include_authors': authors, 'restart': restart}


def oauth_setup(app, provider, fields, resume=False):
    """Start or resume setup for X/Inoreader; never claim an import not performed."""
    if not isinstance(fields, dict) or type(resume) is not bool:
        raise StateError('Ungültige OAuth-Einstellungen.')
    if provider not in ('x', 'inoreader'):
        raise StateError('Unbekannter OAuth-Anbieter.')
    adapter = app.x_connection if provider == 'x' else app.connection
    if not resume:
        if provider == 'x':
            from account_settings import account_action
            outcome = account_action(app, provider, 'connect', fields)
        elif provider == 'inoreader' and not fields.get('confirmCost'):
            outcome = app.integration_action({'id': provider, 'action': 'connect', 'fields': fields})
        else:
            from account_settings import account_action
            outcome = account_action(app, provider, 'connect', fields)
        if outcome.get('state') == 'error':
            return outcome
        if outcome.get('authorizationUrl'):
            return {**outcome, 'state': 'authorization_required', 'resumeAction': 'setup',
                    'message': 'OAuth abschließen; anschließend Setup fortsetzen.'}
        # Already authorized still requires the same resume/import path.
    if not adapter.status().get('connected'):
        return {'state': 'authorization_required',
                'message': 'OAuth wurde nicht abgeschlossen; Setup und Import wurden nicht ausgeführt.'}
    if provider == 'inoreader':
        result = app.import_inoreader_entries(max_pages=fields.get('maxPages', 5), page_size=fields.get('pageSize', 100))
        return {'state': 'connected' if result['complete'] else 'partial',
                'message': 'Inoreader-Artikel wurden lokal importiert.' if result['complete'] else 'Import teilweise abgeschlossen; erneut ausführen, um fortzufahren.',
                **result, 'importSupported': True}
    spec = _x_approval(fields)
    result = app.import_x_bookmarks(spec['approval'], max_pages=spec['max_pages'],
                                    max_results=spec['max_results'], restart=spec['restart'])
    report = result.get('xImport') if isinstance(result, dict) else None
    if not isinstance(report, dict) or type(report.get('count')) is not int or report['count'] < 0:
        raise StateError('X-Importer hat keine gültige Importanzahl gemeldet.', 502)
    complete = report.get('complete') is True
    return {'state': 'connected' if complete else 'partial',
            'message': 'X-Lesezeichen wurden importiert.' if complete else 'X-Import ist unvollständig und kann fortgesetzt werden.',
            'imported': report['count'], 'xImport': report,
            'budget': report.get('budget'), 'importSupported': True}

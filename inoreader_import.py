"""Bounded, read-only Inoreader entry ingestion and idempotent local catalog persistence."""
import re
from urllib.parse import urlsplit

from triage_state import StateError, atomic_json

MAX_ENTRIES = 500
MAX_PAGE_SIZE = 100
MAX_PAGES = 5
ID = re.compile(r'(?:tag:google\.com,2005:reader/item/)?[A-Za-z0-9:.,_-]{1,512}')


def valid_id(value):
    return isinstance(value, str) and bool(ID.fullmatch(value))


def normalize_entries(entries):
    if not isinstance(entries, list) or len(entries) > MAX_ENTRIES:
        raise StateError('Inoreader lieferte eine ungültige oder zu große Artikelliste.', 502)
    normalized = []
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise StateError('Inoreader lieferte einen ungültigen Eintrag.', 502)
        publisher = entry.get('publisher') if isinstance(entry.get('publisher'), dict) else {}
        item_id = entry.get('id')
        url = entry.get('url')
        if not valid_id(item_id) or item_id in seen:
            raise StateError('Inoreader lieferte eine ungültige oder doppelte Eintrags-ID.', 502)
        try:
            parts = urlsplit(url)
            valid_url = parts.scheme in ('http', 'https') and bool(parts.hostname) and not parts.username and not parts.password
        except (TypeError, ValueError):
            valid_url = False
        if not valid_url:
            raise StateError('Inoreader lieferte eine ungültige Artikeladresse.', 502)
        seen.add(item_id)
        title = entry.get('title') if isinstance(entry.get('title'), str) else ''
        content = entry.get('content') if isinstance(entry.get('content'), str) else ''
        excerpt = entry.get('excerpt') if isinstance(entry.get('excerpt'), str) else content
        published = entry.get('published') if isinstance(entry.get('published'), str) else None
        author = entry.get('author') if isinstance(entry.get('author'), str) else ''
        publisher = entry.get('publisher') if isinstance(entry.get('publisher'), dict) else {}
        normalized.append({'id': 'inoreader:' + item_id, 'ref': item_id, 'url': url,
            'title': title[:5000] or 'Inhalt nicht verfügbar', 'excerpt': excerpt[:1200],
            'text': content[:200000], 'author': author[:500],
            'publisher': {'title': str(publisher.get('title') or '')[:500], 'url': str(publisher.get('url') or '')[:2048]},
            'createdAt': published, 'origin': 'inoreader', 'originAccountId': None,
            'missingContent': not bool(title), 'contentStatus': 'partial' if content else 'unavailable'})
    return normalized


def import_entries(adapter, catalog_path, max_pages=MAX_PAGES, page_size=MAX_PAGE_SIZE):
    """Fetch at most max_pages and atomically merge entries without replacing old catalog data."""
    if type(max_pages) is not int or not 1 <= max_pages <= MAX_PAGES:
        raise StateError('Inoreader-Seitenlimit muss zwischen 1 und %s liegen.' % MAX_PAGES, 400)
    if type(page_size) is not int or not 1 <= page_size <= MAX_PAGE_SIZE:
        raise StateError('Inoreader-Seitengröße muss zwischen 1 und %s liegen.' % MAX_PAGE_SIZE, 400)
    if not hasattr(adapter, 'read_entries_page'):
        raise StateError('Der n8n-Inoreader-Workflow ist veraltet. Bitte den Eintrags-Endpunkt gemäß Inoreader-Vertrag einrichten.', 409)
    cursor, pages, fetched, complete = None, 0, [], False
    for _ in range(max_pages):
        response = adapter.read_entries_page(cursor=cursor, limit=page_size)
        if not isinstance(response, dict) or not isinstance(response.get('entries'), list):
            raise StateError('Der Inoreader-Workflow hat keine Einträge geliefert.', 502)
        batch = normalize_entries(response['entries'])
        if len(batch) > page_size:
            raise StateError('Inoreader lieferte mehr Einträge als angefordert.', 502)
        fetched.extend(batch)
        if len(fetched) > MAX_ENTRIES:
            raise StateError('Inoreader lieferte zu viele Einträge.', 502)
        next_cursor = response.get('nextCursor')
        if next_cursor is None:
            complete = response.get('complete') is True
            break
        if not isinstance(next_cursor, str) or not next_cursor or len(next_cursor) > 4096 or next_cursor == cursor:
            raise StateError('Der Inoreader-Workflow lieferte einen ungültigen Cursor.', 502)
        cursor = next_cursor
        pages += 1
        if pages >= max_pages:
            break
    path = catalog_path
    existing = {'schemaVersion': 1, 'entries': {}}
    try:
        import json
        from pathlib import Path
        p = Path(path)
        if p.exists():
            existing = json.loads(p.read_text())
            if existing.get('schemaVersion') != 1 or not isinstance(existing.get('entries'), dict):
                raise ValueError
    except (OSError, ValueError, TypeError):
        raise StateError('Der lokale Inoreader-Katalog ist beschädigt und blieb unverändert.', 500) from None
    merged = dict(existing['entries'])
    for item in fetched:
        old = merged.get(item['id'], {})
        merged[item['id']] = {**old, **item}
    atomic_json(path, {'schemaVersion': 1, 'entries': merged})
    return {'imported': len(fetched), 'total': len(merged), 'complete': complete, 'pages': pages + (1 if fetched or not complete else 0)}

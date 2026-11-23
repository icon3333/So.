"""Read-only inputs for the cockpit; originals stay at their sources."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from x_content import load as load_x_content
from import_settings import import_status


def item(source, key, title, kind, **fields):
    return dict(id=hashlib.sha256(f'{source}:{key}'.encode()).hexdigest(),
                source=source, title=title, kind=kind, **fields)


def result(state, items=(), skipped=0):
    return {'state': state, 'items': list(items), 'skipped': skipped}


def visible(path):
    return not path.name.startswith('.') and not path.is_symlink()


def scan_desktop(desktop, name):
    root = desktop / name
    if root.is_symlink():
        return result('error')
    try:
        paths = sorted(root.iterdir(), key=lambda p: p.name.casefold())
        items = []
        for path in paths:
            if not visible(path) or path.is_symlink():
                continue
            if name == 'Projects':
                if path.is_dir():
                    items.append(item('desktop', f'{name}/{path.name}', path.name, 'project',
                                      detail=f'Desktop/{name}/{path.name}', group='Projects'))
                continue
            if path.is_dir():
                group = path.name
                kind = 'reading' if group.casefold() in ('reading', 'reading to do') else 'task'
                for parent, dirs, files in os.walk(path, onerror=lambda exc: (_ for _ in ()).throw(exc)):
                    dirs[:] = sorted(d for d in dirs if visible(Path(parent) / d) and not (Path(parent) / d).is_symlink())
                    for filename in sorted(files, key=str.casefold):
                        entry = Path(parent) / filename
                        if visible(entry) and not entry.is_symlink() and entry.is_file():
                            rel = entry.relative_to(root).as_posix()
                            items.append(item('desktop', f'{name}/{rel}', filename, kind,
                                              detail=f'Desktop/{name}/{rel}', group=group))
            elif path.is_file() and not path.is_symlink():
                items.append(item('desktop', f'{name}/{path.name}', path.name, 'task',
                                  detail=f'Desktop/{name}/{path.name}', group='To'))
        return result('ready', items)
    except FileNotFoundError:
        return result('missing')
    except OSError:
        return result('error')


def scan_keep(root):
    try:
        paths = sorted(root.iterdir(), key=lambda p: p.name.casefold())
    except FileNotFoundError:
        return result('missing')
    except OSError:
        return result('error')
    items, skipped = [], 0
    for path in paths:
        if path.suffix.lower() != '.json' or not visible(path) or path.is_symlink() or not path.is_file():
            continue
        try:
            note = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(note, dict) or not any(k in note for k in
                    ('title', 'textContent', 'listContent', 'attachments')):
                raise ValueError
            if note.get('isTrashed'):
                continue
            lines = [str(note.get('textContent') or '')]
            for entry in note.get('listContent') or []:
                marker = '☑' if entry.get('isChecked') else '☐'
                lines.append(f"{marker} {entry.get('text', '')}")
            detail = '\n'.join(filter(None, lines))
            title = str(note.get('title') or '').strip() or (detail.splitlines() or ['Ohne Titel'])[0][:120]
            items.append(item('keep', path.name, title, 'task', detail=detail,
                              archived=bool(note.get('isArchived')), attachments=len(note.get('attachments') or [])))
        except (OSError, ValueError, TypeError, AttributeError):
            skipped += 1
    return result('partial' if skipped else 'ready', items, skipped)


def scan_posteo(adapter):
    if not adapter or not Path(adapter).is_file():
        return result('unconfigured')
    try:
        run = subprocess.run([sys.executable, '-B', adapter, 'inventory', '--json'],
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             timeout=65, text=True, check=True)
        rows = json.loads(run.stdout)['tasks']
        if not isinstance(rows, list):
            raise ValueError
        items = []
        for row in rows:
            if not row.get('resource') or not row.get('summary'):
                continue
            due = re.match(r'^(\d{4})(\d\d)(\d\d)', row.get('due') or '')
            status = row.get('status') or ''
            items.append(item('posteo', row['resource'], row['summary'], 'task',
                              group=', '.join(row.get('categories') or []), recurring=bool(row.get('rrule')),
                              closed=status in ('COMPLETED', 'CANCELLED'), due='-'.join(due.groups()) if due else ''))
        skipped = len(rows) - len(items)
        return result('partial' if skipped else 'ready', items, skipped)
    except (subprocess.SubprocessError, OSError, ValueError, KeyError, TypeError, AttributeError):
        return result('error')


def local_sources(app=None):
    if app is not None:
        settings = {provider: import_status(app, provider) for provider in ('keep', 'to', 'projects')}
        desktop_root = getattr(app, 'import_root', None) or os.environ.get('LESELISTE_IMPORT_ROOT')
        def configured(provider):
            value = settings[provider]
            if value['state'] not in ('readable', 'empty'):
                return None
            if desktop_root:
                return Path(desktop_root).expanduser().resolve() / value['path']
            return Path(value['path']).expanduser() if value['path'] else None
        keep_path, to_path, projects_path = configured('keep'), configured('to'), configured('projects')
        if not desktop_root:
            # Legacy absolute defaults are resolved independently per provider.
            return {
                'to': scan_desktop(to_path.parent, to_path.name) if to_path else result(settings['to']['state']),
                'projects': scan_desktop(projects_path.parent, projects_path.name) if projects_path else result(settings['projects']['state']),
                'keep': scan_keep(keep_path) if keep_path else result(settings['keep']['state']),
            }
        return {
            'to': scan_desktop(to_path.parent, 'To') if to_path else result(settings['to']['state']),
            'projects': scan_desktop(projects_path.parent, 'Projects') if projects_path else result(settings['projects']['state']),
            'keep': scan_keep(keep_path) if keep_path else result(settings['keep']['state']),
        }
    home = Path.home()
    # Desktop contents are enabled only on the actual Mac unless explicitly set by the operator.
    desktop_value = os.environ.get('LESELISTE_DESKTOP')
    desktop = Path(desktop_value).expanduser() if desktop_value else (home / 'Desktop' if sys.platform == 'darwin' else None)
    keep = Path(os.environ.get('LESELISTE_KEEP_EXPORT', str(home / 'Downloads' / 'Takeout' / 'Keep'))).expanduser()
    return {'to': scan_desktop(desktop, 'To') if desktop else result('unconfigured'),
            'projects': scan_desktop(desktop, 'Projects') if desktop else result('unconfigured'),
            'keep': scan_keep(keep)}


def posteo_source():
    adapter = Path.home() / '.hermes/skills/productivity/tasks-caldav/scripts/posteo_tasks.py'
    return scan_posteo(os.environ.get('LESELISTE_POSTEO_ADAPTER', str(adapter)))


def global_sources(articles, saved_ids, decisions, x_content, posteo=None):
    """Normalize imported reading items for global review without mutating originals."""
    saved = set(saved_ids)
    items = []
    for article in articles:
        if article.get('id') not in saved:
            continue
        origin = article.get('origin') or 'inoreader'
        source = 'X' if origin == 'x' else 'Inoreader'
        content = x_content.get(article.get('id'), {}) if source == 'X' else {}
        topic = content.get('topic') or article.get('topic') or ''
        text = content.get('text') or article.get('excerpt') or article.get('text') or ''
        format_name = article.get('format') or ('X post' if source == 'X' else 'Artikel')
        choice = decisions.get(article['id'], {}).get('choice')
        items.append(item('reading', article['id'], article.get('title') or 'Ohne Titel', 'reading',
                          group=source, sourceLabel=source, topic=topic, format=format_name,
                          detail=text, url=article.get('url'), closed=choice in ('keep', 'remove'),
                          author=article.get('author') or content.get('author') or '',
                          xContent=content or None))
    entries = {'reading': result('ready', items)}
    if posteo is not None:
        entries['posteo'] = posteo
    return entries

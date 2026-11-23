"""Safe, operator-configured local import roots. Tests only inspect metadata."""
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
import sys
from datetime import datetime, timezone

PROVIDERS = {'keep': 'Keep', 'to': 'To', 'projects': 'Projects'}


def _checked_at():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _settings_path(app):
    return Path(app.data_root) / 'import-settings.json'


def _read(app):
    try:
        data = json.loads(_settings_path(app).read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _root(app):
    # Operator-defined trusted root; never supplied by a browser request.
    configured = getattr(app, 'import_root', None) or os.environ.get('LESELISTE_IMPORT_ROOT')
    if not configured:
        return None
    try:
        root = Path(configured).expanduser().resolve(strict=True)
        for private in (Path(app.data_root).resolve(), Path(__file__).resolve().parent):
            if private.is_relative_to(root) or root.is_relative_to(private):
                return None
        return root if root.is_dir() else None
    except (OSError, RuntimeError):
        return None


def _target(root, relative):
    if not isinstance(relative, str) or not relative or '\\' in relative or '\x00' in relative:
        return None
    rel = PurePosixPath(relative)
    if any(part in ('.', '..') for part in relative.split('/')):
        return None
    if rel.is_absolute() or any(part in ('', '.', '..') for part in rel.parts):
        return None
    candidate = root.joinpath(*rel.parts)
    try:
        resolved = candidate.resolve(strict=True)
        if not resolved.is_relative_to(root) or candidate.is_symlink():
            return None
        # Reject symlinks anywhere in path, including intermediates.
        current = root
        for part in rel.parts:
            current = current / part
            if current.is_symlink():
                return None
        return candidate if resolved.is_dir() else None
    except (OSError, RuntimeError):
        return None


def _default_path(app, provider):
    """Resolve each legacy default independently unless explicitly overridden."""
    settings = _read(app)
    if provider in settings:
        return None
    home = Path.home()
    base = getattr(app, 'import_root', None) or os.environ.get('LESELISTE_IMPORT_ROOT')
    if base:
        return {'keep': 'Keep', 'to': 'To', 'projects': 'Projects'}[provider]
    if provider == 'keep':
        return os.environ.get('LESELISTE_KEEP_EXPORT', str(home / 'Downloads' / 'Takeout' / 'Keep'))
    desktop = os.environ.get('LESELISTE_DESKTOP')
    if not desktop and sys.platform == 'darwin':
        desktop = str(home / 'Desktop')
    return str(Path(desktop).expanduser() / {'to': 'To', 'projects': 'Projects'}[provider]) if desktop else None


def _status(app, provider, relative, checked_at):
    state = 'unconfigured'
    message = 'Wähle einen Ordner unter dem freigegebenen Importverzeichnis.'
    root = _root(app)
    if root and relative:
        target = _target(root, relative)
        if target:
            try:
                # Validate the provider-specific directory; do not infer status from
                # unrelated children or files elsewhere beneath the import root.
                expected = {'keep': 'keep', 'to': 'to', 'projects': 'projects'}[provider]
                if target.name.casefold() != expected:
                    return {'state': 'unavailable', 'message': 'Der ausgewählte Ordner passt nicht zu diesem Import.',
                            'checkedAt': checked_at, 'path': relative}
                with os.scandir(target) as entries:
                    entries = [entry for entry in entries if not entry.name.startswith('.') and not entry.is_symlink()]
                    state = 'readable' if entries else 'empty'
                message = 'Ordner ist lesbar.' if state == 'readable' else 'Ordner ist leer.'
            except OSError:
                state, message = 'unavailable', 'Ordner ist nicht verfügbar.'
        else:
            state, message = 'unavailable', 'Ordner ist nicht verfügbar oder liegt außerhalb des freigegebenen Importverzeichnisses.'
    elif relative:
        state, message = 'unavailable', 'Freigegebenes Importverzeichnis ist nicht verfügbar.'
    return {'state': state, 'message': message, 'checkedAt': checked_at, 'path': relative or ''}


def import_status(app, provider):
    if provider not in PROVIDERS:
        raise ValueError('Unknown import provider')
    stored = _read(app).get(provider, {})
    if not isinstance(stored, dict):
        stored = {}
    relative = stored.get('path') if stored else _default_path(app, provider)
    if relative and not (getattr(app, 'import_root', None) or os.environ.get('LESELISTE_IMPORT_ROOT')):
        path = Path(relative).expanduser()
        exists = path.is_dir()
        if exists:
            try:
                with os.scandir(path) as entries:
                    exists = any(not entry.name.startswith('.') and not entry.is_symlink() for entry in entries)
            except OSError:
                exists = False
        return {'state': ('readable' if exists else 'empty') if path.is_dir() else 'missing',
                'message': 'Ordner ist lesbar.' if exists else 'Ordner ist leer oder nicht verfügbar.',
                'checkedAt': None, 'path': relative}
    return _status(app, provider, relative, stored.get('checkedAt'))


def import_action(app, provider, action, fields):
    if provider not in PROVIDERS or action not in ('test', 'connect'):
        raise ValueError('Invalid import action')
    if not isinstance(fields, dict):
        raise ValueError('Invalid import fields')
    path = fields.get('path')
    root = _root(app)
    if not root and action == 'test' and path and path == _default_path(app, provider):
        status = import_status(app, provider)
        status['checkedAt'] = _checked_at()
        return status
    if root and isinstance(path, str):
        rel = PurePosixPath(path)
        if rel.is_absolute() or any(part in ('..', '.') for part in rel.parts):
            return {'state': 'unavailable', 'message': 'Ungültiger oder außerhalb liegender Importpfad.',
                    'checkedAt': _checked_at(), 'path': path}
    checked_at = _checked_at()
    # Test is deliberately non-persistent. Connect persists only a validated relative path.
    status = _status(app, provider, path, checked_at)
    if action == 'connect' and status['state'] in ('readable', 'empty'):
        data = _read(app)
        data[provider] = {'path': path, 'checkedAt': checked_at}
        destination = _settings_path(app)
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.import-settings-', dir=destination.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(data, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
    return status

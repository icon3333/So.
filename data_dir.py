"""Validate the private production store before opening any application files."""
import os
from pathlib import Path


def production_dir(root):
    raw = os.environ.get('SO_DATA_DIR', '')
    path = Path(raw)
    if not path.is_absolute() or not path.is_dir():
        raise ValueError('SO_DATA_DIR must name an existing absolute directory')
    resolved, code = path.resolve(), Path(root).resolve()
    if (resolved != path or resolved.is_relative_to(code) or code.is_relative_to(resolved)
            or 'archive_data' in resolved.parts or resolved.name != 'prod_data'):
        raise ValueError('SO_DATA_DIR must be a separate prod_data directory, not an archive or checkout')
    st = resolved.stat()
    if st.st_uid != os.getuid() or st.st_mode & 0o077 or any(p.is_symlink() for p in resolved.iterdir()):
        raise ValueError('SO_DATA_DIR must be owner-private and contain no symlinks')
    return resolved

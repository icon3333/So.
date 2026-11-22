"""Local X article enrichment cache and FxTwitter retrieval."""
import json
import re
import urllib.error
import urllib.request
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

SCHEMA_VERSION = 1
X_HOSTS = {"x.com", "www.x.com"}
POST_ID = re.compile(r"/status/(\d+)(?:/|$)")


def x_post_id(url):
    try:
        parsed = urlsplit(url)
    except (TypeError, ValueError):
        return None
    if parsed.scheme not in ("http", "https") or parsed.hostname not in X_HOSTS:
        return None
    match = POST_ID.search(parsed.path)
    return match.group(1) if match else None


def is_x_article(article):
    try:
        parsed = urlsplit(article.get("url", ""))
        return parsed.scheme in ("http", "https") and parsed.hostname in X_HOSTS
    except (TypeError, ValueError):
        return False


def load(path):
    path = Path(path)
    if not path.exists():
        return {"schemaVersion": SCHEMA_VERSION, "entries": {}}
    try:
        value = json.loads(path.read_text())
        if value.get("schemaVersion") != SCHEMA_VERSION or not isinstance(value.get("entries"), dict):
            raise ValueError
        return value
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Der X-Inhalt-Cache ist beschädigt.") from exc


def save(path, cache):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as out:
            os.fchmod(out.fileno(), 0o660)
            json.dump(cache, out, ensure_ascii=False, indent=2)
            out.write('\n')
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def fetch(url, timeout=8):
    post_id = x_post_id(url)
    if not post_id:
        raise ValueError("Nur x.com/www.x.com-Status-URLs können abgerufen werden.")
    request = urllib.request.Request(
        "https://api.fxtwitter.com/status/" + post_id,
        headers={"Accept": "application/json", "User-Agent": "Leseliste/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read(1024 * 1024))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("X-Inhalt konnte nicht abgerufen werden.") from exc
    tweet = payload.get("tweet") if isinstance(payload, dict) else None
    if not isinstance(tweet, dict):
        raise RuntimeError("X-Inhalt ist nicht verfügbar.")
    author = tweet.get("author") if isinstance(tweet.get("author"), dict) else {}
    text = str(tweet.get("text") or "").strip()
    if isinstance(tweet.get('article'), dict):
        article = tweet['article']
        content = article.get('content') or {}
        blocks = content.get('blocks', article.get('blocks', []))
        article_text = '\n\n'.join(str(block.get('text') or '').strip() for block in blocks
                                   if isinstance(block, dict) and block.get('text')).strip()
        text = '\n\n'.join(part for part in (text, article_text) if part)
    media = tweet.get("media")
    if isinstance(media, dict):
        media = media.get('all') or media.get('photos') or media.get('videos') or []
    if not text and not media:
        raise RuntimeError("Kein lesbarer Posttext und keine Medien abrufbar.")
    return {
        "status": "partial",
        "author": str(author.get("name") or author.get("screen_name") or "").strip(),
        "title": "",
        "summary": "",
        "topic": "",
        "takeaway": "",
        "assessment": "",
        "text": text,
        "sourceUrl": "https://api.fxtwitter.com/status/" + post_id,
        "retrievedAt": datetime.now(timezone.utc).isoformat(),
        "limitations": ["Rohtext von FxTwitter; keine redaktionelle Zusammenfassung.",
                         "Threadkontext und beigefügte Medien nicht vollständig ausgewertet."],
        "media": media if isinstance(media, list) else [],
    }

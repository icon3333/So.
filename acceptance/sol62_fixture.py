"""Deterministic, synthetic-only fixture for Sol 6.2 browser acceptance.

No live exports, private files, host home directories or network services are read.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path


def _id(source: str, n: int) -> str:
    return hashlib.sha256(f"sol62-synthetic:{source}:{n}".encode()).hexdigest()


def make_fixture(root: Path) -> tuple[Path, Path]:
    """Create isolated HOME and prod_data, containing 49 mixed synthetic items."""
    home = root / "home"
    prod = root / "prod_data"
    home.mkdir(mode=0o700)
    prod.mkdir(mode=0o700)
    desktop = home / "Desktop"
    (desktop / "To" / "Reading").mkdir(parents=True)
    (desktop / "To" / "Tasks").mkdir(parents=True)
    (desktop / "Projects" / "Synthetic project").mkdir(parents=True)
    keep = home / "Downloads" / "Takeout" / "Keep"
    keep.mkdir(parents=True)

    articles = []
    native = {}
    for i in range(16):
        aid = _id("inoreader", i)
        articles.append({
            "id": aid, "ref": f"synthetic-inoreader-{i:02d}",
            "title": f"Synthetic reading {i:02d}",
            "excerpt": f"Synthetic excerpt for shared-area fixture {i:02d}.",
            "url": f"https://example.invalid/reading/{i:02d}",
            "source": "Synthetic Inoreader", "topic": "Shared synthetic area" if i < 5 else "Reading",
            "format": "Article", "origin": "inoreader", "originAccountId": None,
        })
    for i in range(8):
        post = f"90000000000000{i:02d}"
        native[f"x:fixture:{post}"] = {
            "ref": f"synthetic-x-{i:02d}", "postId": post,
            "title": f"Synthetic X post {i:02d}", "excerpt": f"Synthetic post excerpt {i:02d}.",
            "url": f"https://x.com/synthetic_user/status/90000000000000{i:02d}",
            "source": "Synthetic X", "topic": "Shared synthetic area" if i < 3 else "Social",
            "format": "X post",
        }
    (prod / "articles.json").write_text(json.dumps({"fetchedAt": None, "articles": articles}, indent=2), encoding="utf-8")
    (prod / "x-bookmarks.json").write_text(json.dumps({"schemaVersion": 1, "accountId": "fixture", "revision": 1, "complete": True, "entries": native}), encoding="utf-8")

    for i in range(10):
        (desktop / "To" / "Reading" / f"synthetic-reading-{i:02d}.txt").write_text(f"Synthetic desktop reading {i:02d}\n", encoding="utf-8")
    for i in range(8):
        (desktop / "To" / "Tasks" / f"synthetic-task-{i:02d}.txt").write_text(f"Synthetic desktop task {i:02d}\n", encoding="utf-8")
    (desktop / "Projects" / "Synthetic project" / "README.txt").write_text("Synthetic project fixture\n", encoding="utf-8")
    for i in range(6):
        note = {"title": f"Synthetic Keep task {i:02d}", "textContent": f"Synthetic Keep detail {i:02d}", "isArchived": False}
        (keep / f"synthetic-{i:02d}.json").write_text(json.dumps(note), encoding="utf-8")
    return home, prod


def isolated_environment(home: Path, prod: Path) -> dict[str, str]:
    env = {"PATH": os.defpath}
    env.update({"HOME": str(home), "SO_DATA_DIR": str(prod),
                "LESELISTE_DESKTOP": str(home / "Desktop"),
                "LESELISTE_KEEP_EXPORT": str(home / "Downloads/Takeout/Keep"),
                "LESELISTE_POSTEO_ADAPTER": str(home / "no-posteo-adapter"),
                "NO_PROXY": "localhost,127.0.0.1", "no_proxy": "localhost,127.0.0.1",
                "HTTP_PROXY": "", "HTTPS_PROXY": "", "ALL_PROXY": ""})
    return env


def temporary_fixture():
    """Convenience context manager yielding (tmp, home, prod)."""
    return tempfile.TemporaryDirectory(prefix="so-sol62-acceptance-")

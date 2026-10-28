"""scene_authors.py — The known scene authors (assets/data/authors.json), for Group Scenes' Manual mode.

The file is {"version": 1, "updated": "...", "authors": [{"name": "...", "alias": ["...", ...]}, ...]}:
the name is the folder an author's scenes are grouped under, the aliases are other names the author
goes by (a handle, a romanisation ...). Typing an alias suggests, and resolves to, the name.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from kkafio.core.logger import logger
from kkafio.core.paths import ASSETS_DATA_DIR

AUTHORS_FILE = "authors.json"

TAG = "AUTHORS"


@dataclass(frozen=True)
class Author:
    name: str
    aliases: tuple[str, ...] = ()


def load_authors(path: Path | None = None) -> list[Author]:
    """The authors in the file, in file order; empty (after a warning) if it is missing or unreadable.
    Names and aliases are stripped; entries without a usable name are dropped."""
    path = path or ASSETS_DATA_DIR / AUTHORS_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8")).get("authors", [])
    except FileNotFoundError:
        logger.warning(TAG, f"{path} not found — no author suggestions")
        return []
    except Exception as e:
        logger.warning(TAG, f"Ignoring unreadable {path}: {e}")
        return []
    authors: list[Author] = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or not entry["name"].strip():
            continue
        aliases = entry.get("alias") or []
        authors.append(Author(entry["name"].strip(),
                              tuple(a.strip() for a in aliases if isinstance(a, str) and a.strip())
                              if isinstance(aliases, list) else ()))
    return authors


def canonical_name(text: str, authors: list[Author]) -> str:
    """The author's name when `text` is a name or an alias of one (any case), else `text` stripped."""
    q = text.strip().casefold()
    for author in authors:
        if author.name.casefold() == q:
            return author.name
    for author in authors:
        if any(a.casefold() == q for a in author.aliases):
            return author.name
    return text.strip()


def suggest_authors(text: str, authors: list[Author], limit: int = 8) -> list[str]:
    """Names of the authors whose name or alias contains `text` (any case), best first: a name that
    starts with it, an alias that starts with it, then any other containing it; file order within each."""
    q = text.strip().casefold()
    if not q:
        return []
    ranked: list[tuple[int, int, str]] = []
    for n, author in enumerate(authors):
        name = author.name.casefold()
        if name.startswith(q):
            rank = 0
        elif any(a.casefold().startswith(q) for a in author.aliases):
            rank = 1
        elif q in name:
            rank = 2
        elif any(q in a.casefold() for a in author.aliases):
            rank = 3
        else:
            continue
        ranked.append((rank, n, author.name))
    ranked.sort()
    return [name for _r, _n, name in ranked[:limit]]

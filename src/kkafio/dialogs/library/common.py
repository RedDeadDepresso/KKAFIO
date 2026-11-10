"""
common.py — The pieces the character, coordinate and scene libraries have in common.

Each library still has its own scan (chara info cache / coordinate cache / hash cache), its own source of
suggestions (series index / character names / authors) and its own `finish()`; what is the same
in all three lives here: which folders count, listing the folders that exist, building a card's
`CharaItem`, ranking the suggestions, and the window's wording.
"""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import fields
from pathlib import Path

from kkafio.core.i18n import t
from kkafio.dialogs.library.model import CharaItem, DialogText


def is_plain_dir(name: str) -> bool:
    """Folders starting with "_" or "." (e.g. _duplicates_) are not series / character / author
    folders: the libraries neither list them nor show the cards inside them."""
    return not name.startswith(("_", "."))


def existing_folders(*roots: Path) -> dict[str, str]:
    """{casefolded name: name} of the folders that exist directly inside the roots (see
    is_plain_dir). When several roots hold a folder of the same name, the first one's spelling wins."""
    known: dict[str, str] = {}
    for root in roots:
        try:
            for entry in sorted(root.iterdir(), key=lambda e: e.name.casefold()):
                if entry.is_dir() and is_plain_dir(entry.name):
                    known.setdefault(entry.name.casefold(), entry.name)
        except OSError:
            pass
    return known


def make_item(png: Path, root: Path, parts: tuple[str, ...], alias: str = "") -> CharaItem | None:
    """The library's item for the card `png`, named after the file. `parts` is its path relative
    to `root`: a card inside a folder has that (first) folder as its series, one directly in
    `root` has none. `alias` is extra text the search also looks at. None if the file can't be
    stat'ed (it vanished)."""
    try:
        st = png.stat()
    except OSError:
        return None
    return CharaItem(path=png, root=root, name=png.stem,
                     alias=alias,
                     series=parts[0] if len(parts) > 1 else None,
                     size=st.st_size, mtime=st.st_mtime,
                     ctime=getattr(st, "st_birthtime", st.st_ctime))    # created: Windows / macOS


def seed_known(known: dict[str, str], items: Iterable[CharaItem]) -> None:
    """Add the folder each item is in to `known` ({casefolded: name}), unless it is there already."""
    for item in items:
        if item.series:
            known.setdefault(item.series.casefold(), item.series)


def rank_suggestions(names: Iterable[str], text: str, limit: int) -> list[str]:
    """At most `limit` of `names`, best first. Nothing typed: all of them, alphabetically. Else
    the ones containing `text` (any case), those starting with it before the rest, each group
    alphabetically."""
    q = text.strip().casefold()
    if not q:
        ranked = sorted(names, key=str.casefold)
    else:
        ranked = sorted((n for n in names if q in n.casefold()),
                        key=lambda n: (not n.casefold().startswith(q), n.casefold()))
    return ranked[:limit]


class Suggester:
    """A tab's `suggest` callback. Calling it gives every suggestion (the existing folders and the
    tab's index); `.existing(text)` gives only the folders that exist — what the window uses when
    the user turns the index suggestions off (suggestions.py). `.set_language(code)` changes the
    language of the index's titles where the tab has one (a no-op elsewhere)."""

    def __init__(self, everything: Callable[[str], list[str]], existing: Callable[[str], list[str]],
                 set_language: Callable[[str], None] | None = None):
        self._everything = everything
        self.existing = existing
        self._set_language = set_language

    def __call__(self, text: str) -> list[str]:
        return self._everything(text)

    def set_language(self, language: str) -> None:
        if self._set_language is not None:
            self._set_language(language)


def dialog_text(prefix: str) -> DialogText:
    """The library's wording from the i18n keys "<prefix>.<field>" — one key per DialogText field,
    e.g. "dialog.manual_scene.no_cards" for prefix "dialog.manual_scene"."""
    names = [f.name for f in fields(DialogText)]
    # tests/test_i18n.py knows this exact f-string: the prefix must be a literal at the call site
    return DialogText(**{name: t(f"{prefix}.{name}") for name in names})


def chain_moves(moves: dict[str, str], renamed: Mapping[Path, Path]) -> None:
    """Add a folder rename ({old path: new path}) to `moves` ({old path: new path} of the cards the
    caches have to follow). A card that was moved earlier keeps its first path as the key."""
    done = {str(old): str(new) for old, new in renamed.items()}
    for first, last in moves.items():
        if last in done:
            moves[first] = done.pop(last)
    moves.update(done)


def rename_known(known: dict[str, str], old: str, new: str) -> None:
    """Replace the folder `old` by `new` in `known` ({casefolded: name})."""
    known.pop(old.casefold(), None)
    known[new.casefold()] = new

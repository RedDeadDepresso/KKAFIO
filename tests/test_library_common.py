"""What the character, coordinate and scene libraries share (kkafio/dialogs/library/common.py), and the
promise that importing the library package never pulls in the window (tkinter)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

import conftest  # noqa: F401  (sandbox env must be set before kkafio is imported)
from kkafio.dialogs.library import DialogText, chara_dialog_text
from kkafio.dialogs.library import common

ROOT = Path(__file__).resolve().parents[1]


def _file(path: Path, data: bytes = b"png") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


# --- which folders count -------------------------------------------------------------------------------

def test_is_plain_dir_rejects_underscore_and_dot_folders():
    assert common.is_plain_dir("Fate") and common.is_plain_dir("a_b") and common.is_plain_dir("1")
    assert not common.is_plain_dir("_duplicates_") and not common.is_plain_dir(".hidden")
    assert not common.is_plain_dir("_") and not common.is_plain_dir(".")


def test_existing_folders_lists_plain_folders_only_and_the_first_spelling_wins(tmp_path):
    (tmp_path / "female" / "Fate").mkdir(parents=True)
    (tmp_path / "female" / "_duplicates_").mkdir()
    (tmp_path / "female" / ".git").mkdir()
    _file(tmp_path / "female" / "loose.png")                       # a file is not a folder
    (tmp_path / "male" / "fate").mkdir(parents=True)               # same name, other case
    (tmp_path / "male" / "SAO").mkdir()
    known = common.existing_folders(tmp_path / "female", tmp_path / "male")
    assert known == {"fate": "Fate", "sao": "SAO"}
    assert common.existing_folders(tmp_path / "male", tmp_path / "female")["fate"] == "fate"


def test_existing_folders_skips_a_root_that_is_missing(tmp_path):
    (tmp_path / "there" / "Fate").mkdir(parents=True)
    assert common.existing_folders(tmp_path / "gone", tmp_path / "there") == {"fate": "Fate"}
    assert common.existing_folders() == {}


# --- the library's items -------------------------------------------------------------------------------

def test_make_item_names_it_after_the_file_and_takes_the_series_from_the_first_folder(tmp_path):
    loose = _file(tmp_path / "Rin.png", b"12345")
    nested = _file(tmp_path / "Fate" / "deeper" / "Saber.png")
    a = common.make_item(loose, tmp_path, ("Rin.png",), alias="Rin Tohsaka")
    b = common.make_item(nested, tmp_path, ("Fate", "deeper", "Saber.png"))
    assert (a.name, a.series, a.alias, a.size, a.path, a.root) == ("Rin", None, "Rin Tohsaka", 5, loose, tmp_path)
    assert (b.name, b.series, b.alias) == ("Saber", "Fate", "")
    assert a.mtime > 0 and a.ctime > 0


def test_make_item_is_none_for_a_file_that_vanished(tmp_path):
    assert common.make_item(tmp_path / "gone.png", tmp_path, ("gone.png",)) is None


def test_seed_known_adds_the_folders_of_the_items_without_overriding(tmp_path):
    items = [common.make_item(_file(tmp_path / rel), tmp_path, Path(rel).parts)
             for rel in ("a.png", "Fate/b.png", "fate/c.png", "SAO/d.png")]
    known = {"sao": "Sao"}
    common.seed_known(known, items)
    assert known == {"sao": "Sao", "fate": "Fate"}                 # untagged ignored; the first spelling stays


# --- suggestions ---------------------------------------------------------------------------------------

def test_rank_suggestions_nothing_typed_is_alphabetical_and_capped():
    names = ["b", "C", "a", "D"]
    assert common.rank_suggestions(names, "", 10) == ["a", "b", "C", "D"]
    assert common.rank_suggestions(names, "   ", 2) == ["a", "b"]


def test_rank_suggestions_puts_names_starting_with_the_text_first():
    names = ["Sword Art Online", "Fate/stay night", "Fate Grand Order", "Re:Fate", "Kirito"]
    assert common.rank_suggestions(names, "fate", 10) == ["Fate Grand Order", "Fate/stay night", "Re:Fate"]
    assert common.rank_suggestions(names, " FATE ", 2) == ["Fate Grand Order", "Fate/stay night"]
    assert common.rank_suggestions(names, "zzz", 10) == []
    assert common.rank_suggestions(iter(names), "kir", 10) == ["Kirito"]     # any iterable


# --- the window's wording ------------------------------------------------------------------------------

@pytest.mark.parametrize("prefix", ["dialog.manual", "dialog.manual_coord", "dialog.manual_scene"])
def test_dialog_text_is_complete_for_every_library(prefix):
    text = common.dialog_text(prefix)
    assert isinstance(text, DialogText)
    assert all(getattr(text, f) and not getattr(text, f).startswith("dialog.") for f in text.__dataclass_fields__)


def test_dialog_text_for_characters_is_the_windows_own_default():
    """The window (dialogs/library/window.py) falls back to chara_dialog_text() when a tab has no wording of its own;
    the Character tab passes dialog_text("dialog.manual"). They must stay the same words."""
    assert common.dialog_text("dialog.manual") == chara_dialog_text()


# --- importing never builds a window ---------------------------------------------------------------

def test_importing_the_library_modules_does_not_import_customtkinter():
    """The modules live inside the window's package, so the window module (and tkinter, which it
    imports optionally) is loaded with them; customtkinter and the window itself are not."""
    code = (
        "import sys\n"
        "import kkafio.dialogs.library.common, kkafio.dialogs.library.characters, kkafio.dialogs.library.coordinates, kkafio.dialogs.library.scenes\n"
        "import kkafio.tasks.manage_library\n"
        "bad = sorted(m for m in sys.modules if m == 'customtkinter')\n"
        "assert not bad, bad\n"
    )
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    subprocess.run([sys.executable, "-c", code], check=True, cwd=ROOT, env=env, capture_output=True, text=True)

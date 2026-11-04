"""Renaming a series folder from the library's sidebar: the on-disk rename and the bookkeeping."""
from pathlib import Path

import pytest

import conftest  # noqa: F401  (sandbox env must be set before kkafio is imported)
from kkafio.dialogs.library.common import chain_moves, rename_known
from kkafio.dialogs.library.model import FolderNameTaken, rename_series_folders


def test_renames_the_folder_in_every_root(tmp_path):
    roots = [tmp_path / "female", tmp_path / "male"]
    for r in roots:
        (r / "Foo").mkdir(parents=True)
    (roots[0] / "Other").mkdir()
    done = rename_series_folders(roots, "foo", "Baz")            # `old` is matched in any case
    assert sorted(p.name for p in roots[0].iterdir()) == ["Baz", "Other"]
    assert (roots[1] / "Baz").is_dir() and len(done) == 2


@pytest.mark.parametrize("new", ["Bar", "bar", "FOO", "foo"])
def test_refuses_a_taken_name_in_any_case(tmp_path, new):
    (tmp_path / "Foo").mkdir()
    (tmp_path / "Bar").mkdir()
    with pytest.raises(FolderNameTaken):
        rename_series_folders([tmp_path], "Foo", new)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Bar", "Foo"]   # nothing was renamed


def test_chain_moves_keeps_the_first_path():
    moves = {"old/x.png": "a/Foo/x.png"}
    chain_moves(moves, {Path("a/Foo/x.png"): Path("a/Baz/x.png"), Path("a/Foo/y.png"): Path("a/Baz/y.png")})
    assert moves == {"old/x.png": "a/Baz/x.png", "a/Foo/y.png": "a/Baz/y.png"}


def test_rename_known():
    known = {"foo": "Foo", "bar": "Bar"}
    rename_known(known, "Foo", "Baz")
    assert known == {"bar": "Bar", "baz": "Baz"}

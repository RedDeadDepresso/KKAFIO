"""The one folder-name sanitiser (core/folder_names.py) behind Group Characters, Group Coordinates
and Group Scenes, and the folder name each of them gets from it."""

import pytest

import conftest  # noqa: F401  (sandbox env must be set before kkafio is imported)
from kkafio.core.folder_names import safe_folder_name


def test_illegal_characters_are_dropped_and_dots_inside_a_name_are_kept():
    assert safe_folder_name("Mr. X") == "Mr. X"
    assert safe_folder_name("a<b>:c") == "abc"
    assert safe_folder_name('A/B:C?') == "ABC"
    assert safe_folder_name("tab\there") == "tabhere"           # control characters too


def test_trailing_dots_and_spaces_go_and_so_does_a_name_made_of_dots():
    assert safe_folder_name("name. ") == "name"
    for bad in ("", "  ", ".", "..", "...", " . . "):
        assert safe_folder_name(bad) == "", bad


@pytest.mark.parametrize("name", ["CON", "con", "NUL.txt", "COM3", "lpt9.x"])
def test_reserved_device_names_have_no_folder(name):
    assert safe_folder_name(name) == ""


def test_only_a_whole_reserved_stem_is_refused():
    assert safe_folder_name("CONSOLE") == "CONSOLE" and safe_folder_name("COM10") == "COM10"


def test_long_names_are_cut():
    assert len(safe_folder_name("a" * 300)) == 100

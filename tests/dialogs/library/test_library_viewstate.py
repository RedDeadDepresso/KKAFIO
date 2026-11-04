"""What the library remembers between runs (kkafio/dialogs/library/viewstate.py)."""

import json

import conftest  # noqa: F401  (sandbox env must be set before kkafio is imported)
from kkafio.core.session_store import SessionStore
from kkafio.dialogs.library import SORT_NAME, SORT_SIZE, SORT_UPDATED
from kkafio.dialogs.library.viewstate import LibraryViewState, TabView


def test_nothing_saved_means_the_defaults(tmp_path):
    s = LibraryViewState(SessionStore(tmp_path / "v.json", "T"))
    assert s.view("scene") == TabView(SORT_UPDATED, True, None)
    assert s.initial_index(["scene", "chara"], 1, [True, True]) == 1


def test_the_last_tab_and_each_tabs_view_are_saved_and_read_back(tmp_path):
    path = tmp_path / "config" / "v.json"
    s = LibraryViewState(SessionStore(path, "T"))
    assert s.remember("chara", {"scene": TabView(SORT_NAME, False, 200.0), "chara": TabView(SORT_SIZE, True, 120)})
    again = LibraryViewState(SessionStore(path, "T"))
    assert again.last_tab == "chara"
    assert again.view("scene") == TabView(SORT_NAME, False, 200.0)
    assert again.view("chara") == TabView(SORT_SIZE, True, 120.0)
    assert again.view("outfit") == TabView()                       # a tab it never saw
    again.remember("scene", {"outfit": TabView(SORT_NAME, False, None)})
    assert LibraryViewState(SessionStore(path, "T")).view("scene") == TabView(SORT_NAME, False, 200.0)   # kept


def test_it_opens_on_the_last_tab_only_if_that_tab_has_cards(tmp_path):
    s = LibraryViewState()
    s.last_tab = "chara"
    assert s.initial_index(["scene", "chara", "outfit"], 0, [True, True, True]) == 1
    assert s.initial_index(["scene", "chara", "outfit"], 0, [True, False, True]) == 0      # empty: the fallback
    assert s.initial_index(["scene", "outfit"], 1, [True, True]) == 1                      # no longer there


def test_bad_values_in_the_file_are_ignored(tmp_path):
    path = tmp_path / "v.json"
    path.write_text("{oops", encoding="utf-8")
    assert LibraryViewState(SessionStore(path, "T")).tabs == {}
    path.write_text(json.dumps({"last_tab": 5, "tabs": {
        "scene": {"sort": "bogus", "descending": "yes", "size": -4},
        "chara": {"sort": "name", "descending": False, "size": True},
        "outfit": "nope"}}), encoding="utf-8")
    s = LibraryViewState(SessionStore(path, "T"))
    assert s.last_tab == "" and set(s.tabs) == {"scene", "chara"}
    assert s.view("scene") == TabView(SORT_UPDATED, True, None)
    assert s.view("chara") == TabView(SORT_NAME, False, None)

"""Walking the library's sidebar with the keyboard (kkafio/dialogs/library/model.py): the order of the
entries and where Up / Down land. The widget wiring (focus, debounce) is in window.py."""

import conftest  # noqa: F401  (sandbox env must be set before kkafio is imported)
from kkafio.dialogs.library import (
    VIEW_ALL, VIEW_SERIES, VIEW_TO_ARCHIVE, VIEW_TO_DELETE, VIEW_UNTAGGED, sidebar_entries, sidebar_step,
)

ENTRIES = sidebar_entries(["Fate", "SAO"])


def test_the_entries_are_in_the_order_the_sidebar_draws_them():
    assert ENTRIES == [(VIEW_ALL, ""), (VIEW_UNTAGGED, ""), (VIEW_TO_ARCHIVE, ""), (VIEW_TO_DELETE, ""),
                       (VIEW_SERIES, "Fate"), (VIEW_SERIES, "SAO")]


def test_down_and_up_walk_through_the_buttons_into_the_folders_and_back():
    pos = (VIEW_ALL, "")
    seen = [pos]
    for _ in range(5):
        pos = sidebar_step(ENTRIES, pos, 1)
        seen.append(pos)
    assert seen == ENTRIES
    for expected in reversed(ENTRIES[:-1]):
        pos = sidebar_step(ENTRIES, pos, -1)
        assert pos == expected


def test_the_walk_stops_at_both_ends():
    assert sidebar_step(ENTRIES, (VIEW_ALL, ""), -1) == (VIEW_ALL, "")
    assert sidebar_step(ENTRIES, (VIEW_SERIES, "SAO"), 1) == (VIEW_SERIES, "SAO")


def test_folder_names_match_ignoring_case():
    assert sidebar_step(ENTRIES, (VIEW_SERIES, "fate"), 1) == (VIEW_SERIES, "SAO")


def test_a_folder_that_is_filtered_out_sits_just_below_to_delete():
    entries = sidebar_entries(["SAO"])                           # "Fate" is hidden by the folder filter
    assert sidebar_step(entries, (VIEW_SERIES, "Fate"), 1) == (VIEW_SERIES, "SAO")
    assert sidebar_step(entries, (VIEW_SERIES, "Fate"), -1) == (VIEW_TO_ARCHIVE, "")


def test_with_no_folders_the_walk_stays_on_the_buttons():
    entries = sidebar_entries([])
    assert sidebar_step(entries, (VIEW_TO_DELETE, ""), 1) == (VIEW_TO_DELETE, "")

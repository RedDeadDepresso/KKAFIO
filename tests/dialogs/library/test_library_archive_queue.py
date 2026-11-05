"""The library's Archive queue (kkafio/dialogs/library/model.py): cards queued for Archive Cards wait in
their own sidebar view, out of every other one, and Restore takes them out again like the trash."""

from pathlib import Path

import conftest  # noqa: F401  (sandbox env must be set before kkafio is imported)
from kkafio.dialogs.library import VIEW_ALL, VIEW_TO_ARCHIVE, VIEW_SERIES, VIEW_TO_DELETE, VIEW_UNTAGGED, CharaItem, LibraryModel


def _item(name, series=None):
    return CharaItem(Path(f"{name}.png"), Path("."), name, series, 1, 1.0)


def _model():
    a, b, c, d = _item("a", "S"), _item("b", "S"), _item("c"), _item("d")
    return LibraryModel([a, b, c, d]), (a, b, c, d)


def _shown(model, mode, series=""):
    model.set_view(mode, series)
    return sorted(i.name for i in model.visible())


def test_an_archived_card_only_shows_in_the_archive_queue():
    model, (a, b, c, d) = _model()
    model.mark_to_archive([a, c])
    assert _shown(model, VIEW_TO_ARCHIVE) == ["a", "c"]
    assert _shown(model, VIEW_ALL) == ["b", "d"]
    assert _shown(model, VIEW_UNTAGGED) == ["d"]
    assert _shown(model, VIEW_SERIES, "S") == ["b"]
    assert _shown(model, VIEW_TO_DELETE) == []
    assert model.to_archive_count() == 2 and model.to_delete_count() == 0
    assert model.counts() == (2, 1)                          # all cards, untagged cards: the queue is in neither
    assert model.series_list() == [("S", 1)] and model.series_count("S") == 1


def test_restore_takes_a_card_out_of_the_archive_queue_and_the_trash():
    model, (a, b, c, d) = _model()
    model.mark_to_archive([a])
    model.mark_to_delete([b])
    model.restore([a, b])
    assert not a.to_archive and not b.to_delete and model.to_archive_count() == 0 and model.to_delete_count() == 0
    assert _shown(model, VIEW_ALL) == ["a", "b", "c", "d"]


def test_a_card_is_in_the_archive_queue_or_the_trash_never_both():
    model, (a, *_rest) = _model()
    model.mark_to_archive([a])
    model.mark_to_delete([a])
    assert a.to_delete and not a.to_archive and a.held
    model.mark_to_archive([a])
    assert a.to_archive and not a.to_delete
    assert _shown(model, VIEW_TO_DELETE) == [] and _shown(model, VIEW_TO_ARCHIVE) == ["a"]


def test_the_search_still_filters_the_archive_queue():
    model, (a, b, *_rest) = _model()
    model.mark_to_archive([a, b])
    model.search = "b"
    assert _shown(model, VIEW_TO_ARCHIVE) == ["b"]

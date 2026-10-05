"""Review Similar Characters: grouping, keep strategies, the dialog's selection
state and the hand-off to Delete Cards. (The CustomTkinter window itself isn't
exercised here — everything it decides lives in `ReviewState`.)"""

import io
import os
from pathlib import Path
from unittest import mock

import pytest
from PIL import Image

from kkafio.system.similar_chara_dialog import (
    ReviewGroup, ReviewState, fit_image, format_size, is_plain_key, reveal_command,
)
from kkafio.tasks import review_similar_chara as rsc
from kkafio.tasks.filter_duplicate_contents import (
    KEEP_FIRST_LEX, KEEP_LAST_LEX, KEEP_NEWEST, KEEP_NONE, KEEP_OLDEST,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _png(seed: str) -> bytes:
    """A 64x64 PNG of smooth random blobs (pHash needs structure; the same seed
    always gives the same cover, different seeds give clearly different ones)."""
    import numpy as np
    rng = np.random.default_rng(sum(ord(c) * 31 ** i for i, c in enumerate(seed)) % 2**32)
    small = Image.fromarray(rng.integers(0, 256, (8, 8), dtype=np.uint8))
    out = io.BytesIO()
    small.resize((64, 64), Image.BICUBIC).convert("RGB").save(out, "PNG")
    return out.getvalue()


def _card(path: Path, pattern: str = "h", extra: bytes = b"") -> Path:
    """A file the game would call a chara card: a PNG followed by the chara marker."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_png(pattern) + "【KoiKatuChara】".encode("utf-8") + extra)
    return path


def _task(folder: Path, **kw):
    cfg = mock.Mock()
    game = folder.parent / (folder.name + "_game")      # elsewhere, so CharaDir isn't read as the game's chara folder
    cfg.game_path = {"charaFemale": str(game / "female"), "charaMale": str(game / "male")}
    cfg.review_similar_chara = {"Mode": rsc.MODE_COVER, "CharaDir": str(folder), "UseCache": True,
                                     "CheckSharedMods": False, "IncludeCoordinates": False,
                                     "AutoResolve": False, "ModsDir": "/mods", **kw}
    return rsc.ReviewSimilarChara(cfg, None)


def _names(group: ReviewGroup) -> list[str]:
    return [p.name for p in group.paths]


# ---------------------------------------------------------------------------
# filename grouping
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stem, base", [
    ("file_1", "file"), ("file_2", "file"), ("data-05", "data"), ("data 7", "data"),
    ("Rin (2)", "Rin"), ("Rin[3]", "Rin"), ("a_1_2", "a"), ("Miku39", "Miku"),
    ("Rin", "Rin"), ("Rin_", "Rin"),
    ("123", "123"),                                       # nothing but a number: left alone
    ("Rin_2024", "Rin_2024"),                              # 4 digits is a year, not a copy number
    ("KoiKatuChara_20200101123456789", "KoiKatuChara_20200101123456789"),  # the game's own names
    ("Koikatu_F_20200101123456789 (1)", "Koikatu_F_20200101123456789"),
])
def test_strip_number_suffix(stem, base):
    assert rsc.strip_number_suffix(stem) == base


def test_filename_groups_are_per_folder_and_recursive(tmp_path):
    cards = [rsc._Card(_card(p)) for p in (
        tmp_path / "Rin.png", tmp_path / "Rin_1.png", tmp_path / "RIN (2).png", tmp_path / "Other.png",
        tmp_path / "sub" / "Rin_1.png", tmp_path / "sub" / "Rin_2.png",
        tmp_path / "lonely" / "Rin_3.png",
    )]
    groups = rsc.group_by_filename(cards)
    assert sorted(_names(g) for g in groups) == sorted([
        ["Rin_1.png", "Rin_2.png"],                       # sub/
        ["RIN (2).png", "Rin.png", "Rin_1.png"],          # top folder, case-insensitive
    ])
    for g in groups:                                      # never mixes folders
        assert len({p.parent for p in g.paths}) == 1


# ---------------------------------------------------------------------------
# name grouping
# ---------------------------------------------------------------------------

def test_name_groups_ignore_case_width_and_unnamed(tmp_path):
    def card(name, first, last):
        return rsc._Card(tmp_path / name, first=first, last=last)
    groups = rsc.group_by_name([
        card("a.png", "Hinana", "Ichikawa"), card("b.png", "hinana", " ICHIKAWA "),
        card("c.png", "Ｈｉｎａｎａ", "Ichikawa"),            # full-width letters
        card("d.png", "Hinana", "Other"),                    # different last name
        card("e.png", "", ""), card("f.png", "", ""),         # unnamed cards never "match"
    ])
    assert [_names(g) for g in groups] == [["a.png", "b.png", "c.png"]]
    assert groups[0].label == "Ichikawa Hinana"


# ---------------------------------------------------------------------------
# cover grouping (real perceptual hashes)
# ---------------------------------------------------------------------------

def test_cover_groups_use_perceptual_hash(tmp_path):
    _card(tmp_path / "a.png", "h")
    _card(tmp_path / "a_resaved.png", "h", extra=b"different data after the cover")
    _card(tmp_path / "b.png", "other cover")
    _card(tmp_path / "c.png", "third cover")
    cards = rsc._Scanner(rsc.MODE_COVER, use_cache=False).scan([tmp_path])
    groups = rsc.build_groups(rsc.MODE_COVER, cards)
    assert [_names(g) for g in groups] == [["a.png", "a_resaved.png"]]


def test_scanner_only_returns_chara_cards_and_skips_duplicates_folder(tmp_path):
    _card(tmp_path / "chara.png")
    (tmp_path / "scene.png").write_bytes(_png("h") + b"\x00KStudio\x00" + "【KoiKatuChara】".encode())
    (tmp_path / "plain.png").write_bytes(_png("h"))
    _card(tmp_path / "_duplicates_" / "chara" / "old.png")
    cards = rsc._Scanner(rsc.MODE_FILENAME, use_cache=False).scan([tmp_path])
    assert [c.path.name for c in cards] == ["chara.png"]


def test_scan_cache_is_reused_and_invalidated(tmp_path):
    a = _card(tmp_path / "a.png", "h")
    with mock.patch.object(rsc, "phash_of_file", wraps=rsc.phash_of_file) as spy:
        rsc._Scanner(rsc.MODE_COVER, True).scan([tmp_path])
        rsc._Scanner(rsc.MODE_COVER, True).scan([tmp_path])
        assert spy.call_count == 1                       # second scan: straight from the cache
        os.utime(a, (a.stat().st_atime, a.stat().st_mtime + 10))
        rsc._Scanner(rsc.MODE_COVER, True).scan([tmp_path])
        assert spy.call_count == 2                       # mtime changed -> hashed again
    assert (tmp_path / rsc.CACHE_FILE).exists()
    no_cache = tmp_path / "nocache"
    _card(no_cache / "a.png")
    rsc._Scanner(rsc.MODE_COVER, False).scan([no_cache])
    assert not (no_cache / rsc.CACHE_FILE).exists()


# ---------------------------------------------------------------------------
# keep strategies
# ---------------------------------------------------------------------------

def _files(tmp_path):
    small = tmp_path / "b_small.png"; small.write_bytes(b"1")
    big = tmp_path / "c_big.png";     big.write_bytes(b"1" * 100)
    mid = tmp_path / "a_mid.png";     mid.write_bytes(b"1" * 10)
    for p, mtime in ((small, 300), (big, 100), (mid, 200)):
        os.utime(p, (mtime, mtime))
    return small, big, mid


def test_select_keep_covers_every_strategy(tmp_path):
    small, big, mid = _files(tmp_path)
    paths = [small, big, mid]
    assert rsc.select_keep(paths, rsc.KEEP_BIGGEST) == big
    assert rsc.select_keep(paths, rsc.KEEP_SMALLEST) == small
    assert rsc.select_keep(paths, KEEP_NEWEST) == small
    assert rsc.select_keep(paths, KEEP_OLDEST) == big
    assert rsc.select_keep(paths, KEEP_LAST_LEX) == big
    assert rsc.select_keep(paths, KEEP_FIRST_LEX) == mid
    assert rsc.select_keep(paths, KEEP_NONE) is None


def test_select_keep_ties_resolve_to_last_name(tmp_path):
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    a.write_bytes(b"xx"); b.write_bytes(b"yy")
    assert rsc.select_keep([a, b], rsc.KEEP_BIGGEST) == b


def test_every_dropdown_choice_is_a_working_strategy(tmp_path):
    small, big, mid = _files(tmp_path)
    for label, key in rsc.KEEP_CHOICES:
        rsc.select_keep([small, big, mid], key)           # must not raise
    assert len({key for _, key in rsc.KEEP_CHOICES}) == len(rsc.KEEP_CHOICES)


# ---------------------------------------------------------------------------
# the dialog's selection state
# ---------------------------------------------------------------------------

def _state(tmp_path):
    small, big, mid = _files(tmp_path)
    other1, other2 = tmp_path / "x.png", tmp_path / "y.png"
    other1.write_bytes(b"1"); other2.write_bytes(b"11")
    groups = [ReviewGroup("g0", "", (small, big, mid)), ReviewGroup("g1", "", (other1, other2))]
    return ReviewState(groups, rsc.select_keep), (small, big, mid, other1, other2)


def test_everything_starts_unlocked_and_unselected(tmp_path):
    state, _ = _state(tmp_path)
    assert state.locked == [False, False] and state.selected_count() == 0
    assert state.is_remaining(0) and state.is_remaining(1)


def test_making_a_choice_locks_the_whole_group(tmp_path):
    state, _ = _state(tmp_path)
    state.set_selected(0, 0, True)
    assert state.locked == [True, False] and state.selected[0][0]     # one lock per group
    state.set_selected(0, 0, False)                                   # un-ticking is a choice too
    assert state.locked[0] and not state.selected[0][0]
    assert state.is_remaining(1)                                      # other groups are unaffected


def test_lock_can_be_toggled_by_hand(tmp_path):
    state, _ = _state(tmp_path)
    state.set_locked(1, True);  assert not state.is_remaining(1)
    state.set_locked(1, False); assert state.is_remaining(1)


def test_auto_select_keeps_one_and_ticks_the_rest(tmp_path):
    state, (small, big, mid, o1, o2) = _state(tmp_path)
    assert state.apply_keep(rsc.KEEP_BIGGEST) == (2, 0, 0)
    assert state.selected == [[True, False, True], [True, False]]
    assert set(state.selected_paths()) == {small, mid, o1}
    assert state.locked == [False, False]                # auto-select never locks


def test_auto_select_skips_locked_groups_and_can_be_reapplied(tmp_path):
    state, (small, big, mid, o1, o2) = _state(tmp_path)
    state.set_selected(0, 2, True)                       # the user's own choice locks group 0
    assert state.apply_keep(rsc.KEEP_SMALLEST) == (1, 1, 0)
    assert state.selected[0] == [False, False, True]     # the whole group is left as the user had it
    assert state.selected[1] == [False, True]            # filled in: keeps the smaller file
    assert state.apply_keep(rsc.KEEP_BIGGEST) == (1, 1, 0)
    assert state.selected[1] == [True, False]            # a different strategy replaces the earlier auto result
    state.set_locked(0, False)                           # unlocking the group makes it fair game again
    assert state.apply_keep(rsc.KEEP_BIGGEST) == (2, 0, 0)
    assert state.selected[0] == [True, False, True]


def test_undo_auto_select_restores_the_previous_ticks(tmp_path):
    state, _ = _state(tmp_path)
    assert not state.can_undo() and state.undo_auto() == (0, 0)
    state.apply_keep(rsc.KEEP_BIGGEST)
    assert state.can_undo() and state.selected_count() == 3
    assert state.undo_auto() == (2, 0)
    assert state.selected_count() == 0 and not state.can_undo()


def test_undo_steps_back_one_auto_select_at_a_time(tmp_path):
    state, _ = _state(tmp_path)
    state.apply_keep(rsc.KEEP_BIGGEST)
    first = [list(row) for row in state.selected]
    state.apply_keep(KEEP_NONE)                          # replaces the first result
    assert state.selected_count() == 5
    state.undo_auto()
    assert state.selected == first                       # back to the first auto-select
    state.undo_auto()
    assert state.selected_count() == 0


def test_undo_keeps_choices_the_user_made_since(tmp_path):
    state, _ = _state(tmp_path)
    state.apply_keep(rsc.KEEP_BIGGEST)                   # [[T,F,T],[T,F]]
    state.set_selected(0, 1, True)                       # the user's own decision in group 0 -> locked
    assert state.undo_auto() == (1, 1)                   # group 1 undone, group 0 kept
    assert state.selected[0] == [True, True, True] and state.selected[1] == [False, False]


def test_auto_select_that_changes_nothing_is_not_undoable(tmp_path):
    state, _ = _state(tmp_path)
    state.set_locked(0, True); state.set_locked(1, True)  # everything locked: nothing to apply
    assert state.apply_keep(rsc.KEEP_BIGGEST) == (0, 2, 0)
    assert not state.can_undo()


def test_keep_none_selects_every_card_and_is_reported_as_full_groups(tmp_path):
    state, _ = _state(tmp_path)
    state.apply_keep(KEEP_NONE)
    assert state.selected_count() == 5 and state.fully_selected_groups() == [0, 1]
    state.set_selected(0, 0, False)
    assert state.fully_selected_groups() == [1]


def test_auto_select_survives_a_vanished_file(tmp_path):
    state, (small, *_rest) = _state(tmp_path)
    small.unlink()
    applied, skipped, failed = state.apply_keep(rsc.KEEP_NEWEST)
    assert (applied, skipped, failed) == (1, 0, 1)       # group 0 couldn't be read, group 1 was fine


def test_format_size():
    assert format_size(512) == "512 B" and format_size(2048) == "2.0 KB"
    assert format_size(int(63.9 * 1024 * 1024)) == "63.9 MB"


def test_windows_reveal_command_selects_the_card_even_with_spaces_and_commas():
    path = Path("D:/SteamLibrary/Koikatsu Party/UserData/chara/female/The Idolmaster/Ichikawa, Hinana_2.png")
    cmd = reveal_command(path, "win32")
    # one string, quotes around the path only: that's the form explorer understands
    assert cmd.startswith('explorer /select,"') and cmd.endswith('Ichikawa, Hinana_2.png"')
    assert cmd.count('"') == 2
    assert reveal_command(path, "darwin") == ["open", "-R", str(path)]
    assert reveal_command(path, "linux") == ["xdg-open", str(path.parent)]


class _Ev:
    def __init__(self, state): self.state = state


def test_lock_keys_do_not_disable_the_shortcut_keys():
    """Regression: on Windows NumLock shows up as state bit 0x8 (and CapsLock as 0x2).
    The W/A/S/D and arrow keys were ignored whenever NumLock was on."""
    for lock_state in (0x0, 0x2, 0x8, 0x2 | 0x8):          # none / CapsLock / NumLock / both
        assert is_plain_key(_Ev(lock_state), "win32"), hex(lock_state)
    assert is_plain_key(_Ev(0x2), "linux") and is_plain_key(_Ev(0x10), "linux")   # X11: NumLock is Mod2
    assert is_plain_key(_Ev(0x1), "win32")                  # Shift (capital letters) is fine


def test_ctrl_alt_and_command_still_mean_a_shortcut():
    assert not is_plain_key(_Ev(0x4), "win32") and not is_plain_key(_Ev(0x4 | 0x8), "win32")   # Ctrl (+NumLock)
    assert not is_plain_key(_Ev(0x20000), "win32")          # Alt on Windows
    assert not is_plain_key(_Ev(0x8), "linux")              # Alt (Mod1) on Linux
    assert not is_plain_key(_Ev(0x8), "darwin") and not is_plain_key(_Ev(0x4), "darwin")
    assert is_plain_key(None, "win32") and is_plain_key(object(), "win32")        # no state -> plain


def test_fit_image_scales_big_covers_down_keeps_aspect_and_never_enlarges():
    big = Image.new("RGB", (1200, 1600))
    out = fit_image(big, (340, 460))
    assert out.width <= 340 and out.height <= 460 and out.mode == "RGBA"
    assert abs(out.width / out.height - 0.75) < 0.01
    small = fit_image(Image.new("RGB", (252, 352)), (340, 460))
    assert small.size == (252, 352)
    assert fit_image(Image.new("RGB", (4000, 400)), (340, 460)).width == 340


# ---------------------------------------------------------------------------
# the task: scan -> dialog -> Delete Cards
# ---------------------------------------------------------------------------

def test_selected_cards_are_handed_to_delete_cards_with_this_tasks_options(tmp_path):
    for name in ("Rin.png", "Rin_1.png", "Rin_2.png", "Other.png"):
        _card(tmp_path / name)
    seen = {}

    def fake_dialog(title, groups, keep_choices, select_keep, extra_details=None):
        seen["groups"] = [[p.name for p in g.paths] for g in groups]
        return [tmp_path / "Rin_1.png", tmp_path / "Rin_2.png"]

    delete_cards = mock.Mock()
    with mock.patch("kkafio.system.similar_chara_dialog.review_dialog", fake_dialog), \
         mock.patch("kkafio.tasks.delete_cards.DeleteCards", delete_cards):
        _task(tmp_path, Mode=rsc.MODE_FILENAME, CheckSharedMods=True, ModsDir="/my/mods").run()

    assert seen["groups"] == [["Rin.png", "Rin_1.png", "Rin_2.png"]]
    settings = delete_cards.call_args.kwargs["settings"]
    assert settings["ContentPaths"] == [str(tmp_path / "Rin_1.png"), str(tmp_path / "Rin_2.png")]
    assert (settings["CheckSharedMods"], settings["IncludeCoordinates"], settings["AutoResolve"],
            settings["ModsDir"]) == (True, False, False, "/my/mods")
    delete_cards.return_value.run.assert_called_once_with()


def test_nothing_is_deleted_when_the_dialog_returns_nothing_or_nothing_matches(tmp_path):
    for name in ("Rin.png", "Rin_1.png"):
        _card(tmp_path / name)
    delete_cards = mock.Mock()
    with mock.patch("kkafio.system.similar_chara_dialog.review_dialog", return_value=[]), \
         mock.patch("kkafio.tasks.delete_cards.DeleteCards", delete_cards):
        _task(tmp_path, Mode=rsc.MODE_FILENAME).run()
    delete_cards.assert_not_called()

    only = tmp_path / "only"
    _card(only / "Solo.png")
    dialog = mock.Mock()
    with mock.patch("kkafio.system.similar_chara_dialog.review_dialog", dialog):
        _task(only, Mode=rsc.MODE_FILENAME).run()
    dialog.assert_not_called()                           # no groups -> no dialog


def test_shared_mod_check_covers_the_reviewed_folder_and_the_games_chara_folders(tmp_path):
    """Reviewing one subfolder (e.g. from the context menu) must not blind Delete Cards'
    shared-mod check to cards elsewhere, or it could delete a mod other characters still use."""
    sub = tmp_path / "sub"
    task = _task(sub)
    task._scan_folders = [sub]
    settings = task._delete_cards_settings([sub / "a.png"])
    game = sub.parent / (sub.name + "_game")
    assert settings["CharaDirs"] == [str(sub), str(game / "female"), str(game / "male")]

    task._scan_folders = [game / "female", game / "male"]          # default scan: no duplicates
    assert task._delete_cards_settings([])["CharaDirs"] == [str(game / "female"), str(game / "male")]


def test_delete_cards_uses_an_explicit_chara_dir_list_for_the_shared_mod_check():
    from kkafio.tasks.delete_cards import DeleteCards
    cfg = mock.Mock()
    cfg.delete_cards = {"CharaDir": "/one"}
    assert DeleteCards(cfg, None).chara_dirs_override is None
    own = DeleteCards(cfg, None, settings={"CharaDir": "/one", "CharaDirs": ["/a", "/b"]})
    assert own.chara_dirs_override == ["/a", "/b"]


def test_unknown_mode_is_a_config_error(tmp_path):
    from kkafio.core.errors import ConfigError
    with pytest.raises(ConfigError):
        _task(tmp_path, Mode="Fuzzy")


def test_delete_cards_accepts_explicit_settings():
    from kkafio.tasks.delete_cards import DeleteCards
    cfg = mock.Mock()
    cfg.delete_cards = {"ContentPaths": ["from-config.png"], "CheckSharedMods": True}
    own = DeleteCards(cfg, None, settings={"ContentPaths": ["mine.png"], "CheckSharedMods": False})
    assert own.content_paths == ["mine.png"] and own.check_shared_mods is False
    assert DeleteCards(cfg, None).content_paths == ["from-config.png"]

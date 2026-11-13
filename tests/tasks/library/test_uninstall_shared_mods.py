"""Uninstall Contents' shared-mod check, and the helpers it shares with Delete Cards."""

from pathlib import Path
from unittest import mock

from kkafio.cards.mods import shared_mods
from kkafio.tasks import uninstall_contents as uc


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------

def test_game_card_dirs_use_overrides_else_the_game_folders():
    gp = {"charaFemale": "F", "charaMale": "M", "scene": "S", "coordinate": "C"}
    assert shared_mods.game_card_dirs(gp) == ([Path("F"), Path("M")], [Path("S")], [Path("C")])
    assert shared_mods.game_card_dirs(gp, ["X"], "Y", "Z") == ([Path("X")], [Path("Y")], [Path("Z")])
    assert shared_mods.game_card_dirs({"charaFemale": "F"}) == ([Path("F")], [], [])      # no Studio


def test_collect_guids_in_use_skips_excluded_files():
    by_file = {"/g/keep.png": ["a"], "/g/gone.png": ["b"]}
    with mock.patch.object(shared_mods, "collect_chara_guids_by_file", lambda d, use_cache: by_file), \
         mock.patch.object(shared_mods, "collect_scene_guids_by_file", lambda d, use_cache: {"/s/x.png": ["c"]}), \
         mock.patch.object(shared_mods, "collect_coord_guids_by_file", lambda d, use_cache: {}):
        got = shared_mods.collect_guids_in_use([Path("/g")], [Path("/s")], [], {Path("/g/gone.png")})
    assert got == {"a", "c"}


def test_keep_unshared_returns_only_free_zipmods():
    got = shared_mods.keep_unshared({Path("a.zipmod"): "ga", Path("b.zipmod"): "gb"}, {"gb"}, "T")
    assert got == [Path("a.zipmod")]


# ---------------------------------------------------------------------------
# Uninstall Contents
# ---------------------------------------------------------------------------

class FakeFM:
    def __init__(self, files, calls):
        self.files, self.calls = files, calls

    def find_supported_files(self, folder):
        return self.files, None

    def find_and_remove(self, label, path, dest):
        self.calls.append(("remove", label, Path(path).name))


def _task(tmp_path, files, calls, **cfg):
    t = uc.UninstallContents.__new__(uc.UninstallContents)
    t.file_manager = FakeFM(files, calls)
    t.game_path = {"mods": str(tmp_path / "game" / "mods")}
    t.input_path = tmp_path / "in"
    t.do_chara = t.do_mods = t.do_coords = t.do_scenes = t.do_overlays = True
    t.check_shared_mods = cfg.get("check", True)
    t.use_cache = cfg.get("use_cache", True)
    t.resolve_png = lambda p: calls.append(("card", p.name))
    return t


def _run(task, in_use, tmp_path, guids):
    (tmp_path / "in").mkdir(exist_ok=True)
    (tmp_path / "game" / "mods").mkdir(parents=True, exist_ok=True)
    caches = []

    def fake_cache(d, include_modpack=False, use_cache=True):
        caches.append((Path(d).name, use_cache))
        return {g: str(d / n) for n, g in guids.get(Path(d).name, {}).items()}

    with mock.patch.object(uc, "validate_input_path", lambda *a, **k: None), \
         mock.patch.object(uc, "build_mods_cache", fake_cache), \
         mock.patch.object(uc, "find_guids_in_use", lambda *a, **k: in_use) as scan:
        task.run()
    return caches, scan


def test_a_zipmod_still_used_by_a_remaining_card_is_kept(tmp_path):
    calls = []
    files = [tmp_path / "in" / "Shared.zipmod", tmp_path / "in" / "Own.zipmod"]
    task = _task(tmp_path, files, calls)
    caches, _ = _run(task, {"g-shared"}, tmp_path,
                     {"mods": {"Shared.zipmod": "g-shared", "Own.zipmod": "g-own"}})
    assert calls == [("remove", "MODS", "Own.zipmod")]
    assert ("in", True) in caches and ("mods", True) in caches       # both mods caches are read


def test_cards_are_uninstalled_before_the_shared_check_and_the_mods(tmp_path):
    calls = []
    files = [tmp_path / "in" / "A.zipmod", tmp_path / "in" / "Card.png"]
    task = _task(tmp_path, files, calls)
    in_use_at_scan = []
    (tmp_path / "in").mkdir(exist_ok=True)
    (tmp_path / "game" / "mods").mkdir(parents=True, exist_ok=True)

    def scan(*a, **k):
        in_use_at_scan.append(list(calls))
        return set()

    with mock.patch.object(uc, "validate_input_path", lambda *a, **k: None), \
         mock.patch.object(uc, "build_mods_cache", lambda *a, **k: {}), \
         mock.patch.object(uc, "find_guids_in_use", scan):
        task.run()
    assert in_use_at_scan == [[("card", "Card.png")]]                  # the card was gone when scanning
    assert calls == [("card", "Card.png"), ("remove", "MODS", "A.zipmod")]


def test_with_the_check_off_nothing_is_scanned_and_every_zipmod_is_removed(tmp_path):
    calls = []
    files = [tmp_path / "in" / "Shared.zipmod"]
    task = _task(tmp_path, files, calls, check=False)
    caches, scan = _run(task, {"g-shared"}, tmp_path, {"mods": {"Shared.zipmod": "g-shared"}})
    assert calls == [("remove", "MODS", "Shared.zipmod")] and caches == []


def test_use_cache_off_is_passed_to_the_mods_caches(tmp_path):
    calls = []
    task = _task(tmp_path, [tmp_path / "in" / "A.zipmod"], calls, use_cache=False)
    caches, _ = _run(task, set(), tmp_path, {})
    assert caches and all(flag is False for _, flag in caches)


def test_a_zipmod_with_no_readable_guid_is_still_removed(tmp_path):
    calls = []
    task = _task(tmp_path, [tmp_path / "in" / "Odd.zipmod"], calls)
    _run(task, {"anything"}, tmp_path, {})
    assert calls == [("remove", "MODS", "Odd.zipmod")]

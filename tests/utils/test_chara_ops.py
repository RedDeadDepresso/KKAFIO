"""Tests for utils/chara_ops.py"""
import json
import zipfile
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_zipmod(path: Path, guid: str) -> Path:
    """Create a minimal zipmod with a manifest.xml containing the given guid."""
    manifest = f"""<?xml version="1.0" encoding="utf-8"?>
<manifest schema-ver="1">
  <guid>{guid}</guid>
  <name>TestMod</name>
  <version>1.0</version>
  <author>Test</author>
</manifest>"""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.xml", manifest)
    return path


# ── guid_from_zipmod ──────────────────────────────────────────────────────────

class TestGuidFromZipmod:
    def test_extracts_guid(self, tmp_path):
        from utils.chara_ops import guid_from_zipmod
        zp = _make_zipmod(tmp_path / "test.zipmod", "com.test.mod")
        assert guid_from_zipmod(zp) == "com.test.mod"

    def test_returns_none_for_missing_manifest(self, tmp_path):
        from utils.chara_ops import guid_from_zipmod
        zp = tmp_path / "bad.zipmod"
        with zipfile.ZipFile(zp, "w") as zf:
            zf.writestr("readme.txt", "nothing")
        assert guid_from_zipmod(zp) is None

    def test_returns_none_for_corrupt_file(self, tmp_path):
        from utils.chara_ops import guid_from_zipmod
        zp = tmp_path / "corrupt.zipmod"
        zp.write_bytes(b"not a zip")
        assert guid_from_zipmod(zp) is None

    def test_returns_none_for_missing_guid_element(self, tmp_path):
        from utils.chara_ops import guid_from_zipmod
        zp = tmp_path / "noguid.zipmod"
        with zipfile.ZipFile(zp, "w") as zf:
            zf.writestr("manifest.xml", "<manifest><name>Test</name></manifest>")
        assert guid_from_zipmod(zp) is None


# ── in_modpack_folder ─────────────────────────────────────────────────────────

class TestInModpackFolder:
    def test_inside_modpack_folder(self, tmp_path):
        from utils.chara_ops import in_modpack_folder
        mods = tmp_path / "mods"
        zp = mods / "Sideloader Modpack" / "mod.zipmod"
        assert in_modpack_folder(zp, mods) is True

    def test_nested_modpack_folder(self, tmp_path):
        from utils.chara_ops import in_modpack_folder
        mods = tmp_path / "mods"
        zp = mods / "Sideloader Modpack - KK" / "author" / "mod.zipmod"
        assert in_modpack_folder(zp, mods) is True

    def test_not_in_modpack_folder(self, tmp_path):
        from utils.chara_ops import in_modpack_folder
        mods = tmp_path / "mods"
        zp = mods / "MyMods" / "mod.zipmod"
        assert in_modpack_folder(zp, mods) is False

    def test_outside_mods_dir(self, tmp_path):
        from utils.chara_ops import in_modpack_folder
        mods = tmp_path / "mods"
        zp = tmp_path / "other" / "mod.zipmod"
        assert in_modpack_folder(zp, mods) is False


# ── load_modpack_index ────────────────────────────────────────────────────────

class TestLoadModpackIndex:
    def test_loads_from_exe_dir(self, tmp_path):
        from utils.chara_ops import load_modpack_index, MODPACK_INDEX_FILE
        import sys
        index = {"generated": "2025-01-01", "count": 2,
                 "guids": {"com.a": "Sideloader Modpack/a.zipmod",
                           "com.b": "Sideloader Modpack/b.zipmod"}}
        (tmp_path / MODPACK_INDEX_FILE).write_text(json.dumps(index), encoding="utf-8")
        with patch.object(sys, "frozen", True, create=True):
            with patch("sys.executable", str(tmp_path / "kkafio_cli.exe")):
                result = load_modpack_index()
        assert result == index["guids"]

    def test_loads_from_mods_dir(self, tmp_path):
        from utils.chara_ops import load_modpack_index, MODPACK_INDEX_FILE
        mods_dir = tmp_path / "mods"
        mods_dir.mkdir()
        index = {"guids": {"com.x": "Sideloader Modpack/x.zipmod"}}
        (mods_dir / MODPACK_INDEX_FILE).write_text(json.dumps(index), encoding="utf-8")
        # No exe-dir index — should fall back to mods_dir
        with patch("sys.executable", str(tmp_path / "other" / "exe")):
            result = load_modpack_index(mods_dir)
        assert "com.x" in result

    def test_returns_none_when_not_found(self, tmp_path):
        from utils.chara_ops import load_modpack_index
        import sys
        # Point exe dir to an empty tmp dir so no real index is found
        empty = tmp_path / "empty_exe"
        empty.mkdir()
        with patch.object(sys, "frozen", True, create=True):
            with patch("sys.executable", str(empty / "kkafio_cli.exe")):
                result = load_modpack_index(tmp_path / "nonexistent_mods")
        assert result is None

    def test_returns_none_for_corrupt_json(self, tmp_path):
        from utils.chara_ops import load_modpack_index, MODPACK_INDEX_FILE
        mods = tmp_path / "mods"
        mods.mkdir()
        (mods / MODPACK_INDEX_FILE).write_text("not json", encoding="utf-8")
        result = load_modpack_index(mods)
        assert result is None


# ── scan_mods ─────────────────────────────────────────────────────────────────

class TestScanMods:
    def test_finds_local_mod(self, tmp_path):
        from utils.chara_ops import scan_mods
        mods = tmp_path / "mods"
        mods.mkdir()
        _make_zipmod(mods / "mymod.zipmod", "com.local.mod")

        result = scan_mods(mods, {"com.local.mod"})
        assert "com.local.mod" in result

    def test_excludes_modpack_when_not_include(self, tmp_path):
        from utils.chara_ops import scan_mods
        mods = tmp_path / "mods"
        sp = mods / "Sideloader Modpack"
        sp.mkdir(parents=True)
        _make_zipmod(sp / "mod.zipmod", "com.modpack.mod")

        result = scan_mods(mods, {"com.modpack.mod"}, include_modpack=False)
        assert "com.modpack.mod" not in result

    def test_includes_modpack_when_flag_set(self, tmp_path):
        from utils.chara_ops import scan_mods
        mods = tmp_path / "mods"
        sp = mods / "Sideloader Modpack"
        sp.mkdir(parents=True)
        _make_zipmod(sp / "mod.zipmod", "com.modpack.mod")

        result = scan_mods(mods, {"com.modpack.mod"}, include_modpack=True)
        assert "com.modpack.mod" in result

    def test_returns_empty_for_nonexistent_dir(self, tmp_path):
        from utils.chara_ops import scan_mods
        result = scan_mods(tmp_path / "nonexistent", {"com.x"})
        assert result == {}

    def test_uses_modpack_index_for_fast_lookup(self, tmp_path):
        from utils.chara_ops import scan_mods, MODPACK_INDEX_FILE
        mods = tmp_path / "mods"
        mods.mkdir()
        # Index says com.fast is in modpack — no actual file needed
        index = {"guids": {"com.fast": "Sideloader Modpack/fast.zipmod"}}
        (mods / MODPACK_INDEX_FILE).write_text(json.dumps(index), encoding="utf-8")

        # Without include_modpack: should resolve (skip) via index — not in result
        result = scan_mods(mods, {"com.fast"}, include_modpack=False)
        assert "com.fast" not in result

    def test_index_resolves_modpack_path_when_included(self, tmp_path):
        from utils.chara_ops import scan_mods, MODPACK_INDEX_FILE
        mods = tmp_path / "mods"
        sp = mods / "Sideloader Modpack"
        sp.mkdir(parents=True)
        zp = _make_zipmod(sp / "fast.zipmod", "com.fast")

        index = {"guids": {"com.fast": "Sideloader Modpack/fast.zipmod"}}
        (mods / MODPACK_INDEX_FILE).write_text(json.dumps(index), encoding="utf-8")

        result = scan_mods(mods, {"com.fast"}, include_modpack=True)
        assert "com.fast" in result
        assert result["com.fast"] == zp

    def test_cache_skips_modpack_path(self, tmp_path):
        """Stale cache should not return a modpack path when include_modpack=False."""
        from utils.chara_ops import scan_mods, MODS_CACHE_FILE
        mods = tmp_path / "mods"
        sp = mods / "Sideloader Modpack"
        sp.mkdir(parents=True)
        zp = _make_zipmod(sp / "mod.zipmod", "com.stale")

        # Write a stale cache that points to the modpack path
        import time
        cache = {
            "mods_dir": str(mods),
            "mtime": time.time() + 9999,  # far future — won't expire
            "guids": {"com.stale": str(zp)},
        }
        (mods.parent / MODS_CACHE_FILE).write_text(json.dumps(cache), encoding="utf-8")

        result = scan_mods(mods, {"com.stale"}, include_modpack=False, use_cache=True)
        assert "com.stale" not in result
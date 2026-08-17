"""Tests for build_modpack_index.py"""
import json
import zipfile
import pytest
from pathlib import Path


def _make_zipmod(path: Path, guid: str) -> Path:
    manifest = f"""<?xml version="1.0" encoding="utf-8"?>
<manifest schema-ver="1"><guid>{guid}</guid><name>Test</name>
<version>1.0</version><author>Test</author></manifest>"""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.xml", manifest)
    return path


class TestIsModpackFolder:
    def test_direct_sideloader_folder(self, tmp_path):
        from build_modpack_index import is_modpack_folder
        mods = tmp_path
        zp = mods / "Sideloader Modpack" / "mod.zipmod"
        assert is_modpack_folder(zp, mods) is True

    def test_nested_sideloader_folder(self, tmp_path):
        from build_modpack_index import is_modpack_folder
        mods = tmp_path
        zp = mods / "Sideloader Modpack - KK" / "author" / "mod.zipmod"
        assert is_modpack_folder(zp, mods) is True

    def test_case_insensitive(self, tmp_path):
        from build_modpack_index import is_modpack_folder
        mods = tmp_path
        zp = mods / "sideloader modpack" / "mod.zipmod"
        assert is_modpack_folder(zp, mods) is True

    def test_non_modpack_folder(self, tmp_path):
        from build_modpack_index import is_modpack_folder
        mods = tmp_path
        zp = mods / "MyMods" / "mod.zipmod"
        assert is_modpack_folder(zp, mods) is False

    def test_file_at_root_of_mods(self, tmp_path):
        from build_modpack_index import is_modpack_folder
        zp = tmp_path / "mod.zipmod"
        assert is_modpack_folder(zp, tmp_path) is False


class TestBuildIndex:
    def test_finds_modpack_guids(self, tmp_path):
        from build_modpack_index import build_index
        sp = tmp_path / "Sideloader Modpack"
        sp.mkdir()
        _make_zipmod(sp / "a.zipmod", "com.test.a")
        _make_zipmod(sp / "b.zipmod", "com.test.b")

        result = build_index(tmp_path)
        assert "com.test.a" in result
        assert "com.test.b" in result

    def test_excludes_non_modpack_folders(self, tmp_path):
        from build_modpack_index import build_index
        local = tmp_path / "MyMods"
        local.mkdir()
        _make_zipmod(local / "local.zipmod", "com.local")

        result = build_index(tmp_path)
        assert "com.local" not in result

    def test_result_contains_relative_path(self, tmp_path):
        from build_modpack_index import build_index
        sp = tmp_path / "Sideloader Modpack"
        sp.mkdir()
        _make_zipmod(sp / "mod.zipmod", "com.test")

        result = build_index(tmp_path)
        assert "com.test" in result
        rel = result["com.test"]
        assert "Sideloader Modpack" in rel
        assert not rel.startswith(str(tmp_path))  # must be relative

    def test_skips_zipmod_without_manifest(self, tmp_path):
        from build_modpack_index import build_index
        sp = tmp_path / "Sideloader Modpack"
        sp.mkdir()
        zp = sp / "bad.zipmod"
        with zipfile.ZipFile(zp, "w") as zf:
            zf.writestr("readme.txt", "nothing")

        result = build_index(tmp_path)
        assert len(result) == 0

    def test_empty_mods_folder(self, tmp_path):
        from build_modpack_index import build_index
        result = build_index(tmp_path)
        assert result == {}

    def test_nested_subfolder_inside_modpack(self, tmp_path):
        from build_modpack_index import build_index
        nested = tmp_path / "Sideloader Modpack" / "Author" / "Pack"
        nested.mkdir(parents=True)
        _make_zipmod(nested / "deep.zipmod", "com.deep")

        result = build_index(tmp_path)
        assert "com.deep" in result
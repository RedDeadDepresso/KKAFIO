"""Tests for tasks/rename_chara.py"""
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestSafe:
    def test_removes_illegal_chars(self):
        from tasks.rename_chara import _safe
        assert _safe('a/b:c*d?"e<f>g|h') == "abcdefgh"

    def test_strips_trailing_dots(self):
        from tasks.rename_chara import _safe
        assert _safe("name.") == "name"

    def test_strips_whitespace(self):
        from tasks.rename_chara import _safe
        assert _safe("  name  ") == "name"

    def test_clean_name_unchanged(self):
        from tasks.rename_chara import _safe
        assert _safe("Tohsaka_Rin") == "Tohsaka_Rin"


class TestStemFor:
    def test_both_names(self):
        from tasks.rename_chara import _stem_for
        assert _stem_for({"lastname": "Tohsaka", "firstname": "Rin"}) == "Tohsaka_Rin"

    def test_only_lastname(self):
        from tasks.rename_chara import _stem_for
        assert _stem_for({"lastname": "Tohsaka", "firstname": ""}) == "Tohsaka"

    def test_only_firstname(self):
        from tasks.rename_chara import _stem_for
        assert _stem_for({"lastname": "", "firstname": "Rin"}) == "Rin"

    def test_empty(self):
        from tasks.rename_chara import _stem_for
        assert _stem_for({"lastname": "", "firstname": ""}) == ""


class TestNameKnown:
    def test_known_if_any_field_set(self):
        from tasks.rename_chara import _name_known
        assert _name_known({"lastname": "X", "firstname": "", "nickname": ""}) is True
        assert _name_known({"lastname": "", "firstname": "Y", "nickname": ""}) is True
        assert _name_known({"lastname": "", "firstname": "", "nickname": "Z"}) is True

    def test_unknown_if_all_empty(self):
        from tasks.rename_chara import _name_known
        assert _name_known({"lastname": "", "firstname": "", "nickname": ""}) is False

    def test_unknown_if_whitespace_only(self):
        from tasks.rename_chara import _name_known
        assert _name_known({"lastname": "  ", "firstname": "", "nickname": ""}) is False


class TestMergeCache:
    def test_adds_new_entries(self):
        from tasks.rename_chara import _merge_cache
        cache = {}
        response = {"key1": {"lastname": "A", "firstname": "B", "nickname": "C"}}
        result = _merge_cache(cache, response)
        assert "key1" in result

    def test_sanitises_illegal_chars(self):
        from tasks.rename_chara import _merge_cache
        response = {"key": {"lastname": "A/B", "firstname": "C", "nickname": "D"}}
        result = _merge_cache({}, response)
        assert "/" not in result["key"]["lastname"]

    def test_preserves_existing_known_entry(self):
        from tasks.rename_chara import _merge_cache
        cache = {"key": {"lastname": "Old", "firstname": "Val", "nickname": ""}}
        response = {"key": {"lastname": "", "firstname": "", "nickname": ""}}
        result = _merge_cache(cache, response)
        # Unknown response should not overwrite known cache entry
        assert result["key"]["lastname"] == "Old"

    def test_overwrites_with_known_response(self):
        from tasks.rename_chara import _merge_cache
        cache = {"key": {"lastname": "", "firstname": "", "nickname": ""}}
        response = {"key": {"lastname": "New", "firstname": "Val", "nickname": ""}}
        result = _merge_cache(cache, response)
        assert result["key"]["lastname"] == "New"

    def test_ignores_non_dict_values(self):
        from tasks.rename_chara import _merge_cache
        result = _merge_cache({}, {"key": "not_a_dict"})
        assert "key" not in result


class TestLoadSaveCache:
    def test_roundtrip(self, tmp_path):
        from tasks.rename_chara import _save_cache, _load_cache
        cache = {"key": {"lastname": "A", "firstname": "B", "nickname": "C"}}
        _save_cache(tmp_path, cache)
        loaded = _load_cache(tmp_path)
        assert loaded == cache

    def test_load_returns_empty_if_missing(self, tmp_path):
        from tasks.rename_chara import _load_cache
        assert _load_cache(tmp_path) == {}

    def test_load_returns_empty_on_corrupt(self, tmp_path):
        from tasks.rename_chara import _load_cache, CACHE_FILENAME
        (tmp_path / CACHE_FILENAME).write_text("not json")
        assert _load_cache(tmp_path) == {}


class TestExport:
    def test_returns_empty_when_no_pngs(self, tmp_path):
        from tasks.rename_chara import export
        result = export(tmp_path)
        assert result == ""

    def test_returns_json_for_unrecognised_card(self, tmp_path):
        from tasks.rename_chara import export
        from utils.classifier import CardType
        png = tmp_path / "card.png"
        png.write_bytes(b"KoiKatuChara")

        mock_kc = MagicMock()
        mock_kc._repr_name.return_value = "TestChar"
        mock_kc.__getitem__ = lambda s, k: {"Parameter": {"personality": 0}}[k]

        with patch("tasks.rename_chara.KoikatuCharaData") as MockKC, \
             patch("utils.classifier.get_card_type", return_value=CardType.KK):
            MockKC.load.return_value = mock_kc
            result = export(tmp_path)

        assert result  # non-empty
        # Should be valid JSON
        data = json.loads(result)
        assert isinstance(data, dict)

    def test_skips_non_kk_cards(self, tmp_path):
        from tasks.rename_chara import export
        from utils.classifier import CardType
        png = tmp_path / "kks.png"
        png.write_bytes(b"KoiKatuCharaSun")

        with patch("utils.classifier.get_card_type", return_value=CardType.KKS):
            result = export(tmp_path)

        assert result == ""


class TestProcess:
    def test_strips_markdown_fences(self, tmp_path):
        from tasks.rename_chara import process
        # Provide invalid JSON in fences — should be stripped and then fail gracefully
        process(tmp_path, "```json\nnot_json\n```")
        # No crash is the assertion

    def test_updates_cache_on_valid_response(self, tmp_path):
        from tasks.rename_chara import process, _load_cache, CACHE_FILENAME
        response = json.dumps({"key1": {"lastname": "Tohsaka", "firstname": "Rin", "nickname": "Rin"}})
        process(tmp_path, response)
        cache = _load_cache(tmp_path)
        assert "key1" in cache

    def test_invalid_json_does_not_crash(self, tmp_path):
        from tasks.rename_chara import process
        process(tmp_path, "this is not json")
        # No exception = pass

    def test_rglob_finds_subfolder_cards(self, tmp_path):
        from tasks.rename_chara import process
        from utils.classifier import CardType
        sub = tmp_path / "Series"
        sub.mkdir()
        png = sub / "card.png"
        png.write_bytes(b"KoiKatuChara")

        mock_kc = MagicMock()
        mock_kc._repr_name.return_value = "key1"
        mock_kc.__getitem__ = MagicMock(return_value={"personality": 0, "lastname": "",
                                                       "firstname": "", "nickname": ""})

        response = json.dumps({"key1": {"lastname": "A", "firstname": "B", "nickname": "C"}})

        with patch("tasks.rename_chara.KoikatuCharaData") as MockKC, \
             patch("utils.classifier.get_card_type", return_value=CardType.KK):
            MockKC.load.return_value = mock_kc
            process(tmp_path, response, update_metadata=False)
        # No crash = subfolder was scanned

    def test_rename_stays_in_subfolder(self, tmp_path):
        from tasks.rename_chara import process, _stem_for
        from utils.classifier import CardType
        sub = tmp_path / "Series"
        sub.mkdir()
        png = sub / "card.png"
        png.write_bytes(b"KoiKatuChara")

        key = "card_key"
        nd = {"lastname": "Tohsaka", "firstname": "Rin", "nickname": "Rin"}
        response = json.dumps({key: nd})
        stem = _stem_for(nd)

        mock_kc = MagicMock()
        mock_kc._repr_name.return_value = key
        mock_kc.__getitem__ = MagicMock(return_value={"personality": 0, "lastname": "",
                                                       "firstname": "", "nickname": ""})

        with patch("tasks.rename_chara.KoikatuCharaData") as MockKC, \
             patch("utils.classifier.get_card_type", return_value=CardType.KK):
            MockKC.load.return_value = mock_kc
            process(tmp_path, response, update_metadata=False, rename_files=True)

        renamed = sub / f"{stem}.png"
        # Renamed file must be in the same subfolder
        assert renamed.exists(), f"Expected {renamed} to exist"
        assert not (tmp_path / f"{stem}.png").exists(), "File must NOT be in root"

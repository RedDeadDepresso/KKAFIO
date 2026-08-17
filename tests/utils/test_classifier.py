"""Tests for utils/classifier.py"""
import pytest
from utils.classifier import CardType, get_card_type, is_male, is_coordinate, PERSONALITIES


# ── get_card_type ─────────────────────────────────────────────────────────────

class TestGetCardType:
    def test_kk_card(self):
        data = b"\x00KoiKatuChara\x00"
        assert get_card_type(data) == CardType.KK

    def test_kksp_card(self):
        data = b"\x00KoiKatuCharaSP\x00"
        assert get_card_type(data) == CardType.KKSP

    def test_kks_card(self):
        data = b"\x00KoiKatuCharaSun\x00"
        assert get_card_type(data) == CardType.KKS

    def test_scene_card(self):
        data = b"\x00KoiKatuChara\x00sceneInfo\x00"
        assert get_card_type(data) == CardType.SCENE

    def test_unknown_card(self):
        data = b"\x00random bytes\x00"
        assert get_card_type(data) == CardType.UNKNOWN

    def test_empty_bytes(self):
        assert get_card_type(b"") == CardType.UNKNOWN

    def test_from_path(self, tmp_path):
        png = tmp_path / "test.png"
        png.write_bytes(b"\x00KoiKatuCharaSun\x00")
        assert get_card_type(png) == CardType.KKS

    def test_kksp_takes_priority_over_kk(self):
        # KoiKatuCharaSP contains KoiKatuChara — KKSP must win
        data = b"KoiKatuCharaSP"
        assert get_card_type(data) == CardType.KKSP

    def test_kks_takes_priority_over_kk(self):
        # KoiKatuCharaSun contains KoiKatuChara — KKS must win
        data = b"KoiKatuCharaSun"
        assert get_card_type(data) == CardType.KKS


class TestIsMale:
    def test_male_card(self):
        assert is_male(b"sex\x00") is True

    def test_female_card(self):
        assert is_male(b"sex\x01") is False

    def test_no_sex_field(self):
        assert is_male(b"random") is False


class TestIsCoordinate:
    def test_coordinate(self):
        assert is_coordinate(b"KoiKatuClothes") is True

    def test_not_coordinate(self):
        assert is_coordinate(b"KoiKatuChara") is False

    def test_empty(self):
        assert is_coordinate(b"") is False


class TestPersonalities:
    def test_not_empty(self):
        assert len(PERSONALITIES) > 0

    def test_no_duplicates(self):
        assert len(PERSONALITIES) == len(set(PERSONALITIES))

    def test_all_strings(self):
        assert all(isinstance(p, str) for p in PERSONALITIES)

    def test_known_entry(self):
        assert "Yandere" in PERSONALITIES
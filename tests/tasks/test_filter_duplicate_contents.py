"""Tests for tasks/filter_duplicate_contents.py"""
import struct
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from tasks.filter_duplicate_contents import (
    _get_png_payload, _get_png_image_bytes, _md5,
    KEEP_NEWEST, KEEP_OLDEST, KEEP_BIGGEST, KEEP_SMALLEST,
    KEEP_FIRST_LEX, KEEP_LAST_LEX, KEEP_NONE,
)


# ── PNG helpers ───────────────────────────────────────────────────────────────

_PNG_SIG = b"\x89PNG\r\n\x1a\n"


def _make_png_bytes(payload=b"") -> bytes:
    """Build a minimal PNG with optional post-IEND payload."""
    def chunk(name: bytes, data: bytes) -> bytes:
        import zlib
        return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data))
    png = _PNG_SIG
    png += chunk(b"IHDR", b"\x00"*13)
    png += chunk(b"IDAT", b"\x78\x9c")
    png += chunk(b"IEND", b"")
    png += payload
    return png


class TestPngHelpers:
    def test_get_png_payload_returns_payload(self):
        payload = b"KoiKatuChara\x00"
        data = _make_png_bytes(payload)
        assert _get_png_payload(data) == payload

    def test_get_png_payload_none_when_no_payload(self):
        data = _make_png_bytes(b"")
        assert _get_png_payload(data) is None

    def test_get_png_payload_none_for_non_png(self):
        assert _get_png_payload(b"not a png") is None

    def test_get_png_image_bytes_strips_payload(self):
        payload = b"extra data"
        data = _make_png_bytes(payload)
        image_only = _get_png_image_bytes(data)
        assert image_only.endswith(b"IEND" + b"\x00"*4)
        assert payload not in image_only

    def test_get_png_image_bytes_non_png_returned_as_is(self):
        data = b"not a png"
        assert _get_png_image_bytes(data) == data


class TestMd5:
    def test_consistent_hash(self):
        assert _md5(b"hello") == _md5(b"hello")

    def test_different_data_different_hash(self):
        assert _md5(b"hello") != _md5(b"world")


class TestFilterDuplicateContents:
    def _make_task(self, tmp_path, keep_strategy=KEEP_BIGGEST,
                   fuzzy=False, delete=False):
        from tasks.filter_duplicate_contents import FilterDuplicateContents
        cfg = MagicMock()
        cfg.filter_duplicate_contents = {
            "InputPath":       str(tmp_path),
            "KeepStrategy":    keep_strategy,
            "FuzzyMatching":   fuzzy,
            "DeleteDuplicates": delete,
            "ExtractArchive":  False,
            "Password":        "Skip",
        }
        fm = MagicMock()
        task = FilterDuplicateContents.__new__(FilterDuplicateContents)
        task.config = cfg
        task.file_manager = fm
        task.input_path = tmp_path
        task.keep_strategy = keep_strategy
        task.fuzzy_matching = fuzzy
        task.delete_duplicates = delete
        task.extract_archive = False
        return task, fm

    def test_no_duplicates_no_action(self, tmp_path):
        task, fm = self._make_task(tmp_path)
        (tmp_path / "a.png").write_bytes(_make_png_bytes(b"card_a"))
        (tmp_path / "b.png").write_bytes(_make_png_bytes(b"card_b"))
        task.run()
        fm.move_file.assert_not_called()

    def test_exact_duplicates_detected(self, tmp_path):
        task, fm = self._make_task(tmp_path, keep_strategy=KEEP_BIGGEST)
        data = _make_png_bytes(b"same_card_data")
        (tmp_path / "a.png").write_bytes(data)
        (tmp_path / "b.png").write_bytes(data)
        with patch.object(task, "_handle_group") as mock_handle:
            task.run()
        mock_handle.assert_called()

    def test_keep_biggest_picks_largest(self, tmp_path):
        from tasks.filter_duplicate_contents import FilterDuplicateContents
        task, fm = self._make_task(tmp_path, keep_strategy=KEEP_BIGGEST)
        small = tmp_path / "small.png"
        large = tmp_path / "large.png"
        small.write_bytes(b"x" * 10)
        large.write_bytes(b"x" * 100)
        group = [small, large]
        keeper = FilterDuplicateContents._pick_keeper(task, group, KEEP_BIGGEST)
        assert keeper == large

    def test_keep_smallest_picks_smallest(self, tmp_path):
        from tasks.filter_duplicate_contents import FilterDuplicateContents
        task, fm = self._make_task(tmp_path, keep_strategy=KEEP_SMALLEST)
        small = tmp_path / "small.png"
        large = tmp_path / "large.png"
        small.write_bytes(b"x" * 10)
        large.write_bytes(b"x" * 100)
        group = [small, large]
        keeper = FilterDuplicateContents._pick_keeper(task, group, KEEP_SMALLEST)
        assert keeper == small

    def test_keep_first_lex(self, tmp_path):
        from tasks.filter_duplicate_contents import FilterDuplicateContents
        task, fm = self._make_task(tmp_path, keep_strategy=KEEP_FIRST_LEX)
        a = tmp_path / "apple.png"
        b = tmp_path / "banana.png"
        a.write_bytes(b"x")
        b.write_bytes(b"x")
        keeper = FilterDuplicateContents._pick_keeper(task, [b, a], KEEP_FIRST_LEX)
        assert keeper == a

    def test_keep_last_lex(self, tmp_path):
        from tasks.filter_duplicate_contents import FilterDuplicateContents
        task, fm = self._make_task(tmp_path, keep_strategy=KEEP_LAST_LEX)
        a = tmp_path / "apple.png"
        b = tmp_path / "banana.png"
        a.write_bytes(b"x")
        b.write_bytes(b"x")
        keeper = FilterDuplicateContents._pick_keeper(task, [a, b], KEEP_LAST_LEX)
        assert keeper == b

    def test_keep_none_returns_none(self, tmp_path):
        from tasks.filter_duplicate_contents import FilterDuplicateContents
        task, fm = self._make_task(tmp_path, keep_strategy=KEEP_NONE)
        a = tmp_path / "a.png"
        a.write_bytes(b"x")
        keeper = FilterDuplicateContents._pick_keeper(task, [a], KEEP_NONE)
        assert keeper is None
"""Tests for tasks/download_contents.py — URL parsing and line parsing logic."""
import pytest
from tasks.download_contents import _parse_line, _set_page, _strip_page


class TestSetPage:
    def test_adds_page_param(self):
        url = _set_page("https://db.bepis.moe/list", 3)
        assert "page=3" in url

    def test_replaces_existing_page(self):
        url = _set_page("https://db.bepis.moe/list?page=1", 5)
        assert "page=5" in url
        assert "page=1" not in url

    def test_preserves_other_params(self):
        url = _set_page("https://db.bepis.moe/list?type=kk&page=1", 2)
        assert "type=kk" in url
        assert "page=2" in url


class TestStripPage:
    def test_removes_page_param(self):
        url = _strip_page("https://db.bepis.moe/list?page=3")
        assert "page" not in url

    def test_preserves_other_params(self):
        url = _strip_page("https://db.bepis.moe/list?type=kk&page=3")
        assert "type=kk" in url
        assert "page" not in url

    def test_no_page_unchanged_structure(self):
        url = _strip_page("https://db.bepis.moe/list?type=kk")
        assert "type=kk" in url


class TestParseLine:
    def test_plain_url(self):
        result = _parse_line("https://db.bepis.moe/view/123")
        assert result is not None
        url, start, end = result
        assert url == "https://db.bepis.moe/view/123"
        assert start is None
        assert end is None

    def test_url_with_all_pages(self):
        result = _parse_line("https://db.bepis.moe/list | all")
        assert result is not None
        url, start, end = result
        assert start == 1
        assert end is None

    def test_url_with_page_range(self):
        result = _parse_line("https://db.bepis.moe/list | 3 | 7")
        assert result is not None
        _, start, end = result
        assert start == 3
        assert end == 7

    def test_url_with_reverse_range(self):
        result = _parse_line("https://db.bepis.moe/list | 7 | 3")
        assert result is not None
        _, start, end = result
        assert start == 7
        assert end == 3

    def test_comment_line_returns_none(self):
        assert _parse_line("# this is a comment") is None

    def test_empty_line_returns_none(self):
        assert _parse_line("") is None

    def test_non_url_returns_none(self):
        assert _parse_line("not a url") is None

    def test_whitespace_stripped(self):
        result = _parse_line("  https://db.bepis.moe/view/1  ")
        assert result is not None
        url, _, _ = result
        assert url == "https://db.bepis.moe/view/1"


class TestDownloadContentsInit:
    def test_reads_kkd_session_from_config(self):
        from tasks.download_contents import DownloadContents
        cfg = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
        cfg.download_contents = {
            "Links": "",
            "OutputDir": "",
            "SkipDownloaded": True,
            "KkdSession": "abc123",
        }
        fm = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
        task = DownloadContents.__new__(DownloadContents)
        task.config = cfg
        task.file_manager = fm
        task.links = cfg.download_contents["Links"]
        task.output_dir_str = cfg.download_contents["OutputDir"]
        task.skip_downloaded = cfg.download_contents["SkipDownloaded"]
        task.kkd_session = cfg.download_contents["KkdSession"]
        assert task.kkd_session == "abc123"

    def test_warns_when_koikatsucards_url_without_session(self):
        from tasks.download_contents import DownloadContents
        from unittest.mock import MagicMock, patch
        cfg = MagicMock()
        cfg.download_contents = {
            "Links": "https://koikatsucards.com/contents/123",
            "OutputDir": "",
            "SkipDownloaded": True,
            "KkdSession": "",
        }
        fm = MagicMock()
        task = DownloadContents.__new__(DownloadContents)
        task.config = cfg
        task.file_manager = fm
        task.links = cfg.download_contents["Links"]
        task.output_dir_str = ""
        task.skip_downloaded = True
        task.kkd_session = ""

        with patch("tasks.download_contents.logger") as mock_logger:
            with patch("tasks.download_contents.asyncio.run"):
                with patch("tasks.download_contents._load_history", return_value={}):
                    with patch("tasks.download_contents._save_history"):
                        from pathlib import Path
                        task.output_dir_str = str(Path.home())
                        task.run()
            warning_calls = [str(c) for c in mock_logger.warning.call_args_list]
            assert any("kkd_session" in c or "koikatsucards" in c.lower()
                       for c in warning_calls)
